/* ClaudeRTOS Lightweight Trace Configuration
 *
 * 하드웨어 부담 없이 Trace를 강화하는 전략:
 *
 * 1. 컴파일 타임 선택적 활성화 (기본 비용 = 0)
 *    원하는 카테고리만 활성화하면 나머지는 전처리기가 제거.
 *
 * 2. 통계 카운터 모드 (TRACE_MODE_STAT)
 *    이벤트를 저장하지 않고 카운터만 증가 → 4바이트/이벤트
 *    WCET 영향: < 10 cycles (현재 링 버퍼 push: ~50 cycles)
 *
 * 3. 링 버퍼 크기 조정
 *    TRACE_RING_SIZE: 32~512, 기본 256
 *    작게 할수록 RAM 절약, 크게 할수록 이력 보존
 *
 * 4. 샘플링 (TRACE_SAMPLE_RATE)
 *    N번 중 1번만 기록. CPU가 높을 때 자동으로 드롭.
 *
 * 5. DWT EXCCNT (ISR 진입/종료 하드웨어 카운터)
 *    Cortex-M4 DWT의 EXCCNT 레지스터를 읽어 ISR 진입 횟수를 얻음.
 *    hook 없이도 ISR 빈도 측정 가능.
 *
 * 6. FreeRTOS Runtime Stats 재활용
 *    이미 활성화된 통계에서 CPU%를 파생 → 추가 hook 불필요.
 */

#ifndef TRACE_CONFIG_H
#define TRACE_CONFIG_H

/* ── 트레이스 모드 ──────────────────────────────────────── */
#define TRACE_MODE_FULL   0   /* 링 버퍼에 전체 이벤트 저장 */
#define TRACE_MODE_STAT   1   /* 카운터만 (최소 오버헤드)    */
#define TRACE_MODE_OFF    2   /* 완전 비활성               */

#ifndef CLAUDERTOS_TRACE_MODE
  #define CLAUDERTOS_TRACE_MODE  TRACE_MODE_FULL
#endif

/* ── 링 버퍼 크기 (이벤트 개수, 2의 제곱수) ─────────────── */
#ifndef TRACE_RING_SIZE
  #define TRACE_RING_SIZE   256U  /* 256 × 16B = 4KB RAM */
#endif

/* ── 카테고리별 선택 활성화 ────────────────────────────── */
/* 각 카테고리를 독립적으로 켜고 끌 수 있음
 * 기본: 모두 활성 (TRACE_MODE_OFF이면 자동 비활성)         */

#ifndef TRACE_ENABLE_CTX_SWITCH
  #define TRACE_ENABLE_CTX_SWITCH  1  /* Context switch (가장 빈번) */
#endif
#ifndef TRACE_ENABLE_ISR
  #define TRACE_ENABLE_ISR         1  /* ISR 진입/종료 */
#endif
#ifndef TRACE_ENABLE_MUTEX
  #define TRACE_ENABLE_MUTEX       1  /* Mutex lock/unlock */
#endif
#ifndef TRACE_ENABLE_MALLOC
  #define TRACE_ENABLE_MALLOC      1  /* malloc/free */
#endif

/* ── 샘플링 레이트 ─────────────────────────────────────── */
/* 1 = 모두 기록, 4 = 4번 중 1번만 → CPU 부담 75% 감소     */
#ifndef TRACE_SAMPLE_RATE
  #define TRACE_SAMPLE_RATE   1U
#endif

/* ── DWT EXCCNT 기반 ISR 측정 (hook 없이 하드웨어 자동) ─── */
/* Cortex-M4 DWT: EXCCNT 레지스터가 ISR 진입마다 자동 증가   */
#define DWT_EXCCNT  (*((volatile uint32_t *)0xE0001014U))
#define TRACE_DWT_ISR_COUNT()  (DWT_EXCCNT)

/* ── 통계 카운터 (TRACE_MODE_STAT 또는 전체 보완용) ───────── */
typedef struct {
    uint32_t ctx_switches;   /* Context switch 횟수 */
    uint32_t isr_entries;    /* ISR 진입 횟수 (DWT or hook) */
    uint32_t mutex_takes;    /* Mutex 획득 시도 */
    uint32_t mutex_timeouts; /* Mutex 타임아웃 */
    uint32_t mallocs;        /* malloc 호출 횟수 */
    uint32_t frees;          /* free 호출 횟수 */
    uint32_t ring_overflows; /* 링 버퍼 오버플로 횟수 */
} TraceStats_t;

