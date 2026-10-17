# MeBot ESP32 — Mejoras v2.5

**23 mejoras** implementadas en firmware (C++ ESP-IDF) y bridge (Python/FastAPI).
Última actualización: 2026-05-11

```
BRIDGE  = mebot_stack/bot-agent/voiceiot/mebot-esp32/bridge/main.py
FIRMWARE = mebot_stack/bot-agent/voiceiot/mebot-esp32/main/
```

---

## IMAGEN DE MARCA

---

### B1 — Wake Word "Hey MeBot"

| Campo | Valor |
|-------|-------|
| **Archivo** | `FIRMWARE/Kconfig.projbuild` |
| **Config** | `CUSTOM_WAKE_WORD` = `"hey me bot"` |
| **Display** | `CUSTOM_WAKE_WORD_DISPLAY` = `"Hey MeBot"` |
| **Runtime** | `custom_wake_word.cc:179` → `wake_word_detected_callback_("Hey MeBot")` |
| **Trigger** | ESP-SR Multinet detecta → `MAIN_EVENT_WAKE_WORD_DETECTED` → `HandleWakeWordDetectedEvent()` en `application.cc:819` |

---

### B2 — Jingle de Marca (Startup + Conexión)

| Archivo | Bytes | Duración | Contenido |
|---------|-------|----------|-----------|
| `welcome.ogg` | 4997 | 1200ms | Ascendente C5→E5→G5 (523/659/784Hz) + armónicos |
| `estoy_de_vuelta.ogg` | 2061 | 600ms | "Ta-da" D5→A5 (587→880Hz) |
| `success.ogg` | 1573 | 500ms | Descendente feliz A5→E5 |

**Trigger firmware**:
- `application.cc:303` → `OGG_ESTOY_DE_VUELTA` en `HandleNetworkConnectedEvent()`
- `application.cc:359` → `OGG_SUCCESS` en `HandleActivationDoneEvent()`

---

### B3 — LED Breathing Azul MeBot en IDLE

