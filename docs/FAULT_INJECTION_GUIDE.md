# Fault Injection Testing Guide
## Automated Error Handling Verification

**Purpose:** Verify system behavior under fault conditions

---

## Overview

The Fault Injection framework enables automated testing of error handling capabilities by intentionally triggering faults and verifying system response.

**Key Features:**
- ✅ Automated fault injection
- ✅ Detection time measurement
- ✅ Recovery verification
- ✅ Critical event capture validation
- ✅ Python automation support

---

## Quick Start

### Firmware Integration

```c
#include "fault_injection.h"
#include "priority_buffer_v4.h"  // V4 (+)

void run_fault_tests(void) {
    // Initialize framework
    FaultInjectionConfig_t config = {
        .enable_recovery = true,
        .timeout_ms = 5000,
        .capture_events = true,
        .verbose = true
    };
    FaultInjection_Init(&config);
    
    // Run all tests
    FaultInjectionResult_t results[FAULT_MAX];
    uint32_t passed = FaultInjection_RunAllTests(results);
    
    printf("Tests passed: %lu\n", passed);
}
```

### Python Automation

```bash
# Run all tests
python3 fault_injection_tester.py /dev/ttyUSB0

# Run specific test
python3 fault_injection_tester.py /dev/ttyUSB0 --test HEAP

# Export results
python3 fault_injection_tester.py /dev/ttyUSB0 --export results.json
```

---

## Supported Fault Types

### 1. Heap Exhaustion ✅ Safe

**Purpose:** Verify heap exhaustion detection and recovery

**Test:**
```c
FaultInjection_HeapExhaustion(&result);
```

**Expected Behavior:**
- ✅ Allocations fail when heap exhausted
- ✅ System detects low heap condition
- ✅ Critical event captured in buffer
- ✅ System recovers after freeing memory

**Verification:**
```
Fault Detected: YES
Detection Time: 150 ms
System Recovered: YES
Recovery Time: 200 ms
Critical Event Captured: YES
Critical Drops: 0 ✅
```

---

### 2. Division by Zero ✅ Safe

**Purpose:** Verify arithmetic error handling

**Test:**
```c
FaultInjection_DivisionByZero(&result);
```

**Expected Behavior:**
- ⚠️ ARM Cortex-M does NOT fault on divide-by-zero
- ✅ Returns 0 or undefined value
- ✅ System continues normally

**Note:** This is a platform-specific behavior test.

---

### 3. Mutex Deadlock ✅ Safe

**Purpose:** Verify deadlock detection with timeouts

**Test:**
```c
FaultInjection_Deadlock(&result);
```

**Expected Behavior:**
- ✅ Mutex timeout triggers detection
- ✅ Task unblocks after timeout
- ✅ System recovers gracefully

**Configuration:**
```c
// Use timeout instead of portMAX_DELAY
xSemaphoreTake(mutex, pdMS_TO_TICKS(1000)); // 1 second timeout
```

---

### 4. Buffer Overflow ✅ Safe

**Purpose:** Verify buffer overflow detection with canaries

**Test:**
```c
FaultInjection_BufferOverflow(&result);
```

**Expected Behavior:**
- ✅ Canary values detect corruption
- ✅ System identifies overflow
- ✅ No critical drops

**Implementation:**
```c
uint32_t canary_before = 0xDEADBEEF;
uint8_t buffer[10];
uint32_t canary_after = 0xCAFEBABE;

// Write past end
for (int i = 0; i < 20; i++) {
    buffer[i] = 0xFF;  // Overflow!
}

// Check canaries
if (canary_before != 0xDEADBEEF || canary_after != 0xCAFEBABE) {
    // Overflow detected!
}
```

---

### 5. Stack Overflow ⚠️ Unsafe

**Purpose:** Verify stack overflow detection

**Warning:** This test WILL crash the system!

**Test:**
```c
// DO NOT RUN in production!
FaultInjection_StackOverflow(&result);
```

