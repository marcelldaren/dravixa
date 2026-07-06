#include "button_bsp.h"
#include "multi_button.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gpio_bsp.h"
EventGroupHandle_t key_groups;
struct Button button1; // Allocate button
#define button1_id 0   // Button ID
#define button1_active 0 // Active level
static void clock_task_callback(void *arg)
{
  button_ticks();              // State callback
}
uint8_t read_button_GPIO(uint8_t button_id)   // Return GPIO level
{
	switch(button_id)
	{
		case button1_id:
      return GPIO_GET(example_key);
			break;
		default:
			break;
	}
  return 0;
}
void Button_SINGLE_CLICK_Callback(void* btn) // Single-click event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    xEventGroupSetBits( key_groups,(0x01<<0) ); 
  }
}

void Button_DOUBLE_CLICK_Callback(void* btn) // Double-click event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    xEventGroupSetBits( key_groups,(0x01<<1) );
  }
}
void Button_PRESS_DOWN_Callback(void* btn) // Press-down event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    printf("DOWN\n");
  }
}
void Button_PRESS_UP_Callback(void* btn) // Release event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    printf("UP\n");
  }
}
void Button_PRESS_REPEAT_Callback(void* btn) // Repeated press event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    printf("PRESS_REPEAT : %d\n",user_button->repeat);
  }
}
void Button_LONG_PRESS_START_Callback(void* btn) // Long-press triggered once event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    printf("LONG_PRESS_START\n");
  }
}
void Button_LONG_PRESS_HOLD_Callback(void* btn) // Long-press continuously triggered event
{
  struct Button *user_button = (struct Button *)btn;
	if(user_button == &button1)
  {
    printf("LONG_PRESS_HOLD\n");
  }
}
void button_Init(void)
{
  key_groups = xEventGroupCreate();
  //xEventGroupSetBits( TaskEven,(0x01<<2) ); 
  button_init(&button1, read_button_GPIO, button1_active , button1_id);      // Initialize button object, callback function, trigger level, button ID
  button_attach(&button1, SINGLE_CLICK, Button_SINGLE_CLICK_Callback);       // Register callback function for single-click
  //button_attach(&button1, LONG_PRESS_START, Button_LONG_PRESS_START_Callback);  // Register callback function for long-press start
  button_attach(&button1, DOUBLE_CLICK, Button_DOUBLE_CLICK_Callback);       // Register callback function for double-click
  //button_attach(&button1, PRESS_REPEAT, Button_PRESS_REPEAT_Callback);      // Register callback function for repeated press
  const esp_timer_create_args_t clock_tick_timer_args = 
  {
    .callback = &clock_task_callback,
    .name = "clock_task",
    .arg = NULL,
  };
  esp_timer_handle_t clock_tick_timer = NULL;
  ESP_ERROR_CHECK(esp_timer_create(&clock_tick_timer_args, &clock_tick_timer));
  ESP_ERROR_CHECK(esp_timer_start_periodic(clock_tick_timer, 1000 * 5));  // 5ms
  button_start(&button1); // Start button
}
