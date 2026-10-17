"""
STT con soporte multi-proveedor (Groq Whisper API, Cloudflare Workers AI, faster-whisper local).
Aplica filtro Silero VAD para prevención de alucinaciones y ruido.
Actualiza dinámicamente el vocabulario usando el catálogo de productos de Ferretería.
"""
import os
import logging
import re
import io
import wave
import asyncio
import numpy as np
import httpx
from faster_whisper import WhisperModel
from faster_whisper.vad import get_speech_timestamps, collect_chunks, VadOptions

logger = logging.getLogger("mebot.stt")

SAMPLE_RATE   = 16000
WHISPER_MODEL = os.getenv("MEBOT_WHISPER_MODEL", "small")
LANGUAGE      = os.getenv("MEBOT_LANGUAGE", "es")

# Vocabulario base como frases de ejemplo (Whisper responde mejor a frases que a palabras sueltas)
_VOCABULARY = os.getenv("MEBOT_VOCABULARY", (
    "Hola, necesito un taladro eléctrico para el hogar. "
    "¿Cuánto cuesta la broca de cemento de cuatro pulgadas? "
    "¿Tiene disponible el disco de corte Bosch? "
    "Quiero crear una orden de mantenimiento para ferretería. "
    "¿Cuántos productos hay en bodega? ¿Cuál es el precio? "
    "Busco pintura, cemento, tornillos, tuercas, tubería eléctrica. "
    "Liliana, ¿qué inventario tienen de martillos y herramientas? "
    "Consulta disponible cantidad total lista todos los productos. "
    "Ferretería El Hogar almacén depósito stock estantería. "
))

# Vocabulario cargado dinámicamente desde el catálogo de Ferretería
_catalog_vocabulary = ""

# Alucinaciones conocidas de Whisper con audio vacío/ruidoso (español)
# Lista ampliada para cubrir salidas comunes con micrófono ESP32 de bajo volumen
_HALLUCINATIONS = {
    "subtítulos por la comunidad de amara.org",
    "subtitulos por la comunidad de amara.org",
    "amara.org",
    "subtítulos realizados por la comunidad de amara.org",
    "gracias por ver el video",
    "gracias por ver",
    "suscríbete",
    "www.",
    "http",
    # Silencio o ruido de fondo
    ".",
    "...",
    "music",
    "música",
    "[música]",
    "(música)",
    "[ruido]",
    "(ruido)",
    "[silencio]",
    "silencio",
    "[inaudible]",
    "(inaudible)",
    # Frases de relleno comunes de Whisper con baja SNR
    "gracias.",
    "ok.",
    "sí.",
    "eh.",
    "este.",
    "bueno.",
    "la transcripción no está disponible",
    "no audio",
    "subtitles",
    "transcription",
}

_model: WhisperModel | None = None


def load_model() -> None:
    global _model
    provider = os.getenv("MEBOT_STT_PROVIDER", "").lower()
    if not provider:
        if os.getenv("GROQ_API_KEY"):
            provider = "groq"
        elif os.getenv("CF_GATEWAY_TOKEN") or os.getenv("CLOUDFLARE_API_TOKEN"):
            provider = "cloudflare"
        else:
            provider = "local"

    if provider == "local":
        logger.info(f"Cargando faster-whisper '{WHISPER_MODEL}' en CPU...")
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        logger.info("Whisper listo.")
    else:
        logger.info(f"STT configurado con proveedor API '{provider}'. Omitiendo carga de faster-whisper local para ahorrar memoria.")


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        load_model()
    return _model


