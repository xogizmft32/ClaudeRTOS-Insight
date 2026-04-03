# ClaudeRTOS 트레이스 가이드 V2 (한국어)

> 영문 버전: `docs/TRACE_GUIDE.md`

> ClaudeRTOS-Insight — 바이브 코딩 × Claude로 시작된 프로젝트입니다.

## 개요

ClaudeRTOS V4.2는 **DWT 하드웨어 카운터**로 오버헤드 없이 ISR 빈도를 측정하고,
**소프트웨어 hook**으로 태스크 전환과 Mutex 타이밍을 추적합니다.

| 방법 | 오버헤드 | 얻을 수 있는 정보 |
|------|---------|----------------|
| DWT EXCCNT (하드웨어) | **0 cycles** | 샘플 구간 ISR 진입 횟수 |
| DWT CYCCNT (타임스탬프) | **3 cycles/이벤트** | 나눗셈 없는 정밀 타임스탬프 |
| FreeRTOS hook (소프트웨어) | **~25 cycles/이벤트** | 컨텍스트 전환 순서, Mutex 타이밍 |

컨텍스트 스위치 1kHz 기준 **총 CPU 오버헤드: 0.028%**

---

## 빠른 시작 — FreeRTOSConfig.h에 3줄

`install.py`가 자동으로 추가합니다. 수동 활성화:

```c
#define CLAUDERTOS_TRACE_ENABLED  1
#include "trace_events.h"

/* 태스크 컨텍스트 전환 */
#define traceTASK_SWITCHED_IN()   TraceEvent_ContextSwitchIn()
#define traceTASK_SWITCHED_OUT()  TraceEvent_ContextSwitchOut()

/* Mutex */
#define traceTAKE_MUTEX(m, t)        TraceEvent_MutexTake((m),(t))
#define traceGIVE_MUTEX(m)           TraceEvent_MutexGive((m))
#define traceTAKE_MUTEX_FAILED(m, t) TraceEvent_MutexTimeout((m))
```

ISR 추적은 자동입니다 — ISR 핸들러에 코드 삽입 불필요.

---

## 트레이스 모드 (trace_config.h)

컴파일 플래그 `-DCLAUDERTOS_TRACE_MODE=N`으로 선택:

| 모드 | 플래그 | RAM | CPU/이벤트 | 동작 내용 |
|------|--------|-----|-----------|---------|
| **FULL** | 0 (기본) | 4KB | ~25 cycles | 전체 이벤트 + ISR 통계 |
| **STAT** | 1 | 28B | ~3 cycles | 카운터만, 링 버퍼 없음 |
| **OFF** | 2 | 0B | 0 | DWT EXCCNT ISR 카운트만 동작 |

```bash
make CFLAGS="-DCLAUDERTOS_TRACE_MODE=1"   # STAT: 최소 풋프린트
make CFLAGS="-DTRACE_SAMPLE_RATE=4"       # FULL, 4번 중 1번 샘플링
```

---

## V2 핵심 개선 사항

### 1. Lock-free 링 버퍼 (Critical Section 제거)

```
이전: taskENTER_CRITICAL_FROM_ISR()  ~18 cycles
      s_ring[idx] = ev                ~4 cycles
      taskEXIT_CRITICAL_FROM_ISR()    ~8 cycles
      합계: ~46 cycles/이벤트

이후: LDREX/STREX (원자적 슬롯 예약)   ~6 cycles
      s_ring[idx] = ev                  ~4 cycles
      DMB                               ~3 cycles
      합계: ~25 cycles/이벤트  (46% 절감)
```

**ISR 레이턴시 영향: 없음** — 인터럽트 마스킹 없음.

### 2. DWT CYCCNT 타임스탬프 (나눗셈 없음)

```c
// 이전: DWT_GetTimestamp_us()  ~10 cycles (나눗셈 포함)
// 이후: TRACE_DWT_CYCCNT        ~3 cycles (LDR 1개)

ev->timestamp_cycles = TRACE_DWT_CYCCNT;
// 호스트에서 변환: µs = cycles / (cpu_hz / 1_000_000)
```

### 3. DWT EXCCNT ISR 빈도 (오버헤드 없음)

```c
// MonitorTask에서 (1Hz):
uint32_t isr_delta = TraceEvents_SampleISRCount();
// 반환값: 마지막 호출 이후 ISR 진입 횟수
// 비용: 3 cycles (LDR 1개)
```

어떤 ISR 핸들러에도 코드를 삽입하지 않습니다.
Cortex-M DWT EXCCNT 레지스터가 예외 진입마다 하드웨어로 자동 증가합니다.

---

## 수집 이벤트

| 이벤트 | Hook | 오버헤드 |
|--------|------|---------|
| `ctx_switch_in` | `traceTASK_SWITCHED_IN` | ~25 cycles |
| `ctx_switch_out` | `traceTASK_SWITCHED_OUT` | ~25 cycles |
| `mutex_take` | `traceTAKE_MUTEX` | ~25 cycles |
| `mutex_give` | `traceGIVE_MUTEX` | ~25 cycles |
| `mutex_timeout` | `traceTAKE_MUTEX_FAILED` | ~25 cycles |
| `malloc` | 래퍼 함수 | ~25 cycles |
| `free` | 래퍼 함수 | ~25 cycles |
| ISR 빈도 | DWT EXCCNT (HW) | **0 cycles** |

**미수집 항목** (개별 ISR hook 제거):
- ISR 개별 타이밍 (ETM 하드웨어 또는 수동 삽입 필요)
- 함수 진입/종료 (`-finstrument-functions`은 WCET 파괴)

---

## 호스트 JSON 컨텍스트

트레이스 데이터는 `session.isr`과 `timeline[]`에 포함됩니다:

```json
{
  "session": {
    "cpu_hz": 180000000,
    "isr": {
      "count_per_sample": 42,
      "ctx_switches": 18,
      "mutex_timeouts": 2,
      "trace_overflows": 0
    }
  },
  "timeline": [
    {"t_us": 1001000, "type": "mutex_take", "mutex_name": "AppMutex", "wait_ticks": 100},
    {"t_us": 1001500, "type": "mutex_timeout", "mutex_name": "AppMutex"},
    {"t_us": 1500000, "type": "malloc", "size": 128, "ptr": "0x20003000"}
  ]
}
```

`t_us`는 `timestamp_cycles`에서 `cpu_hz`로 변환합니다:
```python
t_us = timestamp_cycles * 1_000_000 // cpu_hz
```

---

## 오버헤드 예산 요약

```
컨텍스트 스위치 1kHz:
  2 이벤트 × 25 cycles = 50 cycles/ms
  50 / 180,000 = 0.028% CPU

ISR 샘플링 1Hz (DWT EXCCNT):
  3 cycles / 1,000ms = 0.000002% CPU  ≈ 0

Mutex (낮은 빈도):
  25 cycles / lock-unlock  ≈ 0

링 버퍼 RAM: 256 × 16B = 4KB
  오버플로 시: 오래된 이벤트 덮어쓰기, overflow_count 추적
```
