# Firmware Demo Example
## ClaudeRTOS-Insight V2.1

**Complete working example for STM32 Nucleo-F446RE**

---

## Overview

This demo creates **4 FreeRTOS tasks** with different priorities to demonstrate:
- ✅ Real-time scheduling
- ✅ Priority inheritance
- ✅ Resource contention
- ✅ Task starvation
- ✅ Adaptive rate control

---

## Task Configuration

### Task 1: High Priority (P3)
**Period:** 100ms  
**Function:** `HighPriorityTask()`

**Behavior:**
- Toggles LED every 100ms
- Measures precise timing with DWT
- Detects timing violations (>10% deviation)
- Tests timestamp rollover handling

**ClaudeRTOS Detection:**
- Timestamp precision validation
- Timing constraint violations

---

### Task 2: Medium Priority (P2)
**Period:** 500ms  
**Function:** `MediumPriorityTask()`

**Behavior:**
- Acquires shared mutex
- Performs work in critical section
- Tests priority inheritance

**ClaudeRTOS Detection:**
- Priority inversion (if blocked by low-priority task)
- Mutex timeout events

---

### Task 3: Low Priority (P1)
**Period:** 1000ms  
**Function:** `LowPriorityTask()`

**Behavior:**
- Competes for shared mutex
- Sends data to queue
- May experience starvation

**ClaudeRTOS Detection:**
- Task starvation (ready but not running)
- Queue operations

---

### Task 4: Monitor (P4 - Highest)
**Period:** 1000ms  
**Function:** `MonitorTask()`

**Behavior:**
- Collects system statistics
- Adjusts sampling rate dynamically
- Demonstrates adaptive rate control

**Adaptive Behavior:**
- High CPU (>80%) → Slow down sampling (2× period)
- Low CPU (<40%) → Speed up sampling (0.75× period)
- Buffer full → Reduce sampling

---

## What Gets Tested

### 1. Timestamp Rollover
**Scenario:** Run for > 24 seconds @ 180MHz

**Detection:**
- DWT counter rolls over at 2^32 cycles (23.86s)
- Enhanced rollover detection activates
- Error count remains 0

**Validation:**
```c
uint32_t rollover_count = DWT_GetRolloverCount();
uint32_t error_count = DWT_GetErrorCount();

// After 30 seconds:
// rollover_count = 1 ✅
// error_count = 0 ✅
```

---

### 2. Priority Inversion
**Scenario:** Low priority task holds mutex while high priority waits

**Setup:**
1. Low priority task acquires mutex
2. Low priority task preempted by medium priority
3. High priority task blocks on mutex

**Detection:**
- ClaudeRTOS analyzer detects: "High-priority task (P3) blocked while low-priority (P1) runs"
- Severity: High
- Recommendation: Use priority inheritance protocol

---

### 3. Task Starvation
**Scenario:** Low priority task never gets CPU time

**Setup:**
1. High priority task runs frequently (100ms)
2. Medium priority task runs periodically (500ms)
3. Low priority task remains in READY state

**Detection:**
- ClaudeRTOS analyzer: "Task 1 (P1) ready but not running"
- Severity: Medium
- Recommendation: Adjust priorities or periods

---

### 4. Adaptive Rate Control
**Scenario:** System load varies over time

**Behavior:**
```
Time 0s:   CPU 30% → Rate = 1000ms (1Hz)
Time 10s:  CPU 85% → Rate = 2000ms (0.5Hz) - Slowed down
Time 20s:  CPU 35% → Rate = 1500ms (0.67Hz) - Speeding up
Time 30s:  CPU 30% → Rate = 1000ms (1Hz) - Back to normal
```

**Validation:**
```c
uint16_t current_rate = RateController_GetRate(&rate_controller);
// Varies between 100ms - 5000ms based on load
```

---

## Build Instructions

### Using Makefile

