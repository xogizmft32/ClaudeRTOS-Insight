# MISRA C:2012 Compliance Guidelines
## Memory Safety and Coding Standards

**Version:** 2.3.0  
**Standard:** MISRA C:2012  
**Status:** Guidelines (NOT fully verified)

---

## ⚠️ DISCLAIMER

This document provides guidelines for MISRA C:2012 compliance.

**Current Status:**
- ❌ NOT formally verified with certified tools
- ❌ NOT independently audited
- ⚠️ Deviations not formally documented

**For safety certification:**
1. Use certified static analysis tools (PC-lint Plus, Polyspace)
2. Document all deviations
3. Obtain independent review
4. Justify safety implications

---

## Overview

MISRA C:2012 provides coding guidelines to ensure:
- **Memory safety** - No undefined behavior
- **Predictability** - Deterministic execution
- **Maintainability** - Clear, readable code
- **Portability** - Platform-independent

---

## Priority Rules (Mandatory)

### Rule 1.3: No Undefined Behavior

**Requirement:** Avoid all undefined behavior

**Common Violations:**
```c
// ❌ BAD: Uninitialized variable
int x;
return x + 5;

// ✅ GOOD: Always initialize
int x = 0;
return x + 5;
```

```c
// ❌ BAD: Array out of bounds
uint8_t arr[10];
arr[10] = 5;  // Undefined!

// ✅ GOOD: Bounds checking
uint8_t arr[10];
if (index < 10) {
    arr[index] = 5;
}
```

---

### Rule 8.9: Static Where Possible

**Requirement:** Minimize scope of objects

```c
// ❌ BAD: Global when local would work
uint32_t counter = 0;

void increment(void) {
    counter++;
}

// ✅ GOOD: Static local
void increment(void) {
    static uint32_t counter = 0;
    counter++;
}
```

---

### Rule 9.1: Initialize All Variables

**Requirement:** All variables initialized before use

```c
// ❌ BAD: Uninitialized
int result;
if (condition) {
    result = 10;
}
return result;  // May be uninitialized!

// ✅ GOOD: Always initialized
int result = 0;
if (condition) {
    result = 10;
}
return result;
```

---

### Rule 11.4: Minimize Pointer Casts

**Requirement:** Avoid dangerous pointer conversions

```c
// ❌ BAD: Arbitrary pointer cast
uint8_t *ptr = (uint8_t*)0x40000000;
uint32_t val = *(uint32_t*)ptr;  // Alignment issue!

// ✅ GOOD: Proper alignment
uint32_t *ptr32 = (uint32_t*)0x40000000;
if (((uintptr_t)ptr32 & 0x3) == 0) {  // Check alignment
    uint32_t val = *ptr32;
}
```

---

### Rule 14.4: Boolean in Conditions

**Requirement:** Explicit boolean comparisons

```c
// ❌ BAD: Implicit boolean
if (ptr) { }
if (count) { }

// ✅ GOOD: Explicit comparison
if (ptr != NULL) { }
if (count != 0U) { }
```

---

### Rule 17.7: Check Return Values

**Requirement:** Don't ignore return values

```c
// ❌ BAD: Ignoring return
pvPortMalloc(1024);  // Memory leak!

// ✅ GOOD: Check return
void *ptr = pvPortMalloc(1024);
if (ptr == NULL) {
    // Handle error
}
```

---

### Rule 21.3: Minimize malloc/free

**Requirement:** Avoid dynamic allocation where possible

```c
// ❌ BAD: Dynamic allocation
void process(void) {
    uint8_t *buf = malloc(512);
    // ...
    free(buf);
}

// ✅ GOOD: Static allocation
void process(void) {
    static uint8_t buf[512];
    // ...
}
```

---

## Code Review Checklist

### ✅ Variables

- [ ] All variables initialized
- [ ] No unused variables
- [ ] Minimum scope (static where possible)
- [ ] Const where appropriate
- [ ] No globals (use static)

### ✅ Functions

