# ClaudeRTOS-Insight — Quick Start Guide

**Goal:** Install → Build → Flash → Connect Host → AI Debug  
**Time:** ~20 minutes  
**AI cost:** ~$0.015/issue (postmortem), $0 with Ollama

> This project was started via **Vibe Coding** — natural language intent → AI-generated code.  
> See [README.md](../README.md#about-vibe-coding) for details.

---

## Prerequisites

### Hardware
- STM32 Nucleo-F446RE (or any STM32F4xx)
- USB cable
- (Recommended) J-Link EDU Mini for high-speed SWO

### Software (Linux/Ubuntu)
```bash
sudo apt install gcc-arm-none-eabi make python3 python3-pip
pip3 install anthropic   # or: openai / google-generativeai
```

---

## Step 1: Extract & Install

```bash
tar -xzf ClaudeRTOS-Insight--FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0   # extracted directory name

# Auto-integrate into your project
python3 install.py --project /path/to/your_project

# UART mode
python3 install.py --project /path/to/your_project --transport uart

# Verify installation
python3 install.py --check /path/to/your_project
```

Auto-installed items:
- 24 ClaudeRTOS source files → `project/claudertos/`
- `FreeRTOSConfig.h` auto-patched (7 settings + trace hooks)
- `CLAUDERTOS_TRACE_ENABLED` guard added

---

## Step 2: Add to main.c

```c
#include "os_monitor_v3.h"
#include "transport.h"
#include "trace_events.h"   // Trace V2 (lock-free, DWT CYCCNT)

int main(void) {
    HAL_Init();
    SystemClock_Config();

    DWT_Init(180000000U);        // enable DWT CYCCNT + EXCCNT
    Transport_Init(180000000U);
    OSMonitorV3_Init();
    TraceEvents_Init();          // lock-free ring buffer init

    s_mutex = xSemaphoreCreateMutex();
    TraceEvents_RegisterMutex(s_mutex, "AppMutex");  // named mutex

    vTaskStartScheduler();
}
```

### Trace Mode (trace_config.h)

| Flag | Mode | RAM | Overhead |
|------|------|-----|---------|
| (default) | FULL — all events | 4KB | 0.028% CPU |
| `-DCLAUDERTOS_TRACE_MODE=1` | STAT — counters only | 28B | ~0 |
| `-DCLAUDERTOS_TRACE_MODE=2` | OFF — zero overhead | 0B | 0 |
| `-DTRACE_SAMPLE_RATE=4` | FULL, 1-in-4 sampling | 4KB | 0.007% CPU |

---

## Step 3: Build & Flash

```bash
cd firmware/examples/demo/
make -j4
make flash          # J-Link
make flash-stlink   # ST-Link (Nucleo built-in)
```

Expected output (SWO or serial):
```
ClaudeRTOS-Insight  Started [ITM]
```

---

## Step 4: Connect Host

```bash
# Protocol validation (no hardware needed)
python3 examples/integrated_demo.py --validate

# J-Link ITM
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink

# UART
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0

# AI mode
python3 examples/integrated_demo.py --port jlink --ai-mode postmortem  # default
python3 examples/integrated_demo.py --port jlink --ai-mode offline     # no AI
```

---

## Step 5: AI Provider Selection

```bash
# Switch AI without changing code
export CLAUDERTOS_AI_PROVIDER=anthropic   # Claude (default)
export CLAUDERTOS_AI_PROVIDER=openai      # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google      # Gemini
export CLAUDERTOS_AI_PROVIDER=ollama      # Local, $0 cost
```

See `docs/AI_USAGE_GUIDE.md` for full details.

---

## Step 6: Understand Results

### AI Call Timing (postmortem default)
```
Issue detected 1st → local display only
Issue detected 2nd → local display only
Issue detected 3rd → AI_READY → Claude/GPT/Gemini called
Issue 4th+         → cache returned (24h TTL)
```

### Analysis Pipeline Output
```
[Rule]        stack_overflow_imminent: HighTask hwm=15W
[Resource]    RG-001 DEADLOCK: Task0↔Task1 cycle (conf=0.95) ★교차검증
[Context]     resources.mutex_holds: Task0→Mutex1, Task1→Mutex2
[CausalGraph] root_cause: Deadlock cycle → HighTask blocked

🔴 [Critical] stack_overflow_imminent — HighTask
   HighTask 스택 오버플로우 임박 (15 words = 60 bytes)
   근본 원인 (신뢰도 91%): ISR 콜백에서 재귀 호출로 스택 소진
   인과 체인: mutex_take → recursive_cb → stack_exhaustion → hwm=15W
   수정:
     파일: main.c:267
     Before: xTaskCreate(..., 256, ...);
     After:  xTaskCreate(..., 512, ...);
```

### Zero-Cost Local Diagnosis (Pattern DB)

| Pattern | Trigger | Cost |
|---------|---------|------|
| KP-001: Mutex Timeout → Priority Inversion | mutex_timeout + priority_inversion | $0 |
| KP-002: Repeated Malloc → Fragmentation | malloc×5 + low_heap | $0 |
| KP-003: Stack HWM Critical | stack_hwm < 20W | $0 |
| KP-004: ISR malloc (Forbidden) | isr_enter → malloc | $0 |
| KP-005: CPU + Heap Saturation | cpu_creep + heap_shrink | $0 |

Add custom: `host/patterns/custom_patterns.json`

---

## FAQ

**Q: Does it work without trace hooks?**  
A: Yes. OS snapshot (CPU%, heap, stack HWM) works without any hooks. Trace is optional.

**Q: How to add custom patterns?**  
A: Create `host/patterns/custom_patterns.json` with the same schema as `known_patterns.json`.

**Q: How is timestamp normalized?**  
A: `TimeNormalizer` converts DWT CYCCNT (cycles), RTOS tick (uptime_ms), and packet timestamp_us into a unified µs timeline for accurate event ordering.

---

**Target:** STM32F446RE @ 180MHz | **RTOS:** FreeRTOS 10.0+  
**Validation:** 20/20 PASS | **Protocol:** Binary V4 (field-based, V3 compatible)  
**Started with:** Vibe Coding × Claude
