#ifndef SERIAL_HANDLER_H
#define SERIAL_HANDLER_H

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <driver/uart.h>
#include <string>

#define SERIAL_HANDLER_BUF_SIZE 256

class SerialHandler {
public:
    static SerialHandler& GetInstance() {
        static SerialHandler instance;
        return instance;
    }

    void Start();
    void Stop();

private:
    SerialHandler();
    ~SerialHandler();

    static void SerialTask(void* arg);
    void ProcessCommand(const std::string& cmd);

    TaskHandle_t task_handle_ = nullptr;
    bool running_ = false;
    char line_buffer_[SERIAL_HANDLER_BUF_SIZE];
    int line_pos_ = 0;
};

#endif
