/* Adaptive Sampler Implementation
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#include "adaptive_sampler.h"
#include "event_classifier.h"
#include <string.h>
#include <stdlib.h>

/* Default configuration values */
#define DEFAULT_CPU_THRESHOLD 80
#define DEFAULT_BUFFER_THRESHOLD 80
#define DEFAULT_MAX_SKIP_MS 10000
#define DEFAULT_BURST_DURATION_MS 10000
#define DEFAULT_BURST_RATE_MS 100
#define DEFAULT_CPU_CHANGE 10
#define DEFAULT_HEAP_CHANGE 1024

/* Assume OSSnapshot_t from event_classifier.c */
typedef struct {
    uint32_t timestamp_high;
    uint32_t timestamp_low;
    uint8_t task_count;
    uint32_t heap_free;
    uint32_t heap_min;
    uint8_t cpu_usage;
    
    struct {
        uint16_t stack_hwm;
        uint8_t state;
        uint8_t priority;
        uint32_t runtime;
    } tasks[16];
} OSSnapshot_t;

/**
 * @brief Check for critical changes
 */
static bool has_critical_change(const OSSnapshot_t *current,
                               const OSSnapshot_t *last)
{
    /* Stack overflow detected */
    for (int i = 0; i < current->task_count; i++) {
        if (current->tasks[i].stack_hwm < STACK_CRITICAL_THRESHOLD &&
            last->tasks[i].stack_hwm >= STACK_CRITICAL_THRESHOLD) {
            return true;  /* Crossing critical threshold! */
        }
    }
    
    /* Heap critically low */
    if (current->heap_free < HEAP_CRITICAL_THRESHOLD &&
        last->heap_free >= HEAP_CRITICAL_THRESHOLD) {
        return true;
    }
    
    /* Task state changed to Blocked */
    for (int i = 0; i < current->task_count; i++) {
        if (current->tasks[i].state == 3 /* BLOCKED */ &&
            last->tasks[i].state != 3) {
            return true;
        }
    }
    
    return false;
}

/**
 * @brief Check for significant changes
 */
static bool has_significant_change(const OSSnapshot_t *current,
                                  const OSSnapshot_t *last,
                                  const AdaptiveSamplerConfig_t *config)
{
    /* CPU usage changed significantly */
    int cpu_delta = abs(current->cpu_usage - last->cpu_usage);
    if (cpu_delta >= config->cpu_change_threshold) {
        return true;
    }
    
    /* Heap changed significantly */
    int heap_delta = abs((int)current->heap_free - (int)last->heap_free);
    if (heap_delta >= (int)config->heap_change_threshold) {
        return true;
    }
    
    /* Any task priority changed */
    for (int i = 0; i < current->task_count; i++) {
        if (current->tasks[i].priority != last->tasks[i].priority) {
            return true;
        }
    }
    
    /* Task count changed */
    if (current->task_count != last->task_count) {
        return true;
    }
    
    return false;
}

/**
 * @brief Initialize adaptive sampler
 */
void AdaptiveSampler_Init(AdaptiveSampler_t *sampler,
                         const AdaptiveSamplerConfig_t *config)
{
    if (sampler == NULL) {
        return;
    }
    
    /* Apply configuration or defaults */
    if (config != NULL) {
        memcpy(&sampler->config, config, sizeof(AdaptiveSamplerConfig_t));
    } else {
        /* Use defaults */
        sampler->config.cpu_overload_threshold = DEFAULT_CPU_THRESHOLD;
        sampler->config.buffer_overload_threshold = DEFAULT_BUFFER_THRESHOLD;
        sampler->config.enable_differential = true;
        sampler->config.max_skip_period_ms = DEFAULT_MAX_SKIP_MS;
        sampler->config.enable_selective_fields = true;
        sampler->config.enable_burst_capture = true;
        sampler->config.burst_duration_ms = DEFAULT_BURST_DURATION_MS;
        sampler->config.burst_rate_ms = DEFAULT_BURST_RATE_MS;
        sampler->config.cpu_change_threshold = DEFAULT_CPU_CHANGE;
        sampler->config.heap_change_threshold = DEFAULT_HEAP_CHANGE;
    }
    
    /* Initialize state */
    sampler->current_mode = SAMPLE_MODE_FULL;
    sampler->in_burst_mode = false;
    sampler->burst_start_time = 0;
    sampler->last_send_time = 0;
    
    memset(&sampler->last_snapshot, 0, sizeof(OSSnapshot_t));
    
    /* Reset statistics */
    sampler->snapshots_collected = 0;
    sampler->snapshots_sent = 0;
    sampler->snapshots_skipped = 0;
    sampler->burst_events = 0;
}

/**
 * @brief Check if snapshot should be sent
 * 
 * WCET: < 8 µs @ 180MHz
 */
