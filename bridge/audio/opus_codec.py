"""
Encode / decode OPUS ↔ PCM para el protocolo ESP32.
El dispositivo envía frames OPUS de 60ms a 16kHz mono.

Usa ctypes directo a libopus — sin dependencia de opuslib.
"""
import ctypes
import ctypes.util
import numpy as np

SAMPLE_RATE   = 16000          # ESP32 mic input rate
TTS_SAMPLE_RATE = 24000        # ESP32 speaker output rate (M5Stack CoreS3)
CHANNELS      = 1
FRAME_MS      = 60
FRAME_SAMPLES     = SAMPLE_RATE     * FRAME_MS // 1000   # 960 samples/frame  (mic)
TTS_FRAME_SAMPLES = TTS_SAMPLE_RATE * FRAME_MS // 1000   # 1440 samples/frame (tts)

# ── Cargar libopus ────────────────────────────────────────────────────────────
_lib_name = ctypes.util.find_library("opus") or "libopus.so.0"
_opus = ctypes.CDLL(_lib_name)

# Constantes de la API Opus
_OPUS_OK             = 0
_OPUS_APPLICATION_VOIP = 2048

# Prototipos de funciones
_opus.opus_decoder_create.restype  = ctypes.c_void_p
_opus.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]

_opus.opus_encoder_create.restype  = ctypes.c_void_p
_opus.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]

_opus.opus_decode.restype  = ctypes.c_int
_opus.opus_decode.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_int32,
    ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
]

_opus.opus_encode.restype  = ctypes.c_int
_opus.opus_encode.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_int,
    ctypes.c_char_p, ctypes.c_int32,
]

_opus.opus_encoder_ctl.restype  = ctypes.c_int
_opus.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

_opus.opus_decoder_destroy.restype = None
_opus.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

_opus.opus_encoder_destroy.restype = None
_opus.opus_encoder_destroy.argtypes = [ctypes.c_void_p]

_OPUS_SET_BITRATE_REQUEST = 4002


class _Decoder:
    def __init__(self):
        err = ctypes.c_int(0)
        self._ptr = _opus.opus_decoder_create(SAMPLE_RATE, CHANNELS, ctypes.byref(err))
        if err.value != _OPUS_OK or not self._ptr:
            raise RuntimeError(f"opus_decoder_create error {err.value}")

    def decode(self, data: bytes) -> bytes:
        out = ctypes.create_string_buffer(FRAME_SAMPLES * CHANNELS * 2)
        n = _opus.opus_decode(
            self._ptr,
            data, len(data),
            out, FRAME_SAMPLES, 0,
        )
        if n < 0:
            raise RuntimeError(f"opus_decode error {n}")
        return out.raw[: n * CHANNELS * 2]

    def __del__(self):
        if self._ptr:
            _opus.opus_decoder_destroy(self._ptr)


class _Encoder:
    """Encoder genérico — instanciar con _Encoder(sample_rate, frame_samples, bitrate)."""
    def __init__(self, sample_rate: int, frame_samples: int, bitrate: int):
        self._frame_samples = frame_samples
        err = ctypes.c_int(0)
        self._ptr = _opus.opus_encoder_create(
            sample_rate, CHANNELS, _OPUS_APPLICATION_VOIP, ctypes.byref(err)
        )
        if err.value != _OPUS_OK or not self._ptr:
            raise RuntimeError(f"opus_encoder_create error {err.value}")
        _opus.opus_encoder_ctl(self._ptr, _OPUS_SET_BITRATE_REQUEST, bitrate)

    def encode(self, pcm: bytes) -> bytes:
        out = ctypes.create_string_buffer(4000)
        n = _opus.opus_encode(
            self._ptr,
            pcm, self._frame_samples,
            out, len(out),
        )
        if n < 0:
            raise RuntimeError(f"opus_encode error {n}")
        return out.raw[:n]

    def __del__(self):
        if self._ptr:
            _opus.opus_encoder_destroy(self._ptr)


def create_decoder() -> _Decoder:
    return _Decoder()


def create_encoder() -> _Encoder:
    """Encoder para micrófono (16kHz, 16kbps) — no se usa en el pipeline actual."""
    return _Encoder(SAMPLE_RATE, FRAME_SAMPLES, 16000)


def create_tts_encoder() -> _Encoder:
    """Encoder para TTS output (24kHz, 24kbps) — enviar audio al ESP32."""
    return _Encoder(TTS_SAMPLE_RATE, TTS_FRAME_SAMPLES, 24000)


# Ganancia digital para el micrófono (compensa el volumen bajo del ESP32 CoreS3)
MIC_DIGITAL_GAIN = 5.0


def decode_opus_frames(frames: list[bytes], decoder: _Decoder) -> np.ndarray:
    """Lista de frames OPUS binarios → array PCM float32 normalizado [-1, 1]."""
    if not frames:
        return np.array([], dtype=np.float32)

    chunks = []
    for frame in frames:
        try:
            pcm_bytes = decoder.decode(frame)
            arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            arr = arr * MIC_DIGITAL_GAIN
            chunks.append(arr)
        except RuntimeError:
            pass  # Frame corrupto — ignorar

    return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)


def encode_pcm_to_opus(pcm_float: np.ndarray, encoder: _Encoder, frame_samples: int = FRAME_SAMPLES) -> list[bytes]:
    """Array PCM float32 → lista de frames OPUS binarios de 60ms."""
    if pcm_float.size == 0:
        return []

    pcm_int16 = (np.clip(pcm_float, -1.0, 1.0) * 32767).astype(np.int16)
    frames = []

    for i in range(0, len(pcm_int16) - frame_samples + 1, frame_samples):
        chunk = pcm_int16[i : i + frame_samples].tobytes()
        try:
            frames.append(encoder.encode(chunk))
        except RuntimeError:
            pass

    return frames
