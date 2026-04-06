/* insight_port_os.c — FreeRTOS 구현
 *
 * OS가 바뀌면 이 파일만 교체.
 * insight_port_os.h 인터페이스를 유지하는 한
 * os_monitor_v3.c 변경 불필요.
 */

#include "../insight_port_os.h"
#include "FreeRTOS.h"
#include "task.h"
#include "portable.h"

/* ── CPU 사용률 계산용 내부 상태 ───────────────────────────── */
static uint32_t s_idle_ticks_prev = 0;
static uint32_t s_total_ticks_prev = 0;
static uint8_t  s_cpu_percent = 0;
static uint32_t s_heap_total = 0;   /* 부팅 시 1회 캐시 */

/* ── 초기화 ───────────────────────────────────────────────── */
void InsightOS_Init(void) {
    /* 부팅 시 힙 총량 캐시 (이후 동적으로 변하지 않음) */
    s_heap_total = (uint32_t)xPortGetFreeHeapSize();
    /* CPU 계산 기준점 */
    s_total_ticks_prev = xTaskGetTickCount();
    s_idle_ticks_prev  = 0;
}

/* ── 현재 태스크 이름 ─────────────────────────────────────── */
const char* InsightOS_GetCurrentTaskName(void) {
    TaskHandle_t h = xTaskGetCurrentTaskHandle();
    if (h == NULL) return "UNKNOWN";
    return pcTaskGetName(h);
}

/* ── 태스크 목록 수집 ─────────────────────────────────────── */
uint32_t InsightOS_GetTaskList(InsightTaskInfo_t *buf, uint32_t maxlen) {
    if (!buf || maxlen == 0) return 0;

    /* FreeRTOS TaskStatus 수집 */
    TaskStatus_t *raw = (TaskStatus_t*)pvPortMalloc(
        sizeof(TaskStatus_t) * maxlen);
    if (!raw) return 0;

    uint32_t total_runtime = 0;
    uint32_t n = uxTaskGetSystemState(raw, maxlen, &total_runtime);
    if (n > maxlen) n = maxlen;

    for (uint32_t i = 0; i < n; i++) {
        buf[i].task_id          = (uint32_t)raw[i].xTaskNumber;
        buf[i].priority         = (uint8_t)raw[i].uxCurrentPriority;
        buf[i].stack_hwm_words  = (uint16_t)raw[i].usStackHighWaterMark;
        buf[i].runtime_ticks    = raw[i].ulRunTimeCounter;

        /* 이름 복사 */
        const char *src = raw[i].pcTaskName;
        size_t j;
        for (j = 0; j < 15 && src[j]; j++)
            buf[i].name[j] = src[j];
        buf[i].name[j] = '\0';

        /* 상태 변환 */
        switch (raw[i].eCurrentState) {
            case eRunning:   buf[i].state = INSIGHT_TASK_RUNNING;   break;
            case eReady:     buf[i].state = INSIGHT_TASK_READY;     break;
            case eBlocked:   buf[i].state = INSIGHT_TASK_BLOCKED;   break;
            case eSuspended: buf[i].state = INSIGHT_TASK_SUSPENDED; break;
            case eDeleted:   buf[i].state = INSIGHT_TASK_DELETED;   break;
            default:         buf[i].state = INSIGHT_TASK_BLOCKED;   break;
        }

        /* CPU% 계산 (total_runtime 기준) */
        if (total_runtime > 0) {
            buf[i].cpu_pct = (uint8_t)(
                (raw[i].ulRunTimeCounter * 100UL) / total_runtime);
        } else {
            buf[i].cpu_pct = 0;
        }
    }

    vPortFree(raw);
    return n;
}

/* ── 힙 정보 ─────────────────────────────────────────────── */
void InsightOS_GetHeapInfo(InsightHeapInfo_t *out) {
    if (!out) return;
    out->free_bytes     = (uint32_t)xPortGetFreeHeapSize();
    out->min_ever_free  = (uint32_t)xPortGetMinimumEverFreeHeapSize();
    /* total: 부팅 시 캐시값. 정확한 값은 configTOTAL_HEAP_SIZE 참조. */
    out->total_bytes    = (s_heap_total > 0)
                          ? (s_heap_total + (s_heap_total - out->free_bytes))
                          : configTOTAL_HEAP_SIZE;
}

/* ── 틱 ──────────────────────────────────────────────────── */
uint32_t InsightOS_GetTickMs(void) {
    return (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
}

/* ── CPU 사용률 ──────────────────────────────────────────── */
uint8_t InsightOS_GetCpuPercent(void) {
    /* Idle 태스크 실행 시간으로 역산 */
    TaskHandle_t idle = xTaskGetIdleTaskHandle();
    if (!idle) return s_cpu_percent;

    TaskStatus_t idle_status;
    vTaskGetInfo(idle, &idle_status, pdFALSE, eInvalid);

    uint32_t now_total = xTaskGetTickCount();
    uint32_t now_idle  = idle_status.ulRunTimeCounter;

    uint32_t dt_total = now_total - s_total_ticks_prev;
    uint32_t dt_idle  = now_idle  - s_idle_ticks_prev;

    if (dt_total > 0) {
        /* CPU% = 100 - idle% */
        uint32_t idle_pct = (dt_idle * 100UL) / dt_total;
        s_cpu_percent = (idle_pct < 100) ? (uint8_t)(100 - idle_pct) : 0;
    }

    s_total_ticks_prev = now_total;
    s_idle_ticks_prev  = now_idle;
    return s_cpu_percent;
}

/* ── 스케줄러 일시 정지 ──────────────────────────────────── */
void InsightOS_SuspendScheduler(void) {
    vTaskSuspendAll();
}

void InsightOS_ResumeScheduler(void) {
    (void)xTaskResumeAll();
}
