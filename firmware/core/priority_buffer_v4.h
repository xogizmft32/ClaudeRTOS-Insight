/* Priority Ring Buffer V4 - Production-Safe Implementation
 * GUARANTEED Critical Event Protection + Complete Safety Checks
 * 
 * Safety Features:
 * - Buffer overflow protection
 * - Array bounds checking  
 * - Assert protection
 * - Input validation
 * - Structure integrity checks
 * - Watchdog integration
 * - Error logging
 * - Double init protection
 * 
 * Thread-Safe: Yes (FreeRTOS critical sections)
 * ISR-Safe: Yes (separate FromISR functions)
 * 
 * Safety-Critical Design - ⚠️ NOT CERTIFIED
 */

#ifndef PRIORITY_BUFFER_V4_H
#define PRIORITY_BUFFER_V4_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "FreeRTOS.h"
#include "task.h"

/* Configuration */
#define PRIORITY_MAX_PACKETS 50
#define CRITICAL_RESERVED_PACKETS 10
#define NORMAL_MAX_PACKETS (PRIORITY_MAX_PACKETS - CRITICAL_RESERVED_PACKETS)
#define PRIORITY_BUFFER_MAGIC 0xDEADBEEF  /* Structure integrity check */
#define MAX_PACKET_SIZE 512  /* Maximum allowed packet size */

/* Safety macros */
#ifndef configASSERT
#define configASSERT(x) if(!(x)) { taskDISABLE_INTERRUPTS(); for(;;); }
#endif

/* Error logging */
#define PRIORITY_BUFFER_LOG_ERROR(msg) \
    do { if (error_log_callback) error_log_callback(msg); } while(0)

/**
 * @brief Event priority levels
 */
typedef enum {
    PRIORITY_CRITICAL  = 0,
    PRIORITY_HIGH      = 1,
    PRIORITY_NORMAL    = 2,
    PRIORITY_LOW       = 3,
    PRIORITY_INVALID   = 0xFF
} EventPriority_t;

/**
 * @brief Buffer state
 */
typedef enum {
    BUFFER_STATE_UNINITIALIZED = 0,
    BUFFER_STATE_INITIALIZED   = 1,
    BUFFER_STATE_CORRUPTED     = 2,
    BUFFER_STATE_SHUTDOWN      = 3
} BufferState_t;

/**
 * @brief Error codes
 */
typedef enum {
    BUFFER_OK = 0,
    BUFFER_ERROR_NULL_POINTER = 1,
    BUFFER_ERROR_INVALID_SIZE = 2,
    BUFFER_ERROR_BUFFER_FULL = 3,
    BUFFER_ERROR_CORRUPTED = 4,
    BUFFER_ERROR_NOT_INITIALIZED = 5,
    BUFFER_ERROR_DOUBLE_INIT = 6,
    BUFFER_ERROR_OVERFLOW = 7,
    BUFFER_ERROR_BOUNDS = 8
} BufferError_t;

/**
 * @brief Production-safe priority buffer with full safety checks
 */
typedef struct {
    /* Structure integrity */
    uint32_t magic_start;  /* Must be PRIORITY_BUFFER_MAGIC */
    BufferState_t state;
    
    /* Main buffer storage */
    uint8_t *buffer;
    size_t capacity;
    
    /* Normal buffer (80%) */
    volatile size_t normal_start;
    volatile size_t normal_end;
    volatile size_t normal_write_index;
    volatile size_t normal_read_index;
    volatile uint16_t normal_packet_count;
    
    /* Reserved buffer (20%) - CRITICAL only */
    volatile size_t reserved_start;
    volatile size_t reserved_end;
    volatile size_t reserved_write_index;
    volatile size_t reserved_read_index;
    volatile uint16_t reserved_packet_count;
    
    /* Packet tracking */
    uint8_t normal_priority_map[NORMAL_MAX_PACKETS];
    size_t normal_packet_sizes[NORMAL_MAX_PACKETS];
    size_t reserved_packet_sizes[CRITICAL_RESERVED_PACKETS];
    
    /* Statistics */
    volatile uint32_t dropped_low;
    volatile uint32_t dropped_normal;
    volatile uint32_t dropped_high;
    volatile uint32_t dropped_critical;
    volatile uint32_t total_writes;
    volatile uint32_t total_reads;
    volatile uint32_t total_drops;
    volatile uint32_t critical_writes;
    volatile uint32_t error_count;
    volatile BufferError_t last_error;
    
    /* Safety features */
    volatile uint32_t watchdog_counter;
    volatile uint32_t max_critical_section_time_us;
    
    /* Structure integrity */
    uint32_t magic_end;  /* Must be PRIORITY_BUFFER_MAGIC */
    
} PriorityBufferV4_t;

/* Error logging callback type */
typedef void (*ErrorLogCallback_t)(const char *message);

/**
 * @brief Set error logging callback
 */
void PriorityBufferV4_SetErrorCallback(ErrorLogCallback_t callback);

