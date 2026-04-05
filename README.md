# ClaudeRTOS-Insight

**AI-assisted FreeRTOS/STM32 Real-Time Debugging System**

[![Version](https://img.shields.io/badge/version-4.2.0-blue.svg)](https://github.com/xogizmft32/ClaudeRTOS-Insight)
[![Validation](https://img.shields.io/badge/validation-20%2F20%20PASS-green.svg)](examples/integrated_demo.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Built with Vibe Coding](https://img.shields.io/badge/built%20with-vibe%20coding-purple.svg)](#about-vibe-coding)

---

## 🎵 About Vibe Coding

이 프로젝트는 **바이브 코딩(Vibe Coding)** 방식으로 시작되었습니다.

바이브 코딩은 2025년 OpenAI 공동창업자 Andrej Karpathy가 제안한 개발 패러다임으로,
자연어로 의도를 설명하면 AI가 코드를 생성하고, 개발자는 방향성과 품질을 검토하는
인간-AI 협업 방식입니다.

이 프로젝트에서는 임베디드 시스템 도메인 지식과 AI의 코드 생성 능력을 결합해서
FreeRTOS/STM32 디버깅 시스템을 반복적으로 설계하고 구현했습니다.

```
개발 방식:
  사람  → 도메인 지식, 아키텍처 방향, 품질 검토
  AI    → 코드 생성, 검증, 문서화, 리팩토링
  결과  → v2.3 → v3.x → , 99개 파일, 20/20 검증 통과
```

> "The hottest new programming language is English." — Andrej Karpathy

---

## Overview

ClaudeRTOS-Insight는 FreeRTOS/STM32 임베디드 시스템을 위한 AI 기반 실시간 디버깅 시스템입니다.

- **펌웨어**: STM32 Nucleo-F446RE (Cortex-M4, 180MHz)에서 OS 상태·이벤트 수집
- **호스트**: N100 PC에서 다단계 로컬 분석 + 선택적 AI 심층 분석
- **비용**: postmortem 모드 기준 ~$0.015/이슈, Ollama 사용 시 $0

---

## Key Features

| 기능 | 내용 |
|------|------|
| **Trace V2** | Lock-free ring buffer (LDREX/STREX), DWT CYCCNT/EXCCNT, 0.028% CPU |
| **Time Normalizer** | CYCCNT cycles / RTOS tick / packet ts → 통합 µs 타임라인 |
| **Event Priority Queue** | CRITICAL(즉시)/HIGH/MEDIUM/LOW 호스트 분석 우선순위 |
| **Resource Graph** | Mutex hold/wait DAG, Deadlock cycle DFS 탐지 |
| **State Machine** | Task 상태 전이 추적, 장기 Blocked/Starvation 감지 |
| **Causal Graph** | DAG 기반 multi-root cause 인과관계 분석 |
| **Orchestrator** | Rule + Pattern + Graph + LLM 통합, 교차 검증 |
| **AI Provider** | Anthropic/OpenAI/Google/Ollama 1줄 교체 |
| **Pattern DB** | KP-001~005 JSON 선언적 패턴, Few-shot 학습 |
| **Binary Protocol V4** | WIRE_PUT 매크로, endian 명시, V3 하위 호환 |

---

## Quick Start

```bash
# 설치
tar -xzf ClaudeRTOS-Insight--FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0
python3 install.py --project /path/to/my_stm32_project

# 검증 (하드웨어 불필요)
python3 examples/integrated_demo.py --validate

# 호스트 연결 (기본: Anthropic Claude)
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink

# 다른 AI 사용 (코드 변경 없이)
export CLAUDERTOS_AI_PROVIDER=openai   # GPT-4o
export CLAUDERTOS_AI_PROVIDER=ollama   # 로컬, 비용 $0
python3 examples/integrated_demo.py --port jlink
```

---

## Analysis Pipeline ()

```
STM32 Firmware (180MHz)
  └── Binary Protocol V4 (ITM/UART)
         │
Host Analysis Pipeline
  ├─ [1] binary_parser        V3/V4 패킷 파싱
  ├─ [2] time_normalizer      CYCCNT / tick / packet ts → 통합 µs  ★NEW
  ├─ [3] analyzer             Rule-based 이슈 감지 (<1ms)
  ├─ [4] event_queue          우선순위별 분석 라우팅             ★NEW
  ├─ [5] prefilter            PatternDB KP 매칭 ($0)
  │       └─ ConstraintChecker  temporal/pair/monotonic
  ├─ [6] correlation_engine   CORR-001~006 (evidence 기반 confidence)
  ├─ [7] state_machine        SM-001~003 상태 전이 추적
  ├─ [8] resource_graph       RG-001~002 Mutex DAG + Deadlock DFS
  ├─ [9] orchestrator         결과 통합 + 교차 검증 (+0.12)
  ├─ [10] causal_graph        DAG multi-root cause 분석           ★NEW
  ├─ [11] token_optimizer     컨텍스트 압축
  └─ [12] AI Provider         Cloud or Local LLM
           anthropic / openai / google / ollama
```

**로컬 분석 전체: < 1ms** — AI는 필요할 때만 호출

---

## Context Structure ()

```json
{
  "session":    {"cpu_hz": 180000000, "isr": {...}},
  "system":     {"cpu_pct": 88, "heap": {...}},
  "tasks":      [...],
  "events":     [...],
  "resources":  {"mutex_holds": {...}, "mutex_waits": {...}},
  "anomalies":  [...],
  "candidates": [{"id":"RG-001","cross_validated":true,...}]
}
```

---

## AI Provider 교체

```python
# 환경 변수 (코드 변경 없음)
export CLAUDERTOS_AI_PROVIDER=openai
export CLAUDERTOS_AI_PROVIDER=ollama   # $0

# 코드에서
debugger = RTOSDebuggerV3(provider='google')
debugger = RTOSDebuggerV3(
    provider='openai_compat',
    base_url='https://api.together.xyz/v1',
    tier1_model='meta-llama/Llama-3.1-70B-Instruct',
)
```

| Provider | Tier1 | Tier2 | 비용/이슈 |
|----------|-------|-------|----------|
| anthropic | claude-sonnet-4-6 | claude-haiku-4-5 | ~$0.0085 |
| openai | gpt-4o | gpt-4o-mini | ~$0.0072 |
| google | gemini-1.5-pro | gemini-1.5-flash | ~$0.0060 |
| ollama | llama3.1:8b | qwen2.5:3b | **$0** |

---

## File Structure

```
firmware/
  core/       binary_protocol V4, trace_events V2, transport
  modules/    os_monitor V3, event_classifier
  port/       port.h, cortex_m4/, esp32/
  examples/   demo/main.c, FreeRTOSConfig.h

host/
  ai/
    providers/  base, anthropic, openai, google, ollama, factory
    rtos_debugger.py   (provider-agnostic)
    response_parser.py
  analysis/
    analyzer.py, debugger_context.py
    time_normalizer.py  ★ 타임스탬프 통합
    event_queue.py      ★ 호스트 우선순위 큐
    correlation_engine.py, orchestrator.py
    state_machine.py, resource_graph.py
    causal_graph.py     ★ DAG 인과관계
  local_analyzer/  prefilter, token_optimizer, local_llm
  parsers/         binary_parser (V3/V4)
  patterns/        known_patterns.json, pattern_db.py, session_learner.py

docs/              EN + KO (_ko suffix) — 22개 문서
install.py         자동 통합 설치기
```

---

## Version History

| 버전 | 날짜 | 주요 변경 |
|------|------|---------|
| **4.2.0** | 2026-04-03 | TimeNormalizer, EventPriorityQueue, CausalGraph(DAG), Context 구조화 |
| 4.1.0 | 2026-04-01 | Resource Graph(deadlock DFS), State Machine, Orchestrator, Confidence Calibration |
| 4.0.0 | 2026-03-31 | AI Provider 추상화 (anthropic/openai/google/ollama), 문서 전면 개정 |
| 3.9.1 | 2026-03-31 | Trace V2 (lock-free, DWT CYCCNT/EXCCNT, 0.028% CPU) |
| 3.9.0 | 2026-03-29 | Pattern DB JSON, Binary Protocol V4, Causal Chain |
| 3.8.0 | 2026-03-28 | AI 구조화 JSON 출력, Correlation Engine, 시나리오 분기 |
| 3.7.0 | 2026-03-28 | Port Layer (Cortex-M4/ESP32), 로컬 분석기 |

전체 이력: [CHANGELOG.md](CHANGELOG.md)

---

## Documentation

| 문서 | 설명 |
|------|------|
| [QUICKSTART_COMPLETE](docs/QUICKSTART_COMPLETE.md) / [ko](docs/QUICKSTART_COMPLETE_ko.md) | 설치~디버깅 전 과정 |
| [AI_USAGE_GUIDE](docs/AI_USAGE_GUIDE.md) / [ko](docs/AI_USAGE_GUIDE_ko.md) | AI 모드·비용·Provider |
| [TRACE_GUIDE](docs/TRACE_GUIDE.md) / [ko](docs/TRACE_GUIDE_ko.md) | Trace V2 상세 |
| [SYSTEM_REVIEW](docs/SYSTEM_REVIEW.md) | 전체 아키텍처 |
| [TESTING_GUIDE](docs/TESTING_GUIDE.md) | 테스트 방법 |
| [WCET_ANALYSIS](docs/WCET_ANALYSIS.md) | 최악 실행 시간 |

---

## Validation

```
Protocol validation:    20/20 PASS
Pipeline simulation:    7/7  PASS ( 신규)
Analysis latency:       0.06ms/cycle (N100 47,563× headroom)
AI cost (anthropic):    ~$0.0085/issue (Critical)
AI cost (ollama):       $0
```

---

**Target:** STM32F446RE @ 180MHz  
**RTOS:** FreeRTOS 10.0+  
**Host:** N100 / Linux / Python 3.8+  
**Started:** Vibe Coding with Claude (Anthropic)
