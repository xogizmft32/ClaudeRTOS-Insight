# Testing Guide
## ClaudeRTOS-Insight

**Complete testing procedures for SIL4 validation**

---

## Test Environment

### Hardware Requirements

**✅ You Have:**
- Ubuntu Server (20.04+)
- Windows PC (10+)
- STM32 Nucleo-F446RE
- ST-Link or J-Link

**Recommended Setup:**
- J-Link EDU Mini ($60) - Better SWO performance
- USB 2.0 connection (avoid USB 3.0 hubs)

---

## Software Setup

### Ubuntu Server

```bash
# Install dependencies
sudo apt update
sudo apt install python3.10 python3-pip build-essential

# Install Python packages
pip3 install anthropic pylink-square pyserial numpy pytest

# Install ARM toolchain
wget https://developer.arm.com/-/media/Files/downloads/gnu/12.2.rel1/binrel/arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi.tar.xz
tar xf arm-gnu-toolchain-12.2.rel1-x86_64-arm-none-eabi.tar.xz
export PATH=$PATH:$PWD/arm-gnu-toolchain-12.2.rel1/bin

# Verify
arm-none-eabi-gcc --version
```

### Windows PC

```powershell
# Install Python 3.10+
# Download from python.org

# Install packages
pip install anthropic pylink-square pyserial

# Install STM32CubeIDE or standalone ARM toolchain
```

---

## Test Suite

### 1. Firmware WCET Tests

**Purpose:** Verify worst-case execution times

**Location:** `tests/test_wcet.c`

**Build:**
```bash
cd firmware/
make clean
make test_wcet
```

**Flash & Run:**
```bash
make flash_test
# Or use STM32CubeIDE
```

**Expected Output:**
```
=== WCET Test Suite ===
Target: STM32F446RE @ 180 MHz
Iterations: 10000

CRC32_Calculate(512B)
  Min: 6650 cycles (36.94 us)
  Avg: 6700 cycles (37.22 us)
  Max: 6750 cycles (37.50 us)
  WCET Guarantee: 60.00 us
  ✅ PASS (margin: 37.5%)

DWT_GetTimestamp_us()
  Min: 120 cycles (0.67 us)
  Avg: 130 cycles (0.72 us)
  Max: 140 cycles (0.78 us)
  WCET Guarantee: 2.00 us
  ✅ PASS (margin: 61.0%)

RingBuffer_Write(512B)
  Min: 6800 cycles (37.78 us)
  Avg: 6900 cycles (38.33 us)
  Max: 7000 cycles (38.89 us)
  WCET Guarantee: 60.00 us
  ✅ PASS (margin: 35.2%)
```

**✅ All tests should PASS**

---

### 2. Python Unit Tests

**Purpose:** Verify host software layers

**Location:** `tests/test_host.py`

**Run:**
```bash
cd host/
pytest tests/test_host.py -v
```

**Expected:**
```
test_binary_parser.py::test_crc_verify PASSED
test_binary_parser.py::test_parse_header PASSED
test_binary_parser.py::test_parse_os_snapshot PASSED
test_event_model.py::test_task_info PASSED
test_event_model.py::test_os_snapshot PASSED
test_analyzer.py::test_stack_overflow_detection PASSED
test_analyzer.py::test_heap_exhaustion PASSED
test_analyzer.py::test_priority_inversion PASSED

============= 8 passed in 0.15s =============
```

---

### 3. Integration Test

**Purpose:** End-to-end validation

**Location:** `examples/integrated_demo.py`

**Run:**
```bash
# Set API key
export ANTHROPIC_API_KEY=your-key-here

# Run demo
python examples/integrated_demo.py --source jlink --duration 60

# Or with ST-Link
python examples/integrated_demo.py --source openocd --duration 60
```

