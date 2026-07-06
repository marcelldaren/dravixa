#include <stdio.h>
#include "user_app.h"
#include "sd_card_bsp.h"
#include "lvgl.h"
#include "gui_guider.h"
#include "events_init.h"
#include "custom.h"
#include "esp_timer.h"
#include "adc_bsp.h"
#include "esp_wifi_bsp.h"
#include "ble_scan_bsp.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "qmi8658c.h"
#include "pcf85063.h"
#include "gpio_bsp.h"
#include "button_bsp.h"

lv_ui guider_ui;
TaskHandle_t pxBleTask;
TaskHandle_t pxWifiTask;
EventGroupHandle_t TaskEven;
void esp_wifi_ble_setscan(uint8_t mode);
void user_app_init(lv_ui *ui);
void SetTheClock_start(lv_ui *ui);
void out_time(ClockModule * clock);
void color_user(void *arg);
void example_app_task(void *pro);
void user_gui_screen(lv_ui *ui);
static void screen_btn_event_handler (lv_event_t *e);
void esp_wifi_scan_w(void *arg);
void esp_ble_scan_w(void *arg);
void example_GPIO_task(void *arg);
void example_button_task(void *arg);
void lv_clear_list(lv_obj_t *obj,uint8_t value);
extern void setBrightnes(uint8_t brig);
ClockModule clock_iniput; 

void user_top_init(void)
{
  TaskEven = xEventGroupCreate();
  xEventGroupSetBits( TaskEven, (0x01<<2) ); // wifi
  xEventGroupSetBits( TaskEven, (0x01<<1) ); // ble
  nvs_flash_Init();
  ble_scan_class_init();
  ble_scan_Init();
  esp32_gpio_init(); // BAT
  button_Init();
  setup_ui(&guider_ui);
  events_init(&guider_ui);
  user_app_init(&guider_ui);
  user_gui_screen(&guider_ui);
  clock_iniput.Hours = 7;
  clock_iniput.minutes = 30;
  clock_iniput.seconds = 30;
  out_time(&clock_iniput);
  SetTheClock_start(&guider_ui);    // Start the clock
  lv_clear_list(guider_ui.screen_list_1, 20); // Clear
  lv_clear_list(guider_ui.screen_list_2, 20); // Clear
}

void user_gui_screen(lv_ui *ui)
{
  lv_obj_add_event_cb(ui->screen_btn_1, screen_btn_event_handler, LV_EVENT_ALL, ui);    // Event
  lv_obj_add_event_cb(ui->screen_btn_2, screen_btn_event_handler, LV_EVENT_ALL, ui);    // Event
  lv_obj_add_event_cb(ui->screen_imgbtn_1, screen_btn_event_handler, LV_EVENT_ALL, ui); // Event
  lv_obj_add_event_cb(ui->screen_imgbtn_2, screen_btn_event_handler, LV_EVENT_ALL, ui); // Event
  lv_obj_add_event_cb(ui->screen_btn_3, screen_btn_event_handler, LV_EVENT_ALL, ui); // Event
  lv_obj_add_event_cb(ui->screen_btn_4, screen_btn_event_handler, LV_EVENT_ALL, ui); // Event
  lv_obj_add_event_cb(ui->screen_slider_1, screen_btn_event_handler, LV_EVENT_ALL, ui); 
}

void user_app_init(lv_ui *ui)
{
  xTaskCreate(example_app_task, "example_app_task", 3000, (void *)ui, 2, NULL);
  xTaskCreate(esp_wifi_scan_w, "esp_wifi_scan_w", 3000, (void *)ui, 2, &pxWifiTask);
  xTaskCreate(esp_ble_scan_w, "esp_ble_scan_w", 3000, (void *)ui, 2, &pxBleTask);
  xTaskCreate(color_user, "color_user", 2000, (void *)ui, 6, NULL); // Switch color
  xTaskCreate(example_GPIO_task, "example_GPIO_task", 3000, (void *)ui, 2, NULL); // GPIO Test
  xTaskCreate(example_button_task, "example_app_task", 3000, (void *)ui, 2, NULL); // Button Test
}

