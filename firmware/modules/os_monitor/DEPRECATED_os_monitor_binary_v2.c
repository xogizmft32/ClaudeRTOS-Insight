/* OS Monitor Module - Binary Protocol with Priority Buffer
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 * 
 * Version 2.0: Priority-based event protection
 */

#include "os_monitor.h"
#include "binary_protocol.h"
#include "crc32.h"
#include "dwt_timestamp.h"
#include "priority_buffer.h"  /* Changed from ring_buffer.h */
#include "../event_classifier.h"  /* NEW: Auto-classification */
#include "FreeRTOS.h"
#include "task.h"
#include <string.h>

/* Configuration */
#define OS_MONITOR_PORT 0U
#define OS_MONITOR_RATE_MS 1000U
#define MAX_TASKS 16U
#define OUTPUT_BUFFER_SIZE 8192U

/* Module state */
static bool initialized = false;
static uint32_t context_switches = 0U;
static uint16_t sequence_number = 0U;

/* Priority buffer for output */
static PriorityBuffer_t output_buffer;
static uint8_t buffer_storage[OUTPUT_BUFFER_SIZE];

/* Task ID mapping */
static TaskHandle_t task_handles[MAX_TASKS];
static uint8_t task_count = 0U;

/* Statistics */
static struct {
    uint32_t snapshots_collected;
    uint32_t critical_events;
    uint32_t warning_events;
    uint32_t normal_events;
    uint32_t buffer_drops;
} monitor_stats = {0};

/* Helper: Get or create task ID */
static uint8_t get_task_id(TaskHandle_t handle)
{
    uint8_t i;
    
    if (handle == NULL) {
        return 0xFFU;
    }
    
    /* Search existing */
    for (i = 0U; i < task_count; i++) {
        if (task_handles[i] == handle) {
            return i;
        }
    }
    
    /* Create new */
    if (task_count < MAX_TASKS) {
        task_handles[task_count] = handle;
        return task_count++;
    }
    
    return 0xFFU;
}

/**
 * @brief Initialize OS monitor with priority buffer
 */
void OSMonitor_Init(void)
{
    if (initialized) {
        return;
    }
    
    /* Initialize priority buffer */
    PriorityBuffer_Init(&output_buffer, buffer_storage, OUTPUT_BUFFER_SIZE);
    
    /* Initialize DWT timestamp */
    DWT_Init();
    
    /* Reset state */
    task_count = 0U;
    sequence_number = 0U;
    memset(task_handles, 0, sizeof(task_handles));
    memset(&monitor_stats, 0, sizeof(monitor_stats));
    
    initialized = true;
}

/**
 * @brief Collect OS snapshot with automatic priority classification
 */
void OSMonitor_Collect(void)
{
    if (!initialized) {
        return;
    }
    
    /* Get timestamp */
    uint64_t timestamp = DWT_GetTimestamp();
    
    /* Create snapshot structure */
    OSSnapshot_t snapshot;
    memset(&snapshot, 0, sizeof(snapshot));
    
    snapshot.timestamp_high = (uint32_t)(timestamp >> 32);
    snapshot.timestamp_low = (uint32_t)(timestamp & 0xFFFFFFFF);
    
    /* Collect heap info */
    snapshot.heap_free = xPortGetFreeHeapSize();
    snapshot.heap_min = xPortGetMinimumEverFreeHeapSize();
    
    /* Collect CPU usage (simplified) */
    snapshot.cpu_usage = 50;  /* TODO: Implement actual CPU calculation */
    
    /* Collect task information */
    TaskStatus_t task_status[MAX_TASKS];
    UBaseType_t num_tasks = uxTaskGetSystemState(task_status, MAX_TASKS, NULL);
    
    snapshot.task_count = (num_tasks < MAX_TASKS) ? num_tasks : MAX_TASKS;
    
    for (uint8_t i = 0; i < snapshot.task_count; i++) {
        snapshot.tasks[i].stack_hwm = task_status[i].usStackHighWaterMark;
        snapshot.tasks[i].state = task_status[i].eCurrentState;
        snapshot.tasks[i].priority = task_status[i].uxCurrentPriority;
        snapshot.tasks[i].runtime = task_status[i].ulRunTimeCounter;
    }
    
    /* Classify event priority automatically */
    EventPriority_t priority = EventClassifier_Classify(&snapshot);
    
    /* Update statistics */
    monitor_stats.snapshots_collected++;
    
    switch (priority) {
        case PRIORITY_CRITICAL:
            monitor_stats.critical_events++;
            break;
        case PRIORITY_HIGH:
            monitor_stats.warning_events++;
            break;
        default:
            monitor_stats.normal_events++;
            break;
    }
    
    /* Encode snapshot to binary */
    uint8_t packet[512];
    size_t packet_size = 0;
    
    /* TODO: Implement binary encoding (use existing binary_protocol) */
    /* For now, simplified encoding */
    memcpy(packet, &snapshot, sizeof(snapshot));
    packet_size = sizeof(snapshot);
    
    /* Add CRC32 */
    uint32_t crc = CRC32_Calculate(packet, packet_size);
    memcpy(&packet[packet_size], &crc, sizeof(crc));
    packet_size += sizeof(crc);
    
    /* Write to priority buffer */
    bool success = PriorityBuffer_Write(&output_buffer, packet, packet_size, priority);
    
    if (!success) {
        monitor_stats.buffer_drops++;
    }
    
    /* Log critical events (optional) */
    if (priority == PRIORITY_CRITICAL) {
        char reason[256];
        EventClassifier_GetReason(&snapshot, reason, sizeof(reason));
        /* TODO: Send to debug output */
    }
}

/**
 * @brief Get data from output buffer
 */
size_t OSMonitor_GetData(uint8_t *buffer, size_t max_size)
{
    if (!initialized || buffer == NULL) {
        return 0;
    }
    
    EventPriority_t priority;
    return PriorityBuffer_Read(&output_buffer, buffer, max_size, &priority);
}

/**
 * @brief Get monitor statistics
 */
void OSMonitor_GetStats(OSMonitorStats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    
    stats->snapshots_collected = monitor_stats.snapshots_collected;
    stats->critical_events = monitor_stats.critical_events;
    stats->warning_events = monitor_stats.warning_events;
    stats->normal_events = monitor_stats.normal_events;
    
    /* Get buffer drop statistics */
    uint32_t dropped_low, dropped_normal, dropped_high, dropped_critical;
    PriorityBuffer_GetStats(&output_buffer, 
                           &dropped_low, &dropped_normal, 
                           &dropped_high, &dropped_critical);
    
    stats->buffer_drops_low = dropped_low;
    stats->buffer_drops_normal = dropped_normal;
    stats->buffer_drops_high = dropped_high;
    stats->buffer_drops_critical = dropped_critical;
    
    /* Critical drops should ALWAYS be 0 */
    if (dropped_critical > 0) {
        /* LOG EMERGENCY: This should never happen! */
    }
}

/**
 * @brief Reset statistics
 */
void OSMonitor_ResetStats(void)
{
    memset(&monitor_stats, 0, sizeof(monitor_stats));
    PriorityBuffer_ResetStats(&output_buffer);
}

/**
 * @brief Check if buffer has data available
 */
bool OSMonitor_HasData(void)
{
    return !PriorityBuffer_IsEmpty(&output_buffer);
}

/**
 * @brief Get free space in buffer
 */
size_t OSMonitor_GetFreeSpace(void)
{
    return PriorityBuffer_GetFreeSpace(&output_buffer);
}
