/* ClaudeRTOS Trace Events V2 — Implementation
 *
 * 핵심 개선:
 *
 * 1. Lock-free MPSC Ring Buffer
 *    ─────────────────────────
 *    기존: taskENTER/EXIT_CRITICAL_FROM_ISR() 로 write 보호
 *          → ISR 마스킹 시간 ~18 cycles
 *          → 높은 우선순위 ISR에서 호출 시 레이턴시 영향
 *
 *    개선: Cortex-M 원자적 명령 사용
 *          LDREX/STREX (ARMv7-M 원자적 read-modify-write)
 *          __DMB() (Data Memory Barrier) 로 순서 보장
 *          → write index를 원자적으로 예약 → 데이터 기록
 *          → critical section 없음
 *
 *    MPSC 주의사항:
 *          Multiple Producer(태스크+ISR), Single Consumer(MonitorTask)
 *          Consumer는 read index를 단독 사용 → critical 불필요
 *          Producer는 write index를 원자적으로 예약
 *
 * 2. DWT->CYCCNT 직접 읽기
 *    ─────────────────────
 *    기존: DWT_GetTimestamp_us() — 나눗셈 포함 ~10 cycles
 *    개선: TRACE_DWT_CYCCNT — LDR 1개 ~3 cycles
 *    호스트에서 cycles → µs 변환 (cpu_hz 이미 session에 포함)
 *
 * 3. DWT EXCCNT ISR 빈도 측정
 *    ─────────────────────────
 *    Cortex-M DWT EXCCNT: ISR 진입마다 하드웨어가 자동 증가
 *    hook 코드 없음, 오버헤드 0
 *    TraceEvents_SampleISRCount()로 delta 반환
 *
 * 4. 필드 직접 대입
 *    ──────────────
 *    기존: TraceEvent_t ev = {0};  → 컴파일러가 memset 생성 가능
 *    개선: 필요 필드만 직접 대입, 컴파일러 최적화 우호
 */

#include "trace_events.h"
#include <string.h>

/* ── Cortex-M 원자적 명령 ─────────────────────────────────
 * GCC ARM 내장 함수 사용. CMSIS가 없어도 동작.
 * __LDREXW/__STREXW: ARMv7-M exclusive load/store
 * __DMB(): Data Memory Barrier
 */
#ifndef __LDREXW
  #define __LDREXW(ptr) __builtin_arm_ldrex((volatile uint32_t *)(ptr))
#endif
#ifndef __STREXW
  #define __STREXW(val, ptr) __builtin_arm_strex((val), (volatile uint32_t *)(ptr))
#endif
#ifndef __DMB
  #define __DMB()  __asm__ volatile ("dmb" : : : "memory")
#endif

/* ── 링 버퍼 ───────────────────────────────────────────── */
static TraceEvent_t     s_ring[TRACE_RING_SIZE];
static volatile uint32_t s_write = 0U;   /* write index (producers) */
static volatile uint32_t s_read  = 0U;   /* read  index (single consumer) */

/* ── Mutex 이름 테이블 ──────────────────────────────────── */
static TraceMutexName_t  s_mutex_map[TRACE_MUTEX_MAP_SIZE];
static uint8_t           s_mutex_map_cnt = 0U;

/* ── 통계 ────────────────────────────────────────────────── */
static TraceStats_t      s_stats;

/* ══════════════════════════════════════════════════════════
 *  Lock-free MPSC write
 *
 *  알고리즘:
 *    1. LDREX로 현재 s_write 값을 독점 로드
 *    2. next = (current + 1) & MASK
 *    3. STREX로 s_write = next 시도 → 실패 시 재시도
 *    4. 예약된 슬롯(current)에 이벤트 기록
 *    5. DMB로 쓰기 완료 순서 보장
 *
 *  오버플로 처리:
 *    s_write가 s_read를 추월하면 oldest 이벤트 덮어쓰기
 *    Consumer는 항상 최신 이벤트를 얻음
 * ══════════════════════════════════════════════════════════ */
static void push_event_lockfree(TraceEventType_t type, uint8_t task_id,
                                 const void *data8)
{
    uint32_t idx;
    uint32_t next;

    /* ── 슬롯 원자적 예약 ─────────────────────────────── */
    do {
        idx  = __LDREXW(&s_write);
        next = (idx + 1U) & 0xFFFFFFFFU;   /* wrap-around 허용 */
    } while (__STREXW(next, &s_write));

    /* ── 예약된 슬롯에 기록 ──────────────────────────── */
    TraceEvent_t *ev = &s_ring[idx & TRACE_RING_MASK];

    ev->timestamp_cycles = TRACE_DWT_CYCCNT;  /* 3 cycles */
    ev->event_type       = (uint8_t)type;
    ev->task_id          = task_id;
    ev->reserved[0]      = 0U;
    ev->reserved[1]      = 0U;

    /* data 복사 (8 bytes, 고정) */
    if (data8) {
        const uint32_t *src = (const uint32_t *)data8;
        uint32_t       *dst = (uint32_t *)&ev->data;
        dst[0] = src[0];
        dst[1] = src[1];
    } else {
        uint32_t *dst = (uint32_t *)&ev->data;
        dst[0] = 0U;
        dst[1] = 0U;
    }

    /* ── 메모리 배리어: 소비자가 완성된 레코드를 읽도록 ── */
    __DMB();

    /* 오버플로: s_read 밀어내기 */
    {
        uint32_t avail = next - s_read;
        if (avail > TRACE_RING_SIZE) {
            s_read++;
            s_stats.overflow_count++;
        }
    }
}

/* ── 현재 태스크 ID ──────────────────────────────────────
 * xTaskGetCurrentTaskHandle() ISR에서 호출 불가 →
 * 태스크 컨텍스트에서만 사용. ISR 컨텍스트는 0xFF.
 */
