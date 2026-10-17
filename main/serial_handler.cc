#include "serial_handler.h"
#include "mcp_server.h"
#include "settings.h"
#include "application.h"

#include <cstring>
#include <esp_log.h>
#include <cJSON.h>

#define TAG "SerialHandler"
#define SERIAL_UART_NUM UART_NUM_1
#define SERIAL_BAUD 115200
#define SERIAL_TXD (GPIO_NUM_17)
#define SERIAL_RXD (GPIO_NUM_18)
#define SERIAL_TASK_STACK 4096
#define SERIAL_TASK_PRIORITY 5

SerialHandler::SerialHandler() : line_pos_(0) {
    memset(line_buffer_, 0, sizeof(line_buffer_));
}

SerialHandler::~SerialHandler() {
    Stop();
}

void SerialHandler::Start() {
    if (running_) return;

    uart_config_t uart_config = {
        .baud_rate = SERIAL_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_LOGI(TAG, "Iniciando UART en GPIO TX:%d RX:%d @ %d baud", SERIAL_TXD, SERIAL_RXD, SERIAL_BAUD);

    uart_param_config(SERIAL_UART_NUM, &uart_config);
    uart_set_pin(SERIAL_UART_NUM, SERIAL_TXD, SERIAL_RXD, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    uart_driver_install(SERIAL_UART_NUM, SERIAL_HANDLER_BUF_SIZE * 2, 0, 0, NULL, 0);

    running_ = true;
    xTaskCreate(SerialTask, "serial_handler", SERIAL_TASK_STACK, this, SERIAL_TASK_PRIORITY, &task_handle_);
    ESP_LOGI(TAG, "Serial handler task creada");
}

void SerialHandler::Stop() {
    running_ = false;
    if (task_handle_) {
        vTaskDelete(task_handle_);
        task_handle_ = nullptr;
    }
    uart_driver_delete(SERIAL_UART_NUM);
    ESP_LOGI(TAG, "Serial handler detenido");
}

void SerialHandler::SerialTask(void* arg) {
    SerialHandler* handler = static_cast<SerialHandler*>(arg);
    uint8_t byte;

    while (handler->running_) {
        int len = uart_read_bytes(SERIAL_UART_NUM, &byte, 1, pdMS_TO_TICKS(100));
        if (len <= 0) continue;

        if (byte == '\n' || byte == '\r') {
            if (handler->line_pos_ > 0) {
                handler->line_buffer_[handler->line_pos_] = '\0';
                std::string cmd(handler->line_buffer_);
                handler->ProcessCommand(cmd);
                handler->line_pos_ = 0;
                memset(handler->line_buffer_, 0, sizeof(handler->line_buffer_));
            }
        } else if (handler->line_pos_ < SERIAL_HANDLER_BUF_SIZE - 1) {
            handler->line_buffer_[handler->line_pos_++] = (char)byte;
        }
    }
    vTaskDelete(NULL);
}

void SerialHandler::ProcessCommand(const std::string& cmd) {
    ESP_LOGI(TAG, "Comando recibido: '%s'", cmd.c_str());

    if (cmd.rfind("WIFI ", 0) == 0) {
        // Formato: WIFI ssid password
        std::string rest = cmd.substr(5);
        size_t space = rest.find(' ');
        if (space != std::string::npos) {
            std::string ssid = rest.substr(0, space);
            std::string password = rest.substr(space + 1);
            ESP_LOGI(TAG, "Configurando WiFi: SSID='%s'", ssid.c_str());

            Settings settings("wifi", true);
            settings.SetString("ssid", ssid);
            settings.SetString("password", password);
            settings.SetString("wifi_mode", "sta");
            settings.SetInt("save_wifi", 1);

            const char* resp = "WIFI OK\n";
            uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));

            // Schedule reboot or reconnect
            Application::GetInstance().Schedule([ssid, password]() {
                ESP_LOGI(TAG, "Credenciales WiFi guardadas. Reiniciando conexión...");
            });
        } else {
            const char* resp = "WIFI ERROR: formato WIFI ssid password\n";
            uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));
        }
    } else if (cmd == "REBOOT") {
        const char* resp = "REBOOT OK\n";
        uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    } else if (cmd == "PING") {
        const char* resp = "OK\n";
        uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));
    } else if (cmd == "INFO") {
        cJSON* json = cJSON_CreateObject();
        cJSON_AddStringToObject(json, "status", "ok");
        cJSON_AddNumberToObject(json, "free_heap", esp_get_free_heap_size());
        cJSON_AddNumberToObject(json, "free_psram", esp_get_free_internal_heap_size());
        cJSON_AddStringToObject(json, "fw_version", "2.6.0");
        char* json_str = cJSON_PrintUnformatted(json);
        std::string response = std::string("INFO ") + json_str + "\n";
        uart_write_bytes(SERIAL_UART_NUM, response.c_str(), response.length());
        cJSON_free(json_str);
        cJSON_Delete(json);
    } else if (cmd == "AUDIO") {
        // Nivel RMS del micrófono (leer desde VAD si está activo)
        float rms = 0.0f;
        // Por ahora retornar 0 (se puede mejorar con acceso al VAD real)
        char resp[32];
        snprintf(resp, sizeof(resp), "AUDIO:%.4f\n", rms);
        uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));
    } else {
        ESP_LOGW(TAG, "Comando desconocido: '%s'", cmd.c_str());
        const char* resp = "ERROR: comando desconocido\n";
        uart_write_bytes(SERIAL_UART_NUM, resp, strlen(resp));
    }
}
