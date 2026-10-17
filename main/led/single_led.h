#ifndef _SINGLE_LED_H_
#define _SINGLE_LED_H_

#include "led.h"
#include <driver/gpio.h>
#include <led_strip.h>
#include <esp_timer.h>
#include <atomic>
#include <mutex>

// MeBot brand colors
#define MEBOT_BLUE_R 59
#define MEBOT_BLUE_G 130
#define MEBOT_BLUE_B 246

class SingleLed : public Led {
public:
    SingleLed(gpio_num_t gpio);
    virtual ~SingleLed();

    void OnStateChanged() override;
    void OnThinkingChanged(bool thinking);
    void OnSpeakingChanged(bool speaking);

private:
    std::mutex mutex_;
    TaskHandle_t blink_task_ = nullptr;
    led_strip_handle_t led_strip_ = nullptr;
    uint8_t r_ = 0, g_ = 0, b_ = 0;
    int blink_counter_ = 0;
    int blink_interval_ms_ = 0;
    esp_timer_handle_t blink_timer_ = nullptr;
    esp_timer_handle_t breathing_timer_ = nullptr;
    esp_timer_handle_t thinking_timer_ = nullptr;
    int breathing_step_ = 0;
    int breathing_direction_ = 1;
    bool thinking_active_ = false;
    bool speaking_active_ = false;

    void StartBlinkTask(int times, int interval_ms);
    void OnBlinkTimer();
    void OnBreathingTimer();
    void OnThinkingTimer();
    void StartBreathing();
    void StopBreathing();
    void StartThinkingAnimation();
    void StopThinkingAnimation();

    void BlinkOnce();
    void Blink(int times, int interval_ms);
    void StartContinuousBlink(int interval_ms);
    void TurnOn();
    void TurnOff();
    void SetColor(uint8_t r, uint8_t g, uint8_t b);
    void SetBrightness(uint8_t brightness);
};

#endif // _SINGLE_LED_H_
