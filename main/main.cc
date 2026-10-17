#include <esp_log.h>
#include <esp_err.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <nvs_sec_provider.h>
#include <driver/gpio.h>
#include <esp_event.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_app_desc.h>
#include <esp_chip_info.h>
#include <esp_flash.h>

#include "application.h"
#include "mebot_build_info.h"

#define TAG "main"

extern "C" void app_main(void)
{
    // ── Banner de arranque ─────────────────────────────────────────────────
    const esp_app_desc_t* app_desc = esp_app_get_description();
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    uint32_t flash_size = 0;
    esp_flash_get_size(NULL, &flash_size);
    ESP_LOGI(TAG, "╔══════════════════════════════════════╗");
    ESP_LOGI(TAG, "║  MeBot  v%s", app_desc->version);
    ESP_LOGI(TAG, "║  Build: %s  IDF: %s", MEBOT_BUILD_NUMBER_STR, app_desc->idf_ver);
    ESP_LOGI(TAG, "║  Compiled: %s %s", app_desc->date, app_desc->time);
    ESP_LOGI(TAG, "║  Chip: ESP32-S3  Cores: %d  Flash: %" PRIu32 "MB", chip.cores, flash_size / (1024 * 1024));
    ESP_LOGI(TAG, "╚══════════════════════════════════════╝");
    // ──────────────────────────────────────────────────────────────────────

    // Initialize NVS flash for WiFi configuration
    // When flash encryption is active, register the flash encryption key
    // as the NVS encryption provider (avoids needing a separate nvs_keys partition)
    nvs_sec_config_flash_enc_t nvs_sec_cfg = NVS_SEC_PROVIDER_CFG_FLASH_ENC_DEFAULT();
    esp_err_t ret = nvs_sec_provider_register_flash_enc(&nvs_sec_cfg, NULL);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_ARG) {
        ESP_LOGW(TAG, "NVS sec provider register: 0x%x", ret);
    }

    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND || ret == ESP_ERR_INVALID_ARG) {
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption (ret=0x%x)", ret);
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize and run the application
    auto& app = Application::GetInstance();
    app.Initialize();
    app.Run();  // This function runs the main event loop and never returns
}