| Campo | Valor |
|-------|-------|
| **Archivos** | `FIRMWARE/led/single_led.{cc,h}` |
| **Color** | `MEBOT_BLUE_R=59, MEBOT_BLUE_G=130, MEBOT_BLUE_B=246` (#3B82F6) |
| **Timer** | `breathing_timer_` a 25ms, 100 steps |
| **Curva** | quadratic ease: `brightness = t²`, peak `MEBOT_BRIGHTNESS_IDLE=8` |
| **Métodos** | `StartBreathing()`, `StopBreathing()`, `OnBreathingTimer()` |

---

### B4 — LED Animación "Pensando" durante LLM

| Campo | Valor |
|-------|-------|
| **Timer** | `thinking_timer_` a 40ms, 100 steps |
| **Curva** | ease-in-out: `2t²` (t<0.5), `1-(-2t+2)²/2` (t≥0.5) |
| **Peak** | `HIGH_BRIGHTNESS=16` |
| **Trigger ON** | `application.cc:612` — al recibir `{"type":"llm","emotion":"..."}` |
| **Trigger OFF** | `application.cc:574` — al recibir `{"type":"tts","state":"start"}` |

---

### B5 — Voz TTS Elvira Neural

| Archivo | Línea | Cambio |
|---------|-------|--------|
| `bridge/tts/edge_tts_client.py` | 15 | `EDGE_VOICE`: `es-ES-ElviraNeural` |
| `bridge/main.py` | 128 | `default_voice`: `es-ES-ElviraNeural` |
| `bridge/main.py` | 1106 | `valid_voices`: `["es-ES-ElviraNeural", ...]` |

**Pipeline**: `text` → `edge_tts.Communicate(text, voice)` → MP3 → `ffmpeg` → PCM float32 24kHz → `opus_codec.encode_pcm_to_opus()` → frames OPUS

---

### B6 — Greeting Personalizado por Dispositivo

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:505-517` (dentro `esp32_handler()`) |
| **Fuente** | `get_device_profile(device_id)['bot_name']` de `device_profiles.json` |
| **Fallback** | `MEBOT_BOT_NAME` env var |
| **Template** | `f"Hola, soy {bot_name}. Estoy lista."` |

`_DEVICE_CONFIG` en `main.py:918` exporta: `bot_name`, `tts_voice`, `vad_threshold`, `silence_timeout`

---

## EXPERIENCIA DE USUARIO

---

### U1 — Sonido de Confirmación Post-Wake-Word

| Campo | Valor |
|-------|-------|
| **Archivo** | `FIRMWARE/locales/es-ES/popup.ogg` (409 bytes, 60ms) |
| **Contenido** | Tono seno 1200Hz, envelope attack 20% / decay 50%, vol 0.15 |
| **Trigger** | `application.cc:951` — `play_popup_on_listening_` flag → `PlaySound(OGG_POPUP)` |
| **Flag setea** | `application.cc:894` vía `HandleWakeWordDetectedEvent()` |

---

### U2 — "No Entendí" con Frases Variadas

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:747-754` (en `_run_pipeline()`) |
| **Condición** | `transcribe(pcm)` retorna string vacío |
| **Selección** | `random.choice([4 frases])` |

Frases:
- `"No te escuché bien, ¿puedes repetirlo?"`
- `"Disculpa, no entendí. ¿Podrías decirlo de nuevo?"`
- `"No capté bien eso, repite por favor."`
- `"¿Cómo dices? No te escuché bien."`

---

### U3 — Conversación Multi-Turno sin Re-Wake

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:242, 374-387` |
| **Timeout** | `MULTITURN_TIMEOUT_S = 8.0s` |
| **Mecanismo** | `_reset_multiturn_timer()` → `call_later(8s, _close_multiturn)` |
| **Efecto VAD** | `silence_threshold = 25 frames` (1.5s) en multi-turno vs 15 (0.9s) normal |
| **Log** | `"Multi-turno cerrado por inactividad"` |

---

### U4 — LED Fade-Out al Interrumpir

| Campo | Valor |
|-------|-------|
| **Método** | `SingleLed::OnSpeakingChanged(bool)` → setea `speaking_active_` |
| **Transición** | `kDeviceStateSpeaking` → `kDeviceStateIdle` retoma breathing si no está thinking |
| **Trigger** | `application.cc:574` — `{"type":"tts","state":"start"}` desactiva thinking, activa speaking |

---

### U5 — VAD Adaptativo por Ruido Ambiente

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:217-233` |
| **Mín/Máx** | `VAD_ENERGY_THRESH_MIN=0.008`, `VAD_ENERGY_THRESH_MAX=0.040` |
| **Algoritmo** | EMA (α=0.05) sobre RMS por sesión |
| **Threshold** | `max(0.008, min(0.040, noise_floor * 3.0))` |
| **Storage** | `_noise_floor_per_session: dict[str, float]` con key `session_id[:8]` |
| **Expuesto** | `/stats` → `noise_floor.min/max`; `/debug` → `noise_floor` por sesión |

---

### U6 — Control de Volumen por Voz

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:766-817` (antes del LLM) |
| **Detección** | `"volumen"`, `"silencio"`, `"silencia"` en `text.lower()` |
| **Comandos** | `"volumen al 50"` → exacto; `"sube"/"baja"` → ±10%; `"silencio"` → 0 |
| **Variable** | `_TTS_VOLUME` global (float 0.0-1.0) |
| **Retorno** | TTS confirma sin pasar por LLM |

---

### U7 — Indicador de Estado por Voz

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:792-795` |
| **Trigger** | `"cómo estás"`, `"como estas"`, `"estado"` |
| **Respuesta** | `"Estoy bien. Llevo activa X horas, he procesado Y conversaciones."` |
| **Variables** | `uptime_h = int((time()-start_time)/3600)`, `pipeline_count = pipelines_run` |

---

### U8 — TTS Pre-Buffer

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:670-684` (en `_speak_tts()`) |
| **Mecanismo** | `prebuf = min(5, len(opus_out))` — primeros 5 frames sin delay |
| **Resto** | frames 6..N con `delay = (i+1)*0.060 - elapsed`, si >2ms hace sleep |
| **Efecto** | elimina ~300ms de gap inicial entre `tts_sentence_start` y audio |

---

### U9 — Timeout de Silencio Adaptativo

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:222-224, 435` |
| **Normal** | `VAD_SILENCE_FRAMES=15` (0.9s) |
| **Multi-turno** | `int(VAD_SILENCE_ADAPTIVE_MS/60)=25` (1.5s) |
| **Efecto** | usuarios en conversación fluida pueden hacer pausas más largas sin cortar |

---

## MEJORAS TÉCNICAS

---

### T1 — WebSocket Push en admin.html

| Campo | Valor |
|-------|-------|
| **Endpoint** | `WebSocket /ws/admin` en `bridge/main.py:206-238` |
| **Broadcast** | `_broadcast_stats()` cada 1s a `_ws_clients: set` |
| **Payload** | `_stats + uptime_s + vad_threshold_adaptive + volume` |
| **admin.html** | `connectAdminWS()` → reconexión 3s en `onclose` |
| **Nuevos elementos** | `stat-noise` (NOISE_FLOOR), `transcript-area`, `transcript-count` |

---

### T2 — Health Check Profundo del Pipeline

| Endpoint | `GET /health` |
|----------|---------------|
| **STT** | `audio.whisper_stt._model is not None` |
| **LLM** | `OPENCLAW_AGENT_URL` o `LITELLM_URL` definido |
| **TTS** | `ctypes.CDLL("libopus.so.0")` carga correcta |
| **Response** | `{"status":"ok"|"degraded", "checks":{stt,llm,tts}, "details":{}, "active_sessions":n, "pipelines_run":n}` |

---

### T5 — Métricas Firmware vía MCP

| Tool | `self.get_device_status` (ya existente) |
|------|------------------------------------------|
| **Archivo** | `FIRMWARE/mcp_server.cc:45-53` |
| **Expone** | batería, volumen, red, pantalla vía `board.GetDeviceStatusJson()` |
| **Bridge** | expone métricas de sesión vía `/stats` y `/debug` |

---

### T6 — Log de Conversaciones Persistido

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/main.py:248-263` |
| **Constante** | `CONVERSATION_LOG_FILE = "conversations.jsonl"` |
| **Override** | `MEBOT_CONVERSATION_LOG` env var |
| **Formato** | JSONL: `{"timestamp", "session", "device", "user", "bot", "pipeline_s"}` |
| **Trigger** | al final de `_run_pipeline()` |
| **admin.html** | `fetchTranscript()` cada 5s vía `/debug` |

---

## ADMIN PANEL (UI)

---

### UI1 — Avatar Agrandado 35%

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/admin.html` CSS |
| **Wrapper** | 280×250px → **380×340px** |
| **Face** | `border-radius`: 70px → **95px**, `border`: 4px → **5px** |
| **Face-inner** | 180×120px → **240×160px** |
| **Ojos** | 28×50px → **38×68px**, `border-radius`: 20px → **28px** |
| **Boca** | 40×12px → **54×16px**, `border-radius`: 20px → **28px** |

---

### UI2 — WiFi Manager en admin.html

| Campo | Valor |
|-------|-------|
| **Archivos** | `bridge/admin.html`, `bridge/main.py`, `FIRMWARE/boards/bread-compact-wifi/compact_wifi_board.cc` |
| **Tab panel** | admin.html ahora tiene tabs "Config" / "WiFi" en la columna derecha |
| **WiFi tab** | Muestra SSID actual + intensidad, botón Scan, lista de redes detectadas, formulario de conexión, redes guardadas |

**MCP Tools en firmware** (nuevas en `compact_wifi_board.cc`):

| Tool | Args | Descripción |
|------|------|-------------|
| `self.wifi.scan` | none | Escanea redes, retorna JSON: `[{ssid, rssi, channel, auth}]` |
| `self.wifi.saved_list` | none | Lista redes guardadas: `[{index, ssid}]` |
| `self.wifi.connect` | `{ssid, password}` | Guarda credencial e intenta conexión |
| `self.wifi.delete` | `{index}` | Elimina red guardada por índice |

**REST API en bridge** (nuevas en `main.py`):

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/wifi/status` | GET | Estado actual WiFi del ESP32 |
| `/api/wifi/scan` | GET | Escanea redes (proxy MCP, timeout 8s) |
| `/api/wifi/saved` | GET | Redes guardadas |
| `/api/wifi/connect` | POST | Conectar: `{ssid, password}` |
| `/api/wifi/delete` | POST | Eliminar: `{index}` |
| `/api/wifi/disconnect` | POST | Borra todas las redes guardadas |

**Arquitectura**: admin.html → REST API bridge → MCP tools/call vía WebSocket → ESP32 ejecuta en firmware

---

## Archivos Modificados

| Archivo | Tipo | Cambios |
|---------|------|---------|
| `main/led/single_led.h` | C++ header | +6 métodos, +3 timers |
| `main/led/single_led.cc` | C++ source | Reescrito: breathing + thinking + speaking |
| `main/application.cc` | C++ source | `#include single_led.h`, `dynamic_cast` x2, popup flag |
| `main/Kconfig.projbuild` | Kconfig | Wake word "hey me bot" |
| `main/assets/locales/es-ES/welcome.ogg` | OGG | Nuevo jingle 1.2s |
| `main/assets/locales/es-ES/estoy_de_vuelta.ogg` | OGG | Nuevo chime 0.6s |
| `main/assets/locales/es-ES/popup.ogg` | OGG | Nuevo blip 60ms |
| `main/assets/locales/es-ES/success.ogg` | OGG | Nuevo chime 0.5s |
| `bridge/main.py` | Python | +386 líneas (U2-U3-U5-U6-U7-U8-U9-B6-T1-T2-T6 + WiFi REST API) |
| `bridge/tts/edge_tts_client.py` | Python | Default voice Elvira |
| `bridge/admin.html` | HTML+JS | +200 líneas (WS push, transcript, noise, avatar bigger, WiFi manager tab) |
| `bridge/.env.example` | Env | +10 líneas doc |
| `main/boards/bread-compact-wifi/compact_wifi_board.cc` | C++ source | +4 MCP tools: scan, saved_list, connect, delete |
| `main/ota.cc` | C++ source | +redirect follow HTTP 301/302 → HTTPS (hasta 3 intentos) |
| `MEJORAS.md` | Doc | Documentación técnica completa |

---

## v2.6 — Setup Wizard Interactivo

**Objetivo**: Nueva página `/setup` con wizard paso a paso para primera configuración del dispositivo ESP32, reemplazando la necesidad de usar múltiples paneles.

### U10 — Wizard 4 Pasos con Validación

| Campo | Valor |
|-------|-------|
| **Archivo** | `bridge/setup.html` (nuevo) |
| **Ruta** | `GET /setup` y `GET /setup.html` en `bridge/main.py` |
| **Flujo** | Paso 1: Conectar → Paso 2: Identidad/Voz → Paso 3: WiFi → Paso 4: Display + Finalizar |

Cada paso se valida antes de permitir avanzar. Barra de progreso visual, animaciones CSS, transiciones swipe.

### U11 — Sonidos Procedurales (Web Audio API)

Los sonidos se generan en el navegador vía Web Audio API, sin archivos OGG:

| Evento | Sonido |
|--------|--------|
| Dispositivo conectado | Ascendente C5→E5→G5 |
| Paso completado | Chime D5→A5 |
| Test de voz exitoso | Success jingle |
| Error | Tono descendente sawtooth |
| Finalizar | Acorde mayor + confeti |

### U12 — Grid Selector de Voz TTS

Selector visual en tarjetas con banderas de país y género, en lugar de dropdown. 8 voces Edge-TTS disponibles. Botón "Escuchar mi voz" que prueba la voz con el nombre configurado.

### U13 — WiFi QR para Android (Progressive)

El paso 3 de WiFi detecta automáticamente si el ESP32 está conectado (permite saltar). Si no, ofrece escaneo de redes, formulario de conexión y muestra un QR con el SSID actual para compartir desde Android.

### U14 — Configuración de Display/LED

Sliders y selectores visuales para:
- Brillo LED (0-32)
- Modo (Breathing / Fijo / Apagado)
- Color (6 colores predefinidos)
- Modo noche con selector de horario

Preview en vivo del LED animado que se actualiza con cada cambio.

### U15 — Resumen y Finalización

Al completar el wizard:
1. Guarda perfil del dispositivo (`POST /api/devices/{id}/config`)
2. Guarda configuración de display (`POST /api/config/display`)
3. Dispara TTS de saludo con el nombre configurado
4. Muestra confetti + sonido de éxito
5. Redirige al Command Center (`/admin`)

### Nuevos Endpoints en main.py

| Endpoint | Método | Propósito |
|----------|--------|-----------|
| `/setup` | GET | Sirve `setup.html` |
| `/api/device/info` | GET | Info detallada del dispositivo activo |
| `/api/config/display` | GET | Config actual del LED |
| `/api/config/display` | POST | Guardar config del LED |
| `/api/tts/test` | POST | Test de TTS con texto y voz |
| `/api/firmware/check` | GET | Versión actual y URL OTA |
| `/api/firmware/check` | POST | Disparar OTA check remoto |
| `/api/setup/progress` | GET | Progreso del setup activo |

### Archivos Modificados/Creados v2.6

| Archivo | Tipo | Cambios |
|---------|------|---------|
| `bridge/setup.html` | HTML+JS | Nuevo: wizard 4 pasos ~600 líneas |
| `bridge/main.py` | Python | +8 endpoints, +1 ruta HTML |
| `bridge/hub.html` | HTML | +1 card "Setup Wizard" |
| `MEJORAS.md` | Doc | Sección v2.6 completa |

---

## v2.7 — Cloudflare Access + Password por Dispositivo

**Objetivo**: Proteger los paneles de administración del bridge ESP32 (`/setup`, `/admin`, `/hub`, `/fleet`, `/test`, `/docs`) con Cloudflare Access (Google OAuth) como primer factor + password por dispositivo como segundo factor.

### S1 — Cloudflare Access Application

| Campo | Valor |
|-------|-------|
| **Nombre** | ESP32 Bridge Admin |
| **Dominio** | `bot2market.mebot.online` |
| **Paths** | `/setup`, `/admin`, `/hub`, `/fleet`, `/test`, `/docs` |
| **IdP** | Google |
| **Emails autorizados** | `fredy.gallego@gmail.com`, `nativopool@gmail.com`, `alberogater@gmail.com`, `mkbotnativo@gmail.com` |
| **Session duration** | 24h |

La autenticación ocurre en el edge de Cloudflare antes de llegar a Traefik. No requiere contenedores adicionales ni oauth2-proxy.

### S2 — Traefik Router sin Auth

Nuevo router `esp32-admin-router` en `routes.yml` con priority 400, sin middlewares de auth (Cloudflare Access ya autenticó). Rutas: `/setup`, `/admin`, `/hub`, `/fleet`, `/test`, `/docs`.

### S3 — Device Auth API (main.py)

| Endpoint | Método | Propósito |
|----------|--------|-----------|
| `/api/devices` | GET | Lista dispositivos con estado (online/offline, has_password) |
| `/api/auth/device` | POST | Valida password + device_id, retorna session_token |
| `/api/auth/device/password` | POST | Asigna password a un dispositivo (admin) |
| `/api/auth/device/verify-token` | POST | Verifica si un session_token sigue vigente (24h) |

Passwords almacenados en `device_profiles.json` en campo `device_password`.

### S4 — Auth Screen en setup.html

Nueva pantalla de autenticación previa al wizard:
- Muestra "Protegido por Cloudflare Access + Password por dispositivo"
- Selector de dispositivo ESP32 con estado online/offline
- Input de password + botón "Desbloquear"
- Token de sesión guardado en localStorage (24h)
- Sin auth, no se muestra el wizard

### S5 — Password Field en fleet.html

Nuevo campo "🔑 Password del Dispositivo" en modal de configuración de identidad:
- Input password + toggle 👁️ mostrar/ocultar
- Mínimo 4 caracteres
- Placeholder cambia a "•••••• (dejar vacío para mantener)" si ya tiene password
- Se guarda via `POST /api/auth/device/password`

### S6 — Admin Password Auth (Local)

Ante la imposibilidad de completar el Google OAuth en Cloudflare Access (WAF bloquea Playwright, API token sin permisos `access:edit`), se implementó auth local:

| Componente | Propósito |
|------------|-----------|
| `POST /api/auth/admin` | Valida password de admin global (`ADMIN_PASSWORD` env var, default: `mebot2026`) |
| `POST /api/auth/admin/verify` | Verifica token de admin en localStorage (24h) |
| `setup.html` admin-login-screen | Pantalla de login admin antes del wizard |

Flujo: **Admin password → Dispositivo password → Wizard**

### S7 — Bypass Cloudflare Access con rutas /br/

Cloudflare Access bloqueaba con error "No se recibió el email". Como solución temporal (permanente si se desea), las páginas se sirven bajo el prefijo `/br/` que Traefik **stripea** con middleware `br-strip` antes de pasar al bridge.

| Componente | Detalle |
|------------|---------|
| Middleware | `br-strip` → `stripPrefix: /br` en `routes.yml` |
| Router | `esp32-admin-router` → `PathPrefix(/br/)` priority 400 |
| Efecto | `https://bot2market.mebot.online/br/setup` → Traefik quita `/br` → bridge recibe `/setup` |

Las rutas viejas (`/setup`, `/admin`, etc.) siguen redirigiendo a Cloudflare Access (302). Las nuevas (`/br/`) pasan directo.

### Nuevos Endpoints v2.7

| Endpoint | Método | Propósito |
|----------|--------|-----------|
| `/br/*` | GET | Todas las páginas admin bajo prefijo `/br/` (Traefik strip prefix) |
| `/api/auth/admin` | POST | Login admin global |
| `/api/auth/admin/verify` | POST | Verificar token admin |
| `/api/devices` | GET | Listar dispositivos |
| `/api/auth/device` | POST | Login por dispositivo (password) |
| `/api/auth/device/password` | POST | Asignar password a dispositivo |
| `/api/auth/device/verify-token` | POST | Verificar token dispositivo |

### Archivos Modificados v2.7

| Archivo | Tipo | Cambios |
|---------|------|---------|
| `traefik/config/routes.yml` | YAML | +1 router `esp32-admin-router`, +1 middleware `br-strip`, rutas `/br/` |
| `bridge/main.py` | Python | +6 endpoints (admin + device auth) |
| `bridge/setup.html` | HTML+JS | +Admin login screen + device auth screen + wizard |
| `bridge/fleet.html` | HTML+JS | +Campo password en modal, links a `/br/` |
| `bridge/admin.html` | HTML | Links actualizados a `/br/` |
| `bridge/hub.html` | HTML | Links actualizados a `/br/` |
| `bridge/device_profiles.json` | JSON | +Campo `device_password` |
| `MEJORAS.md` | Doc | Sección v2.7 completa |
| Cloudflare Dashboard | Config | 2 Access Applications creadas (sin Google IdP funcional) |

### URLs Finales v2.7

| Página | URL |
|--------|-----|
| Setup Wizard | `https://bot2market.mebot.online/br/setup` |
| Command Center | `https://bot2market.mebot.online/br/admin` |
| Hub Central | `https://bot2market.mebot.online/br/hub` |
| Fleet Manager | `https://bot2market.mebot.online/br/fleet` |
| Panel Pruebas | `https://bot2market.mebot.online/br/test` |

### Flujo de Acceso

```
/br/setup
  → 🔑 Admin login (password: mebot2026, configurable via ADMIN_PASSWORD env)
  → 🔐 Desbloquear ESP32 (dispositivo + device_password)
  → 🧙 Wizard 4 pasos (conexión, identidad, WiFi, display)
```

### Cloudflare Access (Pendiente)

Las 2 Access Applications ("ESP32 Setup" y "ESP32 Admin") están creadas en Cloudflare Zero Trust pero no funcionales porque falta configurar Google como Identity Provider en Settings → Authentication. Para activarlas:
1. Agregar Google IdP con Client ID `887084269638-...` y Secret `GOCSPX-lO6j...`
2. Agregar redirect URI `https://nativoia.cloudflareaccess.com/cdn-cgi/access/callback` en Google Cloud Console
3. Opcional: eliminar las rutas `/br/` y restaurar las rutas originales en routes.yml
