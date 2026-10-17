"""
Cliente LLM para mebot-bridge.

Soporta dos backends (configurado por variables de entorno):

  Modo 1 — OpenClaw Agent API  (OPENCLAW_AGENT_URL definido)
  ─────────────────────────────────────────────────────────
  Usa el agente OpenClaw del stack bot2market directamente.
  El agente tiene contexto completo de NativoERP y mantiene
  el historial de conversación por session_id en el servidor.

    OPENCLAW_AGENT_URL=http://bot2market-agent:18789
    OPENCLAW_AGENT_USER=mercader.antigravity@gmail.com
    OPENCLAW_AGENT_ID=main

  Modo 2 — OpenAI-compatible (LiteLLM / OpenRouter)  [default]
  ─────────────────────────────────────────────────────────────
  Llama a cualquier proxy OpenAI-compatible.

    LITELLM_URL=https://openrouter.ai/api/v1
    LLM_KEY=sk-or-v1-...
    LLM_MODEL=google/gemma-3-4b-it:free
"""

import os
import logging
import httpx
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("mebot.llm")

# ── Modo 1: OpenClaw Agent API ───────────────────────────────────────────────
OPENCLAW_AGENT_URL = os.getenv("OPENCLAW_AGENT_URL", "").rstrip("/")
OPENCLAW_AGENT_USER = os.getenv("OPENCLAW_AGENT_USER", "mebot@mebot.local")
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", "main")

# ── Modo 2: OpenAI-compatible (LiteLLM / OpenRouter) ────────────────────────
LITELLM_URL = os.getenv("LITELLM_URL", "http://bot2market-litellm:4000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-3-4b-it:free")
LLM_KEY = os.getenv("LLM_KEY", "dummy")

def get_system_prompt() -> str:
    return os.getenv(
        "MEBOT_SYSTEM_PROMPT",
        (
            "Eres un asistente de voz compacto integrado en un dispositivo ESP32. "
            "Responde de forma breve (máximo 2-3 oraciones). "
            "Habla en el mismo idioma que el usuario. "
            "Nunca uses emojis ni markdown, solo texto plano."
        ),
    )


MAX_HISTORY = int(os.getenv("MEBOT_MAX_HISTORY", "6"))
MAX_TOKENS = int(os.getenv("MEBOT_MAX_TOKENS", "200"))
TIMEOUT_S = float(os.getenv("MEBOT_LLM_TIMEOUT", "30"))


# ── Modo 1: OpenClaw Agent ───────────────────────────────────────────────────


async def _chat_openclaw(user_text: str, session_id: str) -> str:
    """
    Envía el mensaje al agente OpenClaw y retorna su respuesta.
    El agente mantiene historial de conversación por session_id.
    """
    url = f"{OPENCLAW_AGENT_URL}/api/ui/chat/send"
    payload = {
        "message": user_text,
        "agentId": OPENCLAW_AGENT_ID,
        "sessionId": session_id,
        "sender": "user",
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Auth-Request-User": OPENCLAW_AGENT_USER,
    }
    logger.debug(
        f"OpenClaw Agent → {url}  session={session_id[:8]}  msg='{user_text[:60]}'"
    )
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    # El agente devuelve {message: {content: "..."}} o {message: "..."}
    msg = data.get("message", {})
    if isinstance(msg, dict):
        answer = msg.get("content", "") or msg.get("text", "")
    elif isinstance(msg, str):
        answer = msg
    else:
        answer = str(data)

    answer = answer.strip()
    logger.debug(f"OpenClaw Agent ← '{answer[:80]}'")
    return answer


# ── Modo 2: OpenAI-compatible ────────────────────────────────────────────────


def _build_messages(
    user_text: str, history: list[dict] | None, with_system: bool
) -> list[dict]:
    """Construye lista de mensajes para OpenAI-compatible API."""
    messages = []
    sys_prompt = get_system_prompt()
    if with_system:
        messages.append({"role": "system", "content": sys_prompt})
        if history:
            messages.extend(history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": user_text})
    else:
        # Modelos sin soporte de role:system (ej. Gemma) — prefijo en el user turn
        first_user = f"[Instrucciones: {sys_prompt}]\n\n{user_text}"
        if history:
            hist = list(history[-MAX_HISTORY:])
            if hist and hist[0]["role"] == "user":
                hist[0] = {
                    **hist[0],
                    "content": f"[Instrucciones: {sys_prompt}]\n\n{hist[0]['content']}",
                }
            messages.extend(hist)
            messages.append({"role": "user", "content": user_text})
        else:
            messages.append({"role": "user", "content": first_user})
    return messages


async def _chat_litellm(user_text: str, history: list[dict] | None) -> str:
    """Llama al proxy OpenAI-compatible con reintentos para modelos sin system role."""
    logger.debug(
        f"LiteLLM → model={LLM_MODEL} url={LITELLM_URL} msg='{user_text[:60]}'"
    )
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt, with_system in enumerate([True, False], 1):
            messages = _build_messages(user_text, history, with_system)
            payload = {
                "model": LLM_MODEL,
                "messages": messages,
                "max_tokens": MAX_TOKENS,
                "temperature": 0.7,
            }
            logger.debug(
                f"LLM request attempt {attempt}: url={LITELLM_URL} model={LLM_MODEL}"
            )
            resp = await client.post(
                f"{LITELLM_URL}/chat/completions",
                headers={
                    "cf-aig-authorization": f"Bearer {os.getenv('CF_GATEWAY_TOKEN', '')}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            logger.debug(f"LLM response status: {resp.status_code}")
            if resp.status_code == 400 and attempt == 1:
                body = resp.text[:300]
                if any(
                    k in body.lower() for k in ("system", "instruction", "developer")
                ):
                    logger.warning(
                        f"LLM: modelo no soporta system role, reintentando. ({body[:100]})"
                    )
                    continue
            resp.raise_for_status()
            break

    answer = resp.json()["choices"][0]["message"]["content"].strip()
    logger.debug(f"LiteLLM ← '{answer[:80]}'")
    return answer


# ── Punto de entrada público ─────────────────────────────────────────────────


async def chat(
    user_text: str,
    history: list[dict] | None = None,
    session_id: str | None = None,
) -> str:
    """
    Envía el texto del usuario al LLM y retorna la respuesta.

    Si OPENCLAW_AGENT_URL está definido, usa el agente OpenClaw (Modo 1).
    De lo contrario usa LiteLLM/OpenRouter (Modo 2).

    session_id: ID de sesión WebSocket — usado por el agente para mantener historial.
    history:    Historial local (usado solo en Modo 2).
    """
    if OPENCLAW_AGENT_URL:
        sid = session_id or "mebot-default"
        logger.info(
            f"LLM: usando OpenClaw Agent en {OPENCLAW_AGENT_URL} (session={sid[:8]})"
        )
        return await _chat_openclaw(user_text, sid)
    else:
        logger.info(f"LLM: usando LiteLLM en {LITELLM_URL} model={LLM_MODEL}")
        return await _chat_litellm(user_text, history)
