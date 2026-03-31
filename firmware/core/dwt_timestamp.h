/* DWT Timestamp Module
 * High-precision timing using ARM Cortex-M DWT
 * Safety-Critical Design - Follows IEC 61508 principles
 * MISRA C:2012 Compliant
 */

#ifndef DWT_TIMESTAMP_H
#define DWT_TIMESTAMP_H

#include <stdint.h>
#include <stdbool.h>

/* DWT Register Definitions */
#define DWT_BASE            0xE0001000U
#define DWT_CTRL            (*((volatile uint32_t *)(DWT_BASE + 0x00U)))
#define DWT_CYCCNT          (*((volatile uint32_t *)(DWT_BASE + 0x04U)))

#define CoreDebug_BASE      0xE000EDF0U
#define CoreDebug_DEMCR     (*((volatile uint32_t *)(CoreDebug_BASE + 0x0CU)))

/* DWT Control Register Bits */
#define DWT_CTRL_CYCCNTENA_Msk  (1UL << 0)

/* CoreDebug DEMCR Register Bits */
#define CoreDebug_DEMCR_TRCENA_Msk  (1UL << 24)

/* Rollover detection threshold (32-bit counter) */
#define DWT_ROLLOVER_THRESHOLD  0x80000000U

/* Maximum timestamp value (for safety checks) */
#define DWT_MAX_TIMESTAMP_US    0xFFFFFFFFFFFFFFFFULL

/**
 * @brief Initialize DWT timestamp module
 * @param cpu_freq_hz CPU frequency in Hz
 * @return true if successful
 * 
 * Must be called before any timestamp operations.
 * WCET: < 5µs
 */
bool DWT_Init(uint32_t cpu_freq_hz);

/**
 * @brief Get current timestamp in microseconds
 * @return 64-bit timestamp in µs (monotonic, handles rollover)
 * 
 * Features:
 * - Automatic rollover detection and handling
 * - Monotonic (always increasing)
 * - Resolution: ~5.5ns @ 180MHz
 * - Range: 584,942 years
 * 
 * WCET: < 2µs @ 180MHz
 * Thread-safe: Yes (atomic reads)
 */
uint64_t DWT_GetTimestamp_us(void);

/**
 * @brief Get raw DWT cycle counter
 * @return 32-bit cycle count
 * 
 * WCET: < 0.1µs
 */
uint32_t DWT_GetCycles(void);

/**
 * @brief Get rollover count (for diagnostics)
 * @return Number of times counter has rolled over
 */
uint32_t DWT_GetRolloverCount(void);

/**
 * @brief Get error count (for diagnostics)
 * @return Number of timestamp errors detected
 */
uint32_t DWT_GetErrorCount(void);

/**
 * @brief Reset timestamp (for testing only)
 * 
 * WARNING: Should not be used in production code
 */
void DWT_Reset(void);

#endif /* DWT_TIMESTAMP_H */
