/* Time Synchronization Module
 * Enables Device ↔ Host timestamp correlation
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#ifndef TIME_SYNC_H
#define TIME_SYNC_H

#include <stdint.h>
#include <stdbool.h>

/**
 * @brief Sync beacon message
 * 
 * Sent periodically to enable host to correlate device timestamps
 * with wall-clock time
 */
typedef struct __attribute__((packed)) {
    uint8_t message_type;      /* MESSAGE_TYPE_SYNC_BEACON */
    uint32_t dwt_timestamp_high;
    uint32_t dwt_timestamp_low;
    uint32_t tick_count;       /* FreeRTOS tick count */
    uint16_t sequence_number;
} SyncBeacon_t;

/* Message type constants */
#define MESSAGE_TYPE_OS_SNAPSHOT  0x01
#define MESSAGE_TYPE_SYNC_BEACON  0x02

/**
 * @brief Time sync configuration
 */
typedef struct {
    uint32_t beacon_interval_ms;  /* How often to send (default: 60000 = 1 minute) */
    bool enabled;
} TimeSyncConfig_t;

/**
 * @brief Time sync state
 */
typedef struct {
    TimeSyncConfig_t config;
    uint32_t last_beacon_time;
    uint16_t beacon_sequence;
    uint32_t beacons_sent;
} TimeSync_t;

/**
 * @brief Initialize time sync
 * 
 * @param sync Pointer to sync structure
 * @param config Configuration (NULL for defaults)
 */
void TimeSync_Init(TimeSync_t *sync, const TimeSyncConfig_t *config);

/**
 * @brief Check if beacon should be sent
 * 
 * Call this periodically (e.g., in main loop)
 * 
 * @param sync Pointer to sync structure
 * @param current_tick Current FreeRTOS tick count
 * @return true if beacon should be sent now
 */
bool TimeSync_ShouldSendBeacon(TimeSync_t *sync, uint32_t current_tick);

/**
 * @brief Create sync beacon
 * 
 * @param sync Pointer to sync structure
 * @param beacon Output beacon structure
 * @return Size of beacon in bytes
 */
size_t TimeSync_CreateBeacon(TimeSync_t *sync, SyncBeacon_t *beacon);

/**
 * @brief Get beacon interval
 * 
 * @param sync Pointer to sync structure
 * @return Beacon interval in milliseconds
 */
uint32_t TimeSync_GetInterval(const TimeSync_t *sync);

/**
 * @brief Get statistics
 * 
 * @param sync Pointer to sync structure
 * @param out_beacons_sent Number of beacons sent (optional)
 */
void TimeSync_GetStats(const TimeSync_t *sync, uint32_t *out_beacons_sent);

#endif /* TIME_SYNC_H */
