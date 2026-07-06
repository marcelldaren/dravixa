#include <stdio.h>
#include "pcf85063.h"
#include "esp_log.h"
#include "i2c_bsp.h"
#include "freertos/FreeRTOS.h"

static char *tag = "pcf85063";

/* Initialize the PCF85063 RTC (Real-Time Clock) */
void pcf85063_init(void)
{
  uint8_t addr = 0x04; // PCF85063 fixed address
  uint8_t buf[20];
  if(PCF85063_register_read(addr, buf, 7) != ESP_OK)
  {
    ESP_LOGW(tag, "PCF85063_read failed\n");
  }
  ESP_LOGI("time", "20%02X/%02X/%02X %02X:%02X:%02X_%X.", buf[6], buf[5], buf[3], buf[2], buf[1]&0x7F, buf[0]&0x7F, buf[4]);
}

/* Convert decimal to BCD (Binary-Coded Decimal) */
uint8_t PCD85063TP_decToBcd(uint8_t val)
{
  return ( (val / 10 * 16) + (val % 10) );
}

/* Convert BCD to decimal */
uint8_t PCD85063TP_bcdToDec(uint8_t val)
{
  return ( (val / 16 * 10) + (val % 16) );
}

/* Write data to the RTC registers */
esp_err_t PCF85063_register_write(uint8_t reg_addr, uint8_t *data, int len)
{
  uint8_t ret;
  ret = I2C_writr_buff(0x51, reg_addr, data, len);
  return ret;
}

/* Read data from the RTC registers */
esp_err_t PCF85063_register_read(uint8_t reg, uint8_t *buf, uint8_t len)
{
  return I2C_read_buff(0x51, reg, buf, len); // Read information
}

/* Calculate the day of the week */
int PCF85063_get_Week(int y, int m, int d)
{
  int week = 0;
  if(m == 1 || m == 2)
  {
    m += 12;
    y--;
  }
  week = (d + 2 * m + 3 * (m + 1) / 5 + y + y / 4 - y / 100 + y / 400) % 7;
  // ret = 0   Sunday
  // ret = N   Nth day of the week
  return (week + 1) % 7;
}

/* Set the time on the RTC */
void PCF85063_setTime(CTime tm, int isAutoCalcWeek)
{
  uint8_t data[7] = {0};
  if (isAutoCalcWeek)
  {
    tm.nWeek = PCF85063_get_Week(tm.nYear, tm.nMonth, tm.nDay);
  }
  bool flag_19xx = true;
  uint16_t yr = tm.nYear;
  if (tm.nYear >= 2000)
  {
    flag_19xx = false;
    yr -= 2000;
  }
  else
  {
    yr -= 1900;
  }
  data[0] = PCD85063TP_decToBcd(tm.nSec);
  data[1] = PCD85063TP_decToBcd(tm.nMin);
  data[2] = PCD85063TP_decToBcd(tm.nHour);
  data[3] = PCD85063TP_decToBcd(tm.nDay);
  data[4] = PCD85063TP_decToBcd(tm.nWeek);
  data[5] = PCD85063TP_decToBcd(tm.nMonth);
  data[6] = PCD85063TP_decToBcd(yr);
  if (flag_19xx)
  {
    data[5] |= 0x80;
  }
  if((PCF85063_register_write(0x04, data, 7) != ESP_OK))
  {
    ESP_LOGW(tag, "PCF85063_Sending failed\n");
  }
}

/* Get the current time from the RTC */
bool PCF85063_getTime(CTime *tm)
{
  uint8_t data[7] = {0};
  uint8_t inbuf = 0x04;
  if(PCF85063_register_read(inbuf, data, 7) != ESP_OK)
  {
    ESP_LOGW(tag, "PCF85063_read failed\n");
  }
  bool flag_19xx = (data[5] >> 7) & 0x01; // Year:19XX_Flag
  bool flag_vl = (data[0] >> 7) & 0x01;   //(Voltage Low)VL=1: Initial data unreliable
  
  tm->nSec   = PCD85063TP_bcdToDec( data[0]&0x7f );
  tm->nMin   = PCD85063TP_bcdToDec( data[1]&0x7f );
  tm->nHour  = PCD85063TP_bcdToDec( data[2]&0x3f );
  tm->nDay   = PCD85063TP_bcdToDec( data[3]&0x3f );
  tm->nWeek  = PCD85063TP_bcdToDec( data[4] );
  tm->nMonth = PCD85063TP_bcdToDec( data[5]&0x1f );
  tm->nYear  = PCD85063TP_bcdToDec( data[6] ); // 0~99
  if (flag_19xx)
  {
   tm->nYear += 1900;
  }
  else
  {
   tm->nYear += 2000;
  }
  return flag_vl;
}

/* Get the time string */
// Format1: 20xx/xx/xx xx:xx:xx
// Format0: xx:xx:xx
void PCF85063_getTmString(int isYMD, uint8_t *outbuf)
{
  CTime tm;
  PCF85063_getTime(&tm);
  if(isYMD)
    sprintf((char*)outbuf, "%02d/%02d/%02d %02d:%02d:%02d", tm.nYear, tm.nMonth, tm.nDay, tm.nHour, tm.nMin, tm.nSec);    
  else
    sprintf((char*)outbuf, "%02d:%02d:%02d", tm.nHour, tm.nMin, tm.nSec );
}

/* Test example */
void PCF85063_example(void* parameter)
{
  // Initialize date: October 24, 2024 (Monday), 09:58:50
  CTime tm = {2024, 10, 1, 24, 9, 58, 50};
  PCF85063_setTime(tm, 1);
  uint8_t data[10] = {0};
  uint8_t inbuf = 0x04;
  static uint8_t buf[50];
  vTaskDelay(1000);
  for(;;)
  {
    if(PCF85063_register_read(inbuf, data, 7) != ESP_OK)
    {
      ESP_LOGW(tag, "PCF85063_read failed\n");
    }
    //ESP_LOGI("tim", "20%02X/%02X/%02X %02X:%02X:%02X.", data[6], data[5], data[3], data[2], data[1]&0x7F, data[0]&0x7F);
    PCF85063_getTmString(1, buf);
    printf("timer:%s\n", (char*)buf);
    vTaskDelay(1000);
  }
}

/* Initialize time setting */
void PCF85063_set_tim_init(void)
{
  // Initialize date: September 1, 2024 (Sunday), 09:58:50
  CTime tm = {2024, 9, 7, 1, 9, 58, 50};
  PCF85063_setTime(tm, 1);
}

/* Get current time and store in buffer */
void PCF85063_get_tim(uint8_t *buf)
{
  uint8_t data[10] = {0};
  if(PCF85063_register_read(0x04, data, 7) != ESP_OK)
  {
    ESP_LOGW(tag, "PCF85063_read failed\n");
  }
  PCF85063_getTmString(1, buf);
}