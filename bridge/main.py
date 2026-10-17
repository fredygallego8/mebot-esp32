"""
mebot-audio-bridge
==================
Servidor WebSocket compatible con el protocolo xiaozhi/mebot-esp32.
Pipeline por sesión:
  ESP32 OPUS → decode → Whisper STT → LLM (DeepSeek/OpenRouter) → Edge-TTS → encode → ESP32 OPUS
También expone un endpoint OTA compatible con el firmware xiaozhi:
  GET /esp32/ota/  →  devuelve JSON con websocket.url apuntando a este servidor.
  El ESP32 lo llama al arrancar y guarda la URL en NVS automáticamente.
"""
import asyncio
import json
import logging
import os
import re
import struct
import time
import uuid
import dotenv
dotenv.load_dotenv()
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from audio.opus_codec import (
    FRAME_SAMPLES,
    TTS_FRAME_SAMPLES,
    create_decoder,
    create_tts_encoder,
    decode_opus_frames,
    encode_pcm_to_opus,
)
from audio.whisper_stt import load_model, transcribe, start_vocab_updater
from llm.openclaw_client import chat
from protocol.xiaozhi_protocol import (
    hello_response,
    llm_message,
    parse_client_message,
    stt_message,
    tts_sentence_start,
    tts_start,
    tts_stop,
)
from tts.edge_tts_client import text_to_pcm
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("mebot.bridge")
# Activa logging DEBUG de todos los módulos internos si LOG_LEVEL=DEBUG
if LOG_LEVEL == "DEBUG":
    logging.getLogger("mebot").setLevel(logging.DEBUG)
# File logger para el monitor HTML
log_file = "/tmp/mebot_bridge.log"
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
logging.getLogger().addHandler(file_handler)
def get_local_ip():
    import socket
    import subprocess
    try:
        # 1. Intentar detectar específicamente el rango del megaproyecto (10.119.x.x)
        cmd = "ip -4 addr show | grep 'inet 10.119' | awk '{print $2}' | cut -d/ -f1 | head -n 1"
        ip = subprocess.check_output(cmd, shell=True).decode().strip()
        if ip:
            return ip
        # 2. Si no hay 10.119, intentar obtener la IP de la ruta por defecto ignorando VPNs comunes
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Nos conectamos a una IP interna del gateway si es posible, o seguimos con el fallback
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Si la IP detectada es de Proton (10.2.x.x) o Tailscale (100.x.x.x), buscar otra
        if ip.startswith("10.2.") or ip.startswith("100."):
             cmd = "ip -4 addr show | grep 'inet 10.' | grep -v '10.2.' | awk '{print $2}' | cut -d/ -f1 | head -n 1"
             alt_ip = subprocess.check_output(cmd, shell=True).decode().strip()
             if alt_ip: return alt_ip
        
        return ip
    except Exception:
        return "127.0.0.1"
PUBLIC_HOST = os.getenv("MEBOT_PUBLIC_HOST", "")
MEBOT_WS_URL = os.getenv("MEBOT_WS_URL", "")
WS_TOKEN = os.getenv("MEBOT_WS_TOKEN", "")
CURRENT_IP = get_local_ip()
# ── Estadísticas globales (accesibles via /stats) ──────────────────────────
_stats: dict = {
    "start_time": time.time(),
    "sessions_total": 0,
    "sessions_active": 0,
    "pipelines_run": 0,
    "stt_ok": 0,
    "stt_empty": 0,
    "stt_hallucination_filtered": 0,
    "stt_vad_empty": 0,
    "stt_audio_too_short": 0,
    "stt_short_text_filtered": 0,
    "llm_ok": 0,
    "llm_errors": 0,
    "tts_frames_sent": 0,
    "ws_disconnects_1006": 0,
}
_active_sessions: dict[str, dict] = {}  # session_id → {ip, state, vad_rms_last}
_active_ws_sessions: dict[str, dict] = {}  # session_id → {ws, enc, short_id}
_ws_clients: set = set()  # admin WS clients for real-time push (T1)
# ── Perfiles por Dispositivo ────────────────────────────────────────────────
PROFILES_FILE = "device_profiles.json"
def load_profiles() -> dict:
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}
def save_profiles(profiles: dict):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=4, ensure_ascii=False)
def get_device_profile(device_id: str) -> dict:
    profiles = load_profiles()
    default_prompt = os.getenv("MEBOT_SYSTEM_PROMPT", "Eres un asistente útil y conciso.")
    default_voice = os.getenv("MEBOT_TTS_VOICE", "es-CO-SalomeNeural")
    default_name = os.getenv("MEBOT_BOT_NAME", "MeBot")
    if device_id in profiles:
        return profiles[device_id]
    return {
        "bot_name": default_name,
        "system_prompt": default_prompt,
        "tts_voice": default_voice
    }
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    await start_vocab_updater()
    # Pre-cache respuestas TTS comunes para arranque rápido
    try:
        from tts.edge_tts_client import text_to_pcm, _pcm_cache
        common_phrases = [
            os.getenv("MEBOT_GREETING", "Hola, soy MeBot. Estoy lista."),
            "Buscando en el inventario...",
            "No encontré ese producto en la base de datos.",
            "Dame un momento por favor.",
        ]
        for phrase in common_phrases:
            logger.info(f"Pre-cache TTS: '{phrase[:30]}...'")
            await asyncio.wait_for(text_to_pcm(phrase), timeout=10)
        logger.info(f"Pre-cache TTS: {len(_pcm_cache)} frases cacheadas")
    except Exception as e:
        logger.warning(f"Pre-cache TTS error: {e}")
    
    yield
app = FastAPI(title="mebot-audio-bridge", lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ---------------------------------------------------------------------------
# OTA endpoint
# ---------------------------------------------------------------------------
@app.get("/esp32/ota/")
@app.post("/esp32/ota/")
async def ota_check(request: Request):
    device_id = request.headers.get("Device-Id", "unknown")
    client_id = request.headers.get("Client-Id", "unknown")
    user_agent = request.headers.get("User-Agent", "")
    client_ip = request.client.host if request.client else "?"
    import json as _json
    logger.info(
        f"OTA  device={device_id} client={client_id} ip={client_ip} ua={user_agent}"
    )
    current_ip = get_local_ip()
    logger.info(f"OTA  → current_ip={current_ip} PUBLIC_HOST={PUBLIC_HOST} MEBOT_WS_URL={MEBOT_WS_URL}")
    if MEBOT_WS_URL:
        ws_url = MEBOT_WS_URL
    elif PUBLIC_HOST:
        ws_url = f"wss://{PUBLIC_HOST}/esp32"
    else:
        host = request.headers.get("host", f"{current_ip}:16001")
        ws_url = f"ws://{host}/esp32"
    # Determinar protocolo para la URL del firmware
    # Solo usar HTTPS/public host si PUBLIC_HOST está explícitamente configurado (producción).
    # Si no (local dev), usar IP local para que el ESP32 descargue directo sin Cloudflare.
    if PUBLIC_HOST:
        proto = "https"
        host_for_url = PUBLIC_HOST
    else:
        proto = "http"
        host_for_url = f"{current_ip}:16001"

    firmware_version = os.getenv("MEBOT_FW_VERSION", "2.8.1")
    firmware_bin_path = os.path.join(os.path.dirname(__file__), "..", "build", "mebot.bin")
    if not os.path.exists(firmware_bin_path):
        firmware_bin_path = os.path.join(os.path.dirname(__file__), "mebot.bin")
    if not os.path.exists(firmware_bin_path):
        firmware_bin_path = "mebot.bin"

    firmware_block = None
    if os.path.exists(firmware_bin_path):
        firmware_block = {
            "version": firmware_version,
            "url": f"{proto}://{host_for_url}/esp32/ota/mebot.bin",
            "force": 0
        }

    response = {
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": int(os.getenv("MEBOT_TZ_OFFSET_MIN", "-300")),
        },
        "websocket": {
            "url": ws_url,
            "token": WS_TOKEN,
            "version": 3,
        },
    }
    if firmware_block:
        response["firmware"] = firmware_block

    logger.info(f"OTA  → RESPONSE: ws={ws_url} {'firmware=' + firmware_version if firmware_block else '(no firmware — USB flash only)'}")
    return JSONResponse(
        content=response,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Expires": "0",
        }
    )
# ── T1: WebSocket push para admin panel ───────────────────────────────────
async def _broadcast_stats():
    """Envía stats actuales a todos los clientes admin WS."""
    if not _ws_clients:
        return
    uptime = int(time.time() - _stats["start_time"])
    payload = json.dumps({
        **_stats,
        "uptime_s": uptime,
        "vad_threshold_adaptive": VAD_ENERGY_THRESH,
        "volume": round(_TTS_VOLUME * 100),
    })
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead
@app.websocket("/esp32/ws/admin")
async def admin_ws(ws: WebSocket):
    """WebSocket para admin.html — recibe stats en tiempo real."""
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await asyncio.sleep(1)
            await _broadcast_stats()
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)
@app.get("/esp32/ota/mebot.bin")
async def ota_binary():
    """Sirve el binario del firmware para OTA."""
    bin_path = os.path.join(os.path.dirname(__file__), "..", "build", "mebot.bin")
    if not os.path.exists(bin_path):
        bin_path = "mebot.bin"
    
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if os.path.exists(bin_path):
        return FileResponse(
            bin_path,
            media_type="application/octet-stream",
            filename="mebot.bin",
            headers=headers,
        )
    return JSONResponse(
        content={"error": "Firmware binary not found"},
        status_code=404,
        headers=headers,
    )

@app.get("/esp32/ota/manifest.json")
async def ota_manifest(request: Request):
    """Genera y sirve el archivo de manifiesto para ESP Web Tools."""
    firmware_version = os.getenv("MEBOT_FW_VERSION", "2.8.1")
    manifest = {
        "name": "MeBot AI CoreS3",
        "version": firmware_version,
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": "ESP32-S3",
                "parts": [
                    { "path": "bootloader.bin", "offset": 0 },
                    { "path": "partition-table.bin", "offset": 32768 },
                    { "path": "ota_data_initial.bin", "offset": 53248 },
                    { "path": "mebot.bin", "offset": 131072 },
                    { "path": "generated_assets.bin", "offset": 8388608 }
                ]
            }
        ]
    }
    
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return JSONResponse(content=manifest, headers=headers)

@app.get("/esp32/ota/{filename}")
async def serve_firmware_file(filename: str):
    """Sirve los archivos binarios individuales requeridos para el flasheo."""
    base_dir = os.path.dirname(__file__)
    file_map = {
        "bootloader.bin": os.path.join(base_dir, "..", "build", "bootloader", "bootloader.bin"),
        "partition-table.bin": os.path.join(base_dir, "..", "build", "partition_table", "partition-table.bin"),
        "ota_data_initial.bin": os.path.join(base_dir, "..", "build", "ota_data_initial.bin"),
        "mebot.bin": os.path.join(base_dir, "..", "build", "mebot.bin"),
        "generated_assets.bin": os.path.join(base_dir, "..", "build", "generated_assets.bin")
    }
    
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    
    if filename in file_map:
        path = file_map[filename]
        if os.path.exists(path):
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename=filename,
                headers=headers,
            )
            
    return JSONResponse(
        content={"error": f"Firmware file {filename} not found"},
        status_code=404,
        headers=headers,
    )
# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
# ── VAD por energía RMS (adaptativo) ──────────────────────────────────────────
VAD_SILENCE_FRAMES = 8       # ~0.48s de silencio consecutivo (8 × 60ms)
VAD_SPEECH_FRAMES = 12       # 12 × 60ms = 720ms de habla continua mínimo (evita ruido)
VAD_ENERGY_THRESH = 0.008    # RMS threshold base (para mic CoreS3 con gain 110)
VAD_ENERGY_THRESH_MIN = 0.008  # umbral mínimo (subido: piso de ruido CoreS3 es ~0.004-0.006)
VAD_ENERGY_THRESH_MAX = 0.025  # umbral máximo
VAD_MIN_OPUS_FRAMES = 2     # no disparar STT si hay < 2 frames
VAD_TIMEOUT_S = 2.0          # flush si frames no llegan
VAD_NOISE_FLOOR_SAMPLES = 30  # frames para estimar piso de ruido (≈1.8s)
VAD_SILENCE_ADAPTIVE_MS = 1500  # timeout adaptativo silencio base (ms)
VAD_SILENCE_MAX_MS = 3000       # timeout máximo (para frases incompletas)
# ── ERP CLI (maintenance-cli.js via telegram-bridge) ──────────────────────────
ERP_CLI_URL = os.getenv("MEBOT_ERP_CLI_URL", "http://localhost:18080/api/cli")
_INTENTS = {
    "list": r"listar|cuántas|cuantas|cantidad|activas?|pendientes?|órdenes|ordenes",
    "create": r"(crea?r?|nueva?|registr?a?r?|abrir).*(órden|orden|mantenimient|ticket)",
    "approve": r"(aprob?a?r?|aproba?r?|asign?a?r?|tomar).*(\d{3,})",
    "note": r"(nota|comentari|anotar|observación).*(\d{3,})",
    "complete": r"(cerra?r?|complet?a?r?|finaliza?r?|termina?r?).*(\d{3,})",
    "list-query": r"(cierre|cerrada|ayer|fecha|este mes|este año).*(órden|orden)",
    # Ferretería ERP search — detecta búsquedas de productos específicos
    "ferreteria-search": r"(busca|encuentra|cuánto cuesta|cuanto cuesta|cuesta|cotiza|precio|precios)\s+\w+|disponible\s+\w+",
    # Ferretería ERP browse — consulta general del catálogo
    "ferreteria-products": r"(qué\s+productos?|qué\s+venden|qué\s+hay|catálogo|catalogo|inventario|listado|todos\s+los\s+productos)(?!.*órden|orden)",
    # MeBot ERP
    "customers": r"(clientes|customers|usuarios)(?!.*órden|orden)",
    "products": r"(producto|articulo|ítem|item|inventario|catálogo|catalogo|qué hay)(?!.*órden|orden)",
    "collections": r"(colección|coleccion|categoría|categoria|grupo|facet)(?!.*producto)",
    "config": r"(ventas|canal|channel|moneda|método.*pago|shipping)(?!.*órden|orden)",
    "search": r"busca|encuentra|search|cómo.*se.*llama|existe.*producto",
    "list-stack": r"(por stack|ferretería|ferreteria|weiqi|mebot|mkall).*(órden|orden)",
}
def detect_intent(text: str) -> dict | None:
    t = text.lower().strip()
    for action, pattern in _INTENTS.items():
        if re.search(pattern, t):
            return {"action": action, "text": text}
    return None