def _is_hallucination(text: str) -> bool:
    t = text.lower().strip()
    
    # 1. Hallucinaciones en la lista estática
    if any(h in t for h in _HALLUCINATIONS):
        return True
        
    # 2. Eliminar puntuación común para analizar
    clean_text = re.sub(r'[\s,\.\-]+', ' ', t).strip()
    words = clean_text.split()
    if not words:
        return True

    # 3. Zumbidos eléctricos (ej. "120v60hz", "120v60w", "120v", "60hz", "120v60hz 120v60hz", etc.)
    # Si todo el texto coincide con patrones de tensión/frecuencia
    is_electrical_noise = all(
        re.match(r'^(120v|60hz|120v60hz|120v60w|120v6040v60hz|240v|50hz|60w|120240v60hz)$', w)
        for w in words
    )
    if is_electrical_noise:
        return True

    # 4. Números extremadamente largos alucinados por Whisper (ej. "322042096420667" o "1148104157x102...")
    # Si alguna palabra es un número de más de 8 dígitos, o contiene secuencias numéricas muy largas con 'x'
    for w in words:
        if w.isdigit() and len(w) >= 8:
            return True
        if re.search(r'\d{8,}', w): # Secuencia de 8+ dígitos
            return True
        if re.match(r'^[0-9x]{10,}$', w): # Secuencia con x de 10+ caracteres
            return True

    # 5. Secuencia repetida o única de números sospechosos ("110", "114", "117") causados por estática
    # Si el texto consiste exclusivamente de estos números repetidos o patrones numéricos
    is_suspicious_seq = all(
        w in {"110", "114", "117", "ciento", "diez", "catorce", "y"} or w.isdigit()
        for w in words
    )
    if is_suspicious_seq:
        # Si la secuencia tiene números sospechosos o es de dígitos
        if any(sn in words for sn in {"110", "114", "117"}):
            return True
        if all(w.isdigit() for w in words):
            return True

    # 6. Caracteres no latinos (asiáticos/coreanos/chinos/japoneses/cirílicos) alucinados en silencio
    if re.search(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u0400-\u04ff]', t):
        return True

    # 7. Sílabas repetitivas alucinadas (ej. "ndo ndo ndo ndo") o palabras de relleno estático
    if re.search(r'(\b\w+\b)( \1){2,}', t):  # Cualquier palabra repetida 3+ veces consecutivas
        return True
    if any(filler in t for filler in ["ndo ndo ndo", "faq faq faq", "experiencia retenciera"]):
        return True

    return False




def _normalize_audio(pcm: np.ndarray) -> np.ndarray:
    """Normaliza volumen y recorta silencio al inicio/final.
    
    IMPORTANTE: el umbral de recorte es 0.001 (no 0.01) porque el micrófono
    del ESP32 S3 produce RMS ~ 0.004-0.013. Un umbral de 0.01 destruía el audio
    antes de enviarlo a Groq, causando transcripciones vacías.
    """
    # Umbral bajo (0.001) para no eliminar audio de bajo volumen del ESP32
    nonzero = np.where(np.abs(pcm) > 0.001)[0]
    if len(nonzero) > 1:
        # Mantener 100ms de contexto (1600 muestras a 16kHz) antes y después
        pcm = pcm[max(0, nonzero[0]-1600):min(len(pcm), nonzero[-1]+1600)]
    rms = np.sqrt(np.mean(pcm ** 2))
    # Normalizar a RMS = 0.15 (buen nivel para Whisper)
    if rms > 0.0005:
        gain = min(0.15 / rms, 10.0)  # ganancia máxima 10x (reducida de 20x para evitar amplificar ruido estático)
        pcm = pcm * gain
    max_val = np.max(np.abs(pcm))
    if max_val > 0.95:
        pcm = pcm * (0.95 / max_val)
    return pcm


def apply_vad_filter(pcm_float32: np.ndarray) -> np.ndarray:
    """Aplica filtro Silero VAD para remover ruido de fondo tenue."""
    try:
        threshold = float(os.getenv("MEBOT_VAD_THRESHOLD", "0.4"))
        vad_options = VadOptions(threshold=threshold)
        timestamps = get_speech_timestamps(pcm_float32, vad_options)
        if not timestamps:
            logger.debug("VAD: No se detectó habla en el audio. Retornando vacío.")
            return np.array([], dtype=np.float32)
        filtered = collect_chunks(pcm_float32, timestamps)
        logger.debug(f"VAD: Audio filtrado de {len(pcm_float32)} a {len(filtered)} muestras.")
        return filtered
    except Exception as exc:
        logger.warning(f"VAD: Error en filtro Silero VAD: {exc}. Usando audio sin filtrar.")
        return pcm_float32


def pcm_to_wav_bytes(pcm_float32: np.ndarray) -> bytes:
    """Convierte un array de PCM float32 a bytes de archivo WAV (16kHz, 16-bit, Mono)."""
    pcm_clipped = np.clip(pcm_float32, -1.0, 1.0)
    pcm_16 = (pcm_clipped * 32767).astype(np.int16)
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_16.tobytes())
    return wav_io.getvalue()


def get_combined_prompt() -> str:
    """Retorna el prompt de vocabulario para Groq Whisper.
    
    Estructura del prompt:
    1. Frase de priming de dominio (ayuda a Whisper a enfocar el idioma y contexto)
    2. Vocabulario base ERP + ferretería
    3. Vocabulario dinámico del catálogo (hasta límite de 800 chars total)
    
    Límite estricto: 800 chars (Groq API max = 896 chars).
    """
    # Frase de priming: orienta a Whisper hacia español colombiano y contexto de ferretería
    priming = "Ferretería Colombia precios herramientas: "
    base = os.getenv("MEBOT_VOCABULARY", _VOCABULARY)
    combined = f"{priming}{base}"
    if _catalog_vocabulary:
        combined = f"{combined} {_catalog_vocabulary}".strip()
    
    if len(combined) > 800:
        truncated = combined[:800]
        last_space = truncated.rfind(' ')
        if last_space != -1:
            combined = truncated[:last_space]
        else:
            combined = truncated
    return combined