```makefile
# Makefile
PROJECT = claudertos_demo
MCU = STM32F446xx

# Sources
SOURCES = \
    main.c \
    ../../../core/dwt_timestamp.c \
    ../../../core/rate_controller.c \
    ../../../core/crc32.c \
    ../../../core/ring_buffer.c \
    FreeRTOS/tasks.c \
    FreeRTOS/queue.c \
    FreeRTOS/list.c \
    FreeRTOS/timers.c \
    FreeRTOS/portable/GCC/ARM_CM4F/port.c \
    system_stm32f4xx.c \
    startup_stm32f446xx.s

# Includes
INCLUDES = \
    -I../../../core \
    -IFreeRTOS/include \
    -IFreeRTOS/portable/GCC/ARM_CM4F \
    -ICMSIS/Include

# Build
all: $(PROJECT).elf

$(PROJECT).elf: $(SOURCES)
	arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 \
	    -mfloat-abi=hard -O2 -g $(INCLUDES) -DSTM32F446xx \
	    -o $@ $(SOURCES)

flash: $(PROJECT).elf
	openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
	    -c "program $(PROJECT).elf verify reset exit"

clean:
	rm -f $(PROJECT).elf
```

### Using STM32CubeIDE

1. Create new STM32 project for Nucleo-F446RE
2. Add FreeRTOS middleware
3. Copy ClaudeRTOS core files to project
4. Add `main.c` to Src/
5. Add include paths
6. Build & Flash

---

## Expected Output

### Serial Output (if printf enabled)
```
High: 100, Med: 20, Low: 10, Rate: 1000 ms
High: 200, Med: 40, Low: 20, Rate: 1000 ms
High: 300, Med: 60, Low: 30, Rate: 1000 ms
...
High: 1000, Med: 200, Low: 95, Rate: 2000 ms  (Rate slowed due to high CPU)
```

### ClaudeRTOS Host Analysis
```bash
python examples/integrated_demo.py --source jlink --duration 60
```

**Expected Issues Detected:**
```json
{
  "issues": [
    {
      "severity": "Medium",
      "type": "task_starvation",
      "description": "Task 1 (P1) ready but not running",
      "affected_tasks": [1]
    },
    {
      "severity": "High",
      "type": "priority_inversion",
      "description": "High-priority task blocked by low-priority mutex holder"
    }
  ]
}
```

---

## Testing Scenarios

### Scenario 1: Normal Operation
**Duration:** 10 seconds  
**Expected:**
- All tasks running
- No errors
- Stable sampling rate (1Hz)

### Scenario 2: High CPU Load
**Trigger:** Increase work in high-priority task  
**Expected:**
- CPU usage > 80%
- Sampling rate increases to 2000ms (0.5Hz)
- System remains stable

### Scenario 3: Rollover Test
**Duration:** 30 seconds  
**Expected:**
- DWT rollover occurs at ~24s
- Rollover count = 1
- Error count = 0
- No timestamp discontinuity

### Scenario 4: Stress Test
**Duration:** 72 hours  
**Expected:**
- No buffer overflows
- No CRC errors
- No stack overflows
- Heap stable

---

## Troubleshooting

### Issue: Tasks not running
**Check:**
```c
// In main()
configASSERT(shared_mutex != NULL);
configASSERT(data_queue != NULL);
```

### Issue: Stack overflow
**Solution:** Increase stack size
```c
xTaskCreate(HighPriorityTask, "High", 512, NULL, 3, NULL);  // 256 → 512
```

### Issue: Heap exhausted
**Check:** FreeRTOSConfig.h
```c
#define configTOTAL_HEAP_SIZE  (64 * 1024)  // Increase if needed
```

---

## Hardware Requirements

- STM32 Nucleo-F446RE
- USB cable
- J-Link or ST-Link debugger
- LED connected to PA5 (onboard LED)

---

## Software Requirements

- arm-none-eabi-gcc 12.2+
- FreeRTOS v10.5.1
- STM32 HAL v1.28.0
- OpenOCD or STM32CubeIDE

---

## Next Steps

1. Build and flash the demo
2. Run for 60 seconds
3. Collect data with Python host
4. Analyze with ClaudeRTOS
5. Review AI-generated recommendations

---

**Happy Testing!** 🚀
