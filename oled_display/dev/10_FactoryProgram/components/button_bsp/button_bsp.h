#ifndef BUTTON_BSP_H
#define BUTTON_BSP_H
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
extern EventGroupHandle_t key_groups;

void button_Init(void);

#endif