**Expected Output:**
```
ClaudeRTOS-Insight  Integration Demo
=========================================

[1/6] Connecting to J-Link...
✅ Connected to STM32F446RE

[2/6] Collecting data (60 seconds)...
  Packets: 58 | Bytes: 4872 | Rate: 1.0 pkt/s
✅ Collected 58 packets

[3/6] Parsing packets...
  OS Snapshots: 58 | Task Events: 0 | CRC Errors: 0
✅ Parsed successfully

[4/6] Analyzing system state...
  Issues found: 2
    - High: Low heap (3840 bytes free)
    - Medium: Task 3 starvation
✅ Analysis complete

[5/6] AI-powered diagnosis (using Sonnet)...
  Model: claude-sonnet-4-20250514
  Cost: $0.05
✅ AI analysis complete

[6/6] Generating report...
✅ Report saved: report_20260313_1530.json

===========================================
Test Complete: ✅ ALL PASSED
```

---

### 4. Stress Test

**Purpose:** 72-hour continuous operation

**Setup:**
```c
// firmware/examples/stress_test/main.c

void stress_test(void) {
    // Create 10 tasks with varying priorities
    for (int i = 0; i < 10; i++) {
        xTaskCreate(TestTask, "Task", 256, (void*)i, i+1, NULL);
    }
    
    // Enable all monitoring
    ClaudeRTOS_RegisterModule(&os_monitor_module);
    ClaudeRTOS_RegisterModule(&task_tracer_module);
    
    // Run for 72 hours
    vTaskStartScheduler();
}
```

**Monitor:**
```bash
python examples/monitor_72h.py --source jlink
```

**Success Criteria:**
- ✅ No buffer overflows
- ✅ No CRC errors
- ✅ No timestamp errors
- ✅ CPU usage < 1%
- ✅ Heap stable

---

## Validation Checklist

### Firmware Tests

- [ ] WCET test passes (all functions < guarantee)
- [ ] CRC32 verification (100% accuracy)
- [ ] Rollover handling (tested at boundaries)
- [ ] Buffer overflow (tested with full buffer)
- [ ] Rate control (CPU 0-100%, buffer 0-100%)

### Host Tests

- [ ] Binary parser (all packet types)
- [ ] CRC verification (detect corrupted packets)
- [ ] Event model (type safety)
- [ ] Analyzer rules (all 5 rules)
- [ ] AI interface (3 models)

### Integration Tests

- [ ] End-to-end workflow
- [ ] J-Link collection
- [ ] OpenOCD collection (if available)
- [ ] AI analysis
- [ ] Report generation

### Stress Tests

- [ ] 72-hour continuous run
- [ ] High CPU load (>90%)
- [ ] Low memory (<10%)
- [ ] Buffer stress (frequent overflow)

---

## Troubleshooting

### Issue: J-Link not found

**Solution:**
```bash
# Check device
lsusb | grep SEGGER

# Install udev rules
sudo cp /opt/SEGGER/JLink/99-jlink.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

### Issue: CRC errors

**Possible Causes:**
- SWO frequency mismatch
- USB signal integrity
- Buffer overflow

**Solution:**
```python
# Reduce SWO speed
collector.swo_speed = 1125000  # Half speed
```

### Issue: WCET test fails

**Investigation:**
```c
// Enable detailed logging
#define WCET_DEBUG 1

// Check optimization level
// Must use -O2 for production
```

---

## Test Tools Summary

| Tool | Purpose | Platform | Required |
|------|---------|----------|----------|
| arm-none-eabi-gcc | Firmware build | Ubuntu/Windows | ✅ Yes |
| pytest | Python tests | Ubuntu/Windows | ✅ Yes |
| J-Link | Data collection | Both | Recommended |
| STM32CubeIDE | Development | Windows | Optional |

---

## Certification Evidence

For SIL4 certification, collect:

1. **WCET Test Results** (tests/wcet_results.txt)
2. **Unit Test Coverage** (>95%)
3. **Integration Test Logs** (72-hour run)
4. **Static Analysis Report** (MISRA C)
5. **Hardware Test Report** (STM32F446RE)

Store in `certification/` directory.

---

**Last Updated:** 2026-03-13  
