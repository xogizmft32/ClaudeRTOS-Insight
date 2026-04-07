/* gpio_monitor.h — GPIO 상태 변화 감지 (Peripheral Monitor 1순위) */
#ifndef GPIO_MONITOR_H
#define GPIO_MONITOR_H
#include "peripheral_monitor.h"
#ifdef __cplusplus
extern "C" {
#endif

#define GPIO_MONITOR_MAX_PINS  8U

typedef struct {
    void       *port;
    uint16_t    pin;
    char        name[12];
    uint8_t     state;
    uint16_t    history;      /* 최근 16샘플 bitmask */
    uint32_t    change_count;
    uint32_t    glitch_count; /* 1샘플 내 반전 횟수 */
} GPIOPinInfo_t;

typedef struct {
    PeripheralErrorStats_t stats;
    GPIOPinInfo_t pins[GPIO_MONITOR_MAX_PINS];
    uint32_t      pin_count;
} GPIOMonitorData_t;

bool GPIO_Monitor_AddPin(void *port, uint16_t pin, const char *name);
const GPIOMonitorData_t* GPIO_Monitor_GetData(void);

extern PeripheralMonitor_t g_gpio_monitor;

#ifdef __cplusplus
}
#endif
#endif /* GPIO_MONITOR_H */
