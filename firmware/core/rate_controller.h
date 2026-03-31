/* Adaptive Rate Controller
 * Dynamically adjusts sampling rates based on system load
 * Safety-Critical Design - Follows IEC 61508 principles
 */

#ifndef RATE_CONTROLLER_H
#define RATE_CONTROLLER_H

#include <stdint.h>
#include <stdbool.h>

/* Rate control policies */
typedef enum {
    RATE_POLICY_FIXED = 0,          /* Fixed rate (no adaptation) */
    RATE_POLICY_ADAPTIVE_CPU = 1,   /* Adapt based on CPU usage */
    RATE_POLICY_ADAPTIVE_BUFFER = 2,/* Adapt based on buffer usage */
    RATE_POLICY_ADAPTIVE_BOTH = 3   /* Adapt based on both */
} RatePolicy_t;

/* Rate controller configuration */
typedef struct {
    uint16_t current_rate_ms;      /* Current sampling rate */
    uint16_t min_rate_ms;          /* Minimum (fastest) rate */
    uint16_t max_rate_ms;          /* Maximum (slowest) rate */
    uint8_t  cpu_threshold_high;   /* CPU threshold for slowdown (%) */
    uint8_t  cpu_threshold_low;    /* CPU threshold for speedup (%) */
    uint32_t buffer_threshold_high;/* Buffer threshold for slowdown */
    uint32_t buffer_threshold_low; /* Buffer threshold for speedup */
    RatePolicy_t policy;           /* Active policy */
} RateController_t;

/**
 * @brief Initialize rate controller
 * @param controller Pointer to controller structure
 * @param min_rate_ms Minimum sampling rate (ms)
 * @param max_rate_ms Maximum sampling rate (ms)
 * @param policy Rate control policy
 * 
 * WCET: < 1µs
 */
void RateController_Init(RateController_t *controller,
                         uint16_t min_rate_ms,
                         uint16_t max_rate_ms,
                         RatePolicy_t policy);

/**
 * @brief Adjust sampling rate based on system state
 * @param controller Pointer to controller
 * @param cpu_usage Current CPU usage (0-100%)
 * @param buffer_used Current buffer usage (bytes)
 * @return New sampling rate (ms)
 * 
 * WCET: < 5µs @ 180MHz
 * Deterministic: Yes
 */
uint16_t RateController_Adjust(RateController_t *controller,
                                uint8_t cpu_usage,
                                uint32_t buffer_used);

/**
 * @brief Get current rate
 * @param controller Pointer to controller
 * @return Current rate in milliseconds
 */
uint16_t RateController_GetRate(const RateController_t *controller);

/**
 * @brief Set rate policy
 * @param controller Pointer to controller
 * @param policy New policy
 */
void RateController_SetPolicy(RateController_t *controller, RatePolicy_t policy);

#endif /* RATE_CONTROLLER_H */
