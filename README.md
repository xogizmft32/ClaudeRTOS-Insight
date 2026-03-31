# ClaudeRTOS-Insight v3.9.1

**AI-assisted FreeRTOS/STM32 Real-Time Debugging System**

[![Version](https://img.shields.io/badge/version-3.9.1-blue.svg)](https://github.com/xogizmft32/ClaudeRTOS-Insight)
[![Validation](https://img.shields.io/badge/validation-20%2F20%20PASS-green.svg)](examples/integrated_demo.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Overview

ClaudeRTOS-Insight는 FreeRTOS/STM32 임베디드 시스템을 위한 AI 기반 실시간 디버깅 시스템입니다.

- **펌웨어**: STM32 Nucleo-F446RE (Cortex-M4, 180MHz)에서 OS 상태·이벤트 수집
- **호스트**: N100 PC에서 로컬 분석 + Claude/GPT/Gemini/Ollama AI 심층 분석
- **비용**: postmortem 모드 기준 ~$0.015/이슈, Ollama 사용 시 $0

---

## Key Features

| 기능 | 내용 |
|------|------|
| **Trace V2** | Lock-free ring buffer (LDREX/STREX), DWT CYCCNT/EXCCNT, 0.028% CPU |
| **AI Provider 추상화** | Anthropic/OpenAI/Google/Ollama 1줄 교체 |
| **Correlation Engine** | CORR-001~006, causal chain (max 10 steps) |
| **Pattern DB** | KP-001~005 JSON 선언적 패턴, 사용자 확장 가능 |
| **Binary Protocol V4** | WIRE_PUT 매크로, endian 명시, V3 하위 호환 |
| **Port Layer** | Cortex-M4 + ESP32, 신규 MCU 이식 1파일 |
| **구조화 JSON 출력** | root_cause_candidates, confidence, causal_chain |

---

## Quick Start

```bash
# 설치
python3 install.py --project /path/to/my_stm32_project

# 검증 (하드웨어 불필요)
python3 examples/integrated_demo.py --validate

# 호스트 연결 (기본: Anthropic Claude)
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink

# 다른 AI 사용 (코드 변경 없이)
export CLAUDERTOS_AI_PROVIDER=openai
export OPENAI_API_KEY=sk-...
python3 examples/integrated_demo.py --port jlink

# 로컬 AI (비용 0)
export CLAUDERTOS_AI_PROVIDER=ollama
python3 examples/integrated_demo.py --port jlink
```

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  STM32 Nucleo-F446RE (Firmware)                      │
│                                                      │
│  MonitorTask (1Hz)                                   │
│    ├── OSMonitorV3_Collect()   ← CPU%, heap, tasks   │
│    ├── TraceEvents_SampleISRCount()  ← DWT EXCCNT    │
│    └── TraceEvents_Read()      ← ctx_switch, mutex   │
│                                                      │
│  Trace Hooks (FreeRTOS):                             │
│    traceTASK_SWITCHED_IN/OUT   ← ~25 cycles          │
│    traceTAKE/GIVE_MUTEX        ← ~25 cycles          │
│    DWT EXCCNT (ISR frequency)  ← 0 cycles            │
│                                                      │
│  Binary Protocol V4 (ITM/UART)                       │
└──────────────────┬───────────────────────────────────┘
                   │ SWO / UART
┌──────────────────▼───────────────────────────────────┐
│  Host (N100 PC)                                      │
│                                                      │
│  binary_parser.py    ← V3/V4 패킷 파싱               │
│  analyzer.py         ← Rule-based 이슈 감지 (<1ms)   │
│  correlation_engine  ← multi-event causal chain      │
│  prefilter.py        ← PatternDB KP 매칭 (비용 $0)   │
│  token_optimizer.py  ← 컨텍스트 압축                  │
│                                                      │
│  AI Provider (교체 가능):                             │
│    AnthropicProvider  → Claude Sonnet/Haiku           │
│    OpenAIProvider     → GPT-4o / GPT-4o-mini          │
│    GoogleProvider     → Gemini 1.5 Pro/Flash           │
│    OllamaProvider     → Llama3/Qwen2.5 (로컬, $0)    │
│                                                      │
│  response_parser.py  ← 구조화 JSON → ParsedResponse  │
└──────────────────────────────────────────────────────┘
```

---

## AI Provider 교체

```python
from ai.rtos_debugger import RTOSDebuggerV3

# Anthropic Claude (기본)
debugger = RTOSDebuggerV3()

# OpenAI GPT-4o
debugger = RTOSDebuggerV3(provider='openai')

# Google Gemini
debugger = RTOSDebuggerV3(provider='google')

# 로컬 Ollama (비용 0)
debugger = RTOSDebuggerV3(provider='ollama')

# 모델 직접 지정
debugger = RTOSDebuggerV3(
    provider='openai',
    tier1_model='gpt-4o',
    tier2_model='gpt-4o-mini',
)

# Together.ai (OpenAI 호환)
debugger = RTOSDebuggerV3(
    provider='openai_compat',
    base_url='https://api.together.xyz/v1',
    tier1_model='meta-llama/Llama-3.1-70B-Instruct',
    tier2_model='meta-llama/Llama-3.1-8B-Instruct',
)
```

---

## Trace Overhead

| 방법 | 오버헤드 | 정보 |
|------|---------|------|
| DWT EXCCNT (HW) | **0 cycles** | ISR 진입 횟수 |
| Context switch hook | **~25 cycles** | 전환 순서·타이밍 |
| Mutex hook | **~25 cycles** | lock/unlock 타이밍 |
| **합계 @ 1kHz** | **0.028% CPU** | |

---

## File Structure

```
firmware/
  core/           binary_protocol V4, trace_events V2, transport
  modules/        os_monitor V3 (port-based), event_classifier
  port/           port.h interface, cortex_m4/, esp32/
  examples/demo/  main.c, FreeRTOSConfig.h, Makefile

host/
  ai/
    providers/    base.py, anthropic.py, openai.py, google.py, ollama.py, factory.py
    rtos_debugger.py  (provider-agnostic)
    response_parser.py
  analysis/       analyzer.py, correlation_engine.py, debugger_context.py
  local_analyzer/ prefilter.py, token_optimizer.py, local_llm.py
  parsers/        binary_parser.py (V3/V4)
  patterns/       known_patterns.json, pattern_db.py

docs/             EN + KO (_ko suffix) documentation
install.py        Auto-integration installer
```

---

## Documentation

| 문서 | 내용 |
|------|------|
| [QUICKSTART_COMPLETE.md](docs/QUICKSTART_COMPLETE.md) / [_ko](docs/QUICKSTART_COMPLETE_ko.md) | 설치~AI 디버깅 전 과정 |
| [AI_USAGE_GUIDE.md](docs/AI_USAGE_GUIDE.md) / [_ko](docs/AI_USAGE_GUIDE_ko.md) | AI 모드·비용·Provider 가이드 |
| [TRACE_GUIDE.md](docs/TRACE_GUIDE.md) / [_ko](docs/TRACE_GUIDE_ko.md) | Trace V2 상세 |
| [TESTING_GUIDE.md](docs/TESTING_GUIDE.md) | 테스트 방법 |
| [WCET_ANALYSIS.md](docs/WCET_ANALYSIS.md) | 최악 실행 시간 분석 |

---

## Version History

| 버전 | 날짜 | 주요 변경 |
|------|------|---------|
| **3.9.1** | 2026-03-31 | Trace V2 (lock-free, DWT CYCCNT/EXCCNT), AI Provider 추상화 |
| 3.9.0 | 2026-03-29 | Pattern DB JSON, Binary Protocol V4, Causal Chain 설정 가능 |
| 3.8.0 | 2026-03-28 | AI 구조화 JSON, Correlation Engine, 시나리오 분기 |
| 3.7.0 | 2026-03-28 | Port Layer, 로컬 분석기 (PreFilter/LocalLLM/TokenOptimizer) |
| 3.6.0 | 2026-03-27 | 비용 최적화 (심각도별 모델 분기, 79% 절감) |
| 3.5.0 | 2026-03-26 | install.py, AI 모드 (offline/postmortem/realtime) |

전체 이력: [CHANGELOG.md](CHANGELOG.md)

---

**Target:** STM32F446RE @ 180MHz | **RTOS:** FreeRTOS 10.0+ | **Validation:** 20/20 PASS
