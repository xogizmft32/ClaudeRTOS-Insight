/* Port Implementation: Cortex-M4 / STM32F4xx
 *
 * 지원 보드:
 *   STM32 Nucleo-F446RE (180 MHz)
 *   STM32F407 Discovery (168 MHz)
 *   STM32F4xx 계열 전반
 *
 * 전송 모드 선택 (컴파일 플래그):
 *   -DPORT_TRANSPORT_ITM   (기본)
 *   -DPORT_TRANSPORT_UART  (PA2/PA3, UART2, 115200)
 *   -DPORT_TRANSPORT_RTT   (Segger RTT — 미구현, stub)
 *
 * 다른 MCU로 이식:
 *   firmware/port/<target>/port_impl.c 파일만 새로 작성
 *   port.h 인터페이스는 그대로 유지
 */

#include "../port.h"
#include "FreeRTOS.h"
#include "task.h"

/* ── 헤더 include (컴파일 타임 선택) ─────────────────────── */
#if defined(PORT_TRANSPORT_UART)
  #include "stm32f4xx_hal.h"
#else
  #include "stm32f4xx.h"   /* ITM, TPI, CoreDebug, DWT */
  #define PORT_TRANSPORT_ITM
#endif

#include <string.h>
#include <stdio.h>

/* ══════════════════════════════════════════════════════
 *  1. 타임스탬프 — ARM DWT
 * ══════════════════════════════════════════════════════ */
static uint32_t s_cpu_hz = 0U;

void port_timestamp_init(uint32_t cpu_hz)
{
    s_cpu_hz = cpu_hz;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT  = 0U;
    DWT->CTRL   |= DWT_CTRL_CYCCNTENA_Msk;
}

uint32_t port_timestamp_us(void)
{
    if (s_cpu_hz == 0U) return 0U;
    return (uint32_t)((uint64_t)DWT->CYCCNT * 1000000ULL / s_cpu_hz);
}


/* ══════════════════════════════════════════════════════
 *  2-A. 전송 — ITM (SWO)
 * ══════════════════════════════════════════════════════ */
#if defined(PORT_TRANSPORT_ITM)

#define ITM_TIMEOUT_CNT  10000U

void port_transport_init(uint32_t cpu_hz)
{
    /* ITM 잠금 해제 */
    ITM->LAR = 0xC5ACCE55U;
    ITM->TCR = ITM_TCR_ITMENA_Msk;
    /* 포트 0 (바이너리), 3 (진단) 활성화 */
    ITM->TER = (1U << PORT_CH_BINARY) | (1U << PORT_CH_DIAG);

    /* SWO 클럭: ACPR = (cpu_hz / 2_250_000) - 1 */
    TPI->ACPR = (cpu_hz / 2250000U) - 1U;
    TPI->SPPR = 2U;               /* NRZ */
    TPI->FFCR = 0x00000100U;      /* 포매터 활성 */
}

static bool itm_send_byte(uint8_t port, uint8_t byte)
{
    if (!(ITM->TCR & ITM_TCR_ITMENA_Msk)) return false;
    if (!(ITM->TER & (1U << port)))        return false;
    uint32_t t = ITM_TIMEOUT_CNT;
    while (ITM->PORT[port].u32 == 0U) {
        if (--t == 0U) return false;
    }
    ITM->PORT[port].u8 = byte;
    return true;
}

size_t port_transport_send(const uint8_t *data, size_t len)
{
    size_t sent = 0U;
    for (size_t i = 0; i < len; i++)
        if (itm_send_byte(PORT_CH_BINARY, data[i])) sent++;
    return sent;
}

void port_transport_diag(const char *msg)
{
    for (const char *p = msg; *p; p++)
        itm_send_byte(PORT_CH_DIAG, (uint8_t)*p);
}

const char *port_transport_name(void) { return "ITM"; }


/* ══════════════════════════════════════════════════════
 *  2-B. 전송 — UART (STM32F4 USART2, PA2/PA3)
 * ══════════════════════════════════════════════════════ */
#elif defined(PORT_TRANSPORT_UART)

static UART_HandleTypeDef s_uart;
static volatile bool      s_uart_busy = false;

void port_transport_init(uint32_t cpu_hz)
{
    (void)cpu_hz;
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART2_CLK_ENABLE();

    GPIO_InitTypeDef g = {0};
    g.Pin       = GPIO_PIN_2 | GPIO_PIN_3;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &g);

    s_uart.Instance          = USART2;
    s_uart.Init.BaudRate     = 115200;
    s_uart.Init.WordLength   = UART_WORDLENGTH_8B;
    s_uart.Init.StopBits     = UART_STOPBITS_1;
    s_uart.Init.Parity       = UART_PARITY_NONE;
    s_uart.Init.Mode         = UART_MODE_TX_RX;
    s_uart.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
    s_uart.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&s_uart);
}

