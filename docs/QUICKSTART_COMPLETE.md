# ClaudeRTOS-Insight V3.9.1 — Quick Start Guide

**Goal:** Install → Build → Flash → Connect Host → AI Debug  
**Time:** ~20 minutes (with auto-installer)  
**AI analysis cost:** ~$0.015/issue (postmortem mode default)

---

## Prerequisites

### Hardware
- STM32 Nucleo-F446RE (or any STM32F4xx)
- USB cable
- (Recommended) J-Link EDU Mini — for high-speed SWO

### Software (Linux/Ubuntu)
```bash
sudo apt install gcc-arm-none-eabi make python3 python3-pip
```

---

## Step 1: Auto-Install

```bash
tar -xzf ClaudeRTOS-Insight-v3.9.1-FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0

# Auto-integrate into your project (ITM mode)
python3 install.py --project /path/to/your_project

# UART mode
python3 install.py --project /path/to/your_project --transport uart

# Check installation
python3 install.py --check /path/to/your_project
```

Auto-installed items:
- 24 ClaudeRTOS source files → `project/claudertos/`
- `FreeRTOSConfig.h` 7 required settings auto-patched (backup created)
- CMake / Makefile integration snippets generated

---

## Step 2: Add 3 Lines to main.c

```c
#include "os_monitor_v3.h"
#include "transport.h"
#include "trace_config.h"   // Lightweight trace (optional)

int main(void) {
    HAL_Init();
    SystemClock_Config();

    // Initialize before scheduler (heap_total boot cache)
    DWT_Init(180000000U);
    Transport_Init(180000000U);
    OSMonitorV3_Init();
    TraceEvents_Init();   // optional

    vTaskStartScheduler();
}
```

### Trace Mode Selection (trace_config.h / compiler flags)

| Flag | Mode | RAM | CPU Impact |
|------|------|-----|-----------|
| (default) | FULL — ring buffer, all events | 4KB | ~50 cycles/event |
| `-DCLAUDERTOS_TRACE_MODE=1` | STAT — counters only | 28B | ~3 cycles/event |
| `-DCLAUDERTOS_TRACE_MODE=2` | OFF — zero overhead | 0B | 0 |
| `-DTRACE_SAMPLE_RATE=4` | FULL, 1-in-4 sampling | 4KB | ~12 cycles/event |

### Lightweight Trace Without Hook (DWT Hardware)
```c
// No FreeRTOS hook needed — DWT EXCCNT counts ISR entries automatically
uint32_t isr_count = TRACE_DWT_ISR_COUNT();  // hardware counter, zero overhead
```

---

## Step 3: Build

```bash
cd firmware/examples/demo/

# ITM mode (default)
make -j4

# UART mode
make -j4 TRANSPORT=UART

# Stat-only trace (minimal RAM/CPU)
make -j4 CFLAGS="-DCLAUDERTOS_TRACE_MODE=1"
```

---

## Step 4: Flash

```bash
make flash          # J-Link
make flash-stlink   # ST-Link (Nucleo built-in)
```

Expected output (SWO or serial at 115200):
```
ClaudeRTOS-Insight V3.9.1.0 Started [ITM]
```

---

## Step 5: Connect Host

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# Protocol validation (no hardware needed)
python3 examples/integrated_demo.py --validate

# J-Link ITM
python3 examples/integrated_demo.py --port jlink

# UART
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0

# AI mode selection
python3 examples/integrated_demo.py --port jlink --ai-mode offline
python3 examples/integrated_demo.py --port jlink --ai-mode postmortem  # default
python3 examples/integrated_demo.py --port jlink --ai-mode realtime
```

---

## Step 6: Understand AI Debug Results

### AI Call Timing (postmortem default)
```
Issue detected 1st time  →  [local display only]
Issue detected 2nd time  →  [local display only]
Issue detected 3rd time  →  [AI_READY] ← Claude API called here
Issue detected 4th+      →  cache returned (no re-call, 24h TTL)
```

### Pattern DB — Zero-Cost Local Diagnosis
Known patterns are diagnosed locally before calling Claude:

| Pattern | Trigger | Cost |
|---------|---------|------|
| KP-001: Mutex Timeout → Priority Inversion | mutex_timeout + priority_inversion | $0 |
| KP-002: Repeated Malloc → Fragmentation | malloc × 5 + low_heap | $0 |
| KP-003: Stack HWM Critical | stack_hwm < 20W | $0 |
| KP-004: ISR malloc (Forbidden) | isr_enter → malloc | $0 |
| KP-005: CPU + Heap Saturation | cpu_creep + heap_shrink | $0 |

Add custom patterns: `host/patterns/custom_patterns.json`

### AI Output (Structured JSON → Human Readable)
```
🔴 [Critical] stack_overflow_imminent — DataProcessor
   DataProcessor 스택 오버플로우 임박 (14 words = 56 bytes)
   근본 원인 (신뢰도 85%): 재귀 호출 깊이가 256 words 스택을 초과
   인과 체인: malloc(128) → recursive_call → stack_exhaustion → hwm=14W
   수정:
     파일: main.c:249
     Before: xTaskCreate(..., 256, ...);
     After:  xTaskCreate(..., 512, ...);
```

---

---

## AI Provider Selection

Switch AI backend with one environment variable:

```bash
export CLAUDERTOS_AI_PROVIDER=anthropic   # default (Claude)
export CLAUDERTOS_AI_PROVIDER=openai      # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google      # Gemini
export CLAUDERTOS_AI_PROVIDER=ollama      # local, $0 cost
```

See `docs/AI_USAGE_GUIDE.md` for details.

## FAQ

**Q: Does it work without `trace_events.h` hooks?**  
A: Yes. OS snapshot collection (CPU%, heap, stack HWM) works without any hooks. Trace is optional and adds timeline events for deeper analysis.

**Q: How to add custom known patterns?**  
A: Create `host/patterns/custom_patterns.json` following the same schema as `known_patterns.json`. It is auto-loaded and takes precedence over built-in patterns.

**Q: Which AI mode should I use?**  
A: See `docs/AI_USAGE_GUIDE.md` for details. Summary: production=`offline`, debugging=`postmortem` (default), dev fast feedback=`realtime`.

---

**Version:** 3.8.0 | **Target:** STM32F446RE @ 180MHz | **RTOS:** FreeRTOS 10.0+  
**Validation:** 20/20 PASS | **AI Cost:** ~$0.015/issue  
**Protocol:** Binary V4 (field-based, endian-explicit, backward-compatible with V3)