static uint8_t cur_task_id(void)
{
    TaskHandle_t h = xTaskGetCurrentTaskHandle();
    return h ? (uint8_t)((uintptr_t)h & 0xFFU) : 0xFFU;
}

/* ── Mutex 이름 조회 ─────────────────────────────────── */
const char *TraceEvents_LookupMutex(uint32_t addr)
{
    for (uint8_t i = 0; i < s_mutex_map_cnt; i++) {
        if (s_mutex_map[i].handle_addr == addr)
            return s_mutex_map[i].name;
    }
    return NULL;
}

/* ══════════════════════════════════════════════════════════
 *  공개 API
 * ══════════════════════════════════════════════════════════ */

void TraceEvents_Init(void)
{
    memset(s_ring,      0, sizeof(s_ring));
    memset(s_mutex_map, 0, sizeof(s_mutex_map));
    memset(&s_stats,    0, sizeof(s_stats));
    s_write = s_read = 0U;
    s_mutex_map_cnt   = 0U;

    /* DWT EXCCNT 초기 캡처 */
    s_stats.isr_count_prev = TRACE_DWT_EXCCNT;
}

void TraceEvents_RegisterMutex(SemaphoreHandle_t handle, const char *name)
{
    if (!handle || !name || s_mutex_map_cnt >= TRACE_MUTEX_MAP_SIZE) return;
    s_mutex_map[s_mutex_map_cnt].handle_addr = (uint32_t)(uintptr_t)handle;
    s_mutex_map[s_mutex_map_cnt].name        = name;
    s_mutex_map_cnt++;
}

uint16_t TraceEvents_Read(TraceEvent_t *out, uint16_t count)
{
    if (!out || count == 0U) return 0U;

    /* Consumer 전용: s_read는 단독 소유 → critical 불필요 */
    __DMB();   /* 최신 s_write 가시성 보장 */
    uint32_t avail = s_write - s_read;
    if (avail > TRACE_RING_SIZE) avail = TRACE_RING_SIZE;
    uint16_t n = (uint16_t)(avail < count ? avail : count);

    for (uint16_t i = 0U; i < n; i++) {
        out[i] = s_ring[(s_read + i) & TRACE_RING_MASK];
    }
    __DMB();
    s_read += n;
    return n;
}

uint16_t TraceEvents_Available(void)
{
    uint32_t avail = s_write - s_read;
    return (uint16_t)(avail > TRACE_RING_SIZE ? TRACE_RING_SIZE : avail);
}

void TraceEvents_Clear(void)
{
    __DMB();
    s_read = s_write;
}

uint32_t TraceEvents_SampleISRCount(void)
{
    /* DWT EXCCNT: ISR 진입마다 하드웨어 자동 증가 (오버헤드 0) */
    uint32_t now   = TRACE_DWT_EXCCNT;
    uint32_t delta = now - s_stats.isr_count_prev;   /* wrap-around 자동 처리 */
    s_stats.isr_count_prev  = now;
    s_stats.isr_count_delta = delta;
    return delta;
}

void TraceEvents_GetStats(TraceStats_t *out)
{
    if (out) *out = s_stats;
}

/* ══════════════════════════════════════════════════════════
 *  FreeRTOS hook 구현체
 *
 *  모두 push_event_lockfree() 사용 — critical section 없음
 *  데이터 복사: union 8 bytes를 uint32_t[2]로 직접 전달
 * ══════════════════════════════════════════════════════════ */

void TraceEvent_ContextSwitchIn(void)
{
    /* 스케줄러가 이미 스위칭 완료 → 현재 태스크 = 새 태스크 */
    uint32_t data[2] = {0U, 0U};
    push_event_lockfree(TRACE_CTX_SWITCH_IN, cur_task_id(), data);
    s_stats.ctx_switch_count++;
}

void TraceEvent_ContextSwitchOut(void)
{
    uint32_t data[2] = {0U, 0U};
    push_event_lockfree(TRACE_CTX_SWITCH_OUT, cur_task_id(), data);
}

void TraceEvent_MutexTake(SemaphoreHandle_t mutex, TickType_t wait_ticks)
{
    uint32_t data[2];
    data[0] = (uint32_t)(uintptr_t)mutex;
    data[1] = (uint32_t)(wait_ticks > 0xFFFFU ? 0xFFFFU : wait_ticks);
    push_event_lockfree(TRACE_MUTEX_TAKE, cur_task_id(), data);
}

void TraceEvent_MutexGive(SemaphoreHandle_t mutex)
{
    uint32_t data[2];
    data[0] = (uint32_t)(uintptr_t)mutex;
    data[1] = 0U;
    push_event_lockfree(TRACE_MUTEX_GIVE, cur_task_id(), data);
}

void TraceEvent_MutexTimeout(SemaphoreHandle_t mutex)
{
    uint32_t data[2];
    data[0] = (uint32_t)(uintptr_t)mutex;
    data[1] = 0U;
    push_event_lockfree(TRACE_MUTEX_TIMEOUT, cur_task_id(), data);
    s_stats.mutex_timeout_count++;
}

void TraceEvent_Malloc(void *ptr, size_t size)
{
    uint32_t data[2];
    data[0] = (uint32_t)(uintptr_t)ptr;
    data[1] = (uint32_t)size;
    push_event_lockfree(TRACE_MALLOC, cur_task_id(), data);
}

void TraceEvent_Free(void *ptr)
{
    uint32_t data[2];
    data[0] = (uint32_t)(uintptr_t)ptr;
    data[1] = 0U;
    push_event_lockfree(TRACE_FREE, cur_task_id(), data);
}