- [ ] Return values checked
- [ ] NULL pointer checks
- [ ] Input validation
- [ ] Explicit return type
- [ ] No recursion (safety-critical)

### ✅ Pointers

- [ ] NULL checks before dereference
- [ ] Alignment verified
- [ ] Bounds checking
- [ ] No pointer arithmetic (use arrays)
- [ ] Const correctness

### ✅ Control Flow

- [ ] Explicit boolean comparisons
- [ ] No fall-through in switch
- [ ] All paths return
- [ ] No goto
- [ ] Bounded loops

### ✅ Types

- [ ] Explicit type conversions
- [ ] U suffix on unsigned constants
- [ ] Fixed-width types (uint32_t, not int)
- [ ] No implicit conversions
- [ ] Enum for constants

---

## Automated Checking

### Cppcheck with MISRA Addon

```bash
# Install
sudo apt-get install cppcheck

# Download MISRA addon
wget https://github.com/danmar/cppcheck/raw/main/addons/misra.py

# Run check
cppcheck --addon=misra.py firmware/core/*.c

# Example output:
# firmware/core/crc32.c:45: style: MISRA 14.4: if(x) should be if(x != 0)
```

### PC-lint Plus (Commercial)

```bash
# Run PC-lint
pclp firmware/core/*.c --misra

# Generate report
pclp firmware/core/*.c --misra --output=misra_report.txt
```

### Configuration File

**misra.txt:**
```
# MISRA C:2012 configuration

# Mandatory rules
enable=1.3   # No undefined behavior
enable=8.9   # Static where possible
enable=9.1   # Initialize variables
enable=11.4  # Pointer conversions
enable=14.4  # Boolean in conditions
enable=17.7  # Check return values
enable=21.3  # Minimize malloc

# Advisory rules
enable=2.1   # No unreachable code
enable=2.2   # No unused parameters
enable=8.4   # Compatible declarations
```

---

## Common Violations in ClaudeRTOS-Insight

### 1. Implicit Boolean Comparisons

**Current Code:**
```c
if (buffer) {
    process_data(buffer);
}
```

**MISRA Compliant:**
```c
if (buffer != NULL) {
    process_data(buffer);
}
```

**Fix:**
```bash
find firmware -name "*.c" -exec sed -i 's/if (\([^=!<>]*\))/if (\1 != 0)/g' {} \;
```

---

### 2. Magic Numbers

**Current Code:**
```c
vTaskDelay(1000);
```

**MISRA Compliant:**
```c
#define DELAY_1_SECOND 1000U
vTaskDelay(DELAY_1_SECOND);
```

---

### 3. Implicit Type Conversions

**Current Code:**
```c
uint8_t a = 255;
uint16_t b = a + 1;  // Implicit conversion
```

**MISRA Compliant:**
```c
uint8_t a = 255U;
uint16_t b = (uint16_t)a + 1U;  // Explicit
```

---

### 4. FreeRTOS API Deviations

**Issue:** FreeRTOS uses patterns that violate MISRA

**Example:**
```c
// FreeRTOS: Uses implicit boolean
if (xSemaphoreTake(mutex, timeout)) { }

// MISRA Compliant wrapper:
if (xSemaphoreTake(mutex, timeout) == pdTRUE) { }
```

**Recommendation:** Create MISRA-compliant wrappers

```c
/* MISRA-compliant FreeRTOS wrappers */

static inline bool MISRA_SemaphoreTake(SemaphoreHandle_t sem, uint32_t timeout) {
    return (xSemaphoreTake(sem, timeout) == pdTRUE);
}

// Usage:
if (MISRA_SemaphoreTake(mutex, 1000U) == true) {
    // ...
}
```

---

## Deviation Documentation

### Required Information

For each deviation, document:

1. **Rule violated:** e.g., Rule 11.5
2. **Location:** File and line number
3. **Reason:** Why deviation is necessary
4. **Safety impact:** Analysis of risks
5. **Mitigation:** How risk is minimized

### Template

