/* gpio_monitor.c — GPIO 상태 변화 감지 구현
 *
 * 동작:
 *   MonitorTask 1Hz 주기에서 PeripheralMonitor_SampleAll() 호출 시
 *   등록된 GPIO 핀을 폴링하여 상태 변화/글리치 감지.
 *
 * 글리치 정의:
 *   이전 샘플과 다른 상태 → 다시 이전 상태로 복귀 (1샘플 내 반전)
 *
 * 오버헤드:
 *   핀 1개당 GPIO_ReadPin() 1회 = ~3 cycles
 *   8핀 최대 = ~24 cycles / 1Hz 주기 → 사실상 무시 가능
 *
 * MISRA C:2012 준수 노트:
 *   - 모든 포인터 비교: != NULL
 *   - 고정폭 타입: uint8_t, uint16_t, uint32_t
 */

#include "gpio_monitor.h"
#include "trace_events.h"
#include "FreeRTOS.h"
#include "task.h"
#include <string.h>

/* ── STM32 HAL 의존성 (없으면 시뮬레이션 모드) ────────────── */
#if defined(STM32F4xx) || defined(STM32F446xx) || defined(STM32F4)
#  include "stm32f4xx_hal.h"
#  define GPIO_READ(port, pin) \
       (HAL_GPIO_ReadPin((GPIO_TypeDef*)(port), (pin)) == GPIO_PIN_SET ? 1U : 0U)
#else
/* 시뮬레이션 모드 — 빌드 오류 없음 */
#  define GPIO_READ(port, pin)  0U
#endif

/* ── 내부 상태 ────────────────────────────────────────────── */
static GPIOMonitorData_t s_data = {0};

/* ── PeripheralMonitor 인터페이스 구현 ───────────────────── */

static void _gpio_init(void) {
    memset(&s_data, 0, sizeof(s_data));
}

static void _gpio_sample(void) {
    uint32_t now = xTaskGetTickCount();

    for (uint32_t i = 0U; i < s_data.pin_count; i++) {
        GPIOPinInfo_t *pin = &s_data.pins[i];
        if (pin->port == NULL) { continue; }

        uint8_t new_state = (uint8_t)GPIO_READ(pin->port, pin->pin);

        /* history bitmask 갱신 (최근 16샘플) */
        pin->history = (uint16_t)((pin->history << 1U) | new_state);

        if (new_state != pin->state) {
            /* 글리치 감지: 이전 상태 변화 후 다시 복귀 */
            uint8_t prev_prev = (uint8_t)((pin->history >> 2U) & 0x01U);
            if (new_state == prev_prev) {
                pin->glitch_count++;
                s_data.stats.overflow_count++;
                s_data.stats.last_error_tick = now;
                /* 글리치 이벤트 기록 */
                TraceEvent_GPIO(TRACE_GPIO_GLITCH,
                                (uint8_t)i, new_state, pin->glitch_count);
            } else {
                /* 정상 상태 변화 */
                pin->change_count++;
                TraceEvent_GPIO(TRACE_GPIO_CHANGE,
                                (uint8_t)i, new_state, pin->change_count);
            }
            pin->state = new_state;
            s_data.stats.success_count++;
        }
    }
}

static void _gpio_get_stats(PeripheralErrorStats_t *out) {
    if (out != NULL) {
        *out = s_data.stats;
    }
}

static void _gpio_reset(void) {
    memset(&s_data.stats, 0, sizeof(s_data.stats));
    for (uint32_t i = 0U; i < s_data.pin_count; i++) {
        s_data.pins[i].change_count = 0U;
        s_data.pins[i].glitch_count = 0U;
        s_data.pins[i].history      = 0U;
    }
}

/* ── 공개 인스턴스 ────────────────────────────────────────── */
PeripheralMonitor_t g_gpio_monitor = {
    .name       = "GPIO",
    .event_base = 0x70U,
    .enabled    = true,
    .init       = _gpio_init,
    .sample     = _gpio_sample,
    .get_stats  = _gpio_get_stats,
    .reset      = _gpio_reset,
};

/* ── 공개 API ─────────────────────────────────────────────── */
bool GPIO_Monitor_AddPin(void *port, uint16_t pin, const char *name) {
    if ((port == NULL) || (name == NULL) ||
        (s_data.pin_count >= GPIO_MONITOR_MAX_PINS)) {
        return false;
    }
    GPIOPinInfo_t *p = &s_data.pins[s_data.pin_count];
    p->port  = port;
    p->pin   = pin;
    p->state = (uint8_t)GPIO_READ(port, pin);

    /* 이름 복사 (최대 11자 + null) */
    size_t len = 0U;
    while ((name[len] != '\0') && (len < 11U)) {
        p->name[len] = name[len];
        len++;
    }
    p->name[len] = '\0';

    s_data.pin_count++;
    return true;
}

const GPIOMonitorData_t* GPIO_Monitor_GetData(void) {
    return &s_data;
}
