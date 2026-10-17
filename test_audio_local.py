#!/usr/bin/env python3
"""
test_audio_local.py — Mini-bridge local para probar speaker del ESP32
=====================================================================
Corre en el laptop. El ESP32 se conecta a este script en lugar del servidor AWS.

¿Qué hace?
  1. Responde al hello del ESP32 (24kHz)
  2. Escucha los frames Opus del micrófono
  3. Genera un tono de prueba (beep + voz sintetizada con espeak/say)
     y lo envía como Opus al speaker del ESP32
  4. Muestra energía RMS del micrófono en tiempo real

Prerequisitos (laptop):
  pip install fastapi uvicorn websockets numpy
  apt install libopus0 libopus-dev ffmpeg espeak  # Linux
  # macOS: brew install opus ffmpeg espeak

Uso:
  python3 test_audio_local.py

  Luego apunta el ESP32 a este laptop:
  - Opción A: cambia temporalmente en el servidor remoto la var MEBOT_PUBLIC_HOST
    al IP del laptop (ej: 192.168.1.X) y reinicia el contenedor.
  - Opción B (más fácil): en el .env local cambia MEBOT_PUBLIC_HOST=<tu IP local>
    y levanta el bridge localmente.

  La prueba más simple: escucha el log "RMS mic" — si sube al hablar, el micrófono
  funciona. Si el ESP32 reproduce el tono de prueba, el speaker funciona.
"""
import asyncio
import ctypes
import ctypes.util
import json
import logging
import math
import struct
import subprocess
import tempfile
import os
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s — %(message)s")
log = logging.getLogger("test_audio")

# ── Opus ─────────────────────────────────────────────────────────────────────
_lib = ctypes.CDLL(ctypes.util.find_library("opus") or "libopus.so.0")
_lib.opus_decoder_create.restype  = ctypes.c_void_p
_lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_encode.restype  = ctypes.c_int
_lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
_lib.opus_encoder_create.restype  = ctypes.c_void_p
_lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_decode.restype  = ctypes.c_int
_lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                              ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
_lib.opus_encoder_ctl.restype  = ctypes.c_int
_lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.opus_encoder_destroy.restype = None
_lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
_lib.opus_decoder_destroy.restype = None
_lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

MIC_RATE      = 16000
TTS_RATE      = 24000
FRAME_MS      = 60
MIC_FRAMES    = MIC_RATE  * FRAME_MS // 1000   # 960 samples
TTS_FRAMES    = TTS_RATE  * FRAME_MS // 1000   # 1440 samples
OPUS_APP_VOIP = 2048
OPUS_BITRATE  = 4002  # OPUS_SET_BITRATE_REQUEST


def _make_decoder(rate: int):
    err = ctypes.c_int(0)
    p = _lib.opus_decoder_create(rate, 1, ctypes.byref(err))
    assert p and err.value == 0, f"decoder error {err.value}"
    return p


def _make_encoder(rate: int, frame_samples: int, bitrate: int = 24000):
    err = ctypes.c_int(0)
    p = _lib.opus_encoder_create(rate, 1, OPUS_APP_VOIP, ctypes.byref(err))
    assert p and err.value == 0, f"encoder error {err.value}"
    _lib.opus_encoder_ctl(p, OPUS_BITRATE, bitrate)
    return p, frame_samples


def _decode(dec, data: bytes, frame_samples: int) -> bytes:
    buf = ctypes.create_string_buffer(frame_samples * 2)
    n = _lib.opus_decode(dec, data, len(data), buf, frame_samples, 0)
    return buf.raw[: n * 2] if n > 0 else b""


def _encode(enc_ptr, frame_samples: int, pcm_bytes: bytes) -> bytes:
    out = ctypes.create_string_buffer(4000)
    n = _lib.opus_encode(enc_ptr, pcm_bytes, frame_samples, out, len(out))
    return out.raw[:n] if n > 0 else b""


