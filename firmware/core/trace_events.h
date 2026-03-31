/* ClaudeRTOS Trace Events V2
 *
 * 개선 사항 (V2):
 *   1. 타임스탬프: DWT_GetTimestamp_us() → DWT->CYCCNT 직접 읽기
 *                  나눗셈 제거 (~10 cycles → ~3 cycles)
 *                  단위: CPU cycles (호스트에서 µs로 변환)
 *   2. Critical section 제거: lock-free MPSC ring buffer 채택
 *                  taskENTER/EXIT_CRITICAL_FROM_ISR() 제거
 *                  (~18 cycles 절감, ISR 레이턴시 영향 없음)
 *   3. DWT EXCCNT: ISR 진입 횟수 하드웨어 카운터 (오버헤드 0)
 *                  traceISR_ENTER/EXIT hook 없이 ISR 빈도 측정
 *   4. 이벤트 구조체 필드 직접 대입 (memset 불필요)
 *
 * 수집 이벤트:
 *   TRACE_CTX_SWITCH_IN/OUT — task 컨텍스트 전환
 *   TRACE_MUTEX_TAKE/GIVE/TIMEOUT — Mutex 상태
 *   TRACE_MALLOC/FREE — 동적 메모리
 *   (ISR 개별 추적은 DWT EXCCNT로 대체 — hook 불필요)
 *
 * FreeRTOSConfig.h 활성화 (3줄):
 *   #define traceTASK_SWITCHED_IN()  TraceEvent_ContextSwitchIn()
 *   #define traceTASK_SWITCHED_OUT() TraceEvent_ContextSwitchOut()
 *   #define traceTAKE_MUTEX(m, t)    TraceEvent_MutexTake((m),(t))
 *   #define traceGIVE_MUTEX(m)       TraceEvent_MutexGive((m))
 *
 * 성능 (Cortex-M4 @ 180MHz):
 *   이벤트당 ~25 cycles = 0.14µs
 *   컨텍스트 스위치 1kHz 기준: 0.028% CPU overhead
 *
 * Safety: NOT CERTIFIED
 */

#ifndef TRACE_EVENTS_H
#define TRACE_EVENTS_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"

/* ── DWT 레지스터 직접 접근 ──────────────────────────────────
 * Cortex-M3/M4/M7/M33 공통. DWT_Init()에서 CYCCNT enable 필요.
 * ARM DDI0403E §C1.8
 */
#define TRACE_DWT_CYCCNT  (*((volatile uint32_t *)0xE0001004U))  /* cycle counter */
#define TRACE_DWT_EXCCNT  (*((volatile uint32_t *)0xE0001014U))  /* ISR entry count */

/** 현재 CPU 사이클 읽기 (3 cycles, 나눗셈 없음) */
static inline uint32_t trace_now_cycles(void)
{
    return TRACE_DWT_CYCCNT;
}

/* ── 이벤트 타입 ─────────────────────────────────────────── */
typedef enum {
    TRACE_CTX_SWITCH_IN  = 0x10U,
    TRACE_CTX_SWITCH_OUT = 0x11U,
    TRACE_MUTEX_TAKE     = 0x30U,
    TRACE_MUTEX_TIMEOUT  = 0x31U,
    TRACE_MUTEX_GIVE     = 0x32U,
    TRACE_MALLOC         = 0x60U,
    TRACE_FREE           = 0x61U,
    /* ISR 개별 hook 없음 — DWT EXCCNT로 빈도 측정 */
} TraceEventType_t;

/* ── 이벤트 레코드 (16 bytes 고정) ──────────────────────────
 *
 * timestamp_cycles: DWT->CYCCNT 기준 (단위: CPU cycles)
 *   호스트 변환: µs = cycles / (cpu_hz / 1_000_000)
 *   wraps every 2^32 / 180e6 ≈ 23.8 seconds
 *
 * 호스트 파서는 cpu_hz를 session.cpu_hz 필드로 받아 변환.
 */
typedef struct {
    uint32_t timestamp_cycles;  /* DWT CYCCNT (cycles) */
    uint8_t  event_type;        /* TraceEventType_t     */
    uint8_t  task_id;           /* 태스크 ID (0xFF = ISR) */
    uint8_t  reserved[2];       /* 정렬 패딩             */
    union {
        struct {                /* CTX_SWITCH_IN/OUT */
            uint8_t  from_id;  /* SWITCH_OUT: 이전 태스크 */
            uint8_t  to_id;    /* SWITCH_IN:  다음 태스크 */
            uint8_t  pad[6];
        } ctx;
        struct {                /* MUTEX_TAKE/GIVE/TIMEOUT */
            uint32_t mutex_addr;   /* SemaphoreHandle_t */
            uint16_t wait_ticks;   /* TAKE: 요청 wait */
            uint8_t  pad[2];
        } mutex;
        struct {                /* MALLOC/FREE */
            uint32_t ptr;
            uint32_t size;
        } mem;
    } data;
} TraceEvent_t;  /* 16 bytes */

/* ── 링 버퍼 설정 ────────────────────────────────────────── */
#define TRACE_RING_SIZE   256U
#define TRACE_RING_MASK   (TRACE_RING_SIZE - 1U)

/* ── Mutex 이름 매핑 ────────────────────────────────────── */
#define TRACE_MUTEX_MAP_SIZE  8U

typedef struct {
    uint32_t    handle_addr;
    const char *name;
} TraceMutexName_t;

/* ── ISR 통계 (DWT EXCCNT 기반) ─────────────────────────── */
typedef struct {
    uint32_t isr_count_prev;   /* 이전 샘플 시점 EXCCNT 값 */
    uint32_t isr_count_delta;  /* 마지막 샘플 구간 ISR 진입 횟수 */
    uint32_t ctx_switch_count; /* 컨텍스트 스위치 누산 (SW 카운터) */
    uint32_t mutex_timeout_count;
    uint32_t overflow_count;   /* 링 버퍼 오버플로 횟수 */
} TraceStats_t;

/* ── 공개 API ────────────────────────────────────────────── */

void     TraceEvents_Init(void);
void     TraceEvents_RegisterMutex(SemaphoreHandle_t handle, const char *name);

/** 이벤트 읽기 — MonitorTask에서 주기적으로 호출
 *  @return 복사된 이벤트 수 */
uint16_t TraceEvents_Read(TraceEvent_t *out, uint16_t count);

uint16_t TraceEvents_Available(void);
void     TraceEvents_Clear(void);

/** ISR 빈도 샘플링 (DWT EXCCNT 기반, 오버헤드 없음)
 *  MonitorTask에서 주기적으로 호출.
 *  @return 마지막 호출 이후 ISR 진입 횟수 */
uint32_t TraceEvents_SampleISRCount(void);

/** 전체 통계 반환 */
void     TraceEvents_GetStats(TraceStats_t *out);

/* ── FreeRTOS hook 구현체 ────────────────────────────────── */
void TraceEvent_ContextSwitchIn(void);
void TraceEvent_ContextSwitchOut(void);
void TraceEvent_MutexTake(SemaphoreHandle_t mutex, TickType_t wait_ticks);
void TraceEvent_MutexGive(SemaphoreHandle_t mutex);
void TraceEvent_MutexTimeout(SemaphoreHandle_t mutex);
void TraceEvent_Malloc(void *ptr, size_t size);
void TraceEvent_Free(void *ptr);

#endif /* TRACE_EVENTS_H */
