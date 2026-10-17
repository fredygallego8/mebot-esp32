#include "remote_config.h"
#include "settings.h"
#include "system_info.h"
#include "boards/common/board.h"
#include "assets/lang_config.h"

#include <cJSON.h>
#include <esp_log.h>
#include <http.h>

#define TAG "RemoteConfig"

// URL base de config: mismo host que OTA, endpoint distinto
// Ej: https://bot2market.mebot.online/esp32/config/
#ifndef CONFIG_REMOTE_CONFIG_URL
#define CONFIG_REMOTE_CONFIG_URL "https://i2.mebot.online/esp32/config/"
#endif

void RemoteConfig::Fetch() {
    auto& board = Board::GetInstance();
    auto network = board.GetNetwork();
    if (!network) {
        ESP_LOGW(TAG, "No network available");
        return;
    }

    auto http = network->CreateHttp(0);
    if (!http) {
        ESP_LOGW(TAG, "Failed to create HTTP client");
        return;
    }

    http->SetHeader("Device-Id",  SystemInfo::GetMacAddress().c_str());
    http->SetHeader("Client-Id",  board.GetUuid());
    http->SetHeader("User-Agent", SystemInfo::GetUserAgent().c_str());
    http->SetHeader("Accept-Language", Lang::CODE);
    http->SetHeader("Content-Type", "application/json");

    Settings settings("wifi", false);
    std::string url = settings.GetString("config_url");
    if (url.empty()) {
        url = CONFIG_REMOTE_CONFIG_URL;
    }

    ESP_LOGI(TAG, "Fetching config from %s", url.c_str());

    if (!http->Open("GET", url)) {
        ESP_LOGW(TAG, "Failed to connect to config server (code=%d)", http->GetLastError());
        return;
    }

    auto status = http->GetStatusCode();
    if (status != 200) {
        ESP_LOGW(TAG, "Config server returned %d", status);
        http->Close();
        return;
    }

    std::string body = http->ReadAll();
    http->Close();

    if (body.empty()) {
        ESP_LOGW(TAG, "Empty config response");
        return;
    }

    auto* root = cJSON_Parse(body.c_str());
    if (!root) {
        ESP_LOGW(TAG, "Invalid JSON: %.80s", body.c_str());
        return;
    }

    // Persistir todos los campos string e int en NVS
    Settings cfg(NVS_NS, true);
    int saved = 0;
    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, root) {
        if (!item->string) continue;
        // NVS key máx 15 chars
        if (strlen(item->string) > 15) {
            ESP_LOGW(TAG, "Key too long (>15): %s — skipped", item->string);
            continue;
        }
        if (cJSON_IsString(item) && item->valuestring) {
            cfg.SetString(item->string, item->valuestring);
            ESP_LOGI(TAG, "  cfg[%s] = \"%s\"", item->string, item->valuestring);
            saved++;
        } else if (cJSON_IsNumber(item)) {
            cfg.SetInt(item->string, (int32_t)item->valuedouble);
            ESP_LOGI(TAG, "  cfg[%s] = %d", item->string, (int32_t)item->valuedouble);
            saved++;
        }
    }
    cJSON_Delete(root);

    ESP_LOGI(TAG, "RemoteConfig: %d keys updated", saved);
}

std::string RemoteConfig::Get(const char* key, const char* default_val) {
    Settings cfg(NVS_NS, false);
    return cfg.GetString(key, default_val ? default_val : "");
}

int32_t RemoteConfig::GetInt(const char* key, int32_t default_val) {
    Settings cfg(NVS_NS, false);
    return cfg.GetInt(key, default_val);
}
