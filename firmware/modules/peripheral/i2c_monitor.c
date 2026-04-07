/* i2c_monitor.c — I2C 트랜잭션 통계 모니터 구현 */

#include "i2c_monitor.h"
#include "trace_events.h"
#include "FreeRTOS.h"
#include "task.h"

#if defined(STM32F4xx) || defined(STM32F4)
#  include "stm32f4xx_hal.h"
#  define I2C_SR1(h)   (((I2C_HandleTypeDef*)(h))->Instance->SR1)
#  define I2C_TIMEOUT_FLAG  I2C_SR1_TIMEOUT
#  define I2C_AF_FLAG       I2C_SR1_AF
#  define I2C_ARLO_FLAG     I2C_SR1_ARLO
#  define I2C_BERR_FLAG     I2C_SR1_BERR
#  define I2C_CLEAR_FLAG(h, f) \
       (((I2C_HandleTypeDef*)(h))->Instance->SR1 &= ~(uint32_t)(f))
#else
#  define I2C_SR1(h)          0U
#  define I2C_TIMEOUT_FLAG    (1U<<14)
#  define I2C_AF_FLAG         (1U<<10)
#  define I2C_ARLO_FLAG       (1U<<9)
#  define I2C_BERR_FLAG       (1U<<8)
#  define I2C_CLEAR_FLAG(h,f) ((void)0)
#endif

static I2CMonitorData_t s_data = {0};
static void             *s_hi2c = NULL;

static void _i2c_init(void) {
    /* I2C_Monitor_Init()에서 이미 핸들 설정됨 */
}

static void _i2c_sample(void) {
    if (s_hi2c == NULL) { return; }
    uint32_t sr1 = I2C_SR1(s_hi2c);
    uint32_t now = xTaskGetTickCount();
    bool error_detected = false;

    if ((sr1 & I2C_TIMEOUT_FLAG) != 0U) {
        s_data.stats.timeout_count++;
        s_data.last_error_code = 0x01U;
        I2C_CLEAR_FLAG(s_hi2c, I2C_TIMEOUT_FLAG);
        TraceEvent_Peripheral(TRACE_I2C_TIMEOUT, 0x01U);
        error_detected = true;
    }
    if ((sr1 & I2C_AF_FLAG) != 0U) {
        s_data.nack_count++;
        s_data.last_error_code = 0x02U;
        I2C_CLEAR_FLAG(s_hi2c, I2C_AF_FLAG);
        TraceEvent_Peripheral(TRACE_I2C_NACK, 0x02U);
        error_detected = true;
    }
    if ((sr1 & I2C_ARLO_FLAG) != 0U) {
        s_data.arb_lost_count++;
        s_data.stats.error_count++;
        I2C_CLEAR_FLAG(s_hi2c, I2C_ARLO_FLAG);
        error_detected = true;
    }
    if (error_detected) {
        s_data.stats.last_error_tick = now;
    }
}

static void _i2c_get_stats(PeripheralErrorStats_t *out) {
    if (out != NULL) { *out = s_data.stats; }
}

static void _i2c_reset(void) {
    s_data.stats.error_count   = 0U;
    s_data.stats.timeout_count = 0U;
    s_data.nack_count          = 0U;
    s_data.arb_lost_count      = 0U;
}

PeripheralMonitor_t g_i2c_monitor = {
    .name       = "I2C",
    .event_base = 0x80U,
    .enabled    = true,
    .init       = _i2c_init,
    .sample     = _i2c_sample,
    .get_stats  = _i2c_get_stats,
    .reset      = _i2c_reset,
};

bool I2C_Monitor_Init(void *hi2c) {
    if (hi2c == NULL) { return false; }
    s_hi2c = hi2c;
    s_data = (I2CMonitorData_t){0};
    return true;
}

const I2CMonitorData_t* I2C_Monitor_GetData(void) { return &s_data; }