async def transcribe_groq(wav_bytes: bytes, prompt: str, lang: str) -> str:
    """Llama a la API de transcripción de Groq (Whisper Large V3).
    
    Parámetros clave:
    - temperature=0: salida determinística (reduce alucinaciones en audio ruidoso)
    - response_format=verbose_json: permite inspeccionar avg_logprob en el futuro
    - prompt: vocabulario de dominio limitado a 800 chars (límite Groq = 896)
    """
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY no configurada")
    
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {key}"
    }
    files = {
        "file": ("speech.wav", wav_bytes, "audio/wav")
    }
    data = {
        "model": os.getenv("GROQ_STT_MODEL", "whisper-large-v3"),
        "language": lang,
        "temperature": "0",           # Determinístico — reduce alucinaciones con audio ruidoso
        "response_format": "verbose_json",  # Incluye segments con no_speech_prob y avg_logprob
    }
    if prompt:
        data["prompt"] = prompt
        
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text}")
        result = resp.json()
        # Filtrar segmentos con alta probabilidad de no-habla (no_speech_prob >= 0.6)
        segments = result.get("segments", [])
        if segments:
            valid = [s for s in segments if s.get("no_speech_prob", 0) < 0.6]
            if not valid:
                logger.info(f"STT (groq): todos los segmentos descartados por no_speech_prob alto")
                return ""
            text = " ".join(s.get("text", "") for s in valid).strip()
        else:
            # Fallback: respuesta sin segments (formato legacy)
            text = result.get("text", "").strip()
        return text


async def transcribe_cloudflare(wav_bytes: bytes) -> str:
    """Llama a la API de Workers AI Whisper vía Cloudflare AI Gateway."""
    account_id = os.getenv("CF_ACCOUNT_ID", "bd69a6636f91b1c0fe511aa72446bb52")
    gateway_id = os.getenv("CF_GATEWAY_ID", "poc-mebot")
    gateway_token = os.getenv("CF_GATEWAY_TOKEN", "")
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN") or os.getenv("CF_API_TOKEN", "")
    
    url = f"https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/workers-ai/@cf/openai/whisper"
    
    headers = {}
    if gateway_token:
        headers["cf-aig-authorization"] = f"Bearer {gateway_token}"
    if cf_api_token:
        headers["Authorization"] = f"Bearer {cf_api_token}"
    
    payload = {
        "audio": list(wav_bytes)
    }
    
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Cloudflare Gateway error {resp.status_code}: {resp.text}")
        return resp.json().get("result", {}).get("text", "").strip()


