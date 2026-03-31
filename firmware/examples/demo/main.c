/* ClaudeRTOS-Insight V3.2 Demo
 *
 * Board  : STM32 Nucleo-F446RE
 * RTOS   : FreeRTOS 10.5+
 * Clock  : 180 MHz (HSE 8 MHz × PLL)
 *
 * 전송 모드 선택 (Makefile 또는 IDE 컴파일 플래그):
 *   ITM  (기본): -DCLAUDERTOS_TRANSPORT_ITM
 *   UART       : -DCLAUDERTOS_TRANSPORT_UART
 *
 * Tasks:
 *   HighPriorityTask  (P3) — 100ms 주기, 타이밍 측정
 *   MediumPriorityTask(P2) — 500ms 주기, mutex 경합
 *   LowPriorityTask   (P1) — 1000ms 주기, queue 전송
 *   MonitorTask       (P4) — 1000ms 주기, OS 스냅샷 수집 + 전송
 */

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include "stm32f4xx_hal.h"
#include <stdio.h>
#include <string.h>

/* ClaudeRTOS V3 API */
#include "dwt_timestamp.h"
#include "rate_controller.h"
#include "transport.h"            /* 통합 전송 레이어 (ITM / UART) */
#include "os_monitor_v3.h"
#include "trace_events.h"    /* trace hook 구현체 */        /* V3 OS 모니터 */

/* ── 설정 ──────────────────────────────────────────── */
#define HIGH_TASK_PERIOD_MS    100U
#define MED_TASK_PERIOD_MS     500U
#define LOW_TASK_PERIOD_MS    1000U
#define MONITOR_PERIOD_MS     1000U
#define MONITOR_STACK_WORDS    512U  /* 넉넉하게 — MonitorTask는 큰 배열 사용 */
#define NORMAL_STACK_WORDS     256U

/* ── 공유 자원 ──────────────────────────────────────── */
static SemaphoreHandle_t s_mutex;
static QueueHandle_t     s_queue;
static RateController_t  s_rate_ctrl;

/* ── 통계 ───────────────────────────────────────────── */
static volatile uint32_t s_high_runs = 0U;
static volatile uint32_t s_med_runs  = 0U;
static volatile uint32_t s_low_runs  = 0U;

/* ═══════════════════════════════════════════════════════
 *  태스크 구현
 * ═══════════════════════════════════════════════════════ */

void HighPriorityTask(void *pvParam)
{
    (void)pvParam;
    TickType_t wakeTime = xTaskGetTickCount();
    uint64_t   lastUs   = 0U;

    for (;;) {
        uint64_t now   = DWT_GetTimestamp_us();
        uint64_t delta = now - lastUs;
        lastUs = now;

        HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);

        /* 부하 시뮬레이션 */
        volatile uint32_t d = 0;
        for (int i = 0; i < 1000; i++) d += (uint32_t)i;
        (void)d;

        if (delta > (uint64_t)(HIGH_TASK_PERIOD_MS * 1100U)) {
            /* 주기 초과 10% — 이벤트 발생 시 AI가 감지 */
        }
        s_high_runs++;
        vTaskDelayUntil(&wakeTime, pdMS_TO_TICKS(HIGH_TASK_PERIOD_MS));
    }
}

void MediumPriorityTask(void *pvParam)
{
    (void)pvParam;
    TickType_t wakeTime = xTaskGetTickCount();

    for (;;) {
        if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100U)) == pdTRUE) {
            volatile uint32_t d = 0;
            for (int i = 0; i < 5000; i++) d += (uint32_t)(i * 2);
            (void)d;
            s_med_runs++;
            xSemaphoreGive(s_mutex);
        }
        /* 타임아웃 = 우선순위 역전 가능 → AnalysisEngine이 감지 */
        vTaskDelayUntil(&wakeTime, pdMS_TO_TICKS(MED_TASK_PERIOD_MS));
    }
}

void LowPriorityTask(void *pvParam)
{
    (void)pvParam;
    uint32_t val = 0U;

    for (;;) {
        if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(50U)) == pdTRUE) {
            val++;
            xQueueSend(s_queue, &val, 0);
            s_low_runs++;
            xSemaphoreGive(s_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(LOW_TASK_PERIOD_MS));
    }
}