def _rms(pcm_bytes: bytes) -> float:
    n = len(pcm_bytes) // 2
    if n == 0: return 0.0
    samples = struct.unpack(f"{n}h", pcm_bytes)
    return (sum(s * s for s in samples) / n) ** 0.5 / 32768.0


# ── Generador de tono de prueba (beep 440Hz) ─────────────────────────────────

def _generate_tone_opus(enc_ptr, frame_samples: int,
                        freq: float = 440.0, duration_s: float = 1.5,
                        rate: int = TTS_RATE) -> list[bytes]:
    """Genera un beep sinusoidal y lo codifica en frames Opus."""
    import struct as st
    total = int(rate * duration_s)
    frames = []
    for i in range(0, total - frame_samples + 1, frame_samples):
        pcm = []
        for j in range(frame_samples):
            t = (i + j) / rate
            s = int(16000 * math.sin(2 * math.pi * freq * t))
            pcm.append(max(-32767, min(32767, s)))
        pcm_bytes = st.pack(f"{frame_samples}h", *pcm)
        frames.append(_encode(enc_ptr, frame_samples, pcm_bytes))
    return frames


def _generate_espeak_opus(text: str, enc_ptr, frame_samples: int) -> list[bytes]:
    """Convierte texto a PCM via espeak y lo codifica en Opus."""
    import struct as st, numpy as np
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    try:
        # espeak → WAV
        r = subprocess.run(
            ["espeak", "-v", "es", "-s", "130", "-w", tmp, text],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            log.warning(f"espeak falló, usando tono. Error: {r.stderr.decode()[:100]}")
            return []
        # WAV → PCM 24kHz mono via ffmpeg
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-f", "s16le", "-ar", str(TTS_RATE), "-ac", "1", "-"],
            capture_output=True, timeout=15)
        if r2.returncode != 0:
            return []
        pcm_all = np.frombuffer(r2.stdout, dtype=np.int16)
        frames = []
        for i in range(0, len(pcm_all) - frame_samples + 1, frame_samples):
            chunk = pcm_all[i:i+frame_samples].tobytes()
            frames.append(_encode(enc_ptr, frame_samples, chunk))
        return frames
    except Exception as e:
        log.warning(f"espeak/ffmpeg error: {e}")
        return []
    finally:
        os.unlink(tmp)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="test-audio-local")


LOCAL_IP = os.popen("ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \\K\\S+'").read().strip() or "127.0.0.1"
LOCAL_PORT = 16000
LOCAL_WS_URL = f"ws://{LOCAL_IP}:{LOCAL_PORT}/"


@app.get("/xiaozhi/ota/")
@app.post("/xiaozhi/ota/")
async def ota(request: Request):
    return JSONResponse({
        "server_time": {"timestamp": 0, "timezone_offset": -300},
        "websocket": {"url": LOCAL_WS_URL, "token": ""},
    })


