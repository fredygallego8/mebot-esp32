# MeBot ESP32 — Voice Agent Multi-Stack Factory Edition

(English | [中文](README_zh.md) | [日本語](README_ja.md))

> Full-stack voice agent factory edition: ESP32-S3 firmware + Python bridge + cloud orchestration. Built for enterprise IoT operations.

## The Complete Stack

```
[M5Stack CoreS3 ESP32-S3]
  ├── Secure Boot v2 + Flash Encryption
  ├── PSRAM Quad 512KB (for local inference)
  ├── Wake word "Hey MeBot" (ESP-SR Multinet)
  ├── TTS: 8 voices EdgeAzure via OpenRouter
  ├── WebSocket: wss://bot2market.mebot.online/esp32
  ├── Setup Wizard (4-step, 2FA admin + per-device password)
  ├── Relay control (GPIO2)
  └── OTA auto-update from bridge
       │
       ▼ wss://bot2market.mebot.online/esp32
[Python Bridge FastAPI]
  ├── Panel Admin /br/admin (live device stats)
  ├── Fleet Manager /br/fleet (device registration)
  ├── Setup Wizard /br/setup (4 steps)
  └── OTA proxy + MCP tool bridge
       │
       ▼ OpenRouter API
[LLM / Agent]  ←→  ERP / LLM Gateway / MeBot Bridge
   ├── OpenRouter models (DeepSeek/GLM/etc)
   ├── Enterprise ERP (Vendure/MeBot)
   └── Customer service bots (Telegram/Webchat)
```

## Quick Start

```bash
cd /home/fredy/Escritorio/projx262/bot_stacks_erp/mebot-esp32

# 1. Compile (ESP-IDF v5.5.4, ~10 min)
./build_espanol.sh

# 2. Flash to M5Stack CoreS3 (~30 sec)
./flash_m5stack.sh [PUERTO]

# 3. Deploy bridge if needed
cd /home/fredy/Escritorio/projx262/bot_stacks_erp/mebot_stack/bot-agent && docker compose up -d

# 4. Connect your device
# Connect to hotspot "Martillo-XXXX", open http://192.168.4.1
```

## Key Files

| File | Purpose |
|------|---------|
| `main/application.cc` | Firmware loop, event dispatcher |
| `main/ota.cc` | OTA update via WebSocket |
| `main/remote_config.cc` | Dynamic config download |
| `main/mcp_server.cc` | MCP tools exposed to agent |
| `bridge/main.py` | FastAPI bridge + admin panel |
| `bridge/setup.html` | 4-step setup wizard UI |
| `flash_m5stack.sh` | esptool flash script |
| `build_espanol.sh` | idf.py build with Spanish config |

## Architecture Deep Dive

See `STATUS_v2.7.md`, `INTEGRACION_OPENCLAW.md`, `docs/kanban.html`.
