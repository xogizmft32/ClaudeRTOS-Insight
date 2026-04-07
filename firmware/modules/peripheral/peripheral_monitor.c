#include "peripheral_monitor.h"

static PeripheralMonitor_t *s_monitors[PERIPHERAL_MONITOR_MAX];
static uint32_t             s_count = 0U;

bool PeripheralMonitor_Register(PeripheralMonitor_t *monitor) {
    if ((monitor == NULL) || (s_count >= PERIPHERAL_MONITOR_MAX)) {
        return false;
    }
    s_monitors[s_count++] = monitor;
    return true;
}

void PeripheralMonitor_InitAll(void) {
    for (uint32_t i = 0U; i < s_count; i++) {
        if ((s_monitors[i] != NULL) && (s_monitors[i]->enabled != false) &&
            (s_monitors[i]->init != NULL)) {
            s_monitors[i]->init();
        }
    }
}

void PeripheralMonitor_SampleAll(void) {
    for (uint32_t i = 0U; i < s_count; i++) {
        if ((s_monitors[i] != NULL) && (s_monitors[i]->enabled != false) &&
            (s_monitors[i]->sample != NULL)) {
            s_monitors[i]->sample();
        }
    }
}

uint32_t PeripheralMonitor_GetCount(void) { return s_count; }
