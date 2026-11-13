#ifndef UUT_CAPTURE_H
#define UUT_CAPTURE_H
#include "stm32f4xx_hal.h"
#include <stddef.h>
/* ADC: ch1 ; GPIO: pin ; SYNC: ch1, ch2 et pin. Temps relatifs en us. */
HAL_StatusTypeDef Capture_Start(const char *mode, uint8_t ch1, uint8_t ch2,
                                uint8_t pin, uint32_t duration_ms);
void Capture_Task(void);
HAL_StatusTypeDef Capture_Abort(void);
uint8_t Capture_IsBusy(void);
void Capture_Status(char *out, size_t size);
void Capture_Block(uint32_t index, char *out, size_t size);
#endif
