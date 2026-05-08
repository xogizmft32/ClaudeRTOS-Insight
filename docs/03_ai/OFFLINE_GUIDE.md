# 오프라인 운용 가이드 — ClaudeRTOS-Insight
# Offline & Air-Gapped Operation Guide

> Deploy and operate ClaudeRTOS-Insight without network access. All local analysis runs with zero network dependency; AI analysis uses Ollama or cached responses.

인터넷 불가 / 폐쇄망 / 보안망 환경에서의 사용 방법.

---

## 오프라인에서 동작하는 기능
*Features That Work Without Any Network Connection*

| 기능 | 구성 요소 | 비고 |
|------|---------|------|
| Rule-based 감지 | `analyzer.py` | <1ms |
| PatternDB 진단 | `prefilter.py` | KP-001~005 + custom |
| Correlation 패턴 | `correlation_engine.py` | CORR-001~006 |
| Task 상태 추적 | `state_machine.py` | SM-001~003 |
| Deadlock 탐지 | `resource_graph.py` | DFS |
| 결과 통합 | `orchestrator.py` | 교차검증 |
| 인과관계 그래프 | `causal_graph.py` | GlobalCausalGraph |
| 이벤트 큐 | `event_queue.py` | Aging/RateLimit |
| 알림 | `alert_manager.py` | 콘솔/파일 |
| 세션 재생 | `replay.py` | PacketRecorder/Replayer |
| Mermaid 출력 | `causal_graph.to_mermaid()` | 파일로 저장 |
| Context 마스킹 | `context_masker.py` | 민감 정보 보호 |
| AI 캐시 재사용 | `response_cache.py` | 사전 저장분 |
| 로컬 AI | Ollama | 모델 사전 다운로드 필요 |

네트워크 필요: Claude/GPT/Gemini API, Ollama 모델 다운로드 (최초 1회)

---

## 운용 모드
*Operation Modes — Choose Based on Your Network Constraints*

### 완전 오프라인 (AI 없음) / Fully Offline (No AI)
```bash
python3 examples/integrated_demo.py --port jlink --ai-mode offline
```
Rule + PatternDB + Correlation + Graph 전체 동작, 비용 $0

### 로컬 AI (Ollama) / Local AI with Ollama
```bash
ollama serve &
export CLAUDERTOS_AI_PROVIDER=ollama
python3 examples/integrated_demo.py --port jlink
```
전체 파이프라인 + 로컬 LLM, 비용 $0  
단, 구조화 JSON 신뢰도는 클라우드 대비 낮음 (llama3.1:8b 권장)

### AI 캐시 재활용 / Reusing Cached AI Responses
이전 온라인 세션의 응답을 오프라인에서 재사용:
```python
debugger.save_session(auto_save=True)   # 온라인 세션 종료 시
# → ~/.claudertos_cache/ai_responses.json 자동 로드됨
```

---

## 폐쇄망 반입 방법
*How to Transfer Files Into an Air-Gapped Network*

### A: Python wheel 패키지 (권장) / Method A: Python Wheel Packages (Recommended)
```bash
# 온라인 환경에서 준비
mkdir offline_wheels
pip download -r host/requirements.txt -d offline_wheels/

# 오프라인 환경에서 설치
pip install --no-index --find-links=offline_wheels/ -r host/requirements.txt
python3 examples/integrated_demo.py --validate   # 37/37 PASS 확인
```

### B: Docker 이미지 반입 / Method B: Docker Image Transfer
```bash
# 온라인: 저장
docker-compose build
docker save claudertos-insight:latest -o claudertos-image.tar

# 오프라인: 로드
docker load -i claudertos-image.tar
docker-compose run --rm claudertos-host --validate
```

### C: Ollama 모델 반입 / Method C: Ollama Model Transfer
```bash
# 온라인: 모델 파일 압축
tar -czf ollama_models.tar.gz ~/.ollama/models/

# 오프라인: 복원 후 사용
tar -xzf ollama_models.tar.gz -C ~/
ollama serve &
export CLAUDERTOS_AI_PROVIDER=ollama
```

---

## 반입 체크리스트
*Air-Gap Transfer Checklist*

```
☐ ClaudeRTOS-Insight-vX.X.X-FINAL.tar.gz
☐ offline_wheels/ (pip wheel 파일)
☐ Python 3.11+ 설치 확인
☐ ~/.claudertos_cache/ai_responses.json (캐시, 선택)
☐ custom_patterns.json (학습 패턴, 선택)

Ollama 사용 시:
☐ ollama_models.tar.gz
☐ ollama 바이너리

Docker 사용 시:
☐ claudertos-image.tar
```

---

## 민감 정보 보호 (부분 네트워크 환경)
*Sensitive Data Protection in Partially Connected Environments*

VPN 등으로 제한적 클라우드 접근이 가능한 경우:
```bash
export CLAUDERTOS_MASK_LEVEL=names      # 태스크/Mutex 이름 익명화
export CLAUDERTOS_MASK_LEVEL=addresses  # 이름 + 메모리 주소
export CLAUDERTOS_MASK_LEVEL=strict     # 전체 마스킹
```
자세한 내용: `host/analysis/context_masker.py`


---

## 네트워크 없는 환경 (폐쇄망) 설치
*Installation in a Fully Air-Gapped Environment*

### pip 의존성 오프라인 설치 / Offline pip Dependency Installation

인터넷이 연결된 별도 시스템에서 `.whl` 파일을 미리 다운로드한다.

```bash
# [인터넷 연결 시스템에서] 의존성 다운로드
mkdir -p ~/claudertos_wheels
pip download -r host/requirements.txt -d ~/claudertos_wheels

# USB/파일 전송 후 [폐쇄망 시스템에서] 설치
pip install --no-index --find-links ~/claudertos_wheels \
    -r host/requirements.txt
```

### Docker 이미지 오프라인 전송 / Offline Docker Image Transfer

```bash
# [인터넷 연결 시스템에서] 이미지 저장
docker-compose build
docker save claudertos-host:latest | gzip > claudertos_image.tar.gz

# [폐쇄망 시스템에서] 이미지 로드
docker load < claudertos_image.tar.gz
docker-compose run --rm claudertos --validate
```

### 최소 설치 (anthropic 없이) / Minimal Install Without Anthropic SDK

네트워크가 전혀 없는 환경에서는 오프라인 모드만 사용한다.

```bash
# 최소 의존성만 설치 (pyserial + numpy)
pip install --no-index --find-links ~/claudertos_wheels pyserial numpy

# AI 없이 Rule 기반 분석만
python3 examples/integrated_demo.py --port simulate --ai-mode offline
python3 claudertos_main.py --port simulate --ai-mode offline
```
