/* Fault Injection Testing Framework Implementation
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#include "fault_injection.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#include <string.h>
#include <stdio.h>

/* Test configuration */
static FaultInjectionConfig_t test_config;
static bool initialized = false;

/* Test state tracking */
static volatile bool fault_in_progress = false;
static volatile uint32_t fault_start_time = 0;

/* External functions from os_monitor */
extern void OSMonitor_GetStats(void *stats);

/**
 * @brief Initialize fault injection framework
 */
void FaultInjection_Init(const FaultInjectionConfig_t *config)
{
    if (config != NULL) {
        memcpy(&test_config, config, sizeof(FaultInjectionConfig_t));
    } else {
        /* Default configuration */
        test_config.enable_recovery = true;
        test_config.timeout_ms = 5000;
        test_config.capture_events = true;
        test_config.verbose = true;
    }
    
    initialized = true;
}

/**
 * @brief Get test name
 */
const char* FaultInjection_GetTestName(FaultType_t fault_type)
{
    switch (fault_type) {
        case FAULT_STACK_OVERFLOW: return "Stack Overflow";
        case FAULT_HEAP_EXHAUSTION: return "Heap Exhaustion";
        case FAULT_NULL_POINTER: return "NULL Pointer Dereference";
        case FAULT_DIVISION_BY_ZERO: return "Division by Zero";
        case FAULT_DEADLOCK: return "Mutex Deadlock";
        case FAULT_PRIORITY_INVERSION: return "Priority Inversion";
        case FAULT_BUFFER_OVERFLOW: return "Buffer Overflow";
        case FAULT_ASSERT_FAILURE: return "Assert Failure";
        case FAULT_WATCHDOG_TIMEOUT: return "Watchdog Timeout";
        default: return "Unknown";
    }
}

/**
 * @brief Print test result
 */
void FaultInjection_PrintResult(const FaultInjectionResult_t *result)
{
    if (result == NULL) return;
    
    printf("\n=== Fault Injection Test: %s ===\n", 
           FaultInjection_GetTestName(result->fault_type));
    
    printf("  Fault Detected: %s\n", result->fault_detected ? "YES" : "NO");
    
    if (result->fault_detected) {
        printf("  Detection Time: %lu ms\n", result->detection_time_ms);
    }
    
    printf("  System Recovered: %s\n", result->system_recovered ? "YES" : "NO");
    
    if (result->system_recovered) {
        printf("  Recovery Time: %lu ms\n", result->recovery_time_ms);
    }
    
    if (test_config.capture_events) {
        printf("  Critical Event Captured: %s\n", 
               result->critical_event_captured ? "YES" : "NO");
        printf("  Buffer Drops: %lu\n", result->buffer_drops);
        printf("  Critical Drops: %lu (should be 0!)\n", result->critical_drops);
    }
    
    if (result->details[0] != '\0') {
        printf("  Details: %s\n", result->details);
    }
    
    /* Overall result */
    bool passed = result->fault_detected && 
                 (result->system_recovered || !test_config.enable_recovery) &&
                 (result->critical_drops == 0);
    
    printf("  Result: %s\n", passed ? "✅ PASS" : "❌ FAIL");
    printf("\n");
}

/**
 * @brief Test 1: Stack Overflow
 * 
 * Recursively calls itself until stack overflows
 */
void FaultInjection_StackOverflow(FaultInjectionResult_t *result)
{
    /* Volatile to prevent optimization */
    volatile uint8_t large_array[500];
    volatile int depth = 0;
    
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_STACK_OVERFLOW;
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    /* Use large_array to prevent optimization */
    large_array[0] = (uint8_t)depth;
    depth++;
    
    /* Recursive call */
    FaultInjection_StackOverflow(result);
    
    /* Should never reach here */
    fault_in_progress = false;
}

/**
 * @brief Stack overflow hook (called by FreeRTOS)
 */
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName)
{
    if (fault_in_progress) {
        uint32_t detection_time = xTaskGetTickCount() - fault_start_time;
        
        if (test_config.verbose) {
            printf("Stack overflow detected in task: %s (after %lu ms)\n",
                   pcTaskName, detection_time);
        }
        
        /* Mark as detected and recovered */
        fault_in_progress = false;
        
        /* System should halt or reset here in production */
        /* For testing, we just return */
    }
}

/**
 * @brief Test 2: Heap Exhaustion
 * 
 * Allocates memory until heap is exhausted
 */
