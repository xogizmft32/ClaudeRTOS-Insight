# 로컬 AI (Ollama) 활용 가이드 — ClaudeRTOS-Insight

네트워크 없이 또는 비용 $0으로 AI 분석을 사용하는 방법.

---

## 로컬 AI의 실제 능력과 한계

> 이 수치는 N100 PC 기준 **추정치**입니다.
> 실제 성능은 모델 버전, 시스템 RAM, 동시 프로세스 수에 따라 달라집니다.

### 모델별 특성 (N100, 16GB RAM 기준)

| 모델 | 디스크 | 속도(추정) | JSON 신뢰도 | 권장 용도 |
|------|--------|-----------|-----------|---------|
| `qwen2.5:1.5b` | ~1GB | 빠름 | 낮음 (스키마 불이행 잦음) | 헬스체크, 단순 요약 |
| `qwen2.5:3b` | ~2GB | 보통 | 중간 | Medium 이슈 요약 |
| `qwen2.5:7b` | ~5GB | 느림 | 높음 | High/Critical 이슈 |
| `llama3.1:8b` | ~5GB | 느림 | 높음 | High/Critical 이슈 |

**주의**: 소형 모델은 구조화 JSON schema를 자주 어깁니다.
`response_parser.py`가 파싱 실패를 텍스트 폴백으로 처리하므로
치명적이지 않지만 분석 품질이 낮아질 수 있습니다.

### 클라우드 AI 대비 한계

| 능력 | 클라우드 (Claude Sonnet) | 로컬 (llama3.1:8b) |
|------|------------------------|-------------------|
| 임베디드 도메인 지식 | 높음 | 중간 |
| 구조화 JSON 일관성 | 높음 | 중간~높음 |
| 한국어 품질 | 높음 | 중간 |
| 네트워크 | 필요 | 불필요 |
| 비용 | ~$0.005~0.015/이슈 | $0 |
| 응답 속도 | ~1~3초 | ~5~30초 (모델 크기에 따라) |

---

## 설치 및 설정

```bash
# Ollama 설치 (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# 모델 다운로드 (한 번만)
ollama pull qwen2.5:3b     # 2GB, 빠름
ollama pull llama3.1:8b    # 5GB, 더 정확

# 서버 시작
ollama serve &

# ClaudeRTOS에서 사용
export CLAUDERTOS_AI_PROVIDER=ollama
python3 examples/integrated_demo.py --port jlink
```

---

## 운영 전략

| 상황 | 권장 설정 |
|------|---------|
| 평상시 개발 | `postmortem` + Anthropic |
| 네트워크 불가 | `CLAUDERTOS_AI_PROVIDER=ollama` |
| 비용 0 원할 때 | Ollama + `llama3.1:8b` |
| 완전 오프라인 | `ai_mode=offline` (AI 없음, PatternDB만) |
| CI/CD | `ai_mode=offline` |

---

## 완전 오프라인 분석 범위

네트워크 없이 `ai_mode=offline`으로 가능한 것:

```
✅ Rule-based 이슈 감지 (스택/힙/CPU/우선순위역전)
✅ PatternDB KP-001~005 매칭 (로컬 진단, $0)
✅ ConstraintChecker (pair/temporal/monotonic)
✅ CorrelationEngine CORR-001~006
✅ StateMachine SM-001~003
✅ ResourceGraph RG-001~002 (데드락 DFS)
✅ Orchestrator 교차검증
✅ GlobalCausalGraph 세션 누산
✅ EventPriorityQueue Aging/RateLimit
✅ Session Learner (학습된 패턴 사용)

❌ root_cause hypothesis (AI 필요)
❌ recommended_actions의 코드 수정 제안 (AI 필요)
❌ 자연어 설명 (AI 필요)
```

오프라인 모드에서도 **패턴 감지, 데드락 탐지, 이슈 분류**는
완전히 동작합니다.

---

## Docker에서 Ollama 사용

```bash
# Ollama 컨테이너 시작
docker-compose --profile ollama up -d claudertos-ollama

# 모델 다운로드
docker exec claudertos-ollama ollama pull qwen2.5:3b

# 분석 실행
CLAUDERTOS_AI_PROVIDER=ollama docker-compose up claudertos-host
```
