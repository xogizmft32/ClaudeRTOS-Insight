# FreeRTOS Hook 및 Trace Macro 사용 가이드
# FreeRTOS Hook and Trace Macro Guide

> How ClaudeRTOS-Insight integrates with FreeRTOS without modifying kernel source files. Uses FreeRTOSConfig.h macros and optional hook functions only.

ClaudeRTOS-Insight는 FreeRTOS 커널 파일을 **수정하지 않습니다**.  
공식 Hook 및 Trace Macro만 사용하므로 FreeRTOS 버전 업그레이드에 영향받지 않습니다.

---

## FreeRTOS Hook vs Trace Macro 개념
*Concept — Hooks vs Trace Macros*

| 구분 | 정의 위치 | 호출 시점 | 용도 |
|------|---------|---------|------|
| **Trace Macro** | `FreeRTOSConfig.h` | 커널 이벤트마다 인라인 확장 | 이벤트 기록 (고성능) |
| **Hook 함수** | 사용자 `.c` | 특정 이벤트에서 콜백 | 오류 처리, 통계 |

---

## ClaudeRTOS에서 사용하는 Trace Macro
*Trace Macros Used by ClaudeRTOS-Insight*

### FreeRTOSConfig.h 설정 위치

```c
/* ClaudeRTOS Trace Macro 연결 — FreeRTOS 커널 파일 수정 없음 */
#include "trace_events.h"   /* ← 이 한 줄로 전체 연결 */

/* Context Switch */
#define traceTASK_SWITCHED_IN()  \
    TraceEvent_CtxSwitchIn(pxCurrentTCB->uxTaskNumber)

#define traceTASK_SWITCHED_OUT() \
    TraceEvent_CtxSwitchOut(pxCurrentTCB->uxTaskNumber)

/* Mutex */
#define traceGIVE_MUTEX(pxMutex) \
    TraceEvent_MutexGive((uint32_t)(pxMutex))

#define traceTAKE_MUTEX(pxMutex, xBlockTime) \
    TraceEvent_MutexTake((uint32_t)(pxMutex), (uint32_t)(xBlockTime))

#define traceBLOCKING_ON_QUEUE_RECEIVE(pxQueue) \
    TraceEvent_MutexTimeout((uint32_t)(pxQueue))

/* Heap */
#define traceMALLOC(pvAddress, uiSize) \
    TraceEvent_Malloc((uint32_t)(pvAddress), (uint32_t)(uiSize))

#define traceFREE(pvAddress, uiSize) \
    TraceEvent_Free((uint32_t)(pvAddress), (uint32_t)(uiSize))
```

install.py --project 실행 시 자동 삽입됩니다.

---

## ClaudeRTOS에서 사용하는 Hook 함수
*Hook Functions Used by ClaudeRTOS-Insight*

### 스택 오버플로우 훅
*Stack Overflow Hook*

`configCHECK_FOR_STACK_OVERFLOW=2` 설정 시 커널이 자동 호출합니다.

```c
/* fault_injection.c / 또는 사용자 파일에 구현 */
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    /* ClaudeRTOS: Critical 이벤트로 기록 후 시스템 정지 */
    TraceEvent_HardFault(FAULT_STACK_OVERFLOW);
    taskDISABLE_INTERRUPTS();
    for(;;);   /* 또는 NVIC_SystemReset() */
}
```

필요한 FreeRTOSConfig.h 설정:
```c
#define configCHECK_FOR_STACK_OVERFLOW   2   /* 패턴 + 마지막 바이트 검사 */
```

### Idle 훅 (CPU 사용률 계산용)
*Idle Hook — CPU Usage Calculation*

```c
void vApplicationIdleHook(void) {
    /* ClaudeRTOS가 Idle 태스크 실행 시간을 이용해 CPU% 계산 */
    /* 이 함수에 직접 코드를 추가할 수 있음 */
    /* 단, blocking 금지 — Idle은 항상 실행 가능 상태여야 함 */
}
```

```c
#define configUSE_IDLE_HOOK   1
```

### Malloc Fail 훅
*Malloc Fail Hook — Heap Exhaustion Detection*

```c
void vApplicationMallocFailedHook(void) {
    /* heap 고갈 시 호출 — ClaudeRTOS가 heap_exhaustion 이벤트 기록 */
    TraceEvent_MallocFail();
}
```

```c
#define configUSE_MALLOC_FAILED_HOOK   1
```

### Tick 훅 (선택)
*Tick Hook (Optional)*

```c
void vApplicationTickHook(void) {
    /* 매 FreeRTOS tick마다 호출 — 타이밍 민감 코드에만 사용 */
    /* ClaudeRTOS는 이 훅을 사용하지 않음 */
}
```

---

## Trace Macro 오버헤드
*Trace Macro Overhead — Measured Impact*

| 매크로 | 오버헤드 (추정) | 비고 |
|--------|----------------|------|
| `traceTASK_SWITCHED_IN/OUT` | ~50 cycles | CYCCNT + 링 버퍼 push |
| `traceGIVE/TAKE_MUTEX` | ~50 cycles | 동일 |
| `traceMALLOC/FREE` | ~50 cycles | 동일 |
| `vApplicationStackOverflowHook` | 무제한 | 오류 처리 후 정지 |

PROFILE_LITE 사용 시 ctx_switch 제외 모든 매크로가 `(void)0`으로 제거됩니다.

---

## 커널 파일 수정 안 하는 이유
*Why We Never Modify FreeRTOS Kernel Files*

FreeRTOS Trace Macro는 `FreeRTOSConfig.h`에서 `#define`으로 재정의하는 **공식 API**입니다. `tasks.c`, `queue.c` 등 커널 파일은 일절 수정하지 않습니다.

장점:
- FreeRTOS 버전 업그레이드 시 커널 파일만 교체하면 됨
- Amazon FreeRTOS, ESP-IDF FreeRTOS 등 파생 버전에도 동일하게 적용
- RTOS 교체 시 `insight_port_os.c` 1개 파일만 수정

---

## 전체 FreeRTOSConfig.h 최소 설정
*Minimum FreeRTOSConfig.h Settings Required*

```c
/* ClaudeRTOS-Insight 필수 설정 */
#define configUSE_TRACE_FACILITY              1   /* uxTaskNumber 활성화 */
#define configGENERATE_RUN_TIME_STATS         1   /* CPU% 계산 필수 */
#define portCONFIGURE_TIMER_FOR_RUN_TIME_STATS() DWT_Init(180000000U)
#define portGET_RUN_TIME_COUNTER_VALUE()      DWT_GetCycles()
#define configCHECK_FOR_STACK_OVERFLOW        2
#define configUSE_MALLOC_FAILED_HOOK          1
#define configUSE_IDLE_HOOK                   0   /* 선택 */

/* Trace Macro — install.py가 자동 삽입 */
#include "trace_events.h"
```