/* Event handler */
static void screen_btn_event_handler (lv_event_t *e)
{
  lv_event_code_t code = lv_event_get_code(e);
  lv_ui *ui = (lv_ui *)e->user_data;
  lv_obj_t * module = e->current_target;
  switch (code)
  {
    case LV_EVENT_CLICKED:
    {
      EventBits_t even = xEventGroupWaitBits(TaskEven, (0x01<<1) | (0x01<<2), pdFALSE, pdFALSE, 10);
      if(module == ui->screen_btn_1) // wifi
      {
        if( even & (0x01<<2) )
        {
          esp_wifi_ble_setscan(1); // wifi

          xEventGroupClearBits( TaskEven, (0x01<<2) );
          xTaskNotifyGive(pxWifiTask);
        }
      }
      else if(module == ui->screen_btn_4) // ble
      {
        if( even & (0x01<<1) )
        {
          esp_wifi_ble_setscan(0); // ble

          xEventGroupClearBits( TaskEven, (0x01<<1) );
          ble_scan_setconf();
          xTaskNotifyGive(pxBleTask);
        }
      }
      else if(module == ui->screen_imgbtn_1)
      {
        lv_obj_clear_flag(ui->screen_cont_6, LV_OBJ_FLAG_HIDDEN); // Show
        lv_obj_add_flag(ui->screen_cont_4, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui->screen_cont_5, LV_OBJ_FLAG_HIDDEN);   
      }
      else if(module == ui->screen_imgbtn_2)
      {
        lv_obj_clear_flag(ui->screen_cont_5, LV_OBJ_FLAG_HIDDEN); // Show
        lv_obj_add_flag(ui->screen_cont_4, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui->screen_cont_6, LV_OBJ_FLAG_HIDDEN);   
      }
      else if( (module == ui->screen_btn_3) || (module == ui->screen_btn_2) )
      {
        lv_obj_clear_flag(ui->screen_cont_4, LV_OBJ_FLAG_HIDDEN); // Show
        lv_obj_add_flag(ui->screen_cont_5, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(ui->screen_cont_6, LV_OBJ_FLAG_HIDDEN);
      }
      else if( module == ui->screen_slider_1 )
      {
        uint8_t value = lv_slider_get_value(module);
        setBrightnes(value);
      }
      break;
    }
    default:
      break;
  }
}

// wifi:1 ble:0
void esp_wifi_ble_setscan(uint8_t mode)
{
  static uint8_t wifi_ble_flag = 0;
  if(mode != wifi_ble_flag)
  {
    if(mode) // wifi needs to release ble
    {
      ble_scan_Deinit();
      espwifi_Init();
    }
    else
    {
      espwifi_Deinit();
      ble_scan_Init();
    }
    wifi_ble_flag = mode;
  }
}

void example_button_task(void *arg)
{
  lv_ui *ui = (lv_ui *)arg;
  for(;;)
  {
    EventBits_t even = xEventGroupWaitBits(key_groups, (0x01<<0) | (0x01<<1), pdTRUE, pdFALSE, 1000);
    if( (even>>0) & 0x01 )
    {
      lv_obj_scroll_by(ui->screen_carousel_1, -600, 0, LV_ANIM_OFF); // Horizontal scroll relative offset (positive right, negative left)
    }
    else if( (even>>1) & 0x01 )
    {
      lv_obj_scroll_by(ui->screen_carousel_1, 600, 0, LV_ANIM_OFF); // Vertical scroll relative offset (positive down, negative up)
    }
  }
}

void out_time(ClockModule * clock)
{
  clock->out_Hours = clock->Hours * 5;
  clock->out_minutes = clock->minutes;
  clock->out_seconds = clock->seconds;
  uint8_t bat = clock->out_minutes / 12;
  clock->out_Hours += bat;

  int16_t Hours_ars = clock_iniput.out_Hours * 6 - 90;
  int16_t Minutes_ars = clock_iniput.out_minutes * 6 - 90;
  int16_t Seconds_ars = clock_iniput.out_seconds * 6 - 90;
  lv_img_set_angle(guider_ui.screen_img_1, Hours_ars * 10);
  lv_img_set_angle(guider_ui.screen_img_2, Minutes_ars * 10);
  lv_img_set_angle(guider_ui.screen_img_3, Seconds_ars * 10);
}

void example_GPIO_task(void *arg)
{
  lv_ui *obj = (lv_ui *)arg;
  for(;;)
  {
    uint8_t _add = 0;
    GPIO_SET(example_out_gpio_1, 0);
    EXIO_Set_State(TCA_GPIO_5, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    if(GPIO_GET(example_in_gpio_2) == 0 && EXIO_Get_State(TCA_GPIO_6) == 0)
    {
      _add++;
    }
    GPIO_SET(example_out_gpio_1, 1);
    EXIO_Set_State(TCA_GPIO_5, 1);
    vTaskDelay(pdMS_TO_TICKS(200));
    if(GPIO_GET(example_in_gpio_2) == 1 && EXIO_Get_State(TCA_GPIO_6) == 1)
    {
      _add++;
    }
    GPIO_SET(example_out_gpio_1, 1);
    EXIO_Set_State(TCA_GPIO_5, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    if(GPIO_GET(example_in_gpio_2) == 1 && EXIO_Get_State(TCA_GPIO_6) == 0)
    {
      _add++;
    }
    if(_add == 3)
    {
      printf("GPIO test passed.\n");
      lv_label_set_text(obj->screen_label_19, "GPIO test passed.");
    }
    else
    {
      printf("GPIO test failed.\n");
      lv_label_set_text(obj->screen_label_19, "GPIO test failed.");
    }
  }
}

void clock_task_callback(void *arg)
{
  static uint8_t bat = 0;
  lv_ui *ui = (lv_ui *)arg;
  clock_iniput.out_seconds++;
  int16_t Seconds_ars = clock_iniput.out_seconds * 6 - 90;
  lv_img_set_angle(ui->screen_img_3, Seconds_ars * 10);
  if(clock_iniput.out_seconds == 60)
  {
    clock_iniput.out_seconds = 0;
    clock_iniput.out_minutes++;
    int16_t Minutes_ars = clock_iniput.out_minutes * 6 - 90;
    lv_img_set_angle(ui->screen_img_2, Minutes_ars * 10);
    if( (clock_iniput.out_minutes == 12) || (clock_iniput.out_minutes == 24) || (clock_iniput.out_minutes == 36) || (clock_iniput.out_minutes == 48) || (clock_iniput.out_minutes == 60))
      bat = 1;
    else
      bat = 0;
  }
  if(clock_iniput.out_minutes == 60)
  {
    clock_iniput.minutes = 0;
  }
  if( bat == 1 )
  {
    bat = 0;
    clock_iniput.out_Hours++;
    int16_t Hours_ars = clock_iniput.out_Hours * 6 - 90;
    lv_img_set_angle(ui->screen_img_1, Hours_ars * 10);
  }
  if(clock_iniput.out_Hours == 60)
  {
    clock_iniput.out_Hours = 0;
  }
}

void lv_clear_list(lv_obj_t *obj, uint8_t value) 
{
  for(signed char i = value-1; i>=0; i--)
  {
    lv_obj_t *imte = lv_obj_get_child(obj, i);
    lv_obj_add_flag(imte, LV_OBJ_FLAG_HIDDEN);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  vTaskDelay(pdMS_TO_TICKS(20));
  lv_obj_invalidate(obj); // Redraw in the next cycle
}

void SetTheClock_start(lv_ui *ui)
{
  const esp_timer_create_args_t clock_tick_timer_args = 
  {
    .callback = &clock_task_callback,
    .name = "clock_task",
    .arg = ui,
  };
  esp_timer_handle_t clock_tick_timer = NULL;
  ESP_ERROR_CHECK(esp_timer_create(&clock_tick_timer_args, &clock_tick_timer));
  ESP_ERROR_CHECK(esp_timer_start_periodic(clock_tick_timer, 1000 * 1000)); // 1 second
}

void example_app_task(void *pro)
{
  lv_ui *obj = (lv_ui *)pro;
  char adc_values[15] = {0};
  char sd_buff[8] = {0};
  char qmi_values[20] = {0};
  char rtc_values[20] = {0};
  float acc[3] = {0};
  float gyro[3] = {0};
  float adc_value;
  float sd_value = 0;
  uint32_t stimes = 0;
  uint32_t adc_test = 0;
  uint32_t qmi_test = 0;
  uint32_t rtc_test = 0;
  adc_bsp_init();
  sd_card_Init();
  qmi8658_init();
  PCF85063_set_tim_init();
  sd_value = sd_card_get_value();
  if(sd_value)
  {
    sprintf(sd_buff, "%.2fG", sd_value);
    lv_label_set_text(obj->screen_label_6, sd_buff);
  }
  for(;;)
  {
    if( (stimes - qmi_test) > 2 )
    {
      qmi_test = stimes;
      qmi8658_read_xyz(acc, gyro);
      //temp = qmi8658_readTemp();
      sprintf(qmi_values, "ax:%.2f ay:%.2f az:%.2f", acc[0], acc[1], acc[2]);
      lv_label_set_text(obj->screen_label_10, qmi_values);
      sprintf(qmi_values, "gx:%.2f gy:%.2f gz:%.2f", gyro[0], gyro[1], gyro[2]);
      lv_label_set_text(obj->screen_label_16, qmi_values);
    }
    if( (stimes - rtc_test) > 4 )
    {
      rtc_test = stimes;
      PCF85063_get_tim((uint8_t *)rtc_values);
      lv_label_set_text(obj->screen_label_8, rtc_values);
    }
    if(stimes - adc_test > 1) //2s
    {
      adc_test = stimes;
      adc_get_value(&adc_value, NULL);
      if(adc_value)
      {
        sprintf(adc_values, "%.2fV", adc_value);
        lv_label_set_text(obj->screen_label_11, adc_values);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
    stimes++;
  }
}

void color_user(void *arg)
{
  lv_ui *ui = (lv_ui *)arg;
  lv_obj_clear_flag(ui->screen_carousel_1, LV_OBJ_FLAG_SCROLLABLE); // Not scrollable
  lv_obj_clear_flag(ui->screen_cont_2, LV_OBJ_FLAG_HIDDEN); // Show
  lv_obj_add_flag(ui->screen_cont_1, LV_OBJ_FLAG_HIDDEN);   
  lv_obj_clear_flag(ui->screen_img_4, LV_OBJ_FLAG_HIDDEN); // Show
  lv_obj_add_flag(ui->screen_img_5, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(ui->screen_img_6, LV_OBJ_FLAG_HIDDEN);
  vTaskDelay(pdMS_TO_TICKS(1500));
  lv_obj_clear_flag(ui->screen_img_5, LV_OBJ_FLAG_HIDDEN); // Show
  lv_obj_add_flag(ui->screen_img_4, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(ui->screen_img_6, LV_OBJ_FLAG_HIDDEN);
  vTaskDelay(pdMS_TO_TICKS(1500));
  lv_obj_clear_flag(ui->screen_img_6, LV_OBJ_FLAG_HIDDEN); // Show
  lv_obj_add_flag(ui->screen_img_4, LV_OBJ_FLAG_HIDDEN);
  lv_obj_add_flag(ui->screen_img_5, LV_OBJ_FLAG_HIDDEN);
  vTaskDelay(pdMS_TO_TICKS(1500));
  lv_obj_clear_flag(ui->screen_cont_1, LV_OBJ_FLAG_HIDDEN); // Show
  lv_obj_add_flag(ui->screen_cont_2, LV_OBJ_FLAG_HIDDEN);  
  lv_obj_add_flag(ui->screen_carousel_1, LV_OBJ_FLAG_SCROLLABLE); // Scrollable
  vTaskDelete(NULL); // Delete task
}

void lv_stop_roll(lv_obj_t *obj, uint8_t value, uint8_t mode)
{
  if(mode == 1)
  {
    //lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    for(signed char i = value-1; i>=0; i--)
    {
      lv_obj_t *imte = lv_obj_get_child(obj, i);
      lv_obj_t *txt = lv_obj_get_child(imte, 1);
      lv_label_set_long_mode(txt, LV_LABEL_LONG_DOT); // Overflow font does not use scrolling
    }
  }
  else
  {
    //lv_obj_add_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    for(signed char i = value-1; i>=0; i--)
    {
      lv_obj_t *imte = lv_obj_get_child(obj, i);
      lv_obj_t *txt = lv_obj_get_child(imte, 1);
      lv_label_set_long_mode(txt, LV_LABEL_LONG_SCROLL_CIRCULAR); // Overflow font uses scrolling
    }
  }
}

void esp_wifi_scan_w(void *arg)
{
  lv_ui *wifi_obj = (lv_ui *)arg;
  static wifi_ap_record_t recdata;
  static uint16_t rec = 0;
  static const char *imgbox = NULL;
  static lv_obj_t *imte;
  static lv_obj_t *label;
  for(;;)
  {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY); // Wait for task notification
    lv_clear_list(wifi_obj->screen_list_1, rec);
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_scan_start(NULL, true)); // Scan for available APs
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_scan_get_ap_num(&rec));
    if(rec != 0)
    {
      if(rec > 19)
      {
        rec = 20;
        for(uint8_t i = 0; i<rec; i++)
        {
          ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_scan_get_ap_record(&recdata));
          imgbox = (const char*)(&(recdata.ssid[0]));
          if (imgbox == NULL)
            break;
          imte = lv_obj_get_child(wifi_obj->screen_list_1, i);
          if (imte != NULL)
          {
            label = lv_obj_get_child(imte, 1);
            if(label != NULL)
            {
              lv_label_set_text(label, imgbox);
              lv_obj_clear_flag(imte, LV_OBJ_FLAG_HIDDEN); // Show
            }
          }
          imgbox = NULL;
          imte = NULL;
          label = NULL;
          vTaskDelay(pdMS_TO_TICKS(300));
        }
        esp_wifi_clear_ap_list();
      }
      else
      {
        for(uint8_t i = 0; i<rec; i++)
        {
          ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_scan_get_ap_record(&recdata));
          imgbox = (const char*)(&(recdata.ssid[0]));
          if (imgbox == NULL)
            break;
          imte = lv_obj_get_child(wifi_obj->screen_list_1, i); // Get child object
          if (imte != NULL)
          {
            label = lv_obj_get_child(imte, 1); // Get child child object
            if(label != NULL)
            {
              lv_label_set_text(label, imgbox);
              lv_obj_clear_flag(imte, LV_OBJ_FLAG_HIDDEN); // Show
            }
          }
          imgbox = NULL;
          imte = NULL;
          label = NULL;
          vTaskDelay(pdMS_TO_TICKS(100));
        }
      }
    }
    xEventGroupSetBits( TaskEven, (0x01<<2) );
  }
}

void esp_ble_scan_w(void *arg)
{
  lv_ui *ble_obj = (lv_ui *)arg;
  static uint16_t rec = 0;
  uint8_t mac[6];
  static lv_obj_t *imte;
  static lv_obj_t *label;
  char imgbox[24] = {0};
  for(;;)
  {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);  
    lv_clear_list(ble_obj->screen_list_2, rec);
    rec = 0;
    for(;xQueueReceive(ble_Queue, mac, 3500) == pdTRUE;)
    {
      imte = lv_obj_get_child(ble_obj->screen_list_2, rec);
      if (imte != NULL)
      {
        label = lv_obj_get_child(imte, 1);
        if(label != NULL)
        {
          sprintf(imgbox, "%d:%d:%d:%d:%d:%d", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
          lv_label_set_text(label, imgbox);
          lv_obj_clear_flag(imte, LV_OBJ_FLAG_HIDDEN); // Show
        }
      }
      imte = NULL;
      label = NULL;
      rec++;
      vTaskDelay(pdMS_TO_TICKS(100));
      if(rec == 20)
        break;
    }
    xEventGroupSetBits( TaskEven, (0x01<<1) );
  }
}