@app.get("/xiaozhi/config/")
async def config():
    return JSONResponse({"bot_name": "TestLocal", "wifi_greet": "Test local activo"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/")
async def handler(ws: WebSocket):
    await ws.accept()
    sid = str(uuid.uuid4())[:8]
    log.info(f"[{sid}] ESP32 conectado")

    mic_dec  = _make_decoder(MIC_RATE)
    tts_enc, tts_fs = _make_encoder(TTS_RATE, TTS_FRAMES)

    listening = False
    frame_count = 0
    rms_sum = 0.0
    rms_n   = 0

    async def send_test_audio():
        """Envía un beep de prueba + mensaje de voz al ESP32."""
        session_id = sid

        await ws.send_text(json.dumps({"type": "llm", "emotion": "neutral", "text": "<TTS>"}))
        await ws.send_text(json.dumps({"type": "tts", "state": "start", "sample_rate": TTS_RATE}))
        await ws.send_text(json.dumps({"type": "tts", "state": "sentence_start",
                                       "text": "Prueba de audio local. El speaker funciona."}))

        # Intentar espeak, si falla usar tono
        frames = _generate_espeak_opus(
            "Prueba de audio local. El speaker funciona correctamente.", tts_enc, TTS_FRAMES)
        if not frames:
            log.info(f"[{sid}] espeak no disponible — usando tono 440Hz")
            frames = _generate_tone_opus(tts_enc, TTS_FRAMES, freq=440, duration_s=1.5)

        log.info(f"[{sid}] Enviando {len(frames)} frames de prueba ({len(frames)*0.06:.1f}s)")
        t0 = asyncio.get_event_loop().time()
        for i, frame in enumerate(frames):
            header = struct.pack(">BBH", 0, 0, len(frame))
            await ws.send_bytes(header + frame)
            elapsed = asyncio.get_event_loop().time() - t0
            delay = (i + 1) * 0.060 - elapsed
            if delay > 0.002:
                await asyncio.sleep(delay)

        await ws.send_text(json.dumps({"type": "tts", "state": "stop"}))
        log.info(f"[{sid}] Audio de prueba enviado.")

    try:
        # Enviar hello de bienvenida
        await ws.send_text(json.dumps({
            "type": "hello", "version": 3, "session_id": sid,
            "transport": "websocket",
            "audio_params": {"format": "opus", "sample_rate": TTS_RATE,
                             "channels": 1, "frame_duration": FRAME_MS},
        }))

        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            raw = msg.get("text")
            data_bytes = msg.get("bytes")

            if raw:
                d = json.loads(raw) if raw else {}
                t = d.get("type", "?")

                if t == "hello":
                    log.info(f"[{sid}] hello recibido — fw={d.get('version','?')} "
                             f"device={d.get('device_id','?')}")
                    await asyncio.sleep(0.5)
                    await send_test_audio()

                elif t == "listen":
                    mode  = d.get("mode", "")
                    state = d.get("state", "")
                    log.info(f"[{sid}] listen mode={mode!r} state={state!r}")
                    if mode in ("auto", "manual", "realtime") or state == "start":
                        listening = True
                        frame_count = 0
                        rms_sum = 0.0
                        rms_n   = 0
                        log.info(f"[{sid}] Escuchando micrófono...")
                    elif state == "stop":
                        listening = False
                        avg_rms = rms_sum / rms_n if rms_n else 0
                        log.info(f"[{sid}] Fin escucha — {frame_count} frames "
                                 f"RMS promedio={avg_rms:.4f} "
                                 f"({'MIC OK: hay señal' if avg_rms > 0.005 else 'ALERTA: señal muy baja'})")

                elif t == "abort":
                    listening = False
                    log.info(f"[{sid}] abort recibido")

            elif data_bytes and listening:
                pcm = _decode(mic_dec, data_bytes, MIC_FRAMES)
                rms = _rms(pcm)
                rms_sum += rms
                rms_n   += 1
                frame_count += 1
                if frame_count % 10 == 0:
                    bar = "█" * int(rms * 200)
                    log.info(f"[{sid}] MIC frame={frame_count:4d}  RMS={rms:.4f}  {bar}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.exception(f"[{sid}] Error: {e}")
    finally:
        avg_rms = rms_sum / rms_n if rms_n else 0
        log.info(f"[{sid}] Sesión cerrada — frames_mic={frame_count}  RMS_avg={avg_rms:.4f}")
        _lib.opus_decoder_destroy(mic_dec)
        _lib.opus_encoder_destroy(tts_enc)


if __name__ == "__main__":
    import uvicorn
    import socket
    # Mostrar IP local para configurar el ESP32
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"\n{'='*60}")
    print(f"  Test bridge corriendo en: ws://{local_ip}:16000/")
    print(f"  OTA endpoint:             http://{local_ip}:16000/xiaozhi/ota/")
    print(f"{'='*60}")
    print(f"  Para conectar el ESP32 a este bridge:")
    print(f"  Cambia MEBOT_PUBLIC_HOST={local_ip} en el servidor")
    print(f"  y reinicia el contenedor, O flashea con:")
    print(f"  CONFIG_XIAOZHI_OTA_URL=\"http://{local_ip}:16000/xiaozhi/ota/\"")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=16000, log_level="warning")
