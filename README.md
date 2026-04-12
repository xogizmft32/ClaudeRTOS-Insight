# ClaudeRTOS-Insight

**AI 보조 설계(AI-Assisted Design) × FreeRTOS/STM32 실시간 디버깅 시스템**

[![Version](https://img.shields.io/badge/version-4.9.6-blue.svg)](CHANGELOG.md)
[![Validation](https://img.shields.io/badge/validation-20%2F20%20PASS-green.svg)](examples/integrated_demo.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![AI-Assisted Design](https://img.shields.io/badge/built%20with-AI--Assisted%20Design-blue.svg)](#about-ai-assisted-design)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](.python-version)

---

## 🤖 About AI-Assisted Design

이 프로젝트는 **AI 보조 설계(AI-Assisted Design)** 방법론으로 개발됐습니다.
CAD(Computer-Aided Design)처럼 AI가 설계 도구로 활용되며, 도메인 전문가가 방향과 품질을 주도합니다.

```
사람  → 도메인 지식, 아키텍처 설계, 검토·검증, 요구사항 정의
AI    → 코드 생성, 문서화, 리팩토링, 시뮬레이션 검증
결과  → v2.3 → v4.9.6, 135개 파일, 20/20 검증 통과
```

---

## 개요

FreeRTOS/STM32 임베디드 시스템을 위한 AI 기반 실시간 디버깅 시스템.

- **펌웨어**: STM32 Nucleo-F446RE (Cortex-M4, 180MHz) — OS 상태·이벤트 수집
- **호스트**: 다단계 로컬 분석 파이프라인 + 선택적 AI 심층 분석
- **비용**: postmortem 모드 ~$0.0085/이슈, Ollama 사용 시 $0
- **오버헤드**: 0.028% CPU, 4KB RAM (PROFILE_STANDARD 기준, 추정치)

**→ 10분 도입 가이드: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**

---

## 주요 기능

| 기능 | 내용 |
|------|------|
| **Binary Protocol V4** | WIRE_PUT 매크로, endian 명시, V3 하위 호환 |
| **Trace V2** | lock-free ring buffer (LDREX/STREX), DWT CYCCNT/EXCCNT |
| **Time Normalizer** | CYCCNT/tick/packet → 통합 µs, wrap-around 자동 보정 |
| **Event Priority Queue** | CRITICAL 즉시/HIGH/MEDIUM/LOW, Aging + Rate Limiting |
| **Correlation Engine** | CORR-001~006, evidence 기반 confidence |
| **Resource Graph** | Mutex hold/wait DAG, Deadlock cycle DFS 탐지 |
| **State Machine** | SM-001~003, 장기 Blocked/Starvation 감지 |
| **Causal Graph** | GlobalCausalGraph 세션 누산 DAG, Mermaid 출력 |
| **Orchestrator** | Rule+Pattern+Graph+LLM 통합, 교차 검증 (+0.12) |
| **Confidence Propagation** | 부모 이슈 confidence → 자식 이슈 자동 상향 |
| **AI Provider** | Anthropic/OpenAI/Google/Ollama 환경 변수 1줄 교체 |
| **Pattern DB** | KP-001~005 JSON, 세션 학습 자동 누산 |
| **Trend Analyzer** | CPU/Heap 슬로프 분석, 포화·고갈 예측 (추정치) |
| **Anomaly Scorer** | Z-score 기반 이상 수치화 |
| **Few-shot Injector** | 과거 해결 사례 자동 주입 → AI 가설 품질 향상 |
| **Hallucination Guard** | AI 응답 주장 vs 실제 데이터 자동 대조 검증 |
| **Context Masker** | 4단계 민감 정보 마스킹 + 프로젝트별 금지 목록 |
| **Alert Manager** | console/file/webhook 다중 채널 Critical 알림 |
| **Session Logger** | .log / .jsonl / .csv 구조화 세션 로깅 |
| **Debug Report** | 세션 결과 Markdown 자동 보고서, Mermaid 인과 다이어그램 |
| **Resource Reporter** | CPU/RAM 오버헤드 자동 보고서 (릴리즈 시점) |
| **Peripheral Monitor** | GPIO 글리치, I2C NACK/Timeout, SPI 오버런 감지 |
| **CORR-007~009** | 페리페럴 ↔ 태스크/CPU/Heap 상관관계 분석 |
| **Peripheral Context** | `peripheral_state` → AI 컨텍스트 자동 삽입 |
| **OS 추상화 (Port Layer)** | insight_port_os.h — RTOS 교체 시 1파일만 수정 |

---

## 빠른 시작

```bash
# 압축 해제 + 의존성 설치
tar -xzf ClaudeRTOS-Insight-v4.9.2-FINAL.tar.gz
cd ClaudeRTOS-Insight-v2.5.0
python3 -m venv .venv && source .venv/bin/activate
pip install -r host/requirements.txt

# 내 프로젝트에 통합
python3 install.py --project /path/to/my_stm32_project
python3 install.py --project /path --peripheral  # GPIO/I2C 모니터 포함

# 검증 (하드웨어 불필요, 1분)
python3 examples/integrated_demo.py --validate

# 실제 연결
export ANTHROPIC_API_KEY=sk-ant-...
python3 examples/integrated_demo.py --port jlink            # J-Link ITM
python3 examples/integrated_demo.py --port uart:/dev/ttyUSB0  # UART
python3 examples/integrated_demo.py --ai-mode offline       # AI 없이 로컬만
```

**→ 상세: [docs/QUICKSTART_COMPLETE_ko.md](docs/QUICKSTART_COMPLETE_ko.md)**

> **PacketRecorder 사용 시 주의**: `start()` 호출 후 `record(snapshot_dict)` 순서를 지켜야 합니다.
> `with PacketRecorder(path) as rec:` context manager가 `start()`를 자동 호출합니다.

> **CorrelationEngine 주의**: CORR-001~006은 `mutex_timeout` 등 특정 이벤트 타입이
> 타임라인에 있어야 발동합니다. Deadlock 탐지는 ResourceGraph(RG-001)가 담당합니다.

---

## 빌드 모드 & 프로파일

| 명령 | 모드 | RAM | CPU | AI |
|------|------|-----|-----|-----|
| `make RELEASE=1` | Zero footprint | **0 B** | **0%** | 없음 (릴리즈 빌드) |
| `make DEBUG=1 PROFILE=LITE` | STAT 모드 | ~28 B | <0.005% | offline 권장 |
| `make DEBUG=1` | STANDARD (기본) | ~4 KB | ~0.028% | postmortem 권장 |
| `make DEBUG=1 PROFILE=EXPERT` | FULL/512이벤트 | ~8 KB | ~0.05% | realtime 가능 |

> ⚠ CPU/RAM 수치는 추정치입니다. 실제 값은 환경에 따라 다릅니다.

---

## 분석 파이프라인

```
STM32 Firmware (Cortex-M4 @ 180MHz)
  ├─ TraceEvents V2  (lock-free LDREX/STREX, DWT CYCCNT/EXCCNT)
  └─ Binary Protocol V4  →  ITM(SWO) or UART

Host Analysis Pipeline
  [1]  collector           ITM/UART 수신
  [2]  binary_parser       V3/V4 파싱, CRC, seq gap 감지
  [3]  time_normalizer     CYCCNT/tick → 통합 µs
  [4]  replay              PacketRecorder / SessionReplayer
  [5]  analyzer            Rule-based 이슈 감지 (<1ms)
  [6]  event_queue         CRITICAL즉시/HIGH/MEDIUM/LOW
  [7]  prefilter           PatternDB KP 매칭 + 페리페럴 패턴 6개 ($0)
  [8]  correlation_engine  CORR-001~006
  [9]  state_machine       SM-001~003 상태 전이
  [10] resource_graph      RG-001~002 데드락 DFS
  [11] orchestrator        통합 + 교차검증 + Confidence Propagation
  [12] causal_graph        GlobalCausalGraph DAG + Mermaid 출력
  [13] trend_analyzer      CPU/Heap 슬로프 + Anomaly Scoring
  [14] few_shot_injector   과거 유사 사례 자동 주입
  [15] token_optimizer     컨텍스트 압축
  [16] context_masker      민감 정보 마스킹 (+ SecretsConfig)
  [17] AI Provider         Cloud or Local LLM
  [18] hallucination_guard AI 주장 vs 실제 데이터 자동 검증

로컬 분석 전체: < 1ms — AI는 필요할 때만 호출
```

---

## AI Provider

```bash
export CLAUDERTOS_AI_PROVIDER=anthropic   # 기본 (Claude Sonnet)
export CLAUDERTOS_AI_PROVIDER=openai      # GPT-4o
export CLAUDERTOS_AI_PROVIDER=google      # Gemini Pro
export CLAUDERTOS_AI_PROVIDER=ollama       # 로컬, 비용 $0
export CLAUDERTOS_AI_PROVIDER=claude_agent # Agent SDK 에이전트 루프
export CLAUDERTOS_AI_PROVIDER=gemini_cli   # Gemini CLI headless (무료)
```

| Provider | 비용/이슈 | 특징 |
|----------|----------|------|
| anthropic | ~$0.0085 | 임베디드 도메인 품질 우수 |
| claude_agent | ~$0.0085 | **에이전트 루프** (multi-turn 자율 분석) |
| openai | ~$0.0072 | 균형형 |
| google | ~$0.0060 | 저비용 (REST API) |
| gemini_cli | **$0 (OAuth)** | Gemini CLI headless, 무료 사용 가능 |
| ollama | **$0** | 로컬, 오프라인, llama3.1:8b 권장 |
| claude_agent | ~$0.0085 | **Agent SDK 에이전트 루프** — 다회 추론 |
| gemini_cli | **$0** | Gemini CLI headless, OAuth 무료 티어 |

---

## 보안 / 오프라인 환경

```bash
# 민감 정보 마스킹
export CLAUDERTOS_MASK_LEVEL=names      # 태스크/Mutex 이름 익명화
export CLAUDERTOS_MASK_LEVEL=addresses  # + 메모리 주소
export CLAUDERTOS_MASK_LEVEL=strict     # + IRQ 번호

# 프로젝트별 금지 목록 (.claudertos_secrets.json)
python3 -c "from analysis.context_masker import SecretsConfig; SecretsConfig.create_template()"

# 완전 오프라인
python3 examples/integrated_demo.py --port jlink --ai-mode offline
```

**→ 폐쇄망 운용: [docs/OFFLINE_GUIDE.md](docs/OFFLINE_GUIDE.md)**

---

## 배포 방식

| 방식 | 명령 | 대상 환경 |
|------|------|---------|
| Python 직접 | `pip install -r host/requirements.txt` | 개발자 |
| Docker | `docker-compose run --rm claudertos-host` | 팀 배포 |
| Single Binary | `./build_binary.sh` → `dist/claudertos` | 현장 배포 (Python 불필요) |

```bash
# Docker 빌드
docker-compose up -d

# Single Binary 빌드
./build_binary.sh            # 현재 OS용
./build_binary.sh --docker   # Linux 바이너리 (Docker)
```

---

## 파일 구조

```
firmware/
  core/              binary_protocol V4, trace_events V2, transport
  modules/           os_monitor V3, event_classifier, adaptive_sampler
    peripheral/      peripheral_monitor, gpio_monitor, i2c_monitor
  port/              port.h, insight_port_os.h
    cortex_m4/       port_impl.c
    esp32/           port_impl.c
    freertos/        insight_port_os.c  ← OS 추상화 FreeRTOS 구현
  tests/             fault_injection (OS+Peripheral 8종)
  examples/demo/     main.c, FreeRTOSConfig.h, Makefile

host/
  ai/                rtos_debugger, response_parser, response_cache
    providers/       anthropic, openai, google, ollama
    hallucination_guard.py
  analysis/          analyzer, correlation_engine, state_machine
                     resource_graph, orchestrator, causal_graph
                     time_normalizer, event_queue, analysis_context
                     alert_manager, context_masker
                     trend_analyzer, few_shot_injector
                     resource_reporter, session_logger, debug_report
  local_analyzer/    prefilter, token_optimizer, local_llm
  parsers/           binary_parser (V3/V4)
  patterns/          known_patterns.json, pattern_db.py, session_learner
    peripheral/      gpio_patterns.json, i2c_patterns.json
  replay.py          PacketRecorder + SessionReplayer
  claudertos_main.py CLI 진입점 (PyInstaller용)

docs/               31개 문서 (→ 아래 문서 목록)
claudertos.spec     PyInstaller 빌드 스펙
build_binary.sh     Single-file binary 빌드 스크립트
install.py          자동 통합 설치기 v4.0
Dockerfile          컨테이너 이미지
docker-compose.yml  멀티컨테이너 (host + ollama + replay)
```

---

## 📚 문서 목록

> 전체 인덱스: [docs/DOCUMENT_INDEX.md](docs/DOCUMENT_INDEX.md)

### 🚀 시작하기

| 문서 | 내용 |
|------|------|
| [GETTING_STARTED.md](docs/GETTING_STARTED.md) | **10분 도입 가이드** — Nucleo-F446RE / 타 MCU |
| [QUICKSTART_COMPLETE_ko.md](docs/QUICKSTART_COMPLETE_ko.md) | 한국어 전체 시작 가이드 |
| [QUICKSTART_COMPLETE.md](docs/QUICKSTART_COMPLETE.md) | 영문 전체 시작 가이드 |
| [QUICK_TROUBLESHOOTING.md](docs/QUICK_TROUBLESHOOTING.md) | 자주 발생하는 문제 해결 |

### ⚙️ 펌웨어 설정

| 문서 | 내용 |
|------|------|
| [TRACE_GUIDE_ko.md](docs/TRACE_GUIDE_ko.md) | Trace 설정, 카테고리 On/Off (한국어) |
| [TRACE_GUIDE.md](docs/TRACE_GUIDE.md) | Trace 설정 (영문) |
| [FREERTOS_HOOK_GUIDE.md](docs/FREERTOS_HOOK_GUIDE.md) | **FreeRTOS Hook / Trace Macro** 사용법 |
| [TRANSPORT_GUIDE.md](docs/TRANSPORT_GUIDE.md) | **ITM vs UART** 비교, 설정, 전환 방법 |
| [HEISENBUG_GUIDE.md](docs/HEISENBUG_GUIDE.md) | **하이젠버그 방지** — 관측 영향 최소화 |
| [ITM_TROUBLESHOOTING.md](docs/ITM_TROUBLESHOOTING.md) | ITM/SWO 연결 문제 해결 |

### 🤖 AI 분석

| 문서 | 내용 |
|------|------|
| [AI_USAGE_GUIDE_ko.md](docs/AI_USAGE_GUIDE_ko.md) | AI 사용 흐름, 캐시, 비용 (한국어) |
| [AI_USAGE_GUIDE.md](docs/AI_USAGE_GUIDE.md) | AI 사용 가이드 (영문) |
| [LOCAL_AI_GUIDE.md](docs/LOCAL_AI_GUIDE.md) | **Ollama 로컬 AI** — 오프라인 AI 분석 |
| [GEMINI_CLI_GUIDE.md](docs/GEMINI_CLI_GUIDE.md) | **Gemini CLI** — 무료 OAuth, headless 설정 |
| [CLAUDE_AGENT_GUIDE.md](docs/CLAUDE_AGENT_GUIDE.md) | **Claude Agent SDK** — 에이전트 루프 분석 |
| [GEMINI_CLI_GUIDE.md](docs/GEMINI_CLI_GUIDE.md) | **Gemini CLI** — 무료 Google AI 분석 |
| [CLAUDE_AGENT_GUIDE.md](docs/CLAUDE_AGENT_GUIDE.md) | **Claude Agent SDK** — 에이전트 루프 분석 |
| [PATTERN_GUIDE_ko.md](docs/PATTERN_GUIDE_ko.md) | 패턴 DB 추가·수정·학습 (한국어) |
| [PATTERN_GUIDE.md](docs/PATTERN_GUIDE.md) | Pattern DB 가이드 (영문) |

### 🏗️ 아키텍처 참조

| 문서 | 내용 |
|------|------|
| [SYSTEM_REVIEW.md](docs/SYSTEM_REVIEW.md) | **전체 파이프라인 [1]~[18]** 컴포넌트 상세 |
| [WCET_ANALYSIS.md](docs/WCET_ANALYSIS.md) | CPU/RAM 오버헤드 추정치 |
| [PRIORITY_BUFFER_ANALYSIS.md](docs/PRIORITY_BUFFER_ANALYSIS.md) | Priority Buffer 설계 분석 |
| [CONCURRENCY_VERIFICATION.md](docs/CONCURRENCY_VERIFICATION.md) | 동시성 안전성 검증 |

### 🌐 운용 환경

| 문서 | 내용 |
|------|------|
| [OFFLINE_GUIDE.md](docs/OFFLINE_GUIDE.md) | **폐쇄망 / 오프라인** 운용, wheel 반입 |
| [TEST_ENVIRONMENT.md](docs/TEST_ENVIRONMENT.md) | 테스트 환경 요구사항, 재현성 체크리스트 |

### 🔒 품질 / 안전성

| 문서 | 내용 |
|------|------|
| [MISRA_C_GUIDELINES.md](docs/MISRA_C_GUIDELINES.md) | MISRA C:2012 준수 현황, Known Deviations |
| [FAULT_INJECTION_GUIDE.md](docs/FAULT_INJECTION_GUIDE.md) | Fault Injection (OS + Peripheral 8종) |
| [SAFETY_AUDIT_SUMMARY.md](docs/SAFETY_AUDIT_SUMMARY.md) | 안전성 감사 요약 |
| [SAFETY_DESIGN_GUIDELINES.md](docs/SAFETY_DESIGN_GUIDELINES.md) | 설계 안전성 지침 |

### ✅ 테스트 / 검증

| 문서 | 내용 |
|------|------|
| [TESTING_GUIDE.md](docs/TESTING_GUIDE.md) | 테스트 시나리오, 검증 방법 |
| [TESTING_CHECKLIST.md](docs/TESTING_CHECKLIST.md) | 릴리즈 전 체크리스트 |

### 📋 이력 / 기타

| 문서 | 내용 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 전체 버전 이력 (v2.3 → v4.9.4) |
| [BUGFIX_REPORT.md](docs/BUGFIX_REPORT.md) | 주요 버그 수정 기록 |

---

## 검증 결과

```
Protocol validation:    20/20 PASS
전 과정 시뮬레이션:      19/19 PASS
분석 파이프라인:         < 1.08ms/사이클 (N100 기준, 추정치)
AI context:             ~111 tokens
Hallucination Guard:    trust_score 자동 산출
주장성 '보장' 표현:      전체 문서 없음 (추정치·설계상·확인됨으로 대체)
```

---

**타깃**: STM32F446RE (Cortex-M4, 180MHz) | **RTOS**: FreeRTOS 10.0+ | **호스트**: Python 3.11+
**전체 이력**: [CHANGELOG.md](CHANGELOG.md) | **개발**: AI 보조 설계 × Claude (Anthropic)