```c
/* MISRA Deviation: Rule 11.5
 * Location: dwt_timestamp.c:45
 * Reason: Hardware register access requires cast
 * Impact: Low - address is fixed and verified
 * Mitigation: Alignment checked, address const
 */
#define DWT_CYCCNT (*((volatile uint32_t*)0xE0001004))
```

---

## Recommended Workflow

### Phase 1: Core Modules (2 weeks)

Focus on critical files:
- `firmware/core/crc32.c`
- `firmware/core/dwt_timestamp.c`
- `firmware/core/ring_buffer.c`
- `firmware/core/priority_buffer.c`

**Steps:**
1. Run cppcheck --addon=misra
2. Fix mandatory violations
3. Document deviations
4. Review with team

---

### Phase 2: Automated Checking (1 week)

**Setup CI/CD:**
```yaml
name: MISRA Check

on: [push, pull_request]

jobs:
  misra:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Install cppcheck
        run: sudo apt-get install cppcheck
      
      - name: Run MISRA check
        run: |
          cppcheck --addon=misra.py \
            --error-exitcode=1 \
            firmware/core/*.c
```

---

### Phase 3: Full Compliance (4 weeks)

Expand to all modules:
- Event classifier
- Adaptive sampler
- Time sync
- OS monitor

**Goal:** Zero mandatory violations

---

## MISRA C Summary by Module

### Priority Buffer

**Compliant Rules:**
- ✅ 1.3: No undefined behavior
- ✅ 8.9: Static functions
- ✅ 9.1: Variables initialized
- ✅ 17.7: Return values checked

**Deviations:**
- ⚠️ 14.4: Some implicit booleans (FreeRTOS API)

**Recommendation:** Add explicit comparisons

---

### Event Classifier

**Compliant Rules:**
- ✅ 1.3: No undefined behavior
- ✅ 9.1: Variables initialized

**Deviations:**
- ⚠️ 11.4: Pointer casts for snapshot access

**Recommendation:** Use proper type definitions

---

### CRC32

**Compliant Rules:**
- ✅ 1.3: No undefined behavior
- ✅ 8.9: Static table
- ✅ 9.1: Variables initialized
- ✅ 21.3: No dynamic allocation

**Deviations:**
- None identified

**Status:** ✅ MISRA compliant

---

## Tools Comparison

| Tool | Cost | MISRA Support | Integration |
|------|------|---------------|-------------|
| Cppcheck | Free | Basic (addon) | Easy |
| PC-lint Plus | $$$ | Complete | Medium |
| Polyspace | $$$$ | Complete + Formal | Complex |
| Coverity | $$$$ | Complete | Medium |

**Recommendation for ClaudeRTOS-Insight:**
- **Development:** Cppcheck (free, adequate)
- **Certification:** PC-lint Plus (industry standard)

---

## Certification Path

### Step 1: Preparation (Current)
- ✅ Document deviations
- ✅ Create MISRA configuration
- ⚠️ Run basic checks

### Step 2: Tools (2 weeks)
- [ ] Acquire PC-lint Plus
- [ ] Configure for project
- [ ] Integrate with build

### Step 3: Remediation (4-6 weeks)
- [ ] Fix all mandatory violations
- [ ] Document advisory deviations
- [ ] Review with safety engineer

### Step 4: Audit (2 weeks)
- [ ] Independent code review
- [ ] Verification of fixes
- [ ] Final documentation

**Total Time:** 8-10 weeks for full compliance

---

## Conclusion

**Current Status:**
- ~70% MISRA aligned
- Core modules mostly compliant
- FreeRTOS API causes deviations

**Next Steps:**
1. Run cppcheck with MISRA addon
2. Fix mandatory violations
3. Document deviations
4. Consider PC-lint Plus for certification

**For Safety Certification:**
- Full MISRA C:2012 compliance required
- Independent verification needed
- Budget 8-10 weeks + tool costs

---

**Version:** 2.3.0  
**Last Updated:** 2026-03-19  
**Status:** Guidelines (verification pending)
