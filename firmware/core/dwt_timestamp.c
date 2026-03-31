/* DWT Timestamp Implementation */

#include "dwt_timestamp.h"

/* Static variables for rollover tracking */
static uint32_t dwt_cpu_freq_hz = 0U;
static uint32_t dwt_last_cycles = 0U;
static uint32_t dwt_rollover_count = 0U;
static bool dwt_initialized = false;

/* Cycles per microsecond */
static uint32_t dwt_cycles_per_us = 0U;

bool DWT_Init(uint32_t cpu_freq_hz)
{
    /* Validate input */
    if ((cpu_freq_hz < 1000000U) || (cpu_freq_hz > 1000000000U)) {
        return false;  /* Invalid frequency */
    }
    
    /* Enable trace */
    CoreDebug_DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    
    /* Reset and enable cycle counter */
    DWT_CYCCNT = 0U;
    DWT_CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    
    /* Store configuration */
    dwt_cpu_freq_hz = cpu_freq_hz;
    dwt_cycles_per_us = cpu_freq_hz / 1000000U;
    dwt_last_cycles = 0U;
    dwt_rollover_count = 0U;
    dwt_initialized = true;
    
    return true;
}

/* Rollover detection thresholds */
#define DWT_ROLLOVER_THRESHOLD  0x80000000U  /* 50% mark */
#define DWT_MAX_REASONABLE_DELTA 0x40000000U /* 25% max jump */

/* Error tracking */
static uint32_t dwt_error_count = 0U;

uint64_t DWT_GetTimestamp_us(void)
{
    uint32_t current_cycles;
    uint32_t delta;
    uint64_t total_cycles;
    uint64_t timestamp_us;
    bool rollover_detected = false;
    
    /* Check initialization */
    if (!dwt_initialized) {
        return 0ULL;
    }
    
    /* Read current cycle count (atomic) */
    current_cycles = DWT_CYCCNT;
    
    /* Calculate delta (handles wraparound) */
    if (current_cycles >= dwt_last_cycles) {
        delta = current_cycles - dwt_last_cycles;
    } else {
        /* Potential rollover or backward jump */
        delta = (0xFFFFFFFFU - dwt_last_cycles) + current_cycles + 1U;
    }
    
    /* Enhanced rollover detection with error checking */
    if ((dwt_last_cycles > DWT_ROLLOVER_THRESHOLD) &&
        (current_cycles < DWT_ROLLOVER_THRESHOLD) &&
        (delta < DWT_MAX_REASONABLE_DELTA)) {
        /* Valid rollover: last value in upper half, current in lower half */
        dwt_rollover_count++;
        rollover_detected = true;
    }
    else if (current_cycles < dwt_last_cycles && 
             delta > DWT_MAX_REASONABLE_DELTA) {
        /* Abnormal backward jump - possible error */
        dwt_error_count++;
        /* Don't increment rollover count */
        /* Use last valid value (defensive) */
        current_cycles = dwt_last_cycles;
    }
    
    dwt_last_cycles = current_cycles;
    
    /* Calculate total cycles (64-bit) */
    total_cycles = ((uint64_t)dwt_rollover_count << 32) | (uint64_t)current_cycles;
    
    /* Convert to microseconds */
    timestamp_us = total_cycles / (uint64_t)dwt_cycles_per_us;
    
    return timestamp_us;
}

uint32_t DWT_GetErrorCount(void)
{
    return dwt_error_count;
}

uint32_t DWT_GetCycles(void)
{
    return DWT_CYCCNT;
}

uint32_t DWT_GetRolloverCount(void)
{
    return dwt_rollover_count;
}

void DWT_Reset(void)
{
    DWT_CYCCNT = 0U;
    dwt_last_cycles = 0U;
    dwt_rollover_count = 0U;
}