size_t port_transport_send(const uint8_t *data, size_t len)
{
    s_uart_busy = true;
    HAL_StatusTypeDef st = HAL_UART_Transmit(&s_uart,
                                              (uint8_t *)data,
                                              (uint16_t)len, 100U);
    s_uart_busy = false;
    return (st == HAL_OK) ? len : 0U;
}

void port_transport_diag(const char *msg)
{
    if (s_uart_busy) return;
    size_t len = strlen(msg);
    HAL_UART_Transmit(&s_uart, (uint8_t *)msg, (uint16_t)len, 50U);
}

const char *port_transport_name(void) { return "UART"; }

#endif /* PORT_TRANSPORT_* */


/* ══════════════════════════════════════════════════════
 *  3. RTOS 추상화 — FreeRTOS
 * ══════════════════════════════════════════════════════ */
static uint32_t        s_heap_total   = 0U;
static volatile uint8_t s_cur_task_id = 0U;

/* Runtime stats 델타 계산용 테이블 */
#define _MAX_T  PORT_TASKS_MAX
static uint32_t s_rt_prev[_MAX_T];
static uint32_t s_rt_total_prev = 0U;

/* task handle → ID 매핑 (handle 주소의 하위 8비트) */
static uint8_t handle_to_id(TaskHandle_t h)
{
    return (uint8_t)((uintptr_t)h & 0xFFU);
}

void port_rtos_get_heap(uint32_t *free_bytes,
                        uint32_t *min_bytes,
                        uint32_t *total_bytes)
{
    *free_bytes  = (uint32_t)xPortGetFreeHeapSize();
    *min_bytes   = (uint32_t)xPortGetMinimumEverFreeHeapSize();
    /* 부팅 시 1회 캐시 */
    if (s_heap_total == 0U)
        s_heap_total = *free_bytes;   /* 첫 호출 = 거의 total */
    *total_bytes = s_heap_total;
}

uint32_t port_rtos_uptime_ms(void)
{
    return (uint32_t)((uint64_t)xTaskGetTickCount() * 1000ULL
                      / configTICK_RATE_HZ);
}

uint8_t port_rtos_current_task_id(void)
{
    return s_cur_task_id;
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
        t->id       = handle_to_id(raw[i].xHandle);
        strncpy(t->name, raw[i].pcTaskName, PORT_TASK_NAME_MAX - 1U);
        t->name[PORT_TASK_NAME_MAX - 1U] = '\0';
        t->priority = (uint8_t)raw[i].uxCurrentPriority;
        t->stack_hwm= (uint16_t)raw[i].usStackHighWaterMark;
        t->runtime_us = raw[i].ulRunTimeCounter;

        /* CPU% = 이번 구간 delta / total delta */
        uint32_t prev = (i < _MAX_T) ? s_rt_prev[i] : 0U;
        uint32_t dt   = (raw[i].ulRunTimeCounter >= prev)
                      ? (raw[i].ulRunTimeCounter - prev) : 0U;
        t->cpu_pct = (uint8_t)((uint64_t)dt * 100ULL / total_delta);
        if (t->cpu_pct > 100U) t->cpu_pct = 100U;
        if (i < _MAX_T) s_rt_prev[i] = raw[i].ulRunTimeCounter;

        /* 상태 정규화 */
        switch (raw[i].eCurrentState) {
            case eRunning:   t->state = PORT_TASK_RUNNING;   break;
            case eReady:     t->state = PORT_TASK_READY;     break;
            case eBlocked:   t->state = PORT_TASK_BLOCKED;   break;
            case eSuspended: t->state = PORT_TASK_SUSPENDED; break;
            case eDeleted:   t->state = PORT_TASK_DELETED;   break;
            default:         t->state = PORT_TASK_BLOCKED;   break;
        }
        cnt++;

        /* 현재 태스크 캐시 갱신 */
        if (raw[i].eCurrentState == eRunning)
            s_cur_task_id = t->id;
    }

    s_rt_total_prev = total_rt;
    *count = cnt;
    return true;
}


/* ══════════════════════════════════════════════════════
 *  4. 크리티컬 섹션
 * ══════════════════════════════════════════════════════ */
uint32_t port_critical_enter(void)
{
    return (uint32_t)taskENTER_CRITICAL_FROM_ISR();
}

void port_critical_exit(uint32_t saved)
{
    taskEXIT_CRITICAL_FROM_ISR((UBaseType_t)saved);
}


/* ══════════════════════════════════════════════════════
 *  5. 플랫폼 정보
 * ══════════════════════════════════════════════════════ */
const char *port_platform_name(void) { return "STM32F4xx (Cortex-M4)"; }
uint32_t    port_cpu_hz(void)        { return s_cpu_hz; }