async def update_catalog_vocabulary_task():
    """Tarea periódica para actualizar el vocabulario de catálogo desde el ERP de Ferretería."""
    global _catalog_vocabulary
    url = os.getenv("FERRETERIA_ERP_URL", "https://erp.ferreteriaelhogar.com/admin-api")
    user = os.getenv("FERRETERIA_ERP_USER", "bot-agent@ferreteriaelhogar.com")
    pwd = os.getenv("FERRETERIA_ERP_PASS", "b0t_Ag3nt_s3cur3_2026")
    
    while True:
        try:
            logger.info("Actualizando vocabulario de catálogo desde el ERP...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Login
                login_payload = {
                    "query": "mutation Login($u:String!,$p:String!){login(username:$u,password:$p){__typename}}",
                    "variables": {"u": user, "p": pwd}
                }
                resp = await client.post(url, json=login_payload)
                if resp.status_code != 200:
                    logger.warning(f"Error login ERP para vocabulario status={resp.status_code}")
                    await asyncio.sleep(60)
                    continue
                token = resp.headers.get("vendure-auth-token", "")
                cookies = resp.cookies
                
                query_payload = {
                    "query": "query { products(options: { take: 200 }) { items { name } } }"
                }
                headers = {}
                if token:
                    headers["vendure-token"] = token
                resp = await client.post(url, json=query_payload, headers=headers, cookies=cookies)
                if resp.status_code == 200:
                    res = resp.json()
                    items = res.get("data", {}).get("products", {}).get("items", [])
                    words = set()
                    for item in items:
                        name = item.get("name", "")
                        if name:
                            for w in re.split(r'\s+', name.lower()):
                                w_clean = re.sub(r'[^\w]', '', w)
                                if len(w_clean) > 2 and w_clean not in {"con", "para", "del", "los", "las", "por", "una", "unos", "unas"}:
                                    words.add(w_clean)
                    
                    _catalog_vocabulary = " ".join(sorted(words))
                    logger.info(f"Vocabulario de catálogo actualizado: {len(words)} palabras únicas.")
                else:
                    logger.warning(f"Error query ERP para vocabulario status={resp.status_code}")
        except Exception as exc:
            logger.warning(f"Excepción al actualizar vocabulario de catálogo: {exc}")
        
        await asyncio.sleep(3600)


async def start_vocab_updater():
    """Lanza la tarea periódica de actualización de catálogo en background."""
    asyncio.create_task(update_catalog_vocabulary_task())


async def transcribe(pcm_float32: np.ndarray, language: str | None = None) -> str:
    """
    PCM float32 a 16kHz → texto transcripto.
    Soporta proveedores: groq, cloudflare, y local (faster-whisper).
    Aplica filtro Silero VAD a todos los proveedores (prevención de alucinaciones).
    Orden del pipeline: VAD crudo → normalizar → transcribir.
    """
    MIN_AUDIO_S = float(os.getenv("MEBOT_MIN_AUDIO_S", "1.0"))

    # Mínimo de audio para evitar enviar clips demasiado cortos.
    if pcm_float32.size < SAMPLE_RATE * MIN_AUDIO_S:
        logger.debug(f"STT: audio demasiado corto ({pcm_float32.size/SAMPLE_RATE:.2f}s < {MIN_AUDIO_S}s) — ignorado")
        return ""

    # 0. Determinar proveedor ANTES del filtrado
    provider = os.getenv("MEBOT_STT_PROVIDER", "").lower()
    if not provider:
        if os.getenv("GROQ_API_KEY"):
            provider = "groq"
        elif os.getenv("CF_GATEWAY_TOKEN") or os.getenv("CLOUDFLARE_API_TOKEN"):
            provider = "cloudflare"
        else:
            provider = "local"

    # 1. Silero VAD PRIMERO (sobre audio crudo — máxima discriminación habla vs ruido)
    #    VAD opera mejor sin amplificación previa, porque fue entrenado con señales naturales.
    pcm_float32 = apply_vad_filter(pcm_float32)
    if pcm_float32.size < SAMPLE_RATE * MIN_AUDIO_S:
        logger.debug(f"STT: Audio filtrado por VAD demasiado corto (< {MIN_AUDIO_S}s) — ignorado")
        return ""

    # 2. Normalizar volumen DESPUÉS del VAD (solo amplificar habla real confirmada)
    pcm_float32 = _normalize_audio(pcm_float32)
    if pcm_float32.size < SAMPLE_RATE * MIN_AUDIO_S:
        logger.debug(f"STT: Audio tras normalización demasiado corto (< {MIN_AUDIO_S}s) — ignorado")
        return ""

    lang = language or LANGUAGE
    prompt = get_combined_prompt()

    logger.debug(f"STT: Usando proveedor '{provider}' para transcripción ({pcm_float32.size/SAMPLE_RATE:.1f}s audio).")

    if provider == "groq":
        try:
            wav_bytes = pcm_to_wav_bytes(pcm_float32)
            text = await transcribe_groq(wav_bytes, prompt, lang)
            if _is_hallucination(text):
                logger.warning(f"STT (groq): Alucinación detectada y descartada: '{text}'")
                return ""
            logger.info(f"STT (groq) resultado: '{text}'")
            return text
        except Exception as exc:
            logger.error(f"STT (groq) falló: {exc}. Reintentando con local...")
            provider = "local"

    if provider == "cloudflare":
        try:
            wav_bytes = pcm_to_wav_bytes(pcm_float32)
            text = await transcribe_cloudflare(wav_bytes)
            if _is_hallucination(text):
                logger.warning(f"STT (cloudflare): Alucinación detectada y descartada: '{text}'")
                return ""
            logger.info(f"STT (cloudflare) resultado: '{text}'")
            return text
        except Exception as exc:
            logger.error(f"STT (cloudflare) falló: {exc}. Reintentando con local...")
            provider = "local"

    if provider == "local":
        model = _get_model()
        
        def run_local():
            segments_gen, info = model.transcribe(
                pcm_float32,
                language=lang,
                beam_size=5,
                vad_filter=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.7,
                log_prob_threshold=-1.0,
                initial_prompt=prompt,
            )
            return list(segments_gen), info

        try:
            segments, info = await asyncio.to_thread(run_local)
            if not segments:
                return ""
            valid = [s for s in segments if s.no_speech_prob < 0.6]
            if not valid:
                logger.debug("STT (local): todos los segmentos descartados (no_speech_prob alto)")
                return ""
            text = " ".join(s.text for s in valid).strip()
            if _is_hallucination(text):
                logger.warning(f"STT (local): Alucinación detectada y descartada: '{text}'")
                return ""
            logger.debug(f"STT (local) resultado: '{text}' (lang={info.language})")
            return text
        except Exception as exc:
            logger.error(f"STT (local) falló: {exc}")
            return ""

    return ""
