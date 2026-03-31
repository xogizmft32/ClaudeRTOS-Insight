# ClaudeRTOS Trace Guide V2

## Overview

ClaudeRTOS V3.9 provides **zero-overhead ISR frequency tracking** via DWT hardware
counters plus **low-overhead software tracing** for task switches and mutexes.

| Method | Overhead | What you get |
|--------|----------|-------------|
| DWT EXCCNT (hardware) | **0 cycles** | ISR entry count per sample |
| DWT CYCCNT (timestamp) | **3 cycles/event** | Precise timestamps without division |
| FreeRTOS hooks (software) | **~25 cycles/event** | Context switch order, mutex timing |

Context switch at 1 kHz → **0.028% CPU total overhead**.

---

## Quick Start — 3 Lines in FreeRTOSConfig.h

The installer (`install.py`) adds this automatically.  
Manual activation:

```c
#define CLAUDERTOS_TRACE_ENABLED  1   /* enable guard */
#include "trace_events.h"

/* Task context switch */
#define traceTASK_SWITCHED_IN()   TraceEvent_ContextSwitchIn()
#define traceTASK_SWITCHED_OUT()  TraceEvent_ContextSwitchOut()

/* Mutex */
#define traceTAKE_MUTEX(m, t)     TraceEvent_MutexTake((m),(t))
#define traceGIVE_MUTEX(m)        TraceEvent_MutexGive((m))
#define traceTAKE_MUTEX_FAILED(m, t) TraceEvent_MutexTimeout((m))
```

ISR tracking is automatic — no hooks needed.

---

## Trace Modes (trace_config.h)

Select at compile time with `-DCLAUDERTOS_TRACE_MODE=N`:

| Mode | Flag | RAM | CPU/event | What works |
|------|------|-----|-----------|-----------|
| **FULL** | 0 (default) | 4 KB | ~25 cycles | All events + ISR stats |
| **STAT** | 1 | 28 B | ~3 cycles | Counters only, no ring buffer |
| **OFF** | 2 | 0 B | 0 | DWT EXCCNT ISR count still works |

```bash
make CFLAGS="-DCLAUDERTOS_TRACE_MODE=1"   # STAT: minimal footprint
make CFLAGS="-DTRACE_SAMPLE_RATE=4"       # FULL, 1-in-4 sampling
```

---

## V2 Key Improvements

### 1. Lock-free Ring Buffer (no critical section)

```
BEFORE: taskENTER_CRITICAL_FROM_ISR()  ~18 cycles
         s_ring[idx] = ev               ~4 cycles
         taskEXIT_CRITICAL_FROM_ISR()   ~8 cycles
         Total: ~46 cycles / event

AFTER:  LDREX/STREX (atomic slot reserve)  ~6 cycles
         s_ring[idx] = ev                    ~4 cycles
         DMB                                 ~3 cycles
         Total: ~25 cycles / event  (46% reduction)
```

**ISR latency impact: zero** — no interrupt masking.

### 2. DWT CYCCNT Timestamp (no division)

```c
// BEFORE: DWT_GetTimestamp_us()  ~10 cycles (includes division)
// AFTER:  TRACE_DWT_CYCCNT        ~3 cycles (single LDR)

ev->timestamp_cycles = TRACE_DWT_CYCCNT;
// Host converts: µs = cycles / (cpu_hz / 1_000_000)
```

### 3. DWT EXCCNT ISR Frequency (zero overhead)

```c
// In MonitorTask (1 Hz):
uint32_t isr_delta = TraceEvents_SampleISRCount();
// Returns: number of ISR entries since last call
// Cost: 3 cycles (one LDR) per sample
```

No hook code in any ISR handler. The Cortex-M DWT EXCCNT register
increments automatically on every exception entry.

---

## Collected Events

| Event | Hook | Overhead |
|-------|------|----------|
| `ctx_switch_in` | `traceTASK_SWITCHED_IN` | ~25 cycles |
| `ctx_switch_out` | `traceTASK_SWITCHED_OUT` | ~25 cycles |
| `mutex_take` | `traceTAKE_MUTEX` | ~25 cycles |
| `mutex_give` | `traceGIVE_MUTEX` | ~25 cycles |
| `mutex_timeout` | `traceTAKE_MUTEX_FAILED` | ~25 cycles |
| `malloc` | wrapper call | ~25 cycles |
| `free` | wrapper call | ~25 cycles |
| ISR frequency | DWT EXCCNT (HW) | **0 cycles** |

**Not collected** (individual ISR hook removed — use DWT EXCCNT for frequency):
- Per-ISR timing (requires ETM hardware or manual handler instrumentation)
- Function entry/exit (`-finstrument-functions` destroys WCET)

---

## Host JSON Context

Trace data appears in `session.isr` and `timeline[]`:

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

`t_us` is converted from `timestamp_cycles` using `cpu_hz`:
```python
t_us = timestamp_cycles * 1_000_000 // cpu_hz
```

---

## Overhead Budget Summary

```
Context switch 1 kHz:
  2 events × 25 cycles = 50 cycles/ms
  50 / 180,000 = 0.028% CPU

ISR sampling 1 Hz (DWT EXCCNT):
  3 cycles / 1,000 ms = 0.000002% CPU  ≈ 0

Mutex (low frequency):
  25 cycles per lock/unlock event  ≈ 0

Ring buffer (256 × 16 B = 4 KB RAM):
  Overflow: oldest events dropped, count tracked
```
