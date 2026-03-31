/* Adaptive Rate Controller Implementation */

#include "rate_controller.h"

void RateController_Init(RateController_t *controller,
                         uint16_t min_rate_ms,
                         uint16_t max_rate_ms,
                         RatePolicy_t policy)
{
    if (controller == NULL) {
        return;
    }
    
    controller->min_rate_ms = min_rate_ms;
    controller->max_rate_ms = max_rate_ms;
    controller->current_rate_ms = (min_rate_ms + max_rate_ms) / 2U;  /* Start at midpoint */
    controller->cpu_threshold_high = 80U;   /* 80% CPU → slow down */
    controller->cpu_threshold_low = 40U;    /* 40% CPU → speed up */
    controller->buffer_threshold_high = 52428U;  /* 80% of 64KB */
    controller->buffer_threshold_low = 26214U;   /* 40% of 64KB */
    controller->policy = policy;
}

uint16_t RateController_Adjust(RateController_t *controller,
                                uint8_t cpu_usage,
                                uint32_t buffer_used)
{
    uint16_t new_rate;
    bool slow_down = false;
    bool speed_up = false;
    
    if (controller == NULL) {
        return 1000U;  /* Default 1Hz */
    }
    
    /* Fixed policy - no adjustment */
    if (controller->policy == RATE_POLICY_FIXED) {
        return controller->current_rate_ms;
    }
    
    new_rate = controller->current_rate_ms;
    
    /* Check CPU-based policy */
    if ((controller->policy == RATE_POLICY_ADAPTIVE_CPU) ||
        (controller->policy == RATE_POLICY_ADAPTIVE_BOTH)) {
        
        if (cpu_usage > controller->cpu_threshold_high) {
            slow_down = true;  /* CPU overloaded */
        } else if (cpu_usage < controller->cpu_threshold_low) {
            speed_up = true;   /* CPU idle */
        }
    }
    
    /* Check buffer-based policy */
    if ((controller->policy == RATE_POLICY_ADAPTIVE_BUFFER) ||
        (controller->policy == RATE_POLICY_ADAPTIVE_BOTH)) {
        
        if (buffer_used > controller->buffer_threshold_high) {
            slow_down = true;  /* Buffer filling up */
        } else if (buffer_used < controller->buffer_threshold_low) {
            speed_up = true;   /* Buffer has space */
        }
    }
    
    /* Apply adjustments */
    if (slow_down) {
        /* Decrease sampling frequency (increase period) */
        new_rate = new_rate * 2U;
        if (new_rate > controller->max_rate_ms) {
            new_rate = controller->max_rate_ms;
        }
    } else if (speed_up) {
        /* Increase sampling frequency (decrease period) */
        new_rate = (new_rate * 3U) / 4U;  /* 25% faster (gradual) */
        if (new_rate < controller->min_rate_ms) {
            new_rate = controller->min_rate_ms;
        }
    }
    
    controller->current_rate_ms = new_rate;
    return new_rate;
}

uint16_t RateController_GetRate(const RateController_t *controller)
{
    if (controller == NULL) {
        return 1000U;
    }
    return controller->current_rate_ms;
}

void RateController_SetPolicy(RateController_t *controller, RatePolicy_t policy)
{
    if (controller == NULL) {
        return;
    }
    controller->policy = policy;
}
