/* Adaptive Sampler - Improved Version
 * Preserves critical data during overload
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#ifndef ADAPTIVE_SAMPLER_H
#define ADAPTIVE_SAMPLER_H

#include <stdint.h>
#include <stdbool.h>

/* Forward declaration */
typedef struct OSSnapshot OSSnapshot_t;

/**
 * @brief Sampling modes
 */
typedef enum {
    SAMPLE_MODE_FULL,      /* Full snapshot with all fields */
    SAMPLE_MODE_CRITICAL,  /* Only critical fields */
    SAMPLE_MODE_MINIMAL    /* Minimal data (critical tasks only) */
} SampleMode_t;

/**
 * @brief Adaptive sampler configuration
 */
typedef struct {
    /* Thresholds */
    uint8_t cpu_overload_threshold;    /* Default: 80% */
    uint8_t buffer_overload_threshold; /* Default: 80% */
    
    /* Differential sampling */
    bool enable_differential;          /* Skip if no change */
    uint32_t max_skip_period_ms;      /* Max time without sending (default: 10s) */
    
    /* Selective fields */
    bool enable_selective_fields;      /* Reduce fields on overload */
    
    /* Burst capture */
    bool enable_burst_capture;         /* High-speed on critical events */
    uint32_t burst_duration_ms;        /* Default: 10s */
    uint16_t burst_rate_ms;            /* Default: 100ms (10Hz) */
    
    /* Change detection thresholds */
    uint8_t cpu_change_threshold;      /* Default: 10% */
    uint32_t heap_change_threshold;    /* Default: 1024 bytes */
} AdaptiveSamplerConfig_t;

/**
 * @brief Adaptive sampler state
 */
typedef struct {
    /* Configuration */
    AdaptiveSamplerConfig_t config;
    
    /* Current state */
    SampleMode_t current_mode;
    bool in_burst_mode;
    uint32_t burst_start_time;
    
    /* Last snapshot for comparison */
    OSSnapshot_t last_snapshot;
    uint32_t last_send_time;
    
    /* Statistics */
    uint32_t snapshots_collected;
    uint32_t snapshots_sent;
    uint32_t snapshots_skipped;
    uint32_t burst_events;
} AdaptiveSampler_t;

/**
 * @brief Initialize adaptive sampler
 * 
 * @param sampler Pointer to sampler structure
 * @param config Configuration (NULL for defaults)
 */
void AdaptiveSampler_Init(AdaptiveSampler_t *sampler, 
                         const AdaptiveSamplerConfig_t *config);

/**
 * @brief Check if snapshot should be sent
 * 
 * Uses differential sampling: sends only if changed significantly
 * or periodic update is needed
 * 
 * @param sampler Pointer to sampler
 * @param snapshot Current snapshot
 * @param cpu_usage Current CPU usage (%)
 * @param buffer_usage Current buffer usage (%)
 * @return true if should send
 * 
 * WCET: < 8 µs @ 180MHz
 */
bool AdaptiveSampler_ShouldSend(AdaptiveSampler_t *sampler,
                               const OSSnapshot_t *snapshot,
                               uint8_t cpu_usage,
                               uint8_t buffer_usage);

/**
 * @brief Get sampling mode
 * 
 * Determines which fields to include based on system load
 * 
 * @param sampler Pointer to sampler
 * @param cpu_usage Current CPU usage (%)
 * @param buffer_usage Current buffer usage (%)
 * @return Sample mode to use
 */
SampleMode_t AdaptiveSampler_GetMode(const AdaptiveSampler_t *sampler,
                                    uint8_t cpu_usage,
                                    uint8_t buffer_usage);

/**
 * @brief Get sampling rate
 * 
 * Returns appropriate sampling interval in milliseconds
 * 
 * @param sampler Pointer to sampler
 * @return Sampling interval in ms
 */
uint32_t AdaptiveSampler_GetRate(const AdaptiveSampler_t *sampler);

/**
 * @brief Trigger burst mode
 * 
 * Switches to high-speed sampling for short period
 * 
 * @param sampler Pointer to sampler
 */
void AdaptiveSampler_TriggerBurst(AdaptiveSampler_t *sampler);

/**
 * @brief Update sampler state
 * 
 * Call after sending snapshot
 * 
 * @param sampler Pointer to sampler
 * @param snapshot Snapshot that was sent
 */
void AdaptiveSampler_Update(AdaptiveSampler_t *sampler,
                           const OSSnapshot_t *snapshot);

/**
 * @brief Get statistics
 * 
 * @param sampler Pointer to sampler
 * @param out_collected Total snapshots collected (optional)
 * @param out_sent Total snapshots sent (optional)
 * @param out_skipped Total snapshots skipped (optional)
 */
void AdaptiveSampler_GetStats(const AdaptiveSampler_t *sampler,
                             uint32_t *out_collected,
                             uint32_t *out_sent,
                             uint32_t *out_skipped);

/**
 * @brief Reset statistics
 * 
 * @param sampler Pointer to sampler
 */
void AdaptiveSampler_ResetStats(AdaptiveSampler_t *sampler);

#endif /* ADAPTIVE_SAMPLER_H */
