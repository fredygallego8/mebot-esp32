"""
Mensajes del protocolo xiaozhi/mebot-esp32 (WebSocket v3).
Referencia: docs/websocket.md
"""
import json


def hello_response(session_id: str) -> str:
    return json.dumps({
        "type": "hello",
        "version": 3,
        "session_id": session_id,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,   # TTS output rate — firmware crea decoder Opus a esta tasa
            "channels": 1,
            "frame_duration": 60,
        },
    })


def stt_message(text: str) -> str:
    return json.dumps({
        "type": "stt",
        "text": text,
        "mode": "realtime",
    })


def llm_message(emotion: str = "neutral") -> str:
    return json.dumps({
        "type": "llm",
        "emotion": emotion,
        "text": "<TTS>",
    })


def tts_start(session_id: str) -> str:
    return json.dumps({
        "type": "tts",
        "state": "start",
        "sample_rate": 24000,   # M5Stack CoreS3 output sample rate
    })


def tts_sentence_start(text: str) -> str:
    return json.dumps({
        "type": "tts",
        "state": "sentence_start",
        "text": text,
    })


def tts_stop(session_id: str) -> str:
    return json.dumps({
        "type": "tts",
        "state": "stop",
    })


def parse_client_message(raw: str) -> dict:
    """Parsea un mensaje JSON del ESP32, retorna {} si es inválido."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
