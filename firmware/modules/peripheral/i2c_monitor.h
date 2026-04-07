/* i2c_monitor.h — I2C 트랜잭션 통계 모니터 (2순위)
 *
 * 기능:
 *   - 타임아웃, NACK, 오버런 횟수 누산
 *   - 1Hz 샘플링으로 I2C 레지스터 오류 플래그 폴링
 *   - 오류 발생 시 TraceEvent로 호스트 전달
 *
 * 사용:
 *   I2C_Monitor_Init(&hi2c1);
 *   PeripheralMonitor_Register(&g_i2c1_monitor);
 */
#ifndef I2C_MONITOR_H
#define I2C_MONITOR_H
#include "peripheral_monitor.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    PeripheralErrorStats_t stats;
    uint32_t nack_count;      /* NACK 수신 횟수 */
    uint32_t bus_busy_count;  /* 버스 Busy 감지 횟수 */
    uint32_t arb_lost_count;  /* Arbitration Lost 횟수 */
    uint8_t  last_error_code; /* 마지막 오류 코드 */
} I2CMonitorData_t;

/**
 * @brief I2C 모니터 초기화
 * @param hi2c HAL I2C 핸들 (I2C_HandleTypeDef*)
 */
bool I2C_Monitor_Init(void *hi2c);
const I2CMonitorData_t* I2C_Monitor_GetData(void);

extern PeripheralMonitor_t g_i2c_monitor;

#ifdef __cplusplus
}
#endif
#endif /* I2C_MONITOR_H */