/**
 * @brief Initialize production-safe priority buffer
 * 
 * @param buf Pointer to buffer structure
 * @param storage Pointer to storage array
 * @param capacity Total size of storage in bytes
 * @return Error code
 * 
 * Safety checks:
 * - Validates all pointers
 * - Checks capacity limits
 * - Prevents double initialization
 * - Sets magic numbers
 * 
 * Thread-safe: Yes
 */
BufferError_t PriorityBufferV4_Init(PriorityBufferV4_t *buf, 
                                    uint8_t *storage, 
                                    size_t capacity);

/**
 * @brief Write data with GUARANTEED critical protection (from Task)
 * 
 * @param buf Buffer to write to
 * @param data Data to write
 * @param len Length of data
 * @param priority Event priority
 * @return Error code (BUFFER_OK on success)
 * 
 * Safety checks:
 * - Validates all pointers
 * - Checks buffer overflow (len vs capacity)
 * - Checks array bounds
 * - Verifies structure integrity
 * - Validates priority
 * - Kicks watchdog
 * 
 * Thread-safe: Yes
 * ISR-safe: NO - use PriorityBufferV4_WriteFromISR
 * 
 * WCET: < 35 µs @ 180MHz (with safety checks)
 */
BufferError_t PriorityBufferV4_Write(PriorityBufferV4_t *buf,
                                     const uint8_t *data,
                                     size_t len,
                                     EventPriority_t priority);

/**
 * @brief Write data from ISR (interrupt context)
 * 
 * @param buf Buffer to write to
 * @param data Data to write
 * @param len Length of data
 * @param priority Event priority
 * @param pxHigherPriorityTaskWoken Set if context switch needed
 * @return Error code
 * 
 * Thread-safe: Yes
 * ISR-safe: YES
 */
BufferError_t PriorityBufferV4_WriteFromISR(PriorityBufferV4_t *buf,
                                            const uint8_t *data,
                                            size_t len,
                                            EventPriority_t priority,
                                            BaseType_t *pxHigherPriorityTaskWoken);

/**
 * @brief Read oldest packet (from Task)
 * 
 * @param buf Buffer to read from
 * @param data Output buffer
 * @param max_len Maximum bytes to read
 * @param out_priority Output priority (optional)
 * @return Number of bytes read (0 on error/empty)
 * 
 * Thread-safe: Yes
 * ISR-safe: NO
 */
size_t PriorityBufferV4_Read(PriorityBufferV4_t *buf,
                             uint8_t *data,
                             size_t max_len,
                             EventPriority_t *out_priority);

/**
 * @brief Read oldest packet from ISR
 * 
 * Thread-safe: Yes
 * ISR-safe: YES
 */
size_t PriorityBufferV4_ReadFromISR(PriorityBufferV4_t *buf,
                                    uint8_t *data,
                                    size_t max_len,
                                    EventPriority_t *out_priority,
                                    BaseType_t *pxHigherPriorityTaskWoken);

/**
 * @brief Verify buffer integrity
 * 
 * Checks:
 * - Magic numbers
 * - State validity
 * - Index bounds
 * - Packet counts
 * 
 * @param buf Buffer to verify
 * @return true if valid
 */
bool PriorityBufferV4_Verify(const PriorityBufferV4_t *buf);

/**
 * @brief Get drop statistics (thread-safe)
 */
void PriorityBufferV4_GetStats(const PriorityBufferV4_t *buf,
                               uint32_t *out_low,
                               uint32_t *out_normal,
                               uint32_t *out_high,
                               uint32_t *out_critical);

/**
 * @brief Get error information
 */
BufferError_t PriorityBufferV4_GetLastError(const PriorityBufferV4_t *buf);

/**
 * @brief Get error string
 */
const char* PriorityBufferV4_GetErrorString(BufferError_t error);

/**
 * @brief Safe shutdown
 * 
 * Marks buffer as shutdown, prevents further writes
 */
void PriorityBufferV4_Shutdown(PriorityBufferV4_t *buf);

/**
 * @brief Reset statistics
 */
void PriorityBufferV4_ResetStats(PriorityBufferV4_t *buf);

/* Memory barrier helpers */
#define DMB() __asm__ volatile ("dmb" : : : "memory")
#define DSB() __asm__ volatile ("dsb" : : : "memory")
#define ISB() __asm__ volatile ("isb" : : : "memory")

/* Watchdog kick (to be implemented by application) */
extern void Application_KickWatchdog(void);

#endif /* PRIORITY_BUFFER_V4_H */

/* ── FIX-10: IsEmpty API (thread-safe, no internal field access) ── */

/**
 * @brief Check if buffer has no data (thread-safe)
 *
 * FIX-10: HasData()가 내부 필드를 직접 접근하는 버그 수정.
 * 이 함수를 통해 critical section 안에서 안전하게 확인.
 *
 * @param buf Buffer to check
 * @return true if completely empty
 */
bool PriorityBufferV4_IsEmpty(const PriorityBufferV4_t *buf);
