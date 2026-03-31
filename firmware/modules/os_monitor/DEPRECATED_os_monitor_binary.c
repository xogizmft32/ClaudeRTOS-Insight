/* OS Monitor Module - Binary Protocol
 * Replaces JSON with efficient binary format
 * Safety-Critical Design - Follows IEC 61508 principles
 */

#include "os_monitor.h"
#include "binary_protocol.h"
#include "crc32.h"
#include "dwt_timestamp.h"
#include "ring_buffer.h"
#include "FreeRTOS.h"
#include "task.h"
#include <string.h>

/* Configuration */
#define OS_MONITOR_PORT 0U
#define OS_MONITOR_RATE_MS 1000U
#define MAX_TASKS 16U

/* Module state */
static bool initialized = false;
static uint32_t context_switches = 0U;
static uint16_t sequence_number = 0U;
static RingBuffer_t output_buffer;

/* Task ID mapping */
static TaskHandle_t task_handles[MAX_TASKS];
static uint8_t task_count = 0U;

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

/* Module initialization */
static void os_monitor_init(void)
{
    RingBuffer_Init(&output_buffer);
    initialized = true;
    context_switches = 0U;
    sequence_number = 0U;
    task_count = 0U;
}

/* Module collection */
static void os_monitor_collect(void)
{
    uint8_t packet_buffer[MAX_PACKET_SIZE];
    OSSnapshotPacket_t *packet;
    TaskInfo_t *task_info;
    TaskStatus_t task_array[MAX_TASKS];
    UBaseType_t num_tasks;
    uint32_t total_runtime;
    size_t packet_size;
    size_t pos;
    UBaseType_t i;
    
    if (!initialized) {
        return;
    }
    
    packet = (OSSnapshotPacket_t *)packet_buffer;
    
    /* Initialize header */
    Protocol_InitHeader(&packet->header, OS_MONITOR_PORT, 
                       PACKET_TYPE_OS_SNAPSHOT, sequence_number++);
    
    /* Fill system info */
    packet->tick = xTaskGetTickCount();
    packet->context_switches = context_switches;
    packet->heap_free = xPortGetFreeHeapSize();
    packet->heap_min = xPortGetMinimumEverFreeHeapSize();
    packet->cpu_usage = 0U;  /* Calculated from runtime stats */
    
    /* Get task list */
    num_tasks = uxTaskGetSystemState(task_array, MAX_TASKS, &total_runtime);
    packet->num_tasks = (uint8_t)num_tasks;
    
    /* Position after header */
    pos = sizeof(OSSnapshotPacket_t);
    
    /* Add task info */
    for (i = 0U; (i < num_tasks) && (i < MAX_TASKS); i++) {
        if ((pos + sizeof(TaskInfo_t)) > (MAX_PACKET_SIZE - 4U)) {
            break;  /* No space */
        }
        
        task_info = (TaskInfo_t *)&packet_buffer[pos];
        task_info->task_id = get_task_id(task_array[i].xHandle);
        task_info->priority = (uint8_t)task_array[i].uxCurrentPriority;
        task_info->state = (uint8_t)task_array[i].eCurrentState;
        task_info->padding = 0U;
        task_info->stack_hwm = task_array[i].usStackHighWaterMark;
        task_info->reserved = 0U;
        task_info->runtime_us = task_array[i].ulRunTimeCounter;
        
        pos += sizeof(TaskInfo_t);
    }
    
    /* Append CRC32 */
    packet_size = CRC32_Append(packet_buffer, pos);
    
    /* Write to ring buffer */
    if (!RingBuffer_Write(&output_buffer, packet_buffer, packet_size)) {
        /* Buffer overflow - handled by ring buffer */
    }
}

/* Module cleanup */
static void os_monitor_deinit(void)
{
    initialized = false;
}

/* Context switch hook */
void OSMonitor_OnContextSwitch(void)
{
    context_switches++;
}

/* Get data for transmission */
size_t OSMonitor_GetData(uint8_t *buffer, size_t max_size)
{
    if (!initialized || (buffer == NULL)) {
        return 0U;
    }
    
    return RingBuffer_Read(&output_buffer, buffer, max_size);
}

/* Module descriptor */
DebugModule_t os_monitor_module = {
    .name = "OS_Monitor",
    .itm_port = OS_MONITOR_PORT,
    .init = os_monitor_init,
    .collect = os_monitor_collect,
    .deinit = os_monitor_deinit,
    .sample_rate_ms = OS_MONITOR_RATE_MS,
    .enabled = true,
    .last_run = 0U,
    .run_count = 0U,
    .error_count = 0U
};
