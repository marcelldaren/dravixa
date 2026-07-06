#include <stdio.h>
#include "gpio_bsp.h"
#include "esp_err.h"
#include "i2c_bsp.h"
#include "driver/gpio.h"
#define TCA9554Addr 0x20     // 7-bit address

#define INPUT      0x00      // Read (input only)
#define OUTPUT     0x01      // Output register
#define PolaRever  0x02      // Polarity inversion (input only, 1: invert)
#define Config     0x03      // Configuration register, 1: input, 0: output

#define TAC_bit_mask 0x01


#define bit_mask 0x01


static void EXIO_TCA_Init(void);
static void BAT_GPIO_Init(void);

static uint8_t EXIO_STATE = 0x00;

void esp32_gpio_init(void)
{
  gpio_config_t gpio_conf = {};
  gpio_conf.intr_type = GPIO_INTR_DISABLE;
  gpio_conf.mode = GPIO_MODE_OUTPUT;
  gpio_conf.pin_bit_mask = (bit_mask<<example_out_gpio_1);
  gpio_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
  gpio_conf.pull_up_en = GPIO_PULLUP_ENABLE;

  ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf)); // ESP32 onboard GPIO

  gpio_conf.intr_type = GPIO_INTR_DISABLE;
  gpio_conf.mode = GPIO_MODE_INPUT;
  gpio_conf.pin_bit_mask = (bit_mask<<example_in_gpio_2) | (bit_mask<<example_key);
  gpio_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
  gpio_conf.pull_up_en = GPIO_PULLUP_ENABLE;

  ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf));


  EXIO_TCA_Init();
  BAT_GPIO_Init();
}
void GPIO_SET(uint8_t pin,uint8_t value)
{
  gpio_set_level((gpio_num_t)pin,value);
}
uint8_t GPIO_GET(uint8_t pin)
{
  return gpio_get_level((gpio_num_t)pin);
}
static void BAT_GPIO_Init(void)
{
  gpio_config_t gpio_conf = {};
  gpio_conf.intr_type = GPIO_INTR_DISABLE;
  gpio_conf.mode = GPIO_MODE_OUTPUT;
  gpio_conf.pin_bit_mask = (bit_mask<<BAT_PIN);
  gpio_conf.pull_down_en = GPIO_PULLDOWN_ENABLE; // Pull-down
  gpio_conf.pull_up_en =  GPIO_PULLUP_DISABLE;

  ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf));
  BAT_OFF();
  BAT_ON();
}
void BAT_ON(void)
{
  gpio_set_level(BAT_PIN,1);
}
void BAT_OFF(void)
{
  gpio_set_level(BAT_PIN,0);
}

static void EXIO_TCA_Init(void)
{
  uint8_t reg = ~(TAC_bit_mask<<TCA_GPIO_5);
  ESP_ERROR_CHECK_WITHOUT_ABORT(I2C_writr_buff(TCA9554Addr,Config,&reg,1)); // Set EXIO5 to output mode, other IOs to input mode
}

uint8_t EXIO_Get_State(uint8_t pin) // Note: This function must initialize the corresponding GPIO before use
{
  uint8_t value;
  uint8_t regs[2];
  regs[0] = INPUT;
  ESP_ERROR_CHECK_WITHOUT_ABORT(I2C_master_write_read_device(TCA9554Addr,regs,1,&regs[1],1));
  value = (regs[1] & (0x01<<pin))>>pin;  // Read the level of the pin
  return value;
}
void EXIO_Set_State(uint8_t pin,uint8_t value) // Note: This function must initialize the corresponding GPIO before use
{
  if(value != 0)
    EXIO_STATE |= (0x01<<pin);
  else
    EXIO_STATE &= ~(0x01<<pin);
  ESP_ERROR_CHECK_WITHOUT_ABORT(I2C_writr_buff(TCA9554Addr,OUTPUT,&EXIO_STATE,1));
}
