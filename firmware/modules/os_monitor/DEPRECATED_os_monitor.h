#ifndef OS_MONITOR_H
#define OS_MONITOR_H

#include <stdint.h>
#include <stddef.h>

/* Module descriptor */
typedef struct {
    const char *name;
    uint8_t itm_port;
    void (*init)(void);
    void (*collect)(void);
    void (*deinit)(void);
    uint16_t sample_rate_ms;
    bool enabled;
    uint32_t last_run;
    uint32_t run_count;
    uint32_t error_count;
} DebugModule_t;

extern DebugModule_t os_monitor_module;

void OSMonitor_OnContextSwitch(void);
size_t OSMonitor_GetData(uint8_t *buffer, size_t max_size);

#endif
