/* OS Monitor Module Header
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 * Version 2.0 with Priority Buffer support
 */

#ifndef OS_MONITOR_H
#define OS_MONITOR_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/**
 * @brief OS Monitor Statistics
 */
typedef struct {
    uint32_t snapshots_collected;
    uint32_t critical_events;
    uint32_t warning_events;
    uint32_t normal_events;
    
    /* Buffer drop statistics by priority */
    uint32_t buffer_drops_low;
    uint32_t buffer_drops_normal;
    uint32_t buffer_drops_high;
    uint32_t buffer_drops_critical;  /* Should always be 0! */
} OSMonitorStats_t;

/**
 * @brief Initialize OS monitor
 * 
 * Initializes priority buffer, DWT timestamp, and internal state
 */
void OSMonitor_Init(void);

/**
 * @brief Collect OS snapshot
 * 
 * Collects system state, automatically classifies priority,
 * and writes to priority buffer
 * 
 * Should be called periodically (e.g., 1Hz)
 */
void OSMonitor_Collect(void);

/**
 * @brief Get data from output buffer
 * 
 * @param buffer Output buffer
 * @param max_size Maximum bytes to read
 * @return Number of bytes read (0 if empty)
 */
size_t OSMonitor_GetData(uint8_t *buffer, size_t max_size);

/**
 * @brief Get monitor statistics
 * 
 * @param stats Pointer to statistics structure
 */
void OSMonitor_GetStats(OSMonitorStats_t *stats);

/**
 * @brief Reset statistics
 */
void OSMonitor_ResetStats(void);

/**
 * @brief Check if buffer has data available
 * 
 * @return true if data available
 */
bool OSMonitor_HasData(void);

/**
 * @brief Get free space in buffer
 * 
 * @return Free space in bytes
 */
size_t OSMonitor_GetFreeSpace(void);

#endif /* OS_MONITOR_H */