/**
 * MonitorTask — V3 API 사용, transport 레이어로 전송
 *
 * 흐름:
 *   1. OSMonitorV3_CacheCurrentTask()  — fault 핸들러용 태스크명 캐시
 *   2. OSMonitorV3_Collect()           — 스냅샷 수집 → V4 buffer
 *   3. RateController_Adjust()         — 샘플링 레이트 조정
 *   4. OSMonitorV3_GetData()           — V4 buffer 에서 우선순위 순으로 꺼냄
 *   5. Transport_SendBinary()          — ITM 또는 UART로 전송
 */
void MonitorTask(void *pvParam)
{
    (void)pvParam;
    TickType_t wakeTime = xTaskGetTickCount();
    uint8_t    pktBuf[MAX_PACKET_SIZE];
    uint32_t   diagCnt = 0U;

    for (;;) {
        /* ── 1. fault 핸들러용 태스크명 캐시 갱신 ── */
        OSMonitorV3_CacheCurrentTask();

        /* ── 1b. ISR 빈도 샘플링 (DWT EXCCNT, 오버헤드 0) ─
         * hook 없이 하드웨어 카운터로 이 주기 동안 ISR 진입 횟수 측정 */
        uint32_t isr_delta = TraceEvents_SampleISRCount();
        (void)isr_delta;   /* OSMonitorV3_Collect 내부에서 활용 예정 */

        /* ── 2. OS 스냅샷 수집 (V3 API) ─────────── */
        OSMonitorV3_Collect();

        /* ── 3. 샘플링 레이트 동적 조정 ─────────── */
        OSMonitorV3Stats_t stats;
        OSMonitorV3_GetStats(&stats);

        /* drops_critical 는 항상 0이어야 함 */
        configASSERT(stats.drops_critical == 0U);

        uint8_t cpu_proxy = (stats.critical_events > 0U) ? 95U : 50U;
        uint32_t load_proxy = stats.drops_low + stats.drops_normal;

        uint16_t rate_ms = RateController_Adjust(&s_rate_ctrl,
                                                  cpu_proxy,
                                                  load_proxy);
        (void)rate_ms;   /* 향후: OSMonitorV3 에 샘플링 레이트 전달 가능 */

        /* ── 4+5. V4 버퍼에서 꺼내서 전송 ─────────
         *
         * 루프: 버퍼가 빌 때까지 우선순위 순으로 전송.
         * CRITICAL 패킷이 먼저 나옴 (reserved buffer).
         */
        while (OSMonitorV3_HasData()) {
            size_t n = OSMonitorV3_GetData(pktBuf, sizeof(pktBuf));
            if (n == 0U) break;
            Transport_SendBinary(pktBuf, n);
        }

        /* ── 5b. Trace 이벤트 배치 전송 ─────────────────────
         * 링 버퍼에서 최대 64개씩 꺼내 Binary Protocol 채널로 전송.
         * Hybrid 모드: VERBOSE 시에만 Transport_SendTrace() 실행.
         * 이벤트당 16B → 64개 × 16B = 1024B / 주기 (최대)
         */
#ifdef CLAUDERTOS_TRACE_ENABLED
        {
            TraceEvent_t trace_batch[64U];
            uint16_t     trace_n = TraceEvents_Read(trace_batch, 64U);
            if (trace_n > 0U) {
                /* trace 이벤트: OS 패킷과 동일한 Binary 채널 사용
                 * 호스트 파서가 packet_type으로 구분 */
                Transport_SendBinary((const uint8_t *)trace_batch,
                                     (size_t)trace_n * sizeof(TraceEvent_t));
            }
        }
#endif /* CLAUDERTOS_TRACE_ENABLED */

        /* ── 진단 메시지 (10초마다, CH_DIAG 채널) ── */
        if (++diagCnt % (10000U / MONITOR_PERIOD_MS) == 0U) {
            char msg[128];
            TraceStats_t tstats;
            TraceEvents_GetStats(&tstats);
            snprintf(msg, sizeof(msg),
                     "[ClaudeRTOS V3.9 %s] "
                     "snaps=%lu crit=%lu "
                     "ctx_sw=%lu isr/s=%lu mux_to=%lu "
                     "trace_ovf=%lu\r\n",
                     Transport_GetModeName(),
                     (unsigned long)stats.snapshots,
                     (unsigned long)stats.critical_events,
                     (unsigned long)tstats.ctx_switch_count,
                     (unsigned long)isr_delta,
                     (unsigned long)tstats.mutex_timeout_count,
                     (unsigned long)tstats.overflow_count);
            Transport_SendDiag(msg);
        }

        vTaskDelayUntil(&wakeTime, pdMS_TO_TICKS(MONITOR_PERIOD_MS));
    }
}