**Expected Behavior:**
- ✅ vApplicationStackOverflowHook() called
- ❌ System halts or resets

**Use Case:** Manual testing only with debugger attached.

---

### 6. NULL Pointer Dereference ⚠️ Unsafe

**Purpose:** Verify hard fault handling

**Warning:** This test triggers hard fault!

**Test:**
```c
// DO NOT RUN in production!
FaultInjection_NullPointer(&result);
```

**Expected Behavior:**
- ✅ HardFault_Handler() called
- ❌ System halts

**Use Case:** Verify fault handler is correctly configured.

---

## Integration with Priority Buffer

### Verification Points

The fault injection tests verify that the Priority Buffer system works correctly under fault conditions:

**1. Critical Event Capture**
```c
// During heap exhaustion
if (heap_free < HEAP_CRITICAL_THRESHOLD) {
    priority = PRIORITY_CRITICAL;
    PriorityBuffer_Write(&buffer, data, len, PRIORITY_CRITICAL);
}

// Verify after test
assert(result.critical_event_captured == true);
assert(result.critical_drops == 0); // NEVER drop critical!
```

**2. Drop Statistics**
```c
uint32_t dropped_low, dropped_critical;
PriorityBuffer_GetStats(&buffer, &dropped_low, NULL, NULL, &dropped_critical);

// During fault:
// - LOW priority may be dropped ✅
// - CRITICAL must NEVER be dropped ✅
assert(dropped_critical == 0);
```

**3. Recovery Behavior**
```c
// After fault recovery
assert(PriorityBuffer_IsFull(&buffer) == false);
assert(system_recovered == true);
```

---

## Automated Testing

### CI/CD Integration

**GitHub Actions Example:**
```yaml
name: Fault Injection Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Build firmware
        run: make clean && make
      
      - name: Flash to board
        run: ./flash.sh
      
      - name: Run fault injection tests
        run: |
          python3 tests/fault_injection_tester.py /dev/ttyUSB0 \
            --export fault_results.json
      
      - name: Upload results
        uses: actions/upload-artifact@v2
        with:
          name: fault-test-results
          path: fault_results.json
```

### Jenkins Integration

```groovy
pipeline {
    agent any
    
    stages {
        stage('Build') {
            steps {
                sh 'make clean && make'
            }
        }
        
        stage('Flash') {
            steps {
                sh './flash.sh'
            }
        }
        
        stage('Fault Injection Tests') {
            steps {
                sh '''
                    python3 tests/fault_injection_tester.py /dev/ttyUSB0 \
                        --export fault_results_${BUILD_NUMBER}.json
                '''
            }
        }
    }
    
    post {
        always {
            archiveArtifacts artifacts: 'fault_results_*.json'
        }
    }
}
```

---

## Test Results Format

### JSON Export

```json
{
  "timestamp": "2026-03-19 10:30:45",
  "port": "/dev/ttyUSB0",
  "results": [
    {
      "fault_type": "HEAP_EXHAUSTION",
      "fault_detected": true,
      "detection_time_ms": 150,
      "system_recovered": true,
      "recovery_time_ms": 200,
      "critical_event_captured": true,
      "buffer_drops": 5,
      "critical_drops": 0,
      "details": "Allocated 50 blocks before exhaustion",
      "passed": true
    }
  ]
}
```

### Console Output

```
==========================================
   Fault Injection Test Suite
==========================================

=== Fault Injection Test: Heap Exhaustion ===
  Fault Detected: YES
  Detection Time: 150 ms
  System Recovered: YES
  Recovery Time: 200 ms
  Critical Event Captured: YES
  Buffer Drops: 5
  Critical Drops: 0 (should be 0!)
  Details: Allocated 50 blocks before exhaustion
  Result: ✅ PASS

==========================================
   Tests Passed: 4 / 4
==========================================
```

---

## Best Practices

### 1. Run Tests Regularly

