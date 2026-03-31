/* Time Synchronization Implementation
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#include "time_sync.h"
#include "dwt_timestamp.h"
#include "FreeRTOS.h"
#include "task.h"
#include <string.h>

/* Default configuration */
#define DEFAULT_BEACON_INTERVAL_MS 60000  /* 1 minute */

/**
 * @brief Initialize time sync
 */
void TimeSync_Init(TimeSync_t *sync, const TimeSyncConfig_t *config)
{
    if (sync == NULL) {
        return;
    }
    
    /* Apply configuration or defaults */
    if (config != NULL) {
        memcpy(&sync->config, config, sizeof(TimeSyncConfig_t));
    } else {
        sync->config.beacon_interval_ms = DEFAULT_BEACON_INTERVAL_MS;
        sync->config.enabled = true;
    }
    
    /* Initialize state */
    sync->last_beacon_time = 0;
    sync->beacon_sequence = 0;
    sync->beacons_sent = 0;
}

/**
 * @brief Check if beacon should be sent
 */
bool TimeSync_ShouldSendBeacon(TimeSync_t *sync, uint32_t current_tick)
{
    if (sync == NULL || !sync->config.enabled) {
        return false;
    }
    
    /* Convert ticks to milliseconds */
    uint32_t current_time_ms = current_tick * portTICK_PERIOD_MS;
    
    /* Check if interval has elapsed */
    if (current_time_ms - sync->last_beacon_time >= 
        sync->config.beacon_interval_ms) {
        sync->last_beacon_time = current_time_ms;
        return true;
    }
    
    return false;
}

/**
 * @brief Create sync beacon
 */
size_t TimeSync_CreateBeacon(TimeSync_t *sync, SyncBeacon_t *beacon)
{
    if (sync == NULL || beacon == NULL) {
        return 0;
    }
    
    /* Get current DWT timestamp */
    uint64_t dwt_timestamp = DWT_GetTimestamp();
    
    /* Get current tick count */
    uint32_t tick_count = xTaskGetTickCount();
    
    /* Fill beacon */
    beacon->message_type = MESSAGE_TYPE_SYNC_BEACON;
    beacon->dwt_timestamp_high = (uint32_t)(dwt_timestamp >> 32);
    beacon->dwt_timestamp_low = (uint32_t)(dwt_timestamp & 0xFFFFFFFF);
    beacon->tick_count = tick_count;
    beacon->sequence_number = sync->beacon_sequence++;
    
    /* Update statistics */
    sync->beacons_sent++;
    
    return sizeof(SyncBeacon_t);
}

/**
 * @brief Get beacon interval
 */
uint32_t TimeSync_GetInterval(const TimeSync_t *sync)
{
    if (sync == NULL) {
        return DEFAULT_BEACON_INTERVAL_MS;
    }
    
    return sync->config.beacon_interval_ms;
}

/**
 * @brief Get statistics
 */
void TimeSync_GetStats(const TimeSync_t *sync, uint32_t *out_beacons_sent)
{
    if (sync == NULL) {
        return;
    }
    
    if (out_beacons_sent != NULL) {
        *out_beacons_sent = sync->beacons_sent;
    }
}
