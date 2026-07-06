#ifndef PCF85063_H
#define PCF85063_H

#include "esp_err.h"
/* PCF85063 RTC Clock Chip */
typedef struct 
{
  int nYear;
  int nMonth;
  int nWeek;
  int nDay;
  int nHour;
  int nMin;
  int nSec;
}CTime;
esp_err_t PCF85063_register_write(uint8_t reg_addr, uint8_t *data,int len);
esp_err_t PCF85063_register_read(uint8_t reg,uint8_t *buf,uint8_t len);
void PCF85063_getTmString(int isYMD,uint8_t *outbuf);
void PCF85063_setTime(CTime tm,int isAutoCalcWeek);


void PCF85063_example(void* parmeter);
void PCF85063_set_tim_init(void);
void PCF85063_get_tim(uint8_t *buf);
#endif