# Claude Agent SDK Provider — 설정 가이드

Claude Code CLI 기반 에이전트 루프 분석 Provider.

---

## 단순 API vs Agent SDK

| 항목 | `anthropic` (기존) | `claude_agent` (신규) |
|------|------|------|
| 분석 방식 | 프롬프트 → 1회 응답 | 프롬프트 → 도구 실행 → 추론 → 응답 |
| 에이전트 루프 | ❌ | ✅ (최대 N회 반복) |
| 추가 설치 | ❌ | `pip install claude-agent-sdk>=0.1.56` |
| 비용 | 동일 | 동일 (API Key 기준) |
| 인증 | ANTHROPIC_API_KEY | ANTHROPIC_API_KEY (동일) |

---

## 설치

```bash
pip install claude-agent-sdk>=0.1.56
```

> Claude Code CLI가 패키지에 자동 번들됩니다. 별도 설치 불필요.

---

## 설정

```bash
export CLAUDERTOS_AI_PROVIDER=claude_agent
export ANTHROPIC_API_KEY=sk-ant-...

python3 examples/integrated_demo.py --port jlink
```

### 모델 오버라이드 (기본: claude-sonnet-4-6)

```bash
export CLAUDE_AGENT_MODEL=claude-opus-4-6
```

---

## 코드 사용

```python
from ai.providers.claude_agent_provider import ClaudeAgentProvider

provider = ClaudeAgentProvider(
    max_turns=5,  # 에이전트 루프 최대 횟수 (기본 5)
)

if provider.is_available():
    response = provider.generate(
        system="FreeRTOS 임베디드 디버깅 전문가.",
        user=context_json,
        max_tokens=2048,
    )
    print(response.text)
```

---

## 주의사항

- `max_turns`가 높을수록 비용 증가 → 임베디드 분석은 3~5회로 충분
- Claude Agent SDK가 설치되지 않으면 `is_available()` → `False` 반환
- 공식 문서: https://platform.claude.com/docs/en/agent-sdk/overview
