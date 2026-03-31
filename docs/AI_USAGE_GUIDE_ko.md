# AI 분석 활용 가이드 (한국어) — ClaudeRTOS-Insight V3.8

> 영문 버전: `docs/AI_USAGE_GUIDE.md`

## 핵심 원칙

```
로컬 Rule-based 탐지 (<1ms) → [ai_ready=True] → Claude API (~0.2–2초)
```
AI는 실시간 제어 루프에 관여하지 않습니다.

---

## AI 모드

| 모드 | 동작 | 권장 환경 |
|------|------|-----------|
| `offline` | AI 미호출, 로컬 탐지만 | 프로덕션, CI |
| `postmortem` (기본) | 3회 연속 → ai_ready | 일반 디버깅 |
| `realtime` | 즉시 ai_ready | 개발 테스트만 |

---

## 비용 구조 (V3.8 기준)

### 심각도별 모델 자동 선택
| 심각도 | 모델 | max_tokens |
|--------|------|-----------|
| Critical | Sonnet | 500 |
| High | Haiku | 250 |
| Medium | Haiku | 150 |
| HardFault | Sonnet | 500 |

### 1시간 세션 비용 (22일/월)
| 시나리오 | 세션 비용 | 월 비용 |
|----------|---------|--------|
| 평온 (High 1종) | ~$0.0003 | ~$0.006 |
| 일반 (Crit1+High2) | ~$0.0041 | ~$0.091 |
| 집중 (Crit2+High5) | ~$0.0085 | ~$0.188 |
| realtime 주의 ⚠ | ~$52 | 위험 |

---

## 비용 절약 5가지

1. **postmortem 모드** — 3회 연속 후 1회만 AI 호출
2. **패턴 DB** — KP 패턴 매칭 시 비용 $0 (로컬 진단)
3. **estimate_cost()** — 호출 전 비용 미리 확인
4. **debug_batch()** — 여러 이슈를 1회 호출로 묶기
5. **캐시 TTL 연장** — `ai_cache_ttl=172800`으로 48h 설정

---

## 패턴 DB 커스터마이징

`host/patterns/custom_patterns.json` 생성:

```json
{
  "patterns": [
    {
      "id": "KP-USER-001",
      "name": "My Custom Pattern",
      "category": "memory",
      "severity": "High",
      "enabled": true,
      "description": "내 애플리케이션 특화 패턴",
      "match": {
        "require_issues": ["low_heap"],
        "require_events": ["malloc"],
        "event_count_min": {"malloc": 3}
      },
      "causal_chain_template": [
        "malloc × {malloc_count}",
        "애플리케이션 특화 원인",
        "heap 고갈 위험"
      ],
      "diagnosis": {
        "root_cause": "...",
        "fix": "...",
        "prevention": "..."
      }
    }
  ]
}
```

---

## 인과 체인 설정

```python
# chain_max_steps 설정 가능 (기본 7, 최대 10)
engine = CorrelationEngine(chain_max_steps=7)   # 권장 (P75 커버)
engine = CorrelationEngine(chain_max_steps=5)   # 단순 패턴용
engine = CorrelationEngine(chain_max_steps=10)  # 복잡한 deadlock용
```

실제 RTOS 장애 데이터 기준:
- P50 (중간값): 5 스텝
- P75: 6 스텝  
- P90: 8 스텝 (DMA+malloc+deadlock 복합)
- **권장 기본값: 7 스텝** (P75~P90 균형)

---

## 시나리오별 권장 설정

```python
# 메모리 집중 분석
corr = CorrelationEngine(chain_max_steps=5)  # 단순 패턴
engine = AnalysisEngine(ai_mode='postmortem')

# Deadlock 집중 분석
corr = CorrelationEngine(chain_max_steps=10)  # 복잡한 체인
pre = PreFilter(chain_max_steps=10)

# 실시간 운영 모니터링
engine = AnalysisEngine(ai_mode='offline')  # AI 없음
```