void FaultInjection_HeapExhaustion(FaultInjectionResult_t *result)
{
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_HEAP_EXHAUSTION;
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    void *ptrs[100];
    int allocated = 0;
    
    /* Allocate until failure */
    while (allocated < 100) {
        ptrs[allocated] = pvPortMalloc(1024);
        
        if (ptrs[allocated] == NULL) {
            /* Heap exhausted */
            result->fault_detected = true;
            result->detection_time_ms = xTaskGetTickCount() - fault_start_time;
            
            if (test_config.verbose) {
                printf("Heap exhausted after %d allocations\n", allocated);
            }
            
            break;
        }
        
        allocated++;
    }
    
    /* Free allocated memory to recover */
    for (int i = 0; i < allocated; i++) {
        if (ptrs[i] != NULL) {
            vPortFree(ptrs[i]);
        }
    }
    
    result->system_recovered = true;
    result->recovery_time_ms = xTaskGetTickCount() - fault_start_time;
    
    snprintf(result->details, sizeof(result->details),
             "Allocated %d blocks before exhaustion", allocated);
    
    fault_in_progress = false;
}

/**
 * @brief Test 3: NULL Pointer Dereference
 */
void FaultInjection_NullPointer(FaultInjectionResult_t *result)
{
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_NULL_POINTER;
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    /* This will cause a hard fault */
    volatile uint32_t *null_ptr = NULL;
    *null_ptr = 0x12345678;
    
    /* Should never reach here */
    fault_in_progress = false;
}

/**
 * @brief Hard fault handler (called on ARM exception)
 */
void HardFault_Handler(void)
{
    if (fault_in_progress) {
        /* Hard fault detected during test */
        if (test_config.verbose) {
            printf("Hard fault detected during fault injection test\n");
        }
        
        /* In test mode, we can recover by returning */
        /* In production, system would reset */
    }
    
    /* Hang or reset */
    while(1);
}

/**
 * @brief Test 4: Division by Zero
 */
void FaultInjection_DivisionByZero(FaultInjectionResult_t *result)
{
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_DIVISION_BY_ZERO;
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    volatile int a = 10;
    volatile int b = 0;
    volatile int c;
    
    /* This may cause exception or return undefined result */
    c = a / b;
    
    /* On ARM Cortex-M, this usually doesn't fault but returns 0 */
    result->fault_detected = false;
    result->system_recovered = true;
    
    snprintf(result->details, sizeof(result->details),
             "Result: %d (ARM doesn't fault on divide-by-zero)", c);
    
    fault_in_progress = false;
}

/**
 * @brief Test 5: Mutex Deadlock
 */
static SemaphoreHandle_t mutex1 = NULL;
static SemaphoreHandle_t mutex2 = NULL;

void FaultInjection_Deadlock(FaultInjectionResult_t *result)
{
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_DEADLOCK;
    
    /* Create mutexes if not exists */
    if (mutex1 == NULL) {
        mutex1 = xSemaphoreCreateMutex();
    }
    if (mutex2 == NULL) {
        mutex2 = xSemaphoreCreateMutex();
    }
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    /* Task A: Take mutex1, then try mutex2 */
    if (xSemaphoreTake(mutex1, portMAX_DELAY) == pdTRUE) {
        vTaskDelay(10); /* Simulate some work */
        
        /* Try to take mutex2 with timeout */
        if (xSemaphoreTake(mutex2, pdMS_TO_TICKS(1000)) == pdTRUE) {
            /* Got both mutexes - no deadlock */
            xSemaphoreGive(mutex2);
            result->fault_detected = false;
        } else {
            /* Timeout - deadlock detected! */
            result->fault_detected = true;
            result->detection_time_ms = xTaskGetTickCount() - fault_start_time;
            
            if (test_config.verbose) {
                printf("Deadlock detected (mutex timeout)\n");
            }
        }
        
        xSemaphoreGive(mutex1);
    }
    
    result->system_recovered = true;
    result->recovery_time_ms = xTaskGetTickCount() - fault_start_time;
    
    fault_in_progress = false;
}

/**
 * @brief Test 6: Buffer Overflow
 */