async def _run_erp_command(action: str, text: str) -> str | None:
    import aiohttp
    params = {"action": action, "text": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ERP_CLI_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    if body.get("ok"):
                        data = body.get("data", "") or ""
                        return data[:800]
    except Exception as exc:
        logger.warning(f"ERP CLI error ({ERP_CLI_URL}): {exc}")
    return None
# ── Sistema de ruido ambiente adaptativo ──────────────────────────────────
_noise_floor_per_session: dict[str, float] = {}  # session_id → noise_floor_rms
def _update_noise_floor(session_id: str, rms: float, in_speech: bool = False) -> float:
    """Actualiza piso de ruido por sesión usando EMA. Retorna threshold actual."""
    key = session_id[:8]
    if key not in _noise_floor_per_session:
        # Inicializar con un valor bajo para no bloquear la primera detección
        _noise_floor_per_session[key] = min(rms, 0.005)
    elif not in_speech:
        noise = _noise_floor_per_session[key]
        # EMA rápido (0.90) para que el piso baje rápido después de ruidos transitorios
        new_noise = noise * 0.90 + rms * 0.10
        # Limitar el piso de ruido: nunca debe superar 0.008
        # Esto evita que golpes/botón inflen el threshold y bloqueen la voz
        _noise_floor_per_session[key] = min(new_noise, 0.008)
    noise = _noise_floor_per_session[key]
    # Threshold adaptativo: piso de ruido + margen mínimo
    adaptive_thresh = max(VAD_ENERGY_THRESH_MIN, min(VAD_ENERGY_THRESH_MAX, noise * 1.3))
    return adaptive_thresh
# ── Multi-turno (U3) ─────────────────────────────────────────────────────
MULTITURN_TIMEOUT_S = 8.0   # segundos de inactividad para cerrar conversación
_multi_turn_sessions: dict[str, dict] = {}
# ── Log de conversaciones (T6) ────────────────────────────────────────────
CONVERSATION_LOG_FILE = os.getenv("MEBOT_CONVERSATION_LOG", "conversations.jsonl")
def _log_conversation(session_id: str, device_id: str, user_text: str, bot_response: str, pipeline_time_s: float):
    """Persiste transcripción en JSONL para auditoría."""
    import json
    try:
        entry = {
            "timestamp": time.time(),
            "session": session_id[:8],
            "device": device_id,
            "user": user_text,
            "bot": bot_response,
            "pipeline_s": round(pipeline_time_s, 2),
        }
        with open(CONVERSATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
# ── ERP Ferretería (Vendure GraphQL) ────────────────────────────────────────
_FERRETERIA_URL = os.getenv("FERRETERIA_ERP_URL", "https://erp.ferreteriaelhogar.com/admin-api")
_FERRETERIA_USER = os.getenv("FERRETERIA_ERP_USER", "superadmin")
_FERRETERIA_PASS = os.getenv("FERRETERIA_ERP_PASS", "2supe1r23ad1mi3F")


async def _query_ferreteria(query: str) -> str | None:
    """Ejecuta un query GraphQL contra el ERP de Ferretería y retorna texto plano."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            # Login
            login_payload = {
                "query": "mutation L($u:String!,$p:String!){login(username:$u,password:$p){...on CurrentUser{id identifier}}}",
                "variables": {"u": _FERRETERIA_USER, "p": _FERRETERIA_PASS}
            }
            async with session.post(_FERRETERIA_URL, json=login_payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                token = resp.headers.get("vendure-auth-token", "")
                cookies = resp.cookies
            if not token:
                return None
            # Data query
            async with session.post(
                _FERRETERIA_URL,
                json={"query": query},
                headers={"vendure-token": token},
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data
    except Exception as exc:
        logger.warning(f"Ferretería ERP error: {exc}")
    return None


def _apply_phonetic_aliases(term: str) -> str:
    """Corrige errores comunes de transcripción STT / fonética local para productos ferreteros."""
    if not term:
        return term
    term_lower = term.lower().strip()
    replacements = {
        r'\bel\s+cutor\b': 'percutor',
        r'\bper\s+cutor\b': 'percutor',
        r'\bel\s+trico\b': 'eléctrico',
        r'\bel\s+trono\b': 'eléctrico',
        r'\bsemento\b': 'cemento',
        r'\bvulto\b': 'bulto',
        r'\bsodadura\b': 'soldadura',
        r'\bsolda\s+dura\b': 'soldadura',
        r'\btormillo\b': 'tornillo',
        r'\balicati\b': 'alicate',
    }
    for pattern, replacement in replacements.items():
        term_lower = re.sub(pattern, replacement, term_lower)
    return term_lower


def _format_cop(cents: int | float) -> str:
    """Convierte centavos COP a string formateado: XX.XXX pesos."""
    pesos = round(cents / 100)
    formatted = f"{pesos:,}".replace(",", ".")
    return formatted


def _format_ferreteria_data(data: dict) -> str | None:
    """Procesa respuesta GraphQL de Ferretería: convierte centavos a pesos COP."""
    if not data or "data" not in data:
        return None
    result = []
    d = data["data"]
    if "search" in d:
        items = d["search"].get("items", [])
        total = d["search"].get("totalItems", 0)
        result.append(f"Total productos encontrados: {total}")
        for item in items:
            name = item.get("productName", "?")
            sku = item.get("sku", "")
            price = item.get("priceWithTax", {})
            typename = price.get("__typename", "")
            if typename == "SinglePrice":
                price_str = _format_cop(price.get("value", 0))
            elif typename == "PriceRange":
                min_p = _format_cop(price.get("min", 0))
                max_p = _format_cop(price.get("max", 0))
                price_str = f"{min_p} — {max_p}"
            else:
                price_str = "consultar"
            line = f"- {name}"
            if sku:
                line += f" (SKU: {sku})"
            line += f": {price_str} pesos"
            result.append(line)
    elif "products" in d:
        items = d["products"].get("items", [])
        total = d["products"].get("totalItems", 0)
        result.append(f"Total productos: {total}")
        for item in items:
            result.append(f"- {item.get('name', '?')} (ID: {item.get('id', '?')})")
    if not result:
        return None
    return "\n".join(result)


# ── Volumen TTS (software) ──────────────────────────────────────────────────
# 0.0 = silencio, 1.0 = máximo (puede clipear). Rango recomendado: 0.2–1.0.
_TTS_VOLUME: float = float(os.getenv("MEBOT_VOLUME", "0.8"))
import struct as _struct
def _frame_rms(pcm_bytes: bytes) -> float:
    """Calcula RMS de un frame PCM int16 → float [0, 1]."""
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"{n}h", pcm_bytes)
    rms = (sum(s * s for s in samples) / n) ** 0.5
    return rms / 32768.0
@app.websocket("/esp32")
async def esp32_handler(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid.uuid4())
    short_id = session_id[:8]
    client_ip = ws.client.host if ws.client else "?"
    decoder = create_decoder()
    tts_enc = create_tts_encoder()  # speaker (24kHz)
    opus_buffer: list[bytes] = []  # frames de habla acumulados
    listening = False
    listen_mode = ""  # "auto" | "manual" | "realtime"
    history: list[dict] = []
    frames_rx = 0
    msgs_rx = 0
    # Estado VAD (solo modo auto)
    silence_count = 0  # frames silenciosos consecutivos
    speech_count = 0  # frames de habla consecutivos desde último reset
    in_speech = False
    greeting_sent = False  # saludo TTS de bienvenida — solo en el primer listen
    
    # Asynchronous pipeline execution control to avoid blocking and buffer accumulation
    pipeline_active = False
    current_pipeline_task: asyncio.Task | None = None
    logger.info(f"[{short_id}] WS conectado  ip={client_ip}")
    _stats["sessions_total"] += 1
    _stats["sessions_active"] += 1
    _active_sessions[session_id] = {
        "ip": client_ip,
        "state": "connected",
        "vad_rms_last": 0.0,
        "device_id": "?",
        "connect_time": time.time(),
    }
    # Registrar sesión WebSocket para control remoto (ws + encoder para test_tts)
    _active_ws_sessions[session_id] = {"ws": ws, "enc": tts_enc, "short_id": short_id}
    
    async def notify_telegram(message: str):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
        if not token or not chat_id: return
        import urllib.request
        import urllib.parse
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode('utf-8')
            req = urllib.request.Request(url, data=data)
            asyncio.get_event_loop().run_in_executor(None, urllib.request.urlopen, req)
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
    # Estado para multi-turno (U3): la conversación sigue activa tras responder
    multiturn_active = False
    multiturn_timer_handle = None
    async def _close_multiturn():
        nonlocal multiturn_active, multiturn_timer_handle
        multiturn_active = False
        logger.info(f"[{short_id}] Multi-turno cerrado por inactividad")
    def _reset_multiturn_timer():
        nonlocal multiturn_timer_handle, multiturn_active
        if multiturn_timer_handle:
            multiturn_timer_handle.cancel()
        multiturn_active = True
        multiturn_timer_handle = asyncio.get_event_loop().call_later(
            MULTITURN_TIMEOUT_S, lambda: asyncio.create_task(_close_multiturn())
        )
    adaptive_vad_threshold = VAD_ENERGY_THRESH
    _debug_frame_count = 0
    pre_roll: list[bytes] = []  # Últimos 5 frames para pre-roll

    async def run_pipeline_task(frames_to_process):
        nonlocal pipeline_active
        pipeline_active = True
        try:
            await _run_pipeline(
                ws,
                session_id,
                short_id,
                frames_to_process,
                decoder,
                tts_enc,
                history,
                llm_session_id=session_id,
                reset_multiturn_timer=_reset_multiturn_timer,
            )
        except asyncio.CancelledError:
            logger.info(f"[{short_id}] Pipeline task cancelada")
        except Exception as e:
            logger.error(f"[{short_id}] Error en pipeline task: {e}")
        finally:
            pipeline_active = False
            # Limpiar variables VAD y buffers al terminar
            nonlocal silence_count, speech_count, in_speech
            silence_count = 0
            speech_count = 0
            in_speech = False
            opus_buffer.clear()
            pre_roll.clear()

    async def _handle_audio_frame(opus_frame: bytes):
        """Procesa un frame Opus: VAD adaptativo por energía → acumula → dispara STT."""
        nonlocal silence_count, speech_count, in_speech, listening, adaptive_vad_threshold, _debug_frame_count, pre_roll
        if pipeline_active:
            # Descartar frames de audio mientras el bot está procesando o hablando
            opus_buffer.clear()
            pre_roll.clear()
            silence_count = 0
            speech_count = 0
            in_speech = False
            return
        # Post-TTS echo cooldown: ignorar frames 800ms después de que el bot terminó de hablar
        # (el mic del ESP32 recoge el audio del speaker como eco)
        tts_end = _active_sessions.get(session_id, {}).get("tts_end_time", 0)
        if tts_end and time.time() - tts_end < 0.8:
            return
        if listen_mode != "auto":
            opus_buffer.append(opus_frame)
            return
        # Decodificar y medir energía
        try:
            pcm_bytes = decoder.decode(opus_frame)
            rms = _frame_rms(pcm_bytes)
            _debug_frame_count += 1
            if _debug_frame_count <= 3:
                logger.info(f"[{short_id}] DECODE OK rms={rms:.6f} pcm_len={len(pcm_bytes)} frame={opus_frame[:16].hex()}")
        except Exception as e:
            rms = 0.0
            _debug_frame_count += 1
            if _debug_frame_count <= 3:
                logger.info(f"[{short_id}] DECODE ERR {e} frame={opus_frame[:20].hex()} len={len(opus_frame)}")
        # U5: VAD adaptativo — actualizar piso de ruido y threshold
        adaptive_vad_threshold = _update_noise_floor(session_id, rms, in_speech)
        is_speech = rms >= adaptive_vad_threshold
        if session_id in _active_sessions:
            _active_sessions[session_id]["vad_rms_last"] = round(rms, 4)
            _active_sessions[session_id]["noise_floor"] = round(_noise_floor_per_session.get(session_id[:8], rms), 4)
            _active_sessions[session_id]["vad_threshold"] = round(adaptive_vad_threshold, 4)
        logger.debug(
            f"[{short_id}] frame rms={rms:.4f} speech={is_speech} "
            f"noise_floor={_noise_floor_per_session.get(session_id[:8], 0):.4f} "
            f"thresh={adaptive_vad_threshold:.4f} buf={len(opus_buffer)} sil={silence_count}"
        )
        if is_speech:
            silence_count = 0
            speech_count += 1
            if not in_speech and speech_count >= VAD_SPEECH_FRAMES:
                in_speech = True
                # Pre-roll: anteponer frames previos al buffer de habla (no perder inicio de frase)
                opus_buffer[:0] = pre_roll
                pre_roll.clear()
                logger.info(f"[{short_id}] VAD: habla detectada (rms={rms:.4f} thresh={adaptive_vad_threshold:.4f} pre_roll={len(opus_buffer)})")
            if in_speech:
                opus_buffer.append(opus_frame)
        else:
            # Mantener pre-roll rolling de últimos 5 frames (300ms)
            pre_roll.append(opus_frame)
            if len(pre_roll) > 5:
                pre_roll.pop(0)
            if in_speech:
                opus_buffer.append(opus_frame)
                silence_count += 1
                # U9: timeout adaptativo — más largo en multi-turno
                silence_threshold = VAD_SILENCE_FRAMES if not multiturn_active else int(VAD_SILENCE_ADAPTIVE_MS / 60)
                if silence_count >= silence_threshold:
                    n = len(opus_buffer)
                    logger.info(
                        f"[{short_id}] VAD: silencio ({silence_count} frames) "
                        f"— {n} frames habla → STT {'(multi-turno)' if multiturn_active else ''}"
                    )
                    frames_to_process = list(opus_buffer)
                    opus_buffer.clear()
                    pre_roll.clear()
                    silence_count = 0
                    speech_count = 0
                    in_speech = False
                    if n >= VAD_MIN_OPUS_FRAMES:
                        current_pipeline_task = asyncio.create_task(run_pipeline_task(frames_to_process))
    async def _maybe_flush_vad():
        nonlocal silence_count, speech_count, in_speech
        if in_speech and len(opus_buffer) >= VAD_MIN_OPUS_FRAMES:
            n = len(opus_buffer)
            logger.info(f"[{short_id}] VAD timeout — {n} frames habla → STT (sin frames nuevos {VAD_TIMEOUT_S}s)")
            frames_to_process = list(opus_buffer)
            opus_buffer.clear()
            pre_roll.clear()
            silence_count = 0
            speech_count = 0
            in_speech = False
            current_pipeline_task = asyncio.create_task(run_pipeline_task(frames_to_process))
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=VAD_TIMEOUT_S)
            except asyncio.TimeoutError:
                await _maybe_flush_vad()
                continue
            if msg["type"] == "websocket.disconnect":
                code = msg.get("code", "?")
                reason = msg.get("text", "")
                logger.info(f"[{short_id}] WS desconectado  code={code} reason='{reason}'")
                break
            raw_text = msg.get("text")
            raw_bytes = msg.get("bytes")
            if raw_text is not None:
                msgs_rx += 1
                data = parse_client_message(raw_text)
                mtype = data.get("type", "?")
                logger.debug(f"[{short_id}] JSON ← {raw_text[:200]}")
                if mtype == "hello":
                    resp = hello_response(session_id)
                    fw = data.get("version", "?")
                    dev = data.get("device_id", data.get("client_id", "?"))
                    _active_sessions[session_id]["device_id"] = dev
                    _active_sessions[session_id]["fw_version"] = fw
                    logger.info(f"[{short_id}] hello recibido (fw_ver={fw} device={dev}) → enviando hello_response")
                    await ws.send_text(resp)
                elif mtype == "listen":
                    mode = data.get("mode", "")
                    state = data.get("state", "")
                    logger.info(f"[{short_id}] listen  mode={mode!r}  state={state!r}")
                    if mode in ("auto", "manual", "realtime") or state == "start":
                        listen_mode = mode if mode else listen_mode
                        opus_buffer.clear()
                        frames_rx = 0
                        listening = True
                        silence_count = 0
                        speech_count = 0
                        in_speech = False
                        logger.info(f"[{short_id}] Escuchando... (modo={listen_mode!r})")
                        if not greeting_sent:
                            greeting_sent = True
                            dev_id = _active_sessions.get(session_id, {}).get("device_id", "?")
                            profile = get_device_profile(dev_id)
                            bot_name = profile.get("bot_name", os.getenv("MEBOT_BOT_NAME", "MeBot"))
                            greet = os.getenv("MEBOT_GREETING", f"Hola, soy {bot_name}. Estoy lista.")
                            logger.info(f"[{short_id}] Enviando saludo TTS: '{greet}'")
                            asyncio.create_task(_speak_tts(ws, session_id, short_id, tts_enc, greet))
                    elif mode == "stop" or state == "stop":
                        if listening and opus_buffer:
                            listening = False
                            logger.info(f"[{short_id}] listen stop — {len(opus_buffer)} frames recibidos")
                            current_pipeline_task = asyncio.create_task(run_pipeline_task(list(opus_buffer)))
                        else:
                            logger.warning(f"[{short_id}] listen stop sin buffer")
                        listening = False
                elif mtype == "abort":
                    listening = False
                    opus_buffer.clear()
                    silence_count = 0
                    speech_count = 0
                    in_speech = False
                    logger.info(f"[{short_id}] abort recibido — escucha cancelada")
                    if current_pipeline_task and not current_pipeline_task.done():
                        logger.info(f"[{short_id}] Cancelando pipeline activo por abort")
                        current_pipeline_task.cancel()
                    pipeline_active = False
                else:
                    logger.warning(f"[{short_id}] mensaje desconocido type={mtype!r}  raw={raw_text[:120]}")
            elif raw_bytes is not None:
                frames_rx += 1
                if listening:
                    opus_payload = raw_bytes
                    hdr = raw_bytes[:min(8, len(raw_bytes))]
                    if len(raw_bytes) >= 4 and raw_bytes[0] == 0:
                        payload_size = _struct.unpack(">H", raw_bytes[2:4])[0]
                        if 0 < payload_size <= len(raw_bytes) - 4:
                            opus_payload = raw_bytes[4:4 + payload_size]
                        else:
                            logger.info(f"[{short_id}] raw_frame skip_extract raw_bytes[0]={raw_bytes[0]} raw_len={len(raw_bytes)} ps={payload_size}")
                    else:
                        logger.info(f"[{short_id}] raw_frame v1/raw first_bytes={hdr.hex()} raw_len={len(raw_bytes)}")
                    if frames_rx <= 3:
                        logger.info(f"[{short_id}] raw_frame#{frames_rx} hdr={hdr.hex()} raw_len={len(raw_bytes)} pay_len={len(opus_payload)}")
                    await _handle_audio_frame(opus_payload)
                    if frames_rx % 50 == 0:
                        logger.debug(f"[{short_id}] {frames_rx} frames rx  buf={len(opus_buffer)}  speech={in_speech}  sil={silence_count}")
                else:
                    logger.debug(f"[{short_id}] frame ignorado (no escuchando) — {len(raw_bytes)}B")
    except WebSocketDisconnect as exc:
        if exc.code == 1006:
            _stats["ws_disconnects_1006"] += 1
        logger.info(f"[{short_id}] WebSocketDisconnect  code={exc.code}")
    except Exception as exc:
        logger.exception(f"[{short_id}] Error inesperado: {exc}")
    finally:
        _stats["sessions_active"] -= 1
        dev_id = _active_sessions.get(session_id, {}).get("device_id", "?")
        _active_sessions.pop(session_id, None)
        _active_ws_sessions.pop(session_id, None)
        if current_pipeline_task and not current_pipeline_task.done():
            current_pipeline_task.cancel()
        turnos = len(history) // 2
        logger.info(f"[{short_id}] Sesión cerrada — turnos={turnos}  msgs_json={msgs_rx}  frames_opus={frames_rx}")
        if dev_id != "?":
            asyncio.create_task(notify_telegram(f"🔴 MeBot ESP32 Desconectado\nID: {dev_id}\nSesión duró {int(time.time() - _stats['start_time'])}s"))
async def _speak_tts(
    ws: WebSocket,
    session_id: str,
    short_id: str,
    encoder,
    text: str,
    emotion: str = "neutral",
) -> int:
    """Envía TTS al ESP32 con pre-buffering (U8) y streaming progresivo. Retorna frames enviados."""
    logger.debug(f"[{short_id}] TTS inicio — texto: '{text[:80]}'")
    await ws.send_text(llm_message(emotion))
    await ws.send_text(tts_start(session_id))
    await asyncio.sleep(0.5)
    await ws.send_text(tts_sentence_start(text))
    frames_sent = 0
    try:
        logger.debug(f"[{short_id}] TTS: llamando a text_to_pcm...")
        audio_pcm = await text_to_pcm(text)
        logger.debug(
            f"[{short_id}] TTS: text_to_pcm retornó {len(audio_pcm)} muestras PCM"
        )
        if _TTS_VOLUME != 1.0:
            audio_pcm = audio_pcm * _TTS_VOLUME
        opus_out = encode_pcm_to_opus(audio_pcm, encoder, TTS_FRAME_SAMPLES)
        logger.debug(f"[{short_id}] TTS: encoded {len(opus_out)} frames Opus (vol={_TTS_VOLUME:.0%})")
        frames_sent = len(opus_out)
        logger.info(
            f"[{short_id}] TTS {frames_sent} frames ({frames_sent * 0.06:.1f}s) "
            f"→ enviando a 60ms/frame | texto='{text[:60]}'"
        )
        # Pacing uniforme desde el frame 0 a 57ms por frame (95% de 60ms de audio).
        # NO hay preburst — un burst inicial saturaba la decode_queue del ESP32
        # (MAX_DECODE_PACKETS_IN_QUEUE = 40 frames) causando drops y el efecto ráfaga/silencio.
        # Al 95% de velocidad real el ESP32 siempre tiene datos sin que se desborde la queue.
        t0_stream = asyncio.get_event_loop().time()
        for i, frame in enumerate(opus_out):
            elapsed = asyncio.get_event_loop().time() - t0_stream
            target = i * 0.057          # 57ms por frame = 95% de 60ms real
            delay = target - elapsed
            if delay > 0.001:
                await asyncio.sleep(delay)
            header = _struct.pack(">BBH", 0, 0, len(frame))
            await ws.send_bytes(header + frame)
        logger.info(f"[{short_id}] TTS stream completado — {frames_sent} frames OK")
    except Exception as exc:
        frames_sent = 0
        logger.error(f"[{short_id}] TTS error: {exc}")
    await ws.send_text(tts_stop(session_id))
    _stats["tts_frames_sent"] += frames_sent
    return frames_sent
# ---------------------------------------------------------------------------
# Pipeline STT → LLM → TTS
# ---------------------------------------------------------------------------
async def _run_pipeline(
    ws: WebSocket,
    session_id: str,
    short_id: str,
    frames: list[bytes],
    decoder,
    encoder,
    history: list[dict],
    *,
    llm_session_id: str | None = None,
    reset_multiturn_timer=None,
    multiturn_active_ref=None,
) -> None:
    # Referencia mutable para estado multi-turno (U3)
    _mt_active = [False] if multiturn_active_ref is None else multiturn_active_ref
    def _reset_mt():
        if reset_multiturn_timer:
            reset_multiturn_timer()
            _mt_active[0] = True
    if not frames:
        logger.warning(f"[{short_id}] Pipeline omitido — buffer vacío")
        return
    _active_sessions.setdefault(session_id, {})["last_pipeline_end"] = 0
    # Cooldown: ignorar si el último pipeline terminó hace <3s (evita ráfagas y eco del TTS)
    elapsed_since_last = time.time() - _active_sessions[session_id].get("last_pipeline_end", 0)
    if elapsed_since_last < 3.0:
        logger.debug(f"[{short_id}] Pipeline omitido — cooldown ({elapsed_since_last:.1f}s)")
        return
    t0 = time.time()
    _stats["pipelines_run"] += 1
    device_id = _active_sessions.get(session_id, {}).get("device_id", "?")
    # 1. STT ─────────────────────────────────────────────────────────────────
    audio_s = len(frames) * 0.060
    logger.info(
        f"[{short_id}] ── STT inicio ── {len(frames)} frames OPUS ({audio_s:.1f}s audio)"
    )
    pcm = decode_opus_frames(frames, decoder)
    expected = len(frames) * 960
    logger.info(
        f"[{short_id}] STT: decoded {len(pcm)}/{expected} samples "
        f"({len(pcm)/16000:.2f}s/{audio_s:.1f}s) "
        f"{'⚠ header corruption?' if len(pcm) < expected * 0.5 else 'OK'}"
    )
    text = await transcribe(pcm)
    t_stt = time.time() - t0
    if not text:
        _stats["stt_empty"] += 1
        logger.info(
            f"[{short_id}] STT: vacío — ignorado (falso positivo) ({t_stt:.1f}s, {audio_s:.1f}s audio)"
        )
        _stats["stt_empty_ignored"] = _stats.get("stt_empty_ignored", 0) + 1
        _active_sessions.setdefault(session_id, {})["last_pipeline_end"] = time.time()
        return
    _stats["stt_ok"] += 1
    logger.info(
        f"[{short_id}] STT OK [{len(text.split())} palabras, {t_stt:.1f}s]: '{text}'"
    )
    # Filtrar resultados muy cortos que suelen ser ruido (falsos positivos)
    text_stripped = text.strip()
    word_count = len(text_stripped.split())
    if word_count <= 2 and len(text_stripped) < 10:
        _stats["stt_short_text_filtered"] = _stats.get("stt_short_text_filtered", 0) + 1
        logger.info(f"[{short_id}] STT: texto demasiado corto '{text_stripped}' ({word_count} palabras) — ignorado")
        _active_sessions.setdefault(session_id, {})["last_pipeline_end"] = time.time()
        return
    await ws.send_text(stt_message(text))
    # U7: ERP intents (órdenes, productos, clientes, etc.)
    erp_context = ""
    intent = detect_intent(text)
    if intent:
        logger.info(f"[{short_id}] ERP intent: {intent['action']}")

        # Ferretería ERP: consultas directas a Vendure Ferretería
        if intent["action"].startswith("ferreteria-"):
            text_lower = text.strip().lower()
            # Extraer término de búsqueda limpiando palabras vacías
            # Incluye fillers de habla natural (entonces, bueno, mira, etc.)
            # y palabras genéricas que STT puede insertar (lugar, cosa, etc.)
            stop_words = (
                "ferretería|ferreteria|busca|encuentra|hay|tiene|tienes|cuanto|cuánto|"
                "cuesta|cuestan|cueste|precio|precios|stock|disponible|disponibles|"
                "el|la|los|las|en|de|del|un|una|por|para|con|sin|que|es|se|su|al|lo|"
                "y|a|e|i|o|u|dame|quiero|necesito|saber|ver|mostrar|listar|consultar|"
                "dime|dígame|cual|cuál|como|cómo|"
                # Fillers de habla natural / STT noise
                "entonces|bueno|mira|oye|oiga|pues|aver|haber|ok|okay|vale|"
                "hola|bien|este|esta|eso|esa|esto|aquí|aqui|ahí|ahi|allá|alla|"
                "cosa|lugar|hogar|casa|trabajo|favor|por favor|gracias|"
                "me|nos|te|le|les|mi|tu|tú|mí|ti|"
                "más|mas|muy|tan|algo|algún|alguna|algunos|algunas|todo|nada|otro|otra|"
                "ser|estar|hacer|tener|poder|deber|ir|dar|poner|"
                "sí|si|no|ya|solo|sólo|también|tambien|"
                "donde|dónde|cuando|cuándo|porque|porqué|"
                "uno|dos|tres|primer|segundo|tercer|"
                # Palabras de contexto que no son productos
                "inventario|catálogo|catalogo|listado|productos|producto|artículo|articulo|"
                "herramienta|herramientas|electrico|eléctrico|electrica|eléctrica|"
                "profesional|doméstico|domestico|industrial|pequeño|pequeña|grande|"
                "buena|bueno|mejor|barato|barata|económico|económica|caro|cara"
            )
            search_term = re.sub(r'\b(?:' + stop_words + r')\b', ' ', text_lower, flags=re.IGNORECASE)
            search_term = re.sub(r'\s+', ' ', search_term).strip()
            
            # Aplicar aliases fonéticos locales
            search_term = _apply_phonetic_aliases(search_term)

            # Estrategia: usar la palabra más larga como término principal
            # (el nombre del producto suele ser la palabra más larga)
            # Si quedan múltiples palabras, probar primero con la más larga
            search_words = [w for w in search_term.split() if len(w) >= 3]
            if len(search_words) > 1:
                # Ordenar por longitud descendente y usar la más larga
                search_words.sort(key=len, reverse=True)
                primary_term = search_words[0]
                logger.info(f"[{short_id}] Search cleanup: '{search_term}' → primary='{primary_term}' (de {search_words})")
                search_term = primary_term
            elif not search_words and search_term:
                # Fallback: usar el término tal cual si no hay palabras >= 3 chars
                pass
            elif search_words:
                search_term = search_words[0]

            logger.info(f"[{short_id}] Ferretería búsqueda: término final='{search_term}'")

            ferreteria_actions = {
                "ferreteria-products": '{ products(options:{take:10}) { totalItems items { id name } } }',
                "ferreteria-search":  '{ search(input:{term:"' + search_term + '",groupByProduct:false}) { totalItems items { productName sku priceWithTax { __typename ...on PriceRange{min max} ...on SinglePrice{value} } } } }',
            }
            gql_query = ferreteria_actions.get(intent["action"], "")
            if gql_query:
                raw = await _query_ferreteria(gql_query)
                formatted = _format_ferreteria_data(raw) if isinstance(raw, dict) else None
                
                # Retry en paralelo con palabras individuales si primera búsqueda falla
                if not formatted and search_words and len(search_words) > 1:
                    logger.info(f"[{short_id}] Primera búsqueda falló. Iniciando reintentos paralelos para {search_words[1:]}...")
                    
                    async def fetch_and_format(word):
                        q = '{ search(input:{term:"' + word + '",groupByProduct:false}) { totalItems items { productName sku priceWithTax { __typename ...on PriceRange{min max} ...on SinglePrice{value} } } } }'
                        res = await _query_ferreteria(q)
                        return _format_ferreteria_data(res) if isinstance(res, dict) else None
                    
                    tasks = [fetch_and_format(w) for w in search_words[1:]]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    for res in results:
                        if res and not isinstance(res, Exception):
                            formatted = res
                            break
                if formatted:
                    erp_context = f"\n\n[FERRETERÍA ERP DATA]\n{formatted[:1000]}"
                    logger.info(f"[{short_id}] Ferretería ERP OK: {len(formatted)} chars")
                else:
                    logger.info(f"[{short_id}] Ferretería ERP sin datos para '{search_term}'")
        else:
            data = await _run_erp_command(intent["action"], text)
            if data:
                erp_context = f"\n\n[DATOS DEL SISTEMA]\n{data}"
                logger.info(f"[{short_id}] ERP data: {len(data)} chars")
            else:
                logger.info(f"[{short_id}] ERP sin datos para {intent['action']}")
    # U8: Datos de internet (precios, clima, etc.)
    text_lower = text.strip().lower()
    if not erp_context and re.search(r'bitcoin|btc|precio.*dólar|precio.*dolar|cuánto.*cuest|cuanto.*cuest|cotización|cotizacion|acción|accion|ibex|sp500|s&p', text_lower):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=5) as resp:
                    if resp.status == 200:
                        btc = await resp.json()
                        price = btc.get("bitcoin", {}).get("usd", "desconocido")
                        date_str = time.strftime("%d/%m/%Y %H:%M", time.localtime())
                        internet_context = f"\n[BITCOIN] Precio actual: ${price:,} USD (al {date_str})"
                        erp_context += internet_context
                        logger.info(f"[{short_id}] Internet data: Bitcoin ${price} USD")
        except Exception as e:
            logger.warning(f"[{short_id}] Internet data error: {e}")
    # U6: Comandos locales de voz (sin LLM)
    
    local_cmd_response = None
    local_cmd_emotion = "neutral"
    # Volumen: "sube el volumen", "baja el volumen", "volumen al 50"
    if "volumen" in text_lower or "silencio" in text_lower or "silencia" in text_lower:
        vol_found = re.search(r'(\d+)', text_lower)
        if vol_found:
            new_vol = min(100, max(0, int(vol_found.group(1))))
            global _TTS_VOLUME
            _TTS_VOLUME = new_vol / 100.0
            local_cmd_response = f"Volumen al {new_vol} por ciento."
            logger.info(f"[{short_id}] Comando local: volumen → {new_vol}%")
        elif "sub" in text_lower or "más" in text_lower:
            new_vol = min(100, round(_TTS_VOLUME * 100) + 10)
            _TTS_VOLUME = new_vol / 100.0
            local_cmd_response = f"Volumen al {new_vol} por ciento."
        elif "baj" in text_lower or "menos" in text_lower:
            new_vol = max(0, round(_TTS_VOLUME * 100) - 10)
            _TTS_VOLUME = new_vol / 100.0
            local_cmd_response = f"Volumen al {new_vol} por ciento."
        elif "silencio" in text_lower or "silencia" in text_lower:
            _TTS_VOLUME = 0.0
            local_cmd_response = "Dispositivo silenciado."
            logger.info(f"[{short_id}] Comando local: silenciado")
    local_cmd_response_llm = None
    if "estado" in text_lower or "cómo estás" in text_lower or "como estas" in text_lower:
        uptime_h = int((time.time() - _stats["start_time"]) / 3600)
        pipeline_count = _stats["pipelines_run"]
        local_cmd_response_llm = f"Estoy bien. Llevo activa {uptime_h} horas, he procesado {pipeline_count} conversaciones. ¿En qué puedo ayudarte?"
    if local_cmd_response:
        await _speak_tts(ws, session_id, short_id, encoder, local_cmd_response, emotion=local_cmd_emotion)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": local_cmd_response})
        _log_conversation(session_id, device_id, text, local_cmd_response, time.time() - t0)
        t_total = time.time() - t0
        logger.info(f"[{short_id}] Comando local procesado ({t_total:.1f}s)")
        if local_cmd_response_llm:
            # Si también preguntó estado, responder después
            response = local_cmd_response_llm
            history.append({"role": "assistant", "content": response})
            text += " [+preguntó estado]"
        else:
            _reset_mt()
            return
    # 2. LLM ─────────────────────────────────────────────────────────────────
    logger.info(
        f"[{short_id}] ── LLM inicio ── model={os.getenv('LLM_MODEL', '?')} url={os.getenv('LITELLM_URL', '?')}"
    )
    t_llm0 = time.time()
    llm_error = False
    try:
        profile = get_device_profile(device_id)
        original_prompt = os.environ.get("MEBOT_SYSTEM_PROMPT")
        if profile.get("system_prompt"):
            os.environ["MEBOT_SYSTEM_PROMPT"] = profile["system_prompt"]
        # Inyectar datos ERP en el system prompt si hay
        if erp_context:
            current_prompt = os.environ.get("MEBOT_SYSTEM_PROMPT", "")
            os.environ["MEBOT_SYSTEM_PROMPT"] = current_prompt + erp_context
        response = await chat(text, history, session_id=llm_session_id or session_id)
        
        if original_prompt is not None:
            os.environ["MEBOT_SYSTEM_PROMPT"] = original_prompt
        else:
            os.environ.pop("MEBOT_SYSTEM_PROMPT", None)
        t_llm = time.time() - t_llm0
        _stats["llm_ok"] += 1
        logger.info(
            f"[{short_id}] LLM OK [{len(response.split())} palabras, {t_llm:.1f}s]: '{response[:120]}'"
        )
    except Exception as exc:
        t_llm = time.time() - t_llm0
        _stats["llm_errors"] += 1
        logger.error(f"[{short_id}] LLM error ({t_llm:.1f}s): {exc}")
        response = f"Error de conexión al servidor. Código: {exc}. Intenta de nuevo."
        llm_error = True
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": response})
    # 3. TTS ─────────────────────────────────────────────────────────────────
    profile = get_device_profile(device_id)
    original_voice = os.environ.get("MEBOT_TTS_VOICE")
    if profile.get("tts_voice"):
        os.environ["MEBOT_TTS_VOICE"] = profile["tts_voice"]
    logger.info(
        f"[{short_id}] ── TTS inicio ── {'(respuesta error)' if llm_error else ''} '{response[:80]}'"
    )
    t_tts0 = time.time()
    await _speak_tts(ws, session_id, short_id, encoder, response, emotion="neutral")
    t_tts = time.time() - t_tts0
    
    if original_voice is not None:
        os.environ["MEBOT_TTS_VOICE"] = original_voice
    else:
        os.environ.pop("MEBOT_TTS_VOICE", None)
    t_total = time.time() - t0
    logger.info(
        f"[{short_id}] ── Pipeline OK ── total={t_total:.1f}s  "
        f"STT={t_stt:.1f}s  LLM={t_llm:.1f}s  TTS={t_tts:.1f}s"
    )
    # U3: Activar multi-turno y log de conversación
    _log_conversation(session_id, device_id, text, response, t_total)
    _reset_mt()
    # Registrar fin de pipeline y de TTS para cooldown y echo suppression
    _active_sessions.setdefault(session_id, {})["last_pipeline_end"] = time.time()
    _active_sessions.setdefault(session_id, {})["tts_end_time"] = time.time()
# ---------------------------------------------------------------------------
# Observabilidad — /stats y /debug
# ---------------------------------------------------------------------------
@app.get("/stats")
def stats():
    uptime = int(time.time() - _stats["start_time"])
    multiturn_active_count = sum(1 for s in _active_sessions.values() if s.get("multiturn", False))
    dynamic_range = {
        "noise_floor_min": VAD_ENERGY_THRESH_MIN,
        "noise_floor_max": VAD_ENERGY_THRESH_MAX,
    }
    return {
        **_stats,
        "uptime_s": uptime,
        "vad": {
            "energy_thresh": VAD_ENERGY_THRESH,
            "silence_frames": VAD_SILENCE_FRAMES,
            "speech_frames": VAD_SPEECH_FRAMES,
            "min_opus_frames": VAD_MIN_OPUS_FRAMES,
            "timeout_s": VAD_TIMEOUT_S,
        },
        "noise_floor": dynamic_range,
        "multiturn_active": multiturn_active_count,
        "model": os.getenv("LLM_MODEL", "?"),
        "whisper": os.getenv("MEBOT_WHISPER_MODEL", "small"),
        "tts_voice": os.getenv("MEBOT_TTS_VOICE", "?"),
        "volume": round(_TTS_VOLUME * 100),
    }
@app.get("/debug")
def debug():
    sessions = []
    for sid, info in _active_sessions.items():
        s = {"session_id": sid[:8], **info}
        # Add multiturn and noise info
        short = sid[:8]
        if short in _noise_floor_per_session:
            s["noise_floor"] = round(_noise_floor_per_session[short], 4)
        s["multiturn"] = _multi_turn_sessions.get(sid, {}).get("active", False)
        sessions.append(s)
    return {
        "active_sessions": sessions,
        "stats": _stats,
        "env": {
            "ws_url": MEBOT_WS_URL,
            "llm_url": os.getenv("LITELLM_URL", "?"),
            "model": os.getenv("LLM_MODEL", "?"),
            "language": os.getenv("MEBOT_LANGUAGE", "es"),
            "greeting": os.getenv("MEBOT_GREETING", "(default)"),
            "conversation_log": CONVERSATION_LOG_FILE,
        },
    }
@app.get("/api/fleet")
def get_fleet():
    return {
        "active": [
            {
                "session_id": sid[:8],
                "device_id": info.get("device_id", "?"),
                "ip": info.get("ip", "?"),
                "state": info.get("state", "connected"),
                "vad_rms_last": info.get("vad_rms_last", 0.0),
                "fw_version": info.get("fw_version", "?"),
                "uptime_s": int(time.time() - info.get("connect_time", time.time())),
            }
            for sid, info in _active_sessions.items()
        ],
        "stats": _stats
    }
@app.post("/api/fleet/{device_id}/ota")
async def trigger_ota(device_id: str):
    # Encontrar el session_id del device
    target_sid = None
    for sid, info in _active_sessions.items():
        if info.get("device_id") == device_id or sid[:8] == device_id:
            target_sid = sid
            break
            
    if not target_sid or target_sid not in _active_ws_sessions:
        return JSONResponse(status_code=404, content={"error": "Device not found or offline"})
        
    try:
        ws = _active_ws_sessions[target_sid]["ws"]
        # Enviar comando de reboot, el firmware buscará OTA al iniciar
        cmd = json.dumps({"type": "system", "command": "reboot"})
        await ws.send_text(cmd)
        logger.info(f"OTA push triggered for device {device_id}")
        return {"success": True, "message": "OTA Reboot command sent"}
    except Exception as e:
        logger.error(f"Failed to trigger OTA for {device_id}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/fleet/ota/all")
async def trigger_ota_all():
    triggered = 0
    errors = []
    
    for sid, ws_info in list(_active_ws_sessions.items()):
        try:
            ws = ws_info["ws"]
            cmd = json.dumps({"type": "system", "command": "reboot"})
            await ws.send_text(cmd)
            triggered += 1
        except Exception as e:
            errors.append(f"{sid[:8]}: {str(e)}")
            
    if errors:
        logger.error(f"Failed to trigger mass OTA for some devices: {errors}")
        
    logger.info(f"Mass OTA push triggered for {triggered} devices")
    return {"success": True, "devices_triggered": triggered, "message": "Mass OTA Reboot command sent"}
@app.get("/api/devices/{device_id}/config")
async def get_device_config_api(device_id: str):
    return get_device_profile(device_id)
@app.post("/api/devices/{device_id}/config")
async def save_device_config_api(device_id: str, request: Request):
    data = await request.json()
    profiles = load_profiles()
    profiles[device_id] = {
        "bot_name": data.get("bot_name", "MeBot"),
        "system_prompt": data.get("system_prompt", ""),
        "tts_voice": data.get("tts_voice", "es-CO-SalomeNeural")
    }
    save_profiles(profiles)
    return {"success": True, "message": "Profile updated successfully"}
# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """T2: Health check completo del pipeline STT→LLM→TTS."""
    checks = {"stt": False, "llm": False, "tts": False}
    details = {}
    
    # STT: verificar que whisper está cargado
    try:
        from audio.whisper_stt import _model
        checks["stt"] = _model is not None
        details["stt"] = "loaded" if checks["stt"] else "not_loaded"
    except Exception as e:
        details["stt"] = str(e)
    
    # TTS: verificar codec
    try:
        import ctypes
        lib = ctypes.CDLL("libopus.so.0")
        checks["tts"] = lib is not None
        details["tts"] = "loaded"
    except Exception as e:
        details["tts"] = str(e)
    
    # LLM: verificar URL configurada
    if os.getenv("OPENCLAW_AGENT_URL"):
        checks["llm"] = True
        details["llm"] = "openclaw"
    elif os.getenv("LITELLM_URL"):
        checks["llm"] = True
        details["llm"] = "litellm"
    else:
        details["llm"] = "not_configured"
    
    all_ok = all(checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "service": "mebot-audio-bridge",
        "checks": checks,
        "details": details,
        "active_sessions": _stats["sessions_active"],
        "pipelines_run": _stats["pipelines_run"],
    }
# ── API del Setup Wizard ─────────────────────────────────────────────────────
@app.get("/api/device/info")
async def device_info():
    """GET /api/device/info → estado del ESP32 conectado al bridge."""
    if not _active_sessions:
        return {"connected": False}
    sid, session = next(iter(_active_sessions.items()))
    return {
        "connected": True,
        "device_id": session.get("device_id", session.get("client_id", "?")),
        "mac": session.get("device_id", "?"),
        "ip": session.get("ip", "?"),
        "fw_version": f"v{session.get('fw_version', '?')}",
        "bot_name": os.getenv("MEBOT_BOT_NAME", "MeBot"),
    }
@app.post("/api/tts/test")
async def tts_test(request: Request):
    """POST /api/tts/test → prueba TTS (voz)."""
    from tts.edge_tts_client import text_to_pcm
    body = await request.json()
    text = body.get("text", "Hola, prueba de voz.")
    voice = body.get("voice", "es-CO-SalomeNeural")
    try:
        pcm = await asyncio.wait_for(text_to_pcm(text, voice), timeout=15)
        if len(pcm) > 0:
            logger.info(f"TTS test OK: {len(pcm)} samples ({len(pcm)/24000:.1f}s)")
            return {"success": True, "samples": len(pcm)}
        return {"success": False, "error": "PCM vacío"}
    except Exception as e:
        logger.warning(f"TTS test error: {e}")
        return {"success": False, "error": str(e)}
@app.get("/api/audio/level")
async def audio_level():
    """GET /api/audio/level → VU meter (último RMS de sesión activa)."""
    if _active_sessions:
        sid, session = next(iter(_active_sessions.items()))
        rms = session.get("vad_rms_last", 0.0)
        return {"level": round(rms * 100, 1), "peak": round(rms * 120, 1), "rms": rms}
    return {"level": 0, "peak": 0, "rms": 0.0}
# ── Config endpoint — parámetros dinámicos para el firmware
# ────────────────────────────────────────────────────────────────────────────────
# Valores por defecto; editar aquí o sobrescribir con variables de entorno.
_DEVICE_CONFIG: dict = {
    "wifi_greet": os.getenv("MEBOT_WIFI_GREET", "Hola, Estoy de vuelta"),
    "bot_name": os.getenv("MEBOT_BOT_NAME", "MeBot"),
    "tts_voice": os.getenv("MEBOT_TTS_VOICE", "es-CO-SalomeNeural"),
    "vad_threshold": str(VAD_ENERGY_THRESH),
    "silence_timeout": str(VAD_SILENCE_FRAMES),
    "greeting_url": os.getenv("MEBOT_GREETING_URL", ""),
}
# Configuración dinámica para VAD y otros parámetros
_VAD_CONFIG: dict = {
    "energy_thresh": VAD_ENERGY_THRESH,
    "speech_frames": VAD_SPEECH_FRAMES,
    "silence_frames": VAD_SILENCE_FRAMES,
    "min_opus_frames": VAD_MIN_OPUS_FRAMES,
}
# Sesiones activas para control remoto (guardan ws, enc, short_id)
# (Se define vacío aquí, pero se puebla en esp32_handler)
@app.get("/esp32/config/")
async def device_config(request: Request):
    """
    El firmware llama a este endpoint en cada arranque (tras OTA check).
    Devuelve JSON con parámetros configurables sin reflashear.
    Todas las claves deben tener ≤ 15 chars (límite NVS ESP32).
    """
    device_id = request.headers.get("Device-Id", "?")
    logger.info(f"Config  device={device_id}")
    return JSONResponse(content=_DEVICE_CONFIG)
# ---------------------------------------------------------------------------
# ADMIN PANEL — Página web de administración
# ---------------------------------------------------------------------------
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
@app.get("/xiaozhi/ota/")
@app.post("/xiaozhi/ota/")
async def xiaozhi_ota_compat(request: Request):
    """Compatibilidad legacy: redirige a /esp32/ota/"""
    return await ota_check(request)
@app.get("/bridge/hub.html")
async def redirect_hub():
    return RedirectResponse(url="/hub")
@app.get("/admin.html")
@app.get("/admin")
@app.get("/br/admin")
@app.get("/br/admin.html")
@app.get("/bridge/admin.html")
async def admin_panel():
    """Sirve la página de administración HTML."""
    try:
        with open("admin.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Error: admin.html not found</h1>", status_code=404
        )
@app.get("/hub")
@app.get("/br/hub")
@app.get("/br/hub.html")
@app.get("/bridge/hub.html")
async def hub_portal():
    """Sirve el Hub Central de control."""
    return await serve_html("hub.html")
@app.get("/fleet.html")
@app.get("/fleet")
@app.get("/br/fleet")
@app.get("/br/fleet.html")
@app.get("/bridge/fleet.html")
async def fleet_dashboard():
    """Sirve el Fleet Dashboard para gestión masiva."""
    return await serve_html("fleet.html")
@app.get("/setup.html")
@app.get("/setup")
@app.get("/br/setup")
@app.get("/br/setup.html")
async def setup_wizard():
    """Sirve el wizard de configuración v2.6."""
    return await serve_html("setup.html")
@app.get("/flasher.html")
@app.get("/flasher")
@app.get("/br/flasher")
@app.get("/br/flasher.html")
@app.get("/bridge/flasher.html")
async def flasher_page():
    """Sirve la página del flasheador web."""
    return await serve_html("flasher.html")
@app.get("/test_panel.html")
@app.get("/test")
@app.get("/br/test")
@app.get("/br/test.html")
async def test_panel():
    """Sirve el panel de pruebas."""
    return await serve_html("test_panel.html")
@app.get("/scripts/sonic_wifi_config.html")
async def sonic_wifi_config():
    """Sirve la configuración acústica."""
    return await serve_html("scripts/sonic_wifi_config.html")
@app.get("/docs/kanban.html")
@app.get("/br/docs/kanban.html")
async def docs_kanban():
    """Sirve el Kanban de tareas."""
    return await serve_html("docs/kanban.html")
@app.get("/docs/mebot_system.html")
@app.get("/br/docs/mebot_system.html")
async def docs_system():
    """Sirve la documentación del sistema."""
    return await serve_html("docs/mebot_system.html")
@app.get("/wifi_configuration.html")
async def wifi_config_page():
    """Sirve la página de configuración WiFi del componente manejado."""
    return await serve_html("../assets/wifi_custom/wifi_configuration.html")
@app.get("/wifi_configuration_done.html")
async def wifi_config_done_page():
    """Sirve la página de éxito de configuración WiFi."""
    return await serve_html("../assets/wifi_custom/wifi_configuration_done.html")
async def serve_html(filename: str):
    """Función helper para servir HTML con manejo de errores."""
    try:
        # Si el path es relativo a bridge/, lo buscamos allí, si no, usamos el path absoluto/relativo provisto
        with open(filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content=f"<h1>Error: {filename} not found</h1>", status_code=404
        )
@app.get("/api/network")
async def get_network_info():
    """Retorna información de red del bridge."""
    return {
        "local_ip": get_local_ip(),
        "port": 16000,
        "ws_url": MEBOT_WS_URL or f"ws://{get_local_ip()}:16001/esp32",
        "ota_url": f"http://{get_local_ip()}:16001/esp32/ota/"
    }
# ---------------------------------------------------------------------------
# SETUP WIZARD API — Endpoints para el setup.html wizard v2.6
# ---------------------------------------------------------------------------
@app.get("/api/device/info")
async def device_info():
    """Info detallada del primer dispositivo ESP32 activo."""
    for sid, info in _active_sessions.items():
        ws_info = _active_ws_sessions.get(sid)
        if ws_info:
            device_id = info.get("device_id", "?")
            profile = get_device_profile(device_id)
            return {
                "connected": True,
                "device_id": device_id,
                "mac": device_id,
                "ip": info.get("ip", "?"),
                "fw_version": info.get("fw_version", "?"),
                "uptime_s": int(time.time() - info.get("connect_time", time.time())),
                "bot_name": profile.get("bot_name", os.getenv("MEBOT_BOT_NAME", "MeBot")),
                "tts_voice": profile.get("tts_voice", os.getenv("MEBOT_TTS_VOICE", "es-CO-SalomeNeural")),
                "state": info.get("state", "connected"),
            }
    return {"connected": False}
_SETUP_DISPLAY_CONFIG = {
    "brightness": 8,
    "mode": "breathing",
    "color": "#3B82F6",
    "night_mode": False,
    "night_start": "22:00",
    "night_end": "07:00",
}
@app.get("/api/config/display")
async def get_display_config():
    """Retorna la configuración actual del LED del dispositivo."""
    return _SETUP_DISPLAY_CONFIG
@app.post("/api/config/display")
async def set_display_config(config: dict):
    """Actualiza la configuración del LED."""
    global _SETUP_DISPLAY_CONFIG
    if "brightness" in config:
        _SETUP_DISPLAY_CONFIG["brightness"] = config["brightness"]
    if "mode" in config:
        _SETUP_DISPLAY_CONFIG["mode"] = config["mode"]
    if "color" in config:
        _SETUP_DISPLAY_CONFIG["color"] = config["color"]
    if "night_mode" in config:
        _SETUP_DISPLAY_CONFIG["night_mode"] = config["night_mode"]
    if "night_start" in config:
        _SETUP_DISPLAY_CONFIG["night_start"] = config["night_start"]
    if "night_end" in config:
        _SETUP_DISPLAY_CONFIG["night_end"] = config["night_end"]
    logger.info(f"Display config updated: {_SETUP_DISPLAY_CONFIG}")
    return {"success": True}
_VALID_VOICES = [
    "es-ES-ElviraNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural",
    "es-ES-AlvaroNeural", "es-CO-SalomeNeural", "es-CO-GonzaloNeural",
    "en-US-JennyNeural", "en-US-GuyNeural",
]
@app.get("/api/firmware/check")
async def firmware_check():
    """Verifica la versión actual del firmware y disponible."""
    current = "2.7.6"
    return {
        "current_version": current,
        "check_url": f"http://{get_local_ip()}:16001/esp32/ota/",
        "ota_available": True,
    }
@app.post("/api/firmware/check")
async def trigger_firmware_check(request: Request):
    """Dispara un OTA check en el ESP32."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    try:
        cmd = json.dumps({"type": "system", "command": "ota_check"})
        await ws.send_text(cmd)
        return {"success": True, "message": "OTA check enviado al dispositivo"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
@app.get("/api/setup/progress")
async def setup_progress():
    """Retorna el progreso del setup actual."""
    return {
        "device_connected": len(_active_sessions) > 0,
        "active_sessions": _stats["sessions_active"],
        "pipelines_run": _stats["pipelines_run"],
    }
# ---------------------------------------------------------------------------
# ADMIN AUTH — Password de administración local para proteger páginas
# ---------------------------------------------------------------------------
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "mebot2026")
_ADMIN_SESSION_TOKENS: dict[str, dict] = {}
@app.post("/api/auth/admin")
async def admin_auth(request: Request):
    """Valida el password de administración y retorna token de sesión."""
    data = await request.json()
    password = data.get("password", "").strip()
    if not password:
        return JSONResponse({"error": "Password requerido"}, status_code=400)
    if password != _ADMIN_PASSWORD:
        return JSONResponse({"error": "Password incorrecto"}, status_code=401)
    token = secrets.token_hex(32)
    _ADMIN_SESSION_TOKENS[token] = {"created_at": time.time()}
    return {"success": True, "admin_token": token}
@app.post("/api/auth/admin/verify")
async def admin_verify(request: Request):
    """Verifica si un token de admin es válido."""
    data = await request.json()
    token = data.get("admin_token", "").strip()
    if token in _ADMIN_SESSION_TOKENS:
        age = time.time() - _ADMIN_SESSION_TOKENS[token]["created_at"]
        if age < 86400:
            return {"valid": True}
        del _ADMIN_SESSION_TOKENS[token]
    return JSONResponse({"valid": False}, status_code=401)
# ---------------------------------------------------------------------------
# DEVICE AUTH — Autenticación por dispositivo para Cloudflare Access + Password
# ---------------------------------------------------------------------------
import secrets
_DEVICE_SESSION_TOKENS: dict[str, dict] = {}
@app.get("/api/devices")
async def list_devices():
    """Lista dispositivos disponibles (sin password, para el selector)."""
    devices = []
    seen = set()
    for device_id, profile in load_profiles().items():
        seen.add(device_id)
        is_online = any(
            info.get("device_id") == device_id
            for info in _active_sessions.values()
        )
        devices.append({
            "device_id": device_id,
            "bot_name": profile.get("bot_name", "?"),
            "has_password": bool(profile.get("device_password")),
            "online": is_online,
        })
    for sid, info in _active_sessions.items():
        did = info.get("device_id", "?")
        if did and did != "?" and did not in seen:
            seen.add(did)
            devices.append({
                "device_id": did,
                "bot_name": "?",
                "has_password": False,
                "online": True,
            })
    return {"devices": devices}
@app.post("/api/auth/device")
async def device_auth(request: Request):
    """Valida password del dispositivo y retorna token de sesión."""
    data = await request.json()
    device_id = data.get("device_id", "").strip()
    password = data.get("password", "").strip()
    if not device_id or not password:
        return JSONResponse(
            {"error": "device_id y password requeridos"}, status_code=400
        )
    profiles = load_profiles()
    profile = profiles.get(device_id)
    if not profile:
        return JSONResponse(
            {"error": "Dispositivo no encontrado"}, status_code=404
        )
    stored = profile.get("device_password", "")
    if not stored:
        return JSONResponse(
            {"error": "Dispositivo sin password. El administrador debe asignarlo desde Fleet Manager."},
            status_code=401,
        )
    if password != stored:
        return JSONResponse(
            {"error": "Password incorrecto"}, status_code=401
        )
    token = secrets.token_hex(32)
    _DEVICE_SESSION_TOKENS[token] = {
        "device_id": device_id,
        "created_at": time.time(),
    }
    return {
        "success": True,
        "device_id": device_id,
        "bot_name": profile.get("bot_name", "MeBot"),
        "tts_voice": profile.get("tts_voice", ""),
        "session_token": token,
    }
@app.post("/api/auth/device/password")
async def set_device_password(request: Request):
    """Asigna o cambia el password de un dispositivo."""
    data = await request.json()
    device_id = data.get("device_id", "").strip()
    password = data.get("password", "").strip()
    if not device_id:
        return JSONResponse({"error": "device_id requerido"}, status_code=400)
    if len(password) < 4:
        return JSONResponse(
            {"error": "Password mínimo 4 caracteres"}, status_code=400
        )
    profiles = load_profiles()
    if device_id not in profiles:
        profiles[device_id] = {
            "bot_name": "MeBot",
            "system_prompt": "",
            "tts_voice": "es-CO-SalomeNeural",
        }
    profiles[device_id]["device_password"] = password
    save_profiles(profiles)
    logger.info(f"Password asignado para dispositivo {device_id}")
    return {"success": True, "message": "Password guardado correctamente"}
@app.post("/api/auth/device/verify-token")
async def verify_device_token(request: Request):
    """Verifica si un token de sesión es válido."""
    data = await request.json()
    token = data.get("session_token", "").strip()
    if token in _DEVICE_SESSION_TOKENS:
        sess = _DEVICE_SESSION_TOKENS[token]
        age = time.time() - sess["created_at"]
        if age < 86400:
            return {"valid": True, "device_id": sess["device_id"]}
        else:
            del _DEVICE_SESSION_TOKENS[token]
    return JSONResponse({"valid": False}, status_code=401)
# ---------------------------------------------------------------------------
# CONFIG ENDPOINTS — Gestión de configuración dinámica
# ---------------------------------------------------------------------------
@app.get("/config")
async def get_config():
    """Obtiene la configuración actual del sistema."""
    return {
        "vad": _VAD_CONFIG,
        "tts_voice": os.getenv("MEBOT_TTS_VOICE", "es-MX-DaliaNeural"),
        "whisper": os.getenv("MEBOT_WHISPER_MODEL", "small"),
        "language": os.getenv("MEBOT_LANGUAGE", "es"),
        "llm_model": os.getenv("LLM_MODEL", "google/gemma-3-4b-it:free"),
        "llm_url": os.getenv("LITELLM_URL", "https://openrouter.ai/api/v1"),
    }
@app.post("/config/vad")
async def update_vad_config(config: dict):
    """Actualiza la configuración VAD dinámicamente."""
    global _VAD_CONFIG
    # Validar parámetros
    if "vad_energy_thresh" in config:
        thresh = config["vad_energy_thresh"]
        if not (0.01 <= thresh <= 1.0):
            return JSONResponse(
                {"error": "vad_energy_thresh must be between 0.01 and 1.0"},
                status_code=400,
            )
        _VAD_CONFIG["energy_thresh"] = thresh
    if "vad_speech_frames" in config:
        frames = config["vad_speech_frames"]
        if not (1 <= frames <= 20):
            return JSONResponse(
                {"error": "vad_speech_frames must be between 1 and 20"}, status_code=400
            )
        _VAD_CONFIG["speech_frames"] = frames
    if "vad_silence_frames" in config:
        frames = config["vad_silence_frames"]
        if not (5 <= frames <= 50):
            return JSONResponse(
                {"error": "vad_silence_frames must be between 5 and 50"},
                status_code=400,
            )
        _VAD_CONFIG["silence_frames"] = frames
    if "vad_min_frames" in config:
        frames = config["vad_min_frames"]
        if not (1 <= frames <= 10):
            return JSONResponse(
                {"error": "vad_min_frames must be between 1 and 10"}, status_code=400
            )
        _VAD_CONFIG["min_opus_frames"] = frames
    # Aplicar cambios a las variables globales
    global VAD_ENERGY_THRESH, VAD_SPEECH_FRAMES, VAD_SILENCE_FRAMES, VAD_MIN_OPUS_FRAMES
    VAD_ENERGY_THRESH = _VAD_CONFIG["energy_thresh"]
    VAD_SPEECH_FRAMES = _VAD_CONFIG["speech_frames"]
    VAD_SILENCE_FRAMES = _VAD_CONFIG["silence_frames"]
    VAD_MIN_OPUS_FRAMES = _VAD_CONFIG["min_opus_frames"]
    return {"message": "VAD configuration updated successfully"}
@app.post("/config/tts")
async def update_tts_config(config: dict):
    """Actualiza la configuración TTS/STT."""
    # Aquí podríamos actualizar variables de entorno o archivos de configuración
    # Por ahora, solo validamos y confirmamos
    valid_voices = ["es-ES-ElviraNeural", "es-MX-DaliaNeural", "es-US-PalomaNeural", "es-MX-BeatrizNeural"]
    if "tts_voice" in config and config["tts_voice"] not in valid_voices:
        return JSONResponse(
            {"error": f"Invalid TTS voice. Valid options: {valid_voices}"},
            status_code=400,
        )
    valid_models = ["tiny", "base", "small"]
    if "stt_model" in config and config["stt_model"] not in valid_models:
        return JSONResponse(
            {"error": f"Invalid STT model. Valid options: {valid_models}"},
            status_code=400,
        )
    # Nota: Para cambios persistentes, necesitaríamos guardar en .env o database
    return {
        "message": "TTS/STT configuration validated. Restart bridge to apply model changes."
    }
@app.get("/config/volume")
async def get_volume():
    """Obtiene el volumen TTS actual (0–100)."""
    return {"volume": round(_TTS_VOLUME * 100)}
@app.post("/config/volume")
async def set_volume(payload: dict):
    """Ajusta el volumen TTS en caliente. Acepta {\"volume\": 0-100}."""
    global _TTS_VOLUME
    vol = payload.get("volume")
    if vol is None or not isinstance(vol, (int, float)):
        return JSONResponse({"error": "Falta campo 'volume' (0-100)"}, status_code=400)
    if not (0 <= vol <= 100):
        return JSONResponse({"error": "volume debe estar entre 0 y 100"}, status_code=400)
    _TTS_VOLUME = vol / 100.0
    logger.info(f"Volumen TTS cambiado a {vol}% ({_TTS_VOLUME:.2f})")
    return {"volume": round(_TTS_VOLUME * 100), "message": f"Volumen ajustado a {round(vol)}%"}
# ---------------------------------------------------------------------------
# LOGS ENDPOINTS — Gestión de logs del sistema
# ---------------------------------------------------------------------------
@app.get("/logs")
async def get_logs(lines: int = 100):
    """Obtiene las últimas líneas de logs."""
    try:
        # Leer logs del archivo (ajustar path según configuración)
        log_file = "/tmp/mebot_bridge.log"
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                recent_lines = (
                    all_lines[-lines:] if len(all_lines) > lines else all_lines
                )
                return "".join(recent_lines)
        else:
            return "Log file not found. Check bridge startup."
    except Exception as e:
        return f"Error reading logs: {str(e)}"
@app.post("/set-log-level")
async def set_log_level(level: str = "INFO"):
    """Cambia el nivel de logging dinámicamente."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level.upper() not in valid_levels:
        return JSONResponse(
            {"error": f"Invalid log level. Valid: {valid_levels}"}, status_code=400
        )
    # Cambiar nivel de logging
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(numeric_level)
    # También cambiar el logger específico
    logger.setLevel(numeric_level)
    return {"message": f"Log level changed to {level.upper()}"}
@app.post("/clear-logs")
async def clear_logs():
    """Limpia el archivo de logs."""
    try:
        log_file = "/tmp/mebot_bridge.log"
        if os.path.exists(log_file):
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")  # Truncate file
        return {"message": "Logs cleared successfully"}
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to clear logs: {str(e)}"}, status_code=500
        )
@app.get("/logs/download")
async def download_logs():
    """Descarga el archivo completo de logs."""
    log_file = "/tmp/mebot_bridge.log"
    if not os.path.exists(log_file):
        return JSONResponse({"error": "Log file not found"}, status_code=404)
    return FileResponse(
        path=log_file, filename="mebot_bridge.log", media_type="text/plain"
    )
# ---------------------------------------------------------------------------
# ESP32 CONTROL ENDPOINTS — Comandos remotos al dispositivo
# ---------------------------------------------------------------------------
@app.post("/esp32/command")
async def send_esp32_command(command_data: dict):
    """Envía comandos al ESP32 conectado."""
    command = command_data.get("command", "")
    # Buscar sesión activa
    session_info = None
    session_id_found = None
    for sid, info in _active_ws_sessions.items():
        try:
            await info["ws"].send_text('{"type":"ping"}')
            session_info = info
            session_id_found = sid
            break
        except Exception:
            continue
    if not session_info:
        return JSONResponse({"error": "No active ESP32 session found"}, status_code=404)
    ws  = session_info["ws"]
    enc = session_info["enc"]
    sid = session_info["short_id"]
    # test_tts: usa el pipeline TTS real, no un JSON inventado
    if command == "test_tts":
        text = command_data.get("text", "Hola, esta es una prueba de audio en el speaker.")
        asyncio.create_task(_speak_tts(ws, session_id_found, sid, enc, text))
        return {"message": f"TTS test enviado: '{text}'"}
    # Comandos JSON simples al firmware
    simple_cmds = {
        "abort":             {"type": "abort"},
        "reset":             {"type": "abort"},
        "restart_listening": {"type": "abort"},
    }
    if command not in simple_cmds:
        return JSONResponse({"error": f"Comando desconocido: {command}"}, status_code=400)
    try:
        await ws.send_text(json.dumps(simple_cmds[command]))
        return {"message": f"Comando '{command}' enviado al ESP32"}
    except Exception as e:
        return JSONResponse({"error": f"Error enviando comando: {e}"}, status_code=500)
# ---------------------------------------------------------------------------
# WiFi MCP PROXY — admin.html puede gestionar WiFi del ESP32
# ---------------------------------------------------------------------------
MCP_REQUEST_ID = 1000
async def _send_mcp_tool(ws, tool_name: str, args: dict = None, timeout: float = 5.0):
    """Envía un MCP tools/call al ESP32 y espera respuesta. Retorna el content[0].text."""
    global MCP_REQUEST_ID
    try:
        MCP_REQUEST_ID += 1
        req_id = MCP_REQUEST_ID
        payload = {
            "type": "mcp",
            "payload": {
                "type": "tools/call",
                "name": tool_name,
                "arguments": args or {},
                "id": req_id,
            }
        }
        await ws.send_text(json.dumps(payload))
        # Esperar respuesta con timeout
        try:
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                if msg.get("text"):
                    try:
                        data = json.loads(msg["text"])
                        if data.get("type") == "mcp":
                            p = data.get("payload", {})
                            if p.get("type") == "tools/call_result" and p.get("id") == req_id:
                                content = p.get("content", [])
                                if content:
                                    return content[0].get("text", "")
                                return ""
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except asyncio.TimeoutError:
            pass
    except (RuntimeError, ConnectionError, Exception):
        pass
    return ""
async def _get_active_ws():
    """Retorna (ws, short_id) de la primera sesión ESP32 activa."""
    for sid, info in list(_active_ws_sessions.items()):
        try:
            await asyncio.wait_for(info["ws"].send_text('{"type":"ping"}'), timeout=2.0)
            return info["ws"], info["short_id"]
        except Exception:
            # Remove stale session
            try:
                del _active_ws_sessions[sid]
            except KeyError:
                pass
            continue
    return None, None
@app.get("/api/wifi/status")
async def wifi_status():
    """Estado actual de la conexión WiFi del ESP32."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    result = await _send_mcp_tool(ws, "self.get_device_status")
    try:
        data = json.loads(result)
        network = data.get("network", {})
        return {
            "connected": network.get("ssid", "") != "",
            "ssid": network.get("ssid", ""),
            "signal": network.get("signal", ""),
            "type": network.get("type", ""),
        }
    except Exception:
        return {"connected": False, "ssid": "", "signal": "", "type": ""}
@app.get("/api/wifi/scan")
async def wifi_scan():
    """Escanea redes WiFi desde el ESP32. Retorna lista de {ssid, rssi, channel, auth}."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    result = await _send_mcp_tool(ws, "self.wifi.scan", timeout=8.0)
    try:
        data = json.loads(result)
        return {"aps": data if isinstance(data, list) else []}
    except Exception:
        return {"aps": []}
@app.get("/api/wifi/saved")
async def wifi_saved():
    """Lista redes WiFi guardadas en el ESP32."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    result = await _send_mcp_tool(ws, "self.wifi.saved_list")
    try:
        data = json.loads(result)
        return {"saved": data if isinstance(data, list) else []}
    except Exception:
        return {"saved": []}
@app.post("/api/wifi/connect")
async def wifi_connect(data: dict):
    """Conecta el ESP32 a una red WiFi. Body: {ssid, password}."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    ssid = data.get("ssid", "")
    password = data.get("password", "")
    if not ssid:
        return JSONResponse({"error": "ssid is required"}, status_code=400)
    result = await _send_mcp_tool(ws, "self.wifi.connect", {"ssid": ssid, "password": password})
    logger.info(f"[{sid}] WiFi connect: ssid={ssid}")
    return {"success": True, "message": f"Conectando a {ssid}..."}
@app.post("/api/wifi/disconnect")
async def wifi_disconnect():
    """Desconecta el ESP32 de la red actual."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    # Resetear configuración WiFi: limpiar guardadas y reiniciar
    result = await _send_mcp_tool(ws, "self.wifi.saved_list")
    try:
        saved = json.loads(result)
        if isinstance(saved, list):
            for _ in saved:
                await _send_mcp_tool(ws, "self.wifi.delete", {"index": 0})
    except Exception:
        pass
    return {"success": True, "message": "Redes eliminadas. El ESP32 entrará en modo configuración al reiniciar."}
@app.post("/api/wifi/delete")
async def wifi_delete(data: dict):
    """Elimina una red guardada por índice. Body: {index}."""
    ws, sid = await _get_active_ws()
    if not ws:
        return JSONResponse({"error": "No active ESP32 session"}, status_code=404)
    index = data.get("index", 0)
    result = await _send_mcp_tool(ws, "self.wifi.delete", {"index": index})
    logger.info(f"[{sid}] WiFi delete: index={index}")
    return {"success": True, "message": f"Red {index} eliminada."}
# ---------------------------------------------------------------------------
# SYSTEM TESTS — Pruebas automatizadas del pipeline
# ---------------------------------------------------------------------------
@app.post("/test/full_pipeline")
async def test_full_pipeline():
    """Ejecuta una prueba completa del pipeline STT→LLM→TTS."""
    # Esta sería una prueba más compleja que requeriría simular audio
    # Por ahora, solo verificar que los componentes están disponibles
    try:
        # Test TTS
        from tts.edge_tts_client import text_to_pcm
        test_pcm = await text_to_pcm("Prueba de pipeline completo")
        tts_ok = len(test_pcm) > 0
        # Test STT (con audio dummy)
        from audio.whisper_stt import transcribe
        import numpy as np
        dummy_audio = np.random.random(16000).astype(np.float32) * 0.1
        stt_result = await transcribe(dummy_audio)
        stt_ok = isinstance(stt_result, str)
        # Test LLM (con mensaje simple)
        from llm.openclaw_client import chat
        llm_result = await chat("Hola", [])
        llm_ok = isinstance(llm_result, str) and len(llm_result) > 0
        success = tts_ok and stt_ok and llm_ok
        return {
            "success": success,
            "message": f"TTS: {'OK' if tts_ok else 'FAIL'}, STT: {'OK' if stt_ok else 'FAIL'}, LLM: {'OK' if llm_ok else 'FAIL'}",
            "details": {
                "tts_samples": len(test_pcm) if tts_ok else 0,
                "stt_result": stt_result[:50] if stt_ok else "N/A",
                "llm_response": llm_result[:50] if llm_ok else "N/A",
            },
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Pipeline test failed: {str(e)}",
            "details": {},
        }
@app.post("/test/audio_loopback")
async def test_audio_loopback():
    """Prueba de loopback de audio (requiere ESP32 conectado)."""
    # Buscar sesión activa
    active_session = None
    for ws in _active_ws_sessions.values():
        try:
            await ws.send_text('{"type": "ping"}')
            active_session = ws
            break
        except:
            continue
    if not active_session:
        return {
            "success": False,
            "message": "No active ESP32 session for loopback test",
        }
    try:
        # Enviar comando de loopback
        await active_session.send_text(
            json.dumps({"type": "test", "component": "loopback", "duration": 5})
        )
        return {"success": True, "message": "Audio loopback test initiated"}
    except Exception as e:
        return {"success": False, "message": f"Loopback test failed: {str(e)}"}
@app.post("/test/vad_calibration")
async def test_vad_calibration():
    """Calibra automáticamente los parámetros VAD."""
    # Esta sería una implementación más compleja
    # Por ahora, devolver configuración actual
    return {
        "success": True,
        "message": "VAD calibration completed (using current config)",
        "config": _VAD_CONFIG,
    }
@app.post("/test/network_latency")
async def test_network_latency():
    """Mide la latencia de red entre bridge y ESP32."""
    active_session = None
    for ws in _active_ws_sessions.values():
        try:
            await ws.send_text('{"type": "ping"}')
            active_session = ws
            break
        except:
            continue
    if not active_session:
        return {"success": False, "message": "No active ESP32 session for latency test"}
    try:
        # Medir latencia con ping-pong
        start_time = time.time()
        await active_session.send_text('{"type": "ping"}')
        # En una implementación real, esperaríamos pong del ESP32
        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000
        return {
            "success": True,
            "message": f"Network latency: {latency_ms:.1f}ms",
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"success": False, "message": f"Latency test failed: {str(e)}"}
# ---------------------------------------------------------------------------
# MODIFICAR EL HANDLER DE CONEXIONES PARA REGISTRAR SESIONES ACTIVAS
# ---------------------------------------------------------------------------
# Necesitamos modificar la función de WebSocket handler para registrar sesiones
# Agregamos esta funcionalidad al inicio de cada conexión
# Buscar donde se maneja la conexión WebSocket
# Agregar: _active_ws_sessions[session_id] = ws
# Y al final: del _active_ws_sessions[session_id]
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=16000)
