#pragma once
#include <string>

/**
 * RemoteConfig — configuración dinámica desde servidor.
 *
 * El dispositivo descarga un JSON de:
 *   CONFIG_OTA_URL/../device-config.json
 * lo guarda en NVS (namespace "rconfig") y expone Get()/GetInt()
 * con fallback al valor por defecto hardcodeado.
 *
 * Formato JSON esperado del servidor:
 * {
 *   "wifi_greet"  : "Hola, Estoy de vuelta",
 *   "bot_name"    : "MeBot",
 *   "greeting_url": ""          // URL a OGG remoto (opcional, futuro)
 * }
 *
 * Límite NVS: claves ≤ 15 chars, valores string ≤ 4000 bytes.
 */
class RemoteConfig {
public:
    /** Descarga config del servidor y persiste en NVS. Llama 1 vez tras conectar. */
    static void Fetch();

    /** Lee valor string de NVS; si no existe devuelve default_val. */
    static std::string Get(const char* key, const char* default_val = "");

    /** Lee valor entero de NVS; si no existe devuelve default_val. */
    static int32_t GetInt(const char* key, int32_t default_val = 0);

    static constexpr const char* NVS_NS = "rconfig";
};