```bash
# Daily automated testing
0 2 * * * /path/to/run_fault_tests.sh
```

### 2. Monitor Critical Drops

```c
if (result.critical_drops > 0) {
    // CRITICAL ERROR - This should NEVER happen!
    log_emergency("Critical events were dropped!");
    alert_team();
}
```

### 3. Collect Long-Term Statistics

```python
# Track fault handling performance over time
results = load_historical_results()
avg_detection_time = calculate_average(results, 'detection_time_ms')
avg_recovery_time = calculate_average(results, 'recovery_time_ms')

print(f"Average detection: {avg_detection_time:.1f} ms")
print(f"Average recovery: {avg_recovery_time:.1f} ms")
```

### 4. Verify Before Release

```bash
# Pre-release checklist
./run_fault_tests.sh
if [ $? -eq 0 ]; then
    echo "✅ All fault tests passed - Ready for release"
else
    echo "❌ Fault tests failed - DO NOT RELEASE"
    exit 1
fi
```

---

## Troubleshooting

### Test Timeout

**Problem:** Test doesn't complete within timeout

**Solutions:**
- Increase timeout: `--timeout 10000`
- Check serial connection
- Verify board is responding

### Critical Drops Detected

**Problem:** `critical_drops > 0`

**This is CRITICAL!** Priority buffer is failing.

**Investigation:**
```c
// Check buffer size
printf("Buffer size: %zu bytes\n", buffer_capacity);
printf("Free space: %zu bytes\n", PriorityBuffer_GetFreeSpace(&buffer));

// Check packet count
printf("Packet count: %u / %u\n", buffer.packet_count, PRIORITY_MAX_PACKETS);
```

**Solutions:**
- Increase buffer size
- Increase PRIORITY_MAX_PACKETS
- Reduce packet size

### System Doesn't Recover

**Problem:** `system_recovered = false`

**Investigation:**
- Check if fault handler is stuck
- Verify recovery code is executed
- Check for secondary faults during recovery

---

## Safety Considerations

### Production Use

⚠️ **DO NOT run fault injection in production systems!**

- Stack overflow test will crash
- NULL pointer test will hard fault
- Tests may disrupt normal operation

### Testing Environment

✅ **Safe for testing:**
- Development boards
- Test benches
- CI/CD pipelines
- Pre-release validation

### Dangerous Tests

Skip these in automated testing:
- `FAULT_STACK_OVERFLOW` - crashes system
- `FAULT_NULL_POINTER` - triggers hard fault
- `FAULT_WATCHDOG_TIMEOUT` - may reset board

---

## Example: Complete Test Session

```bash
$ python3 fault_injection_tester.py /dev/ttyUSB0

==================================================
  Fault Injection Test Suite
==================================================

==================================================
Testing: HEAP_EXHAUSTION
==================================================
  === Fault Injection Test: Heap Exhaustion ===
  Fault Detected: YES
  Detection Time: 150 ms
  System Recovered: YES
  Recovery Time: 200 ms
  Critical Event Captured: YES
  Buffer Drops: 5
  Critical Drops: 0 (should be 0!)
  Details: Allocated 50 blocks before exhaustion
  Result: ✅ PASS

  HEAP_EXHAUSTION: ✅ PASS

==================================================
Testing: DEADLOCK
==================================================
  === Fault Injection Test: Mutex Deadlock ===
  Fault Detected: YES
  Detection Time: 1050 ms
  System Recovered: YES
  Recovery Time: 1100 ms
  Result: ✅ PASS

  DEADLOCK: ✅ PASS

==================================================
  Tests Passed: 4 / 4 (100.0%)
==================================================

✓ Results exported to fault_test_results.json
```

---

## Conclusion

Fault injection testing provides confidence that:
- ✅ Faults are detected quickly
- ✅ System recovers gracefully
- ✅ Critical events are never lost
- ✅ Priority buffer works under stress

**Use regularly to maintain system reliability!**

---

**Last Updated:** 2026-03-19