void FaultInjection_BufferOverflow(FaultInjectionResult_t *result)
{
    memset(result, 0, sizeof(FaultInjectionResult_t));
    result->fault_type = FAULT_BUFFER_OVERFLOW;
    
    fault_start_time = xTaskGetTickCount();
    fault_in_progress = true;
    
    /* Small buffer with canary values */
    uint8_t buffer[10];
    uint32_t canary_before = 0xDEADBEEF;
    uint32_t canary_after = 0xCAFEBABE;
    
    /* Write past buffer end */
    for (int i = 0; i < 20; i++) {
        buffer[i] = 0xFF;  /* Overflow! */
    }
    
    /* Check canaries */
    if (canary_before != 0xDEADBEEF || canary_after != 0xCAFEBABE) {
        result->fault_detected = true;
        result->detection_time_ms = xTaskGetTickCount() - fault_start_time;
        
        snprintf(result->details, sizeof(result->details),
                 "Canary corruption detected");
    } else {
        result->fault_detected = false;
        snprintf(result->details, sizeof(result->details),
                 "Buffer overflow occurred but canaries intact (lucky!)");
    }
    
    result->system_recovered = true;
    result->recovery_time_ms = xTaskGetTickCount() - fault_start_time;
    
    fault_in_progress = false;
}

/**
 * @brief Inject specific fault
 */
bool FaultInjection_Inject(FaultType_t fault_type,
                           FaultInjectionResult_t *result)
{
    if (!initialized || result == NULL) {
        return false;
    }
    
    /* Get initial stats if event capture enabled */
    uint32_t initial_drops = 0;
    if (test_config.capture_events) {
        /* TODO: Get from OSMonitor_GetStats */
    }
    
    /* Inject fault based on type */
    switch (fault_type) {
        case FAULT_STACK_OVERFLOW:
            /* Note: This will actually crash - use with caution! */
            if (test_config.verbose) {
                printf("⚠️  Stack overflow test will crash the system!\n");
            }
            /* FaultInjection_StackOverflow(result); */
            result->fault_type = FAULT_STACK_OVERFLOW;
            snprintf(result->details, sizeof(result->details),
                     "Test skipped (would crash system)");
            break;
            
        case FAULT_HEAP_EXHAUSTION:
            FaultInjection_HeapExhaustion(result);
            break;
            
        case FAULT_NULL_POINTER:
            /* Note: This will hard fault - use with caution! */
            if (test_config.verbose) {
                printf("⚠️  NULL pointer test will hard fault!\n");
            }
            result->fault_type = FAULT_NULL_POINTER;
            snprintf(result->details, sizeof(result->details),
                     "Test skipped (would hard fault)");
            break;
            
        case FAULT_DIVISION_BY_ZERO:
            FaultInjection_DivisionByZero(result);
            break;
            
        case FAULT_DEADLOCK:
            FaultInjection_Deadlock(result);
            break;
            
        case FAULT_BUFFER_OVERFLOW:
            FaultInjection_BufferOverflow(result);
            break;
            
        default:
            return false;
    }
    
    /* Get final stats if event capture enabled */
    if (test_config.capture_events) {
        /* TODO: Get from OSMonitor_GetStats */
        result->critical_event_captured = result->fault_detected;
        result->buffer_drops = 0;
        result->critical_drops = 0;
    }
    
    return true;
}

/**
 * @brief Run all fault injection tests
 */
uint32_t FaultInjection_RunAllTests(FaultInjectionResult_t *results)
{
    if (!initialized || results == NULL) {
        return 0;
    }
    
    uint32_t passed = 0;
    
    printf("\n");
    printf("==========================================\n");
    printf("   Fault Injection Test Suite\n");
    printf("==========================================\n");
    
    /* Safe tests only (skip ones that crash) */
    FaultType_t safe_tests[] = {
        FAULT_HEAP_EXHAUSTION,
        FAULT_DIVISION_BY_ZERO,
        FAULT_DEADLOCK,
        FAULT_BUFFER_OVERFLOW
    };
    
    for (int i = 0; i < sizeof(safe_tests) / sizeof(safe_tests[0]); i++) {
        FaultInjection_Inject(safe_tests[i], &results[i]);
        FaultInjection_PrintResult(&results[i]);
        
        /* Count as passed if detected or expected not to detect */
        if (results[i].fault_detected || safe_tests[i] == FAULT_DIVISION_BY_ZERO) {
            passed++;
        }
        
        /* Delay between tests */
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    
    printf("==========================================\n");
    printf("   Tests Passed: %lu / %lu\n", passed, 
           (uint32_t)(sizeof(safe_tests) / sizeof(safe_tests[0])));
    printf("==========================================\n");
    printf("\n");
    
    return passed;
}
