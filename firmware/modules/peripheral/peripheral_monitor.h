/* peripheral_monitor.h — Peripheral 모니터링 공통 인터페이스
 *
 * 페리페럴 디버깅 확장 프레임워크.
 * 각 모듈을 독립적으로 추가하고 레지스트리에 등록.
 *
 * 확장 우선순위:
 *   [1] gpio_monitor.h/c  — GPIO 상태 변화 / 글리치
 *   [2] i2c_monitor.h/c   — I2C 트랜잭션 통계
 *   [3] spi_monitor.h/c   — SPI 오버런
 *   [4] uart_monitor.h/c  — UART 오류 추적
 *   [5] adc_monitor.h/c   — ADC 이상 감지
 *   [후] emif_monitor.h/c — EMIF 접근 (MPU 연계)
 *
 * Binary Protocol 이벤트 타입 예약:
 *   0x70-0x7F: GPIO
 *   0x80-0x8F: I2C
 *   0x90-0x9F: SPI
 *   0xA0-0xAF: UART
 *   0xB0-0xBF: ADC
 *   0xC0-0xCF: DMA/EMIF
 *   0xD0-0xFF: 사용자 정의
 */
#ifndef PERIPHERAL_MONITOR_H
#define PERIPHERAL_MONITOR_H
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t error_count;
    uint32_t timeout_count;
    uint32_t overflow_count;
    uint32_t success_count;
    uint32_t last_error_tick;
} PeripheralErrorStats_t;

typedef struct {
    const char *name;
    uint8_t     event_base;   /* 이벤트 타입 기준값 */
    bool        enabled;
    void (*init)(void);
    void (*sample)(void);
    void (*get_stats)(PeripheralErrorStats_t *out);
    void (*reset)(void);
} PeripheralMonitor_t;

#define PERIPHERAL_MONITOR_MAX  8U

bool     PeripheralMonitor_Register(PeripheralMonitor_t *monitor);
void     PeripheralMonitor_InitAll(void);
void     PeripheralMonitor_SampleAll(void);
uint32_t PeripheralMonitor_GetCount(void);

#ifdef __cplusplus
}
#endif
#endif /* PERIPHERAL_MONITOR_H */
