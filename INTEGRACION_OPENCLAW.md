# MeBot ESP32 — Estado de Integración
**v2.3.0+b28 · Launch Day · 2026-04-28**

---

## Progreso Global

| Componente | Estado | % |
|-----------|--------|---|
| Firmware ESP32 | ✅ v2.3.0+b28 Operativo | 95% |
| Bridge Python | ✅ v2.3.0+admin Operativo | 100% |
| LLM / Agente | ✅ OpenRouter + Agent API | 90% |
| Producción AWS | ✅ Desplegado (Nginx/TLS) | 85% |

---

## Checklist — Lo que funciona HOY (28 Abr 2026)

- ✅ **Firmware v2.3.0+b28** — Compilado con BinaryProtocol3 forzado (v3)
- ✅ **Panel Admin Web** — Acceso total vía `http://10.54.66.90:16000/admin`
- ✅ **BinaryProtocol3** — Audio claro (24kHz) y sin cortes (60ms rate-limit)
- ✅ **VAD Dinámico** — Umbral ajustable desde la web (0.02 - 0.05 RMS)
- ✅ **OTA de Producción** — Endpoint `/xiaozhi/ota/` entrega URLs WSS/TLS
- ✅ **Bridge Persistente** — Corriendo bajo systemd (mebot-bridge.service)
- ✅ **LCD Bubbles** — Chat visual en tiempo real (Usuario/Asistente)
- ✅ **Greeting TTS** — Saludo automático al iniciar escucha
- ✅ **Control Remoto** — Reset y Test TTS desde panel administrativo
- ⏳ **Wake word nativo ES** — Trabajo final en modelo Porcupine/Espressif
- ⏳ **Contexto ERP Completo** — Integración final con bot2market en curso
- ✅ **Git Clean** — Branch `main` sincronizada con todos los fixes de audio

---

## Arquitectura Final

```
WiFi LAN / AWS Cloud
│
├── M5Stack CoreS3 (ESP32-S3)
│     Firmware v2.3.0+b28 · OTA → bridge
│     Micrófono I2S → Opus 16kHz → VAD (Bridge)
│     Bridge → Opus 24kHz (BP3) → Altavoz I2S
│     LCD ILI9342C: Chat bubbles LVGL
│
└── mebot-bridge (FastAPI / Python 3.11)
      Port: 16000 (Local) / 443 (Nginx WSS)
      │
      ├── Whisper STT (modelo small, CPU optimized)
      ├── gTTS + Edge-TTS (Dual-mode fallback)
      └── OpenClaw Agent API / OpenRouter
            google/gemma-3-4b-it:free
```

---

## Protocolo WebSocket (v3)

- **Header Audio (TTS):** `[0x00][0x00][SZ_HI][SZ_LO]` (4 bytes)
- **Sample Rate:** 24000 Hz (Speaker) / 16000 Hz (Mic)
- **Frame Rate:** 60ms (16.6 fps)
- **NVS Config:** Todas las variables persistentes en el ESP32 (WiFi, URL, Token)

---

## Comandos Útiles para el Launch

```bash
# Verificar estado del servicio
sudo systemctl status mebot-bridge

# Ver logs en tiempo real (o vía /admin)
tail -f /tmp/mebot_bridge.log

# Forzar OTA en el ESP32
# Reiniciar dispositivo -> Botón físico -> Chequeo automático

# Test de audio directo (TTS)
curl -X POST http://localhost:16000/esp32/command \
     -H "Content-Type: application/json" \
     -d '{"command":"test_tts", "text":"MeBot sistema operativo y listo para producción"}'
```

---

*MeBot v2.3.0+b28 · Launch Day · NativoERP / Antigravity AI · 2026-04-28*
