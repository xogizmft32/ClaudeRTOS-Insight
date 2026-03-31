/* Port Implementation: ESP32 (FreeRTOS + Xtensa LX6/LX7)
 *
 * 지원 칩: ESP32, ESP32-S2, ESP32-S3, ESP32-C3(RISC-V)
 * 전송:   UART0 (USB-UART) 기본. ITM 없음.
 *
 * 이식 포인트:
 *   - 타임스탬프: esp_timer_get_time() (µs, 64bit)
 *   - 전송: uart_write_bytes() (UART0 또는 UART1)
 *   - 태스크: FreeRTOS API (거의 동일, uxTaskGetSystemState 사용)
 *   - 크리티컬: portENTER_CRITICAL_ISR / portEXIT_CRITICAL_ISR
 *
 * 빌드:
 *   idf.py build 또는 PlatformIO
 *   PORT=esp32 (CMakeLists.txt에서 설정)
 */

#include "../port.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "driver/uart.h"
#include <string.h>
#include <stdio.h>

#define PORT_UART_NUM     UART_NUM_0
#define PORT_UART_BAUD    115200
#define PORT_UART_TIMEOUT_MS  50

static uint32_t s_cpu_hz = 0U;

/* ── 1. 타임스탬프 ────────────────────────────────────── */
void port_timestamp_init(uint32_t cpu_hz) { s_cpu_hz = cpu_hz; }

uint32_t port_timestamp_us(void)
{
    return (uint32_t)(esp_timer_get_time() & 0xFFFFFFFFULL);
}

/* ── 2. 전송 (UART0) ────────────────────────────────── */
static volatile bool s_tx_busy = false;

void port_transport_init(uint32_t cpu_hz)
{
    (void)cpu_hz;
    /* ESP-IDF: UART0는 보통 이미 초기화됨 (console용)
     * 별도 UART를 쓸 경우 아래 설정 활성화 */
#if 0
    uart_config_t cfg = {
        .baud_rate  = PORT_UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
    };
    uart_driver_install(PORT_UART_NUM, 256, 256, 0, NULL, 0);
    uart_param_config(PORT_UART_NUM, &cfg);
#endif
}

size_t port_transport_send(const uint8_t *data, size_t len)
{
    s_tx_busy = true;
    int n = uart_write_bytes(PORT_UART_NUM, (const char *)data, len);
    /* 전송 완료 대기 (최대 50ms) */
    uart_wait_tx_done(PORT_UART_NUM,
                      pdMS_TO_TICKS(PORT_UART_TIMEOUT_MS));
    s_tx_busy = false;
    return (n > 0) ? (size_t)n : 0U;
}

void port_transport_diag(const char *msg)
{
    if (s_tx_busy || !msg) return;
    uart_write_bytes(PORT_UART_NUM, msg, strlen(msg));
}

const char *port_transport_name(void) { return "UART(ESP32)"; }

/* ── 3. RTOS (FreeRTOS — ESP-IDF) ─────────────────── */
static uint32_t s_heap_total  = 0U;
static uint32_t s_rt_prev[PORT_TASKS_MAX];
static uint32_t s_rt_total_prev = 0U;

void port_rtos_get_heap(uint32_t *free_bytes,
                        uint32_t *min_bytes,
                        uint32_t *total_bytes)
{
    *free_bytes = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_DEFAULT);
    *min_bytes  = (uint32_t)heap_caps_get_minimum_free_size(MALLOC_CAP_DEFAULT);
    if (s_heap_total == 0U)
        s_heap_total = *free_bytes;
    *total_bytes = s_heap_total;
}

uint32_t port_rtos_uptime_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

uint8_t port_rtos_current_task_id(void)
{
    TaskHandle_t h = xTaskGetCurrentTaskHandle();
    return h ? (uint8_t)((uintptr_t)h & 0xFFU) : 0U;
}

bool port_rtos_get_tasks(PortTaskInfo_t *out, uint8_t *count)
{
    if (!out || !count) return false;
    TaskStatus_t raw[PORT_TASKS_MAX];
    uint32_t total_rt = 0U;
    UBaseType_t n = uxTaskGetSystemState(raw, PORT_TASKS_MAX, &total_rt);
    if (n == 0U) return false;

    uint32_t total_delta = (total_rt > s_rt_total_prev)
                         ? (total_rt - s_rt_total_prev) : 1U;
    uint8_t cnt = 0U;
    for (UBaseType_t i = 0; i < n && cnt < PORT_TASKS_MAX; i++) {
        PortTaskInfo_t *t = &out[cnt];
        t->id = (uint8_t)((uintptr_t)raw[i].xHandle & 0xFFU);
        strncpy(t->name, raw[i].pcTaskName, PORT_TASK_NAME_MAX-1);
        t->name[PORT_TASK_NAME_MAX-1] = '\0';
        t->priority  = (uint8_t)raw[i].uxCurrentPriority;
        t->stack_hwm = (uint16_t)raw[i].usStackHighWaterMark;
        t->runtime_us= raw[i].ulRunTimeCounter;
        uint32_t dt  = (raw[i].ulRunTimeCounter > s_rt_prev[i])
                     ? (raw[i].ulRunTimeCounter - s_rt_prev[i]) : 0U;
        t->cpu_pct   = (uint8_t)((uint64_t)dt*100ULL/total_delta);
        if (t->cpu_pct > 100U) t->cpu_pct = 100U;
        s_rt_prev[i] = raw[i].ulRunTimeCounter;
        switch(raw[i].eCurrentState) {
            case eRunning:   t->state=PORT_TASK_RUNNING;   break;
            case eReady:     t->state=PORT_TASK_READY;     break;
            case eBlocked:   t->state=PORT_TASK_BLOCKED;   break;
            case eSuspended: t->state=PORT_TASK_SUSPENDED; break;
            default:         t->state=PORT_TASK_BLOCKED;   break;
        }
        cnt++;
    }
    s_rt_total_prev = total_rt;
    *count = cnt;
    return true;
}

/* ── 4. 크리티컬 섹션 ────────────────────────────── */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
uint32_t port_critical_enter(void) { taskENTER_CRITICAL(&s_mux); return 0U; }
void     port_critical_exit(uint32_t s) { (void)s; taskEXIT_CRITICAL(&s_mux); }

/* ── 5. 플랫폼 정보 ──────────────────────────────── */
const char *port_platform_name(void) { return "ESP32 (Xtensa LX6)"; }
uint32_t    port_cpu_hz(void)        { return s_cpu_hz; }
