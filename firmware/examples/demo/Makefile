# Makefile — ClaudeRTOS-Insight V3.2
# Target: STM32 Nucleo-F446RE (STM32F446RE @ 180MHz)
#
# 전송 모드 선택:
#   make              → ITM (기본)
#   make TRANSPORT=UART → UART (115200 baud via ST-Link VCOM)

PROJECT = claudertos_demo

# 전송 모드
TRANSPORT ?= ITM
ifeq ($(TRANSPORT),UART)
  TRANSPORT_DEF = -DCLAUDERTOS_TRANSPORT_UART
else
  TRANSPORT_DEF = -DCLAUDERTOS_TRANSPORT_ITM
endif

# 툴체인
PREFIX   = arm-none-eabi-
CC       = $(PREFIX)gcc
OBJCOPY  = $(PREFIX)objcopy
SIZE     = $(PREFIX)size

# MCU
MCU = -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard

# 정의
DEFS = -DSTM32F446xx -DUSE_HAL_DRIVER $(TRANSPORT_DEF)

# 최적화
OPT = -O2 -g3

# C 플래그
CFLAGS  = $(MCU) $(DEFS) $(OPT)
CFLAGS += -Wall -Wextra -Werror
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -std=c11

# 링커 플래그
LDFLAGS  = $(MCU) $(OPT)
LDFLAGS += -specs=nano.specs
LDFLAGS += -Wl,--gc-sections
LDFLAGS += -Wl,-Map=$(PROJECT).map
LDFLAGS += -T STM32F446RETx_FLASH.ld

# ── 소스 ────────────────────────────────────────────────
CORE_DIR   = ../../core
MODULE_DIR = ../../modules
FREERTOS_DIR = FreeRTOS

CORE_SRC = \
    $(CORE_DIR)/binary_protocol.c \
    $(CORE_DIR)/crc32.c \
    $(CORE_DIR)/dwt_timestamp.c \
    $(CORE_DIR)/priority_buffer_v4.c \
    $(CORE_DIR)/rate_controller.c \
    $(CORE_DIR)/ring_buffer.c \
    $(CORE_DIR)/transport.c \
    $(CORE_DIR)/trace_events.c

MODULE_SRC = \
    $(MODULE_DIR)/adaptive_sampler.c \
    $(MODULE_DIR)/event_classifier.c \
    $(MODULE_DIR)/os_monitor/os_monitor_v3.c \
    $(MODULE_DIR)/time_sync.c

FREERTOS_SRC = \
    $(FREERTOS_DIR)/tasks.c \
    $(FREERTOS_DIR)/queue.c \
    $(FREERTOS_DIR)/list.c \
    $(FREERTOS_DIR)/timers.c \
    $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c \
    $(FREERTOS_DIR)/portable/MemMang/heap_4.c

APP_SRC = main.c

SOURCES = $(APP_SRC) $(CORE_SRC) $(MODULE_SRC) $(FREERTOS_SRC)
OBJECTS = $(SOURCES:.c=.o)

# ── 인클루드 ──────────────────────────────────────────────
INCLUDES = \
    -I$(CORE_DIR) \
    -I$(MODULE_DIR) \
    -I$(MODULE_DIR)/os_monitor \
    -I$(FREERTOS_DIR)/include \
    -I$(FREERTOS_DIR)/portable/GCC/ARM_CM4F

# ── 빌드 타깃 ─────────────────────────────────────────────
all: $(PROJECT).elf $(PROJECT).bin
	@echo "Build complete! [Transport=$(TRANSPORT)]"
	@$(SIZE) $(PROJECT).elf

$(PROJECT).elf: $(OBJECTS)
	@echo "Linking..."
	$(CC) $(LDFLAGS) -o $@ $(OBJECTS)

$(PROJECT).bin: $(PROJECT).elf
	$(OBJCOPY) -O binary $< $@

%.o: %.c
	@echo "CC $<"
	$(CC) $(CFLAGS) $(INCLUDES) -c $< -o $@

# ── 플래시 ────────────────────────────────────────────────
flash: $(PROJECT).bin
	openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
	    -c "program $(PROJECT).bin verify reset exit 0x08000000"

flash-stlink: $(PROJECT).bin
	st-flash write $(PROJECT).bin 0x08000000

# ── 정리 ─────────────────────────────────────────────────
clean:
	rm -f $(OBJECTS) $(PROJECT).elf $(PROJECT).bin $(PROJECT).map

.PHONY: all clean flash flash-stlink
