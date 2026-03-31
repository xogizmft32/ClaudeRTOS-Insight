/* Fault Injection Testing Framework
 * Enables automated testing of error handling
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#ifndef FAULT_INJECTION_H
#define FAULT_INJECTION_H

#include <stdint.h>
#include <stdbool.h>

/**
 * @brief Fault types that can be injected
 */
typedef enum {
    FAULT_NONE = 0,
    FAULT_STACK_OVERFLOW,      /* Recursive calls until stack overflow */
    FAULT_HEAP_EXHAUSTION,     /* Allocate until heap exhausted */
    FAULT_NULL_POINTER,        /* Dereference NULL pointer */
    FAULT_DIVISION_BY_ZERO,    /* Divide by zero */
    FAULT_DEADLOCK,            /* Create mutex deadlock */
    FAULT_PRIORITY_INVERSION,  /* Trigger priority inversion */
    FAULT_BUFFER_OVERFLOW,     /* Write past buffer end */
    FAULT_ASSERT_FAILURE,      /* Trigger assertion */
    FAULT_WATCHDOG_TIMEOUT,    /* Block until watchdog fires */
    FAULT_MAX
} FaultType_t;

/**
 * @brief Fault injection result
 */
typedef struct {
    FaultType_t fault_type;
    bool fault_detected;           /* Was fault detected by system? */
    uint32_t detection_time_ms;    /* Time to detect (if detected) */
    bool system_recovered;         /* Did system recover? */
    uint32_t recovery_time_ms;     /* Time to recover (if recovered) */
    
    /* Event capture during fault */
    bool critical_event_captured;  /* Was critical event in buffer? */
    uint32_t buffer_drops;         /* Drops during fault */
    uint32_t critical_drops;       /* Critical drops (should be 0!) */
    
    char details[128];             /* Additional details */
} FaultInjectionResult_t;

/**
 * @brief Fault injection configuration
 */
typedef struct {
    bool enable_recovery;          /* Allow system to recover */
    uint32_t timeout_ms;           /* Max time to wait (default: 5000ms) */
    bool capture_events;           /* Capture events during fault */
    bool verbose;                  /* Print debug info */
} FaultInjectionConfig_t;

/**
 * @brief Initialize fault injection framework
 * 
 * @param config Configuration (NULL for defaults)
 */
void FaultInjection_Init(const FaultInjectionConfig_t *config);

/**
 * @brief Inject a specific fault
 * 
 * @param fault_type Type of fault to inject
 * @param result Output result structure
 * @return true if test completed (may have failed)
 */
bool FaultInjection_Inject(FaultType_t fault_type, 
                           FaultInjectionResult_t *result);

/**
 * @brief Run all fault injection tests
 * 
 * @param results Array to store results (size: FAULT_MAX)
 * @return Number of tests passed
 */
uint32_t FaultInjection_RunAllTests(FaultInjectionResult_t *results);

/**
 * @brief Get test name
 * 
 * @param fault_type Fault type
 * @return String name
 */
const char* FaultInjection_GetTestName(FaultType_t fault_type);

/**
 * @brief Print test result
 * 
 * @param result Test result to print
 */
void FaultInjection_PrintResult(const FaultInjectionResult_t *result);

/* Individual fault injection functions */
void FaultInjection_StackOverflow(FaultInjectionResult_t *result);
void FaultInjection_HeapExhaustion(FaultInjectionResult_t *result);
void FaultInjection_NullPointer(FaultInjectionResult_t *result);
void FaultInjection_DivisionByZero(FaultInjectionResult_t *result);
void FaultInjection_Deadlock(FaultInjectionResult_t *result);
void FaultInjection_PriorityInversion(FaultInjectionResult_t *result);
void FaultInjection_BufferOverflow(FaultInjectionResult_t *result);
void FaultInjection_AssertFailure(FaultInjectionResult_t *result);

#endif /* FAULT_INJECTION_H */