/* ═══════════════════════════════════════════════════════
 *  하드웨어 초기화
 * ═══════════════════════════════════════════════════════ */

void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    osc.OscillatorType      = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState            = RCC_HSE_ON;
    osc.PLL.PLLState        = RCC_PLL_ON;
    osc.PLL.PLLSource       = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLM            = 8;
    osc.PLL.PLLN            = 360;
    osc.PLL.PLLP            = RCC_PLLP_DIV2;
    osc.PLL.PLLQ            = 7;
    HAL_RCC_OscConfig(&osc);
    HAL_PWREx_EnableOverDrive();

    clk.ClockType      = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                         RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider  = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV4;
    clk.APB2CLKDivider = RCC_HCLK_DIV2;
    HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_5);
}

void GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    GPIO_InitTypeDef g = {0};
    g.Pin   = GPIO_PIN_5;
    g.Mode  = GPIO_MODE_OUTPUT_PP;
    g.Pull  = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOA, &g);
}

/* ═══════════════════════════════════════════════════════
 *  main
 * ═══════════════════════════════════════════════════════ */

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    GPIO_Init();

    /* Transport 초기화 (ITM 또는 UART, 컴파일 타임 선택) */
    DWT_Init(180000000U);
    Transport_Init(180000000U);

    /* OS Monitor V3 초기화 (heap_total 부팅 캐시 포함) */
    OSMonitorV3_Init();

    /* Trace 초기화 (DWT CYCCNT 기반 lock-free ring buffer)
     * FreeRTOSConfig.h의 traceTASK_SWITCHED_IN 등 hook이 여기서 활성화됨 */
    TraceEvents_Init();

    /* Mutex 이름 등록 — AI 분석 시 주소 대신 이름으로 표시 */

    /* Rate controller */
    RateController_Init(&s_rate_ctrl, 100U, 5000U, RATE_POLICY_ADAPTIVE_BOTH);

    /* 시작 메시지 (진단 채널) */
    char startMsg[80];
    snprintf(startMsg, sizeof(startMsg),
             "ClaudeRTOS-Insight V3.9.0 Started [%s]\r\n",
             Transport_GetModeName());
    Transport_SendDiag(startMsg);

    /* 동기화 객체 */
    s_mutex = xSemaphoreCreateMutex();
    s_queue = xQueueCreate(10U, sizeof(uint32_t));
    configASSERT(s_mutex != NULL && s_queue != NULL);
    TraceEvents_RegisterMutex(s_mutex, "AppMutex");

    /* 태스크 생성 */
    xTaskCreate(HighPriorityTask,   "High",    NORMAL_STACK_WORDS, NULL, 3, NULL);
    xTaskCreate(MediumPriorityTask, "Medium",  NORMAL_STACK_WORDS, NULL, 2, NULL);
    xTaskCreate(LowPriorityTask,    "Low",     NORMAL_STACK_WORDS, NULL, 1, NULL);
    xTaskCreate(MonitorTask,        "Monitor", MONITOR_STACK_WORDS, NULL, 4, NULL);

    vTaskStartScheduler();
    for (;;) {}
}

/* ═══════════════════════════════════════════════════════
 *  FreeRTOS 훅
 * ═══════════════════════════════════════════════════════ */

void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName)
{
    (void)xTask;
    /* HardFault 핸들러와 동일한 경로: V4 CRITICAL 버퍼에 기록
     * (OSMonitorV3_HardFaultCapture가 전송까지 담당)
     * 여기서는 텍스트 진단만 */
    char msg[64];
    snprintf(msg, sizeof(msg),
             "STACK_OVERFLOW: task=%s\r\n", pcTaskName ? pcTaskName : "?");
    Transport_SendDiag(msg);

    while (1) {
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_SET);
        HAL_Delay(100U);
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_RESET);
        HAL_Delay(100U);
    }
}

void vApplicationMallocFailedHook(void)
{
    Transport_SendDiag("MALLOC_FAILED: heap exhausted\r\n");
    while (1) {}
}

/* Runtime stats (FreeRTOS가 호출) */
void vConfigureTimerForRunTimeStats(void) {}
uint32_t vGetRunTimeCounterValue(void)
{
    return (uint32_t)DWT_GetTimestamp_us();
}
