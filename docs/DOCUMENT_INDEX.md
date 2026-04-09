# 문서 인덱스 — ClaudeRTOS-Insight

전체 31개 문서 구조와 용도 안내.  
README의 각 섹션에서 바로 접근 가능합니다.

---

## 🚀 시작하기

| 문서 | 내용 | 비고 |
|------|------|------|
| [GETTING_STARTED.md](GETTING_STARTED.md) | **10분 도입 가이드** | Section A: Nucleo-F446RE, B: 타 MCU |
| [QUICKSTART_COMPLETE_ko.md](QUICKSTART_COMPLETE_ko.md) | 한국어 전체 시작 가이드 | venv, Docker, Replay |
| [QUICKSTART_COMPLETE.md](QUICKSTART_COMPLETE.md) | 영문 전체 시작 가이드 | |
| [QUICK_TROUBLESHOOTING.md](QUICK_TROUBLESHOOTING.md) | 자주 발생하는 문제 해결 | |

---

## ⚙️ 펌웨어 설정

| 문서 | 내용 | 비고 |
|------|------|------|
| [TRACE_GUIDE_ko.md](TRACE_GUIDE_ko.md) | Trace 설정 (한국어) | 카테고리별 On/Off |
| [TRACE_GUIDE.md](TRACE_GUIDE.md) | Trace 설정 (영문) | |
| [FREERTOS_HOOK_GUIDE.md](FREERTOS_HOOK_GUIDE.md) | **FreeRTOS Hook / Trace Macro** | 커널 미수정 확인 포함 |
| [TRANSPORT_GUIDE.md](TRANSPORT_GUIDE.md) | **ITM vs UART** 비교 | 설정, 전환, 한계 |
| [HEISENBUG_GUIDE.md](HEISENBUG_GUIDE.md) | **하이젠버그 방지** | 체크리스트, 패턴 표 |
| [ITM_TROUBLESHOOTING.md](ITM_TROUBLESHOOTING.md) | ITM/SWO 연결 문제 | |

---

## 🤖 AI 분석

| 문서 | 내용 | 비고 |
|------|------|------|
| [AI_USAGE_GUIDE_ko.md](AI_USAGE_GUIDE_ko.md) | AI 사용 흐름 (한국어) | Cache, TokenOptimizer, Queue |
| [AI_USAGE_GUIDE.md](AI_USAGE_GUIDE.md) | AI 사용 가이드 (영문) | |
| [LOCAL_AI_GUIDE.md](LOCAL_AI_GUIDE.md) | **Ollama 로컬 AI** | 모델별 특성, 운영 전략 |
| [PATTERN_GUIDE_ko.md](PATTERN_GUIDE_ko.md) | 패턴 DB 추가·수정·학습 (한국어) | |
| [PATTERN_GUIDE.md](PATTERN_GUIDE.md) | Pattern DB 가이드 (영문) | schema, constraints |

---

## 🏗️ 아키텍처 참조

| 문서 | 내용 | 비고 |
|------|------|------|
| [SYSTEM_REVIEW.md](SYSTEM_REVIEW.md) | **전체 파이프라인** [1]~[18] | 컴포넌트 상세, 알려진 한계 |
| [WCET_ANALYSIS.md](WCET_ANALYSIS.md) | CPU/RAM 오버헤드 추정치 | 추정치임 명시 |
| [PRIORITY_BUFFER_ANALYSIS.md](PRIORITY_BUFFER_ANALYSIS.md) | Priority Buffer 설계 | V1 vs V2 비교 |
| [CONCURRENCY_VERIFICATION.md](CONCURRENCY_VERIFICATION.md) | 동시성 안전성 | lock-free 분석 |

---

## 🌐 운용 환경

| 문서 | 내용 | 비고 |
|------|------|------|
| [OFFLINE_GUIDE.md](OFFLINE_GUIDE.md) | **폐쇄망 / 오프라인** 운용 | wheel/Docker 반입, 체크리스트 |
| [TEST_ENVIRONMENT.md](TEST_ENVIRONMENT.md) | 테스트 환경 요구사항 | 재현성 체크리스트 |

---

## 🔒 품질 / 안전성

| 문서 | 내용 | 비고 |
|------|------|------|
| [MISRA_C_GUIDELINES.md](MISRA_C_GUIDELINES.md) | MISRA C:2012 준수 | Known Deviations 문서화 |
| [FAULT_INJECTION_GUIDE.md](FAULT_INJECTION_GUIDE.md) | **Fault Injection** | OS + Peripheral 8종 |
| [SAFETY_AUDIT_SUMMARY.md](SAFETY_AUDIT_SUMMARY.md) | 안전성 감사 요약 | |
| [SAFETY_DESIGN_GUIDELINES.md](SAFETY_DESIGN_GUIDELINES.md) | 설계 안전성 지침 | |

---

## ✅ 테스트 / 검증

| 문서 | 내용 | 비고 |
|------|------|------|
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | 테스트 시나리오 | Fault Injection, Replay |
| [TESTING_CHECKLIST.md](TESTING_CHECKLIST.md) | 릴리즈 전 체크리스트 | |

---

## 📋 이력 / 기타

| 문서 | 내용 |
|------|------|
| [../CHANGELOG.md](../CHANGELOG.md) | 전체 버전 이력 (v2.3 → v4.9.0) |
| [BUGFIX_REPORT.md](BUGFIX_REPORT.md) | 주요 버그 수정 기록 |

---

## 문서 커버리지 확인

| 기능 | 문서 |
|------|------|
| 설치 (install.py) | QUICKSTART_COMPLETE_ko, GETTING_STARTED |
| 빌드 모드/프로파일 | TRACE_GUIDE, QUICKSTART |
| ITM/UART 선택 | **TRANSPORT_GUIDE** |
| FreeRTOS Hook 설정 | **FREERTOS_HOOK_GUIDE** |
| AI 분석 흐름 | AI_USAGE_GUIDE |
| 패턴 DB 추가 | PATTERN_GUIDE |
| 오프라인 운용 | **OFFLINE_GUIDE** |
| 민감 정보 마스킹 | OFFLINE_GUIDE, AI_USAGE_GUIDE |
| 로컬 AI | **LOCAL_AI_GUIDE** |
| Peripheral 디버깅 | FAULT_INJECTION_GUIDE |
| 세션 로깅/보고서 | SYSTEM_REVIEW |
| 하이젠버그 방지 | **HEISENBUG_GUIDE** |
| 릴리즈 빌드 | TRACE_GUIDE |
| MISRA 준수 | **MISRA_C_GUIDELINES** |
| Hallucination 대응 | SYSTEM_REVIEW |
