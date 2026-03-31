/* Lock-Free Ring Buffer Implementation */

#include "ring_buffer.h"
#include <string.h>

/* Memory barrier for ARM Cortex-M */
#define MEMORY_BARRIER()  __asm__ __volatile__ ("dmb" ::: "memory")

void RingBuffer_Init(RingBuffer_t *rb)
{
    if (rb == NULL) {
        return;
    }
    
    rb->write_pos = 0U;
    rb->read_pos = 0U;
    rb->overflow_count = 0U;
    rb->dropped_bytes = 0U;
    
    /* Clear buffer (optional, for determinism) */
    memset(rb->buffer, 0, RING_BUFFER_SIZE);
}

bool RingBuffer_Write(RingBuffer_t *rb, const uint8_t *data, size_t length)
{
    uint32_t write_pos;
    uint32_t read_pos;
    uint32_t available_space;
    size_t i;
    
    /* Input validation */
    if ((rb == NULL) || (data == NULL) || (length == 0U)) {
        return false;
    }
    
    if (length > RING_BUFFER_SIZE) {
        return false;  /* Too large */
    }
    
    /* Read positions (volatile reads) */
    write_pos = rb->write_pos;
    read_pos = rb->read_pos;
    
    /* Calculate available space (leave 1 byte gap) */
    available_space = (read_pos - write_pos - 1U) & RING_BUFFER_MASK;
    
    if (length > available_space) {
        rb->overflow_count++;
        return false;  /* Buffer full */
    }
    
    /* Copy data */
    for (i = 0U; i < length; i++) {
        rb->buffer[(write_pos + i) & RING_BUFFER_MASK] = data[i];
    }
    
    /* Memory barrier before updating write position */
    MEMORY_BARRIER();
    
    /* Update write position (atomic) */
    rb->write_pos = (write_pos + length) & RING_BUFFER_MASK;
    
    return true;
}

bool RingBuffer_Write_Policy(RingBuffer_t *rb, const uint8_t *data, 
                              size_t length, OverflowPolicy_t policy)
{
    uint32_t write_pos;
    uint32_t read_pos;
    uint32_t available_space;
    size_t i;
    
    /* Input validation */
    if ((rb == NULL) || (data == NULL) || (length == 0U)) {
        return false;
    }
    
    if (length > RING_BUFFER_SIZE) {
        return false;  /* Too large */
    }
    
    /* Read positions (volatile reads) */
    write_pos = rb->write_pos;
    read_pos = rb->read_pos;
    
    /* Calculate available space (leave 1 byte gap) */
    available_space = (read_pos - write_pos - 1U) & RING_BUFFER_MASK;
    
    if (length > available_space) {
        /* Buffer full - apply policy */
        rb->overflow_count++;
        
        if (policy == OVERFLOW_DROP_OLDEST) {
            /* Make room by advancing read pointer (drop oldest) */
            size_t needed = length - available_space;
            
            /* Safety limit: don't drop more than MAX_PACKET_SIZE */
            if (needed > 512U) {  /* MAX_PACKET_SIZE = 512 */
                needed = 512U;
            }
            
            /* Advance read pointer (drop old data) */
            read_pos = (read_pos + needed) & RING_BUFFER_MASK;
            
            /* Memory barrier before updating read position */
            MEMORY_BARRIER();
            rb->read_pos = read_pos;
            rb->dropped_bytes += needed;
            
            /* Recalculate available space */
            available_space = (read_pos - write_pos - 1U) & RING_BUFFER_MASK;
            
            /* Check if we have enough space now */
            if (length > available_space) {
                return false;  /* Still not enough space */
            }
        } else {
            /* OVERFLOW_DROP_NEWEST: reject new data */
            return false;
        }
    }
    
    /* Copy data */
    for (i = 0U; i < length; i++) {
        rb->buffer[(write_pos + i) & RING_BUFFER_MASK] = data[i];
    }
    
    /* Memory barrier before updating write position */
    MEMORY_BARRIER();
    
    /* Update write position (atomic) */
    rb->write_pos = (write_pos + length) & RING_BUFFER_MASK;
    
    return true;
}

size_t RingBuffer_Read(RingBuffer_t *rb, uint8_t *data, size_t max_length)
{
    uint32_t write_pos;
    uint32_t read_pos;
    uint32_t available_data;
    size_t to_read;
    size_t i;
    
    /* Input validation */
    if ((rb == NULL) || (data == NULL) || (max_length == 0U)) {
        return 0U;
    }
    
    /* Read positions */
    write_pos = rb->write_pos;
    read_pos = rb->read_pos;
    
    /* Calculate available data */
    available_data = (write_pos - read_pos) & RING_BUFFER_MASK;
    
    if (available_data == 0U) {
        return 0U;  /* Buffer empty */
    }
    
    /* Determine how much to read */
    to_read = (max_length < available_data) ? max_length : available_data;
    
    /* Copy data */
    for (i = 0U; i < to_read; i++) {
        data[i] = rb->buffer[(read_pos + i) & RING_BUFFER_MASK];
    }
    
    /* Memory barrier before updating read position */
    MEMORY_BARRIER();
    
    /* Update read position */
    rb->read_pos = (read_pos + to_read) & RING_BUFFER_MASK;
    
    return to_read;
}

size_t RingBuffer_Available(const RingBuffer_t *rb)
{
    uint32_t write_pos;
    uint32_t read_pos;
    
    if (rb == NULL) {
        return 0U;
    }
    
    write_pos = rb->write_pos;
    read_pos = rb->read_pos;
    
    return (write_pos - read_pos) & RING_BUFFER_MASK;
}

size_t RingBuffer_Free(const RingBuffer_t *rb)
{
    uint32_t write_pos;
    uint32_t read_pos;
    
    if (rb == NULL) {
        return 0U;
    }
    
    write_pos = rb->write_pos;
    read_pos = rb->read_pos;
    
    /* Leave 1 byte gap to distinguish full/empty */
    return ((read_pos - write_pos - 1U) & RING_BUFFER_MASK);
}

RingBufferStatus_t RingBuffer_GetStatus(const RingBuffer_t *rb)
{
    size_t used;
    
    if (rb == NULL) {
        return RING_BUFFER_OVERFLOW;
    }
    
    used = RingBuffer_Available(rb);
    
    if (used >= RING_BUFFER_WARNING_THRESHOLD) {
        return RING_BUFFER_WARNING;
    }
    
    if (rb->overflow_count > 0U) {
        return RING_BUFFER_OVERFLOW;
    }
    
    return RING_BUFFER_OK;
}

uint32_t RingBuffer_GetOverflowCount(const RingBuffer_t *rb)
{
    if (rb == NULL) {
        return 0U;
    }
    
    return rb->overflow_count;
}

void RingBuffer_Clear(RingBuffer_t *rb)
{
    if (rb == NULL) {
        return;
    }
    
    rb->write_pos = 0U;
    rb->read_pos = 0U;
    rb->overflow_count = 0U;
}
