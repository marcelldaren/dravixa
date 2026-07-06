#ifndef GPIO_BSP_H
#define GPIO_BSP_H

#define TCA_GPIO_0 0
#define TCA_GPIO_1 1
#define TCA_GPIO_2 2
#define TCA_GPIO_3 3
#define TCA_GPIO_4 4
#define TCA_GPIO_5 5
#define TCA_GPIO_6 6
#define TCA_GPIO_7 7

#define example_out_gpio_1 7
#define example_in_gpio_2  8

#define BAT_PIN 16    //Controls BAT
#define example_key 0

void esp32_gpio_init(void);
void BAT_ON(void);
void BAT_OFF(void);
uint8_t EXIO_Get_State(uint8_t pin);
void EXIO_Set_State(uint8_t pin,uint8_t value);
void GPIO_SET(uint8_t pin,uint8_t value);
uint8_t GPIO_GET(uint8_t pin);
#endif