"""
TTS — Edge-TTS primario, gTTS fallback, espeak offline último recurso.
Usa pipe en memoria en vez de archivos temporales.
Caché de PCM para frases repetidas.
"""
import os
import io
import asyncio
import logging
import hashlib
import re
import numpy as np

logger = logging.getLogger("mebot.tts")

SAMPLE_RATE_OUT = 24000   # M5Stack CoreS3 output sample rate
GTTS_LANG       = os.getenv("MEBOT_LANGUAGE", "es")
# NOTA: EDGE_VOICE se lee dinámicamente en cada llamada (no en importación)
# para que los perfiles de dispositivo puedan cambiarla en runtime
EDGE_VOICE_DEFAULT = "es-CO-SalomeNeural"
TTS_TIMEOUT     = 4.0      # timeout por intento


def sanitize_prices_for_tts(text: str) -> str:
    if not text:
        return text
    # Reemplazar "$ X pesos/cop" -> "X pesos" para evitar duplicar
    text = re.sub(r'\$\s*(\d+(?:[.,]\d+)*)\s*(?i:pesos|cop|colombianos)', r'\1 pesos', text)
    # Reemplazar "$ X" -> "X pesos"
    text = re.sub(r'\$\s*(\d+(?:[.,]\d+)*)', r'\1 pesos', text)
    return text

# Caché de PCM: text_hash → np.ndarray float32
_pcm_cache: dict[str, np.ndarray] = {}


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


async def _ffmpeg_to_pcm(audio_bytes: bytes, input_fmt: str = "mp3") -> np.ndarray:
    """Decodifica bytes de audio (MP3/WAV) → PCM float32 24kHz mono via ffmpeg pipe."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-f", "s16le",
        "-ar", str(SAMPLE_RATE_OUT),
        "-ac", "1",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(audio_bytes), timeout=TTS_TIMEOUT
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {stderr.decode(errors='replace')[:200]}")
    pcm = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


async def _edge_tts(text: str, voice: str | None = None) -> bytes:
    """Edge-TTS → bytes MP3 (lee la voz dinámicamente desde env si no se especifica)."""
    import edge_tts
    # Leer voz en tiempo de llamada (no en importación) para respetar cambios de perfil
    effective_voice = voice or os.getenv("MEBOT_TTS_VOICE", EDGE_VOICE_DEFAULT)
    communicate = edge_tts.Communicate(text, effective_voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def _gtts(text: str) -> bytes:
    """gTTS (Google) → bytes MP3."""
    from gtts import gTTS
    loop = asyncio.get_event_loop()
    def _run():
        tts = gTTS(text=text, lang=GTTS_LANG, slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        return buf.getvalue()
    return await loop.run_in_executor(None, _run)


async def _espeak_pcm(text: str) -> np.ndarray:
    """espeak-ng offline → pipe directo a ffmpeg → PCM float32 24kHz.
    Sin archivos temporales ni pyttsx3."""
    proc_espeak = await asyncio.create_subprocess_exec(
        "espeak-ng", "--stdout", "-v", "spanish+f3", "-s", "140", "-a", "200", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    proc_ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-f", "s16le",
        "-ar", str(SAMPLE_RATE_OUT),
        "-ac", "1",
        "-",
        stdin=proc_espeak.stdout,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    proc_espeak.stdout.close()
    stdout, _ = await asyncio.wait_for(
        proc_ffmpeg.communicate(), timeout=TTS_TIMEOUT
    )
    await proc_espeak.wait()
    if proc_ffmpeg.returncode != 0:
        raise RuntimeError("espeak → ffmpeg pipeline falló")
    pcm = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) > 0:
        logger.info(f"TTS espeak: {len(pcm)} samples @ 24kHz ({len(pcm)/24000:.2f}s)")
    return pcm


async def text_to_pcm(text: str, voice: str | None = None) -> np.ndarray:
    """
    Texto → array PCM float32 a 24kHz mono.
    Orden: Edge-TTS → gTTS → espeak offline.
    Resultados cacheados por texto.
    """
    text = sanitize_prices_for_tts(text)
    # Leer voz efectiva en tiempo de llamada para respetar cambios de perfil en runtime
    effective_voice = voice or os.getenv("MEBOT_TTS_VOICE", EDGE_VOICE_DEFAULT)

    h = _text_hash(text + effective_voice)
    if h in _pcm_cache:
        logger.debug(f"TTS cache hit: '{text[:30]}...'")
        return _pcm_cache[h]

    last_exc = None

    # Intento 1: Edge-TTS (rápido, buena calidad)
    try:
        logger.debug(f"TTS Edge-TTS: voice={effective_voice} text='{text[:50]}'")
        mp3 = await asyncio.wait_for(_edge_tts(text, effective_voice), timeout=TTS_TIMEOUT)
        logger.debug(f"TTS Edge-TTS: OK ({len(mp3)} bytes MP3)")
        pcm = await _ffmpeg_to_pcm(mp3)
        _pcm_cache[h] = pcm
        return pcm
    except Exception as exc:
        last_exc = exc
        logger.warning(f"TTS Edge-TTS falló ({exc})")

    # Intento 2: gTTS
    try:
        logger.debug(f"TTS gTTS: lang={GTTS_LANG} text='{text[:50]}'")
        mp3 = await asyncio.wait_for(_gtts(text), timeout=TTS_TIMEOUT)
        logger.debug(f"TTS gTTS: OK ({len(mp3)} bytes MP3)")
        pcm = await _ffmpeg_to_pcm(mp3)
        _pcm_cache[h] = pcm
        return pcm
    except Exception as exc:
        last_exc = exc
        logger.warning(f"TTS gTTS falló ({exc})")

    # Intento 3: espeak offline
    try:
        pcm = await asyncio.wait_for(_espeak_pcm(text), timeout=TTS_TIMEOUT)
        if len(pcm) > 0:
            _pcm_cache[h] = pcm
            return pcm
        raise RuntimeError("espeak produjo PCM vacío")
    except Exception as exc:
        last_exc = exc
        logger.warning(f"TTS espeak falló ({exc})")

    raise RuntimeError(f"Todos los TTS fallaron. Último error: {last_exc}")


def voice_for_language(lang: str) -> str:
    voices = {
        "es":    "es-CO-SalomeNeural",
        "es-co": "es-CO-SalomeNeural",
        "es-es": "es-ES-ElviraNeural",
        "en":    "en-US-AriaNeural",
        "pt":    "pt-BR-FranciscaNeural",
        "zh":    "zh-CN-XiaoxiaoNeural",
    }
    return voices.get(lang.lower(), EDGE_VOICE_DEFAULT)