bool AdaptiveSampler_ShouldSend(AdaptiveSampler_t *sampler,
                               const OSSnapshot_t *snapshot,
                               uint8_t cpu_usage,
                               uint8_t buffer_usage)
{
    if (sampler == NULL || snapshot == NULL) {
        return true;  /* Safe default: always send */
    }
    
    sampler->snapshots_collected++;
    
    /* Always send if differential sampling disabled */
    if (!sampler->config.enable_differential) {
        return true;
    }
    
    /* In burst mode: always send */
    if (sampler->in_burst_mode) {
        return true;
    }
    
    /* Check for critical changes - always send */
    if (has_critical_change(snapshot, &sampler->last_snapshot)) {
        /* Trigger burst mode */
        if (sampler->config.enable_burst_capture) {
            AdaptiveSampler_TriggerBurst(sampler);
        }
        return true;
    }
    
    /* Check for significant changes */
    if (has_significant_change(snapshot, &sampler->last_snapshot, 
                              &sampler->config)) {
        return true;
    }
    
    /* Periodic update (even if no change) */
    uint32_t current_time = 0;  /* TODO: Get from FreeRTOS tick */
    if (current_time - sampler->last_send_time >= 
        sampler->config.max_skip_period_ms) {
        return true;  /* Force periodic update */
    }
    
    /* No significant change - skip */
    sampler->snapshots_skipped++;
    return false;
}

/**
 * @brief Get sampling mode
 */
SampleMode_t AdaptiveSampler_GetMode(const AdaptiveSampler_t *sampler,
                                    uint8_t cpu_usage,
                                    uint8_t buffer_usage)
{
    if (sampler == NULL) {
        return SAMPLE_MODE_FULL;
    }
    
    if (!sampler->config.enable_selective_fields) {
        return SAMPLE_MODE_FULL;
    }
    
    /* Check overload conditions */
    bool overloaded = (cpu_usage >= sampler->config.cpu_overload_threshold) ||
                     (buffer_usage >= sampler->config.buffer_overload_threshold);
    
    if (!overloaded) {
        return SAMPLE_MODE_FULL;
    }
    
    /* Severe overload: minimal */
    if (cpu_usage >= 95 || buffer_usage >= 95) {
        return SAMPLE_MODE_MINIMAL;
    }
    
    /* Moderate overload: critical fields only */
    return SAMPLE_MODE_CRITICAL;
}

/**
 * @brief Get sampling rate
 */
uint32_t AdaptiveSampler_GetRate(const AdaptiveSampler_t *sampler)
{
    if (sampler == NULL) {
        return 1000;  /* Default: 1Hz */
    }
    
    /* Burst mode: high speed */
    if (sampler->in_burst_mode) {
        return sampler->config.burst_rate_ms;
    }
    
    /* Normal mode: 1Hz */
    return 1000;
}

/**
 * @brief Trigger burst mode
 */
void AdaptiveSampler_TriggerBurst(AdaptiveSampler_t *sampler)
{
    if (sampler == NULL) {
        return;
    }
    
    sampler->in_burst_mode = true;
    sampler->burst_start_time = 0;  /* TODO: Get from FreeRTOS tick */
    sampler->burst_events++;
}

/**
 * @brief Update sampler state
 */
void AdaptiveSampler_Update(AdaptiveSampler_t *sampler,
                           const OSSnapshot_t *snapshot)
{
    if (sampler == NULL || snapshot == NULL) {
        return;
    }
    
    /* Save snapshot for next comparison */
    memcpy(&sampler->last_snapshot, snapshot, sizeof(OSSnapshot_t));
    
    /* Update send time */
    sampler->last_send_time = 0;  /* TODO: Get from FreeRTOS tick */
    
    /* Update sent counter */
    sampler->snapshots_sent++;
    
    /* Check if burst mode should end */
    if (sampler->in_burst_mode) {
        uint32_t current_time = 0;  /* TODO: Get from FreeRTOS tick */
        if (current_time - sampler->burst_start_time >= 
            sampler->config.burst_duration_ms) {
            sampler->in_burst_mode = false;
        }
    }
}

/**
 * @brief Get statistics
 */
void AdaptiveSampler_GetStats(const AdaptiveSampler_t *sampler,
                             uint32_t *out_collected,
                             uint32_t *out_sent,
                             uint32_t *out_skipped)
{
    if (sampler == NULL) {
        return;
    }
    
    if (out_collected != NULL) *out_collected = sampler->snapshots_collected;
    if (out_sent != NULL) *out_sent = sampler->snapshots_sent;
    if (out_skipped != NULL) *out_skipped = sampler->snapshots_skipped;
}

/**
 * @brief Reset statistics
 */
void AdaptiveSampler_ResetStats(AdaptiveSampler_t *sampler)
{
    if (sampler == NULL) {
        return;
    }
    
    sampler->snapshots_collected = 0;
    sampler->snapshots_sent = 0;
    sampler->snapshots_skipped = 0;
    sampler->burst_events = 0;
}
