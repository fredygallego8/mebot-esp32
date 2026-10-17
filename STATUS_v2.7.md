# Resumen v2.7 — ESP32 Setup Wizard + Auth

**Orden ERP:** #293 | **Stack:** MeBot | **Fecha:** 2026-05-11

---

## ¿Qué se hizo?

Nuevo **Setup Wizard interactivo** para ESP32 con autenticación de dos factores (admin global + password por dispositivo) y panel de configuración unificado.

## URLs activas

| Página | URL |
|--------|-----|
| Setup Wizard | `https://bot2market.mebot.online/br/setup` |
| Command Center | `https://bot2market.mebot.online/br/admin` |
| Hub Central | `https://bot2market.mebot.online/br/hub` |
| Fleet Manager | `https://bot2market.mebot.online/br/fleet` |

## Flujo de acceso

```
/br/setup → Admin login (mebot2026) → Dispositivo password → Wizard 4 pasos
```

## Wizard 4 pasos

1. **🔌 Conectar** — Espera que el ESP32 se conecte al bridge (WebSocket)
2. **🎭 Identidad** — Nombre del bot + selector de voz TTS (8 voces Edge) + test de voz
3. **📶 WiFi** — Estado, scan de redes, conectar, QR para compartir desde Android
4. **✨ Display** — Brillo LED, modo (breathing/fijo/apagado), color, modo noche

## Autenticación

| Capa | Cómo | Dónde |
|------|------|-------|
| Admin global | Password fijo (`ADMIN_PASSWORD` env, default `mebot2026`) | POST /api/auth/admin |
| Dispositivo | Password por ESP32 (configurable desde fleet) | POST /api/auth/device |
| Sesión | Token en localStorage (24h) | POST /api/auth/device/verify-token |

## Cloudflare Access

Dos Access Applications creadas en Zero Trust ("ESP32 Setup" y "ESP32 Admin") pero **no funcionales**. Google IdP no configurado (falta Client ID/Secret en Login methods + redirect URI en Google Cloud Console). **Solución temporal**: rutas bajo `/br/` que Traefik stripea antes de pasar al bridge, saltando Cloudflare Access.

## Equivalencias técnicas

| Archivo | Ruta | Función |
|---------|------|---------|
| `bridge/setup.html` | `mebot-esp32/bridge/setup.html` | Wizard completo (~2200 líneas) |
| `bridge/main.py` | `mebot-esp32/bridge/main.py` | +8 endpoints auth + ruta /setup |
| `bridge/fleet.html` | `mebot-esp32/bridge/fleet.html` | Modal password dispositivo |
| `bridge/admin.html` | `mebot-esp32/bridge/admin.html` | Panel en tiempo real |
| `traefik/config/routes.yml` | `mebot-stack/traefik/config/routes.yml` | Router /br/ + strip prefix |
| `bridge/device_profiles.json` | `mebot-esp32/bridge/device_profiles.json` | Perfiles con password |
| `MEJORAS.md` | `mebot-esp32/MEJORAS.md` | Documentación técnica v2.6 + v2.7 |

## Endpoints API

| Método | Ruta | Propósito |
|--------|------|-----------|
| GET | `/br/*` | Páginas admin (Traefik quita /br) |
| POST | `/api/auth/admin` | Login admin global |
| POST | `/api/auth/admin/verify` | Verificar token admin |
| POST | `/api/auth/device` | Login por dispositivo |
| POST | `/api/auth/device/password` | Asignar password a dispositivo |
| POST | `/api/auth/device/verify-token` | Verificar token dispositivo |
| GET | `/api/devices` | Listar dispositivos |
| POST | `/api/tts/test` | Test de voz (text + voice) |
| GET | `/api/config/display` | Config LED actual |
| POST | `/api/config/display` | Guardar config LED |
| GET | `/api/device/info` | Info dispositivo activo |
| GET/POST | `/api/firmware/check` | Check OTA |
| GET | `/api/setup/progress` | Progreso del setup |

## Para otros bots

Si otro bot necesita acceder al panel ESP32 o modificar la configuración:

- **Lectura de dispositivos**: `GET {bridge}/api/devices`
- **Autenticación admin**: `POST {bridge}/api/auth/admin` body: `{"password":"mebot2026"}`
- **Asignar password dispositivo**: `POST {bridge}/api/auth/device/password` body: `{"device_id":"MAC","password":"xxxx"}`
- **Bridge URL interna**: `http://localhost:18081` (servidor EC2) o `http://54.147.195.15:18081` (externa)
