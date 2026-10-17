#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/oled_display.h"
#include "system_reset.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "mcp_server.h"
#include "lamp_controller.h"
#include "led/single_led.h"
#include "assets/lang_config.h"

#include <esp_log.h>
#include <driver/i2c_master.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <ssid_manager.h>
#include <wifi_manager.h>
#include <esp_wifi.h>
#include <cJSON.h>

#ifdef SH1106
#include <esp_lcd_panel_sh1106.h>
#endif

#define TAG "CompactWifiBoard"

class CompactWifiBoard : public WifiBoard {
private:
    i2c_master_bus_handle_t display_i2c_bus_;
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    Display* display_ = nullptr;
    Button boot_button_;
    Button touch_button_;
    Button volume_up_button_;
    Button volume_down_button_;

    void InitializeDisplayI2c() {
        i2c_master_bus_config_t bus_config = {
            .i2c_port = (i2c_port_t)0,
            .sda_io_num = DISPLAY_SDA_PIN,
            .scl_io_num = DISPLAY_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &display_i2c_bus_));
    }

    void InitializeSsd1306Display() {
        // SSD1306 config
        esp_lcd_panel_io_i2c_config_t io_config = {
            .dev_addr = 0x3C,
            .on_color_trans_done = nullptr,
            .user_ctx = nullptr,
            .control_phase_bytes = 1,
            .dc_bit_offset = 6,
            .lcd_cmd_bits = 8,
            .lcd_param_bits = 8,
            .flags = {
                .dc_low_on_data = 0,
                .disable_control_phase = 0,
            },
            .scl_speed_hz = 400 * 1000,
        };

        ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c_v2(display_i2c_bus_, &io_config, &panel_io_));

        ESP_LOGI(TAG, "Install SSD1306 driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = -1;
        panel_config.bits_per_pixel = 1;

        esp_lcd_panel_ssd1306_config_t ssd1306_config = {
            .height = static_cast<uint8_t>(DISPLAY_HEIGHT),
        };
        panel_config.vendor_config = &ssd1306_config;

#ifdef SH1106
        ESP_ERROR_CHECK(esp_lcd_new_panel_sh1106(panel_io_, &panel_config, &panel_));
#else
        ESP_ERROR_CHECK(esp_lcd_new_panel_ssd1306(panel_io_, &panel_config, &panel_));
#endif
        ESP_LOGI(TAG, "SSD1306 driver installed");

        // Reset the display
        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
        if (esp_lcd_panel_init(panel_) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize display");
            display_ = new NoDisplay();
            return;
        }
        ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_, false));

        // Set the display to on
        ESP_LOGI(TAG, "Turning display on");
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_, true));

        display_ = new OledDisplay(panel_io_, panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
        touch_button_.OnPressDown([this]() {
            Application::GetInstance().StartListening();
        });
        touch_button_.OnPressUp([this]() {
            Application::GetInstance().StopListening();
        });

        volume_up_button_.OnClick([this]() {
            auto codec = GetAudioCodec();
            auto volume = codec->output_volume() + 10;
            if (volume > 100) {
                volume = 100;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });

        volume_up_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(100);
            GetDisplay()->ShowNotification(Lang::Strings::MAX_VOLUME);
        });

        volume_down_button_.OnClick([this]() {
            auto codec = GetAudioCodec();
            auto volume = codec->output_volume() - 10;
            if (volume < 0) {
                volume = 0;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });

        volume_down_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(0);
            GetDisplay()->ShowNotification(Lang::Strings::MUTED);
        });
    }

    // WiFi MCP tools — administración de red desde el bridge/admin.html
    void InitializeTools() {
        static LampController lamp(LAMP_GPIO);

        // self.wifi.scan — escanea redes WiFi disponibles
        McpServer::GetInstance().AddTool("self.wifi.scan",
            "Scan available WiFi networks. Returns JSON array of {ssid, rssi, authmode}.",
            PropertyList(),
            [](const PropertyList&) -> ReturnValue {
                uint16_t ap_count = 0;
                uint16_t max_aps = 20;
                std::vector<wifi_ap_record_t> ap_records(max_aps);
                esp_wifi_scan_start(NULL, true);
                esp_wifi_scan_get_ap_records(&max_aps, ap_records.data());
                esp_wifi_scan_get_ap_num(&ap_count);
                if (ap_count > max_aps) ap_count = max_aps;

                auto root = cJSON_CreateArray();
                for (int i = 0; i < ap_count; i++) {
                    auto ap = cJSON_CreateObject();
                    cJSON_AddStringToObject(ap, "ssid", (const char*)ap_records[i].ssid);
                    cJSON_AddNumberToObject(ap, "rssi", ap_records[i].rssi);
                    cJSON_AddNumberToObject(ap, "channel", ap_records[i].primary);
                    const char* auth = "OPEN";
                    if (ap_records[i].authmode == WIFI_AUTH_WEP) auth = "WEP";
                    else if (ap_records[i].authmode == WIFI_AUTH_WPA_PSK) auth = "WPA";
                    else if (ap_records[i].authmode == WIFI_AUTH_WPA2_PSK) auth = "WPA2";
                    else if (ap_records[i].authmode == WIFI_AUTH_WPA_WPA2_PSK) auth = "WPA/WPA2";
                    else if (ap_records[i].authmode == WIFI_AUTH_WPA3_PSK) auth = "WPA3";
                    cJSON_AddStringToObject(ap, "auth", auth);
                    cJSON_AddItemToArray(root, ap);
                }
                return root;
            });

        // self.wifi.saved_list — lista de redes guardadas
        McpServer::GetInstance().AddTool("self.wifi.saved_list",
            "List saved WiFi networks (SSID only, passwords hidden).",
            PropertyList(),
            [](const PropertyList&) -> ReturnValue {
                auto& ssid_mgr = SsidManager::GetInstance();
                auto list = ssid_mgr.GetSsidList();
                auto root = cJSON_CreateArray();
                for (size_t i = 0; i < list.size(); i++) {
                    auto item = cJSON_CreateObject();
                    cJSON_AddNumberToObject(item, "index", i);
                    cJSON_AddStringToObject(item, "ssid", list[i].ssid.c_str());
                    cJSON_AddItemToArray(root, item);
                }
                return root;
            });

        // self.wifi.connect — conectar a una red
        McpServer::GetInstance().AddTool("self.wifi.connect",
            "Connect to a WiFi network. Provide SSID and password.",
            PropertyList({
                Property("ssid", kPropertyTypeString),
                Property("password", kPropertyTypeString)
            }),
            [](const PropertyList& props) -> ReturnValue {
                auto ssid = props["ssid"].value<std::string>();
                auto pwd = props["password"].value<std::string>();
                SsidManager::GetInstance().AddSsid(ssid, pwd);
                auto& app = Application::GetInstance();
                if (app.GetDeviceState() == kDeviceStateIdle) {
                    WifiManager::GetInstance().StopStation();
                    WifiManager::GetInstance().StartStation();
                }
                return true;
            });

        // self.wifi.delete — eliminar red guardada por índice
        McpServer::GetInstance().AddTool("self.wifi.delete",
            "Delete a saved WiFi network by index. Use self.wifi.saved_list first to get index.",
            PropertyList({
                Property("index", kPropertyTypeInteger, 0, 20)
            }),
            [](const PropertyList& props) -> ReturnValue {
                int idx = props["index"].value<int>();
                auto& ssid_mgr = SsidManager::GetInstance();
                auto list = ssid_mgr.GetSsidList();
                if (idx >= 0 && idx < (int)list.size()) {
                    ssid_mgr.RemoveSsid(idx);
                    return true;
                }
                return false;
            });
    }

public:
    CompactWifiBoard() :
        boot_button_(BOOT_BUTTON_GPIO),
        touch_button_(TOUCH_BUTTON_GPIO),
        volume_up_button_(VOLUME_UP_BUTTON_GPIO),
        volume_down_button_(VOLUME_DOWN_BUTTON_GPIO) {
        InitializeDisplayI2c();
        InitializeSsd1306Display();
        InitializeButtons();
        InitializeTools();
    }

    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
#ifdef AUDIO_I2S_METHOD_SIMPLEX
        static NoAudioCodecSimplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT, AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
#else
        static NoAudioCodecDuplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS, AUDIO_I2S_GPIO_DOUT, AUDIO_I2S_GPIO_DIN);
#endif
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }
};

DECLARE_BOARD(CompactWifiBoard);