/* ── FreeRTOSConfig.h hook 매크로 (선택적 활성화) ──────── */
/* 이 파일을 include한 후 FreeRTOSConfig.h에 아래를 붙여넣기:

#if TRACE_ENABLE_CTX_SWITCH && (CLAUDERTOS_TRACE_MODE != TRACE_MODE_OFF)
  #define traceTASK_SWITCHED_IN()   TraceEvent_ContextSwitchIn()
  #define traceTASK_SWITCHED_OUT()  TraceEvent_ContextSwitchOut()
#endif

#if TRACE_ENABLE_ISR && (CLAUDERTOS_TRACE_MODE != TRACE_MODE_OFF)
  #define traceISR_ENTER()          TraceEvent_ISREnter(__get_IPSR())
  #define traceISR_EXIT()           TraceEvent_ISRExit(__get_IPSR())
#endif

#if TRACE_ENABLE_MUTEX && (CLAUDERTOS_TRACE_MODE != TRACE_MODE_OFF)
  #define traceTAKE_MUTEX(m, t)     TraceEvent_MutexTake((m),(t))
  #define traceGIVE_MUTEX(m)        TraceEvent_MutexGive((m))
#endif
*/

/* ── OFF 모드: 모든 함수를 no-op으로 ────────────────────── */
#if CLAUDERTOS_TRACE_MODE == TRACE_MODE_OFF
  #define TraceEvent_ContextSwitchIn()       do{}while(0)
  #define TraceEvent_ContextSwitchOut()      do{}while(0)
  #define TraceEvent_ISREnter(n)             do{}while(0)
  #define TraceEvent_ISRExit(n)              do{}while(0)
  #define TraceEvent_MutexTake(m,t)          do{}while(0)
  #define TraceEvent_MutexGive(m)            do{}while(0)
  #define TraceEvent_MutexTimeout(m)         do{}while(0)
  #define TraceEvent_Malloc(p,s)             do{}while(0)
  #define TraceEvent_Free(p)                 do{}while(0)
  #define TraceEvents_Init()                 do{}while(0)
  #define TraceEvents_Read(out, cnt)         (0)
  #define TraceEvents_Available()            (0)
#endif

/* ── STAT 모드: 카운터만 증가, 링 버퍼 사용 안 함 ─────────
 * 링 버퍼 push(~50 cycles) 대신 increment(~3 cycles)
 * RAM: 링 버퍼(4KB) 대신 통계 구조체(28 bytes)
 */
#if CLAUDERTOS_TRACE_MODE == TRACE_MODE_STAT
  extern TraceStats_t g_trace_stats;
  #define TraceEvent_ContextSwitchIn()  do{g_trace_stats.ctx_switches++;}while(0)
  #define TraceEvent_ContextSwitchOut() do{}while(0)
  #define TraceEvent_ISREnter(n)        do{g_trace_stats.isr_entries++;}while(0)
  #define TraceEvent_ISRExit(n)         do{}while(0)
  #define TraceEvent_MutexTake(m,t)     do{g_trace_stats.mutex_takes++;}while(0)
  #define TraceEvent_MutexGive(m)       do{}while(0)
  #define TraceEvent_MutexTimeout(m)    do{g_trace_stats.mutex_timeouts++;}while(0)
  #define TraceEvent_Malloc(p,s)        do{g_trace_stats.mallocs++;}while(0)
  #define TraceEvent_Free(p)            do{g_trace_stats.frees++;}while(0)
  #define TraceEvents_Init()            do{memset(&g_trace_stats,0,sizeof(g_trace_stats));}while(0)
  #define TraceEvents_Read(out,cnt)     (0)
  #define TraceEvents_Available()       (0)
#endif

/* ── 샘플링 래퍼 (FULL 모드에서 N번 중 1번만) ───────────── */
#if CLAUDERTOS_TRACE_MODE == TRACE_MODE_FULL && TRACE_SAMPLE_RATE > 1
  #define _TRACE_SAMPLE_CTX_SWITCH()  \
      static uint32_t _ts_cnt=0; \
      if((++_ts_cnt % TRACE_SAMPLE_RATE) != 0) { /* skip */ } else
  /* 실제 함수 호출은 trace_events.h의 원본 사용 */
#endif

#endif /* TRACE_CONFIG_H */
