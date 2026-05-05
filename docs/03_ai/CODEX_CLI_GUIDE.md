# OpenAI Codex CLI 연동 가이드 — ClaudeRTOS-Insight

Codex CLI headless 모드를 사용한 AI 분석 Provider 설정 안내.

---

## 목차

1. [Codex CLI란?](#codex-cli란)
2. [설치](#설치)
3. [인증 설정](#인증-설정)
4. [ClaudeRTOS에 연결](#claudertos에-연결)
5. [모델 선택](#모델-선택)
6. [비용 및 한도](#비용-및-한도)
7. [문제 해결](#문제-해결)
8. [전체 Provider 비교](#전체-provider-비교)

---

## Codex CLI란?

Codex CLI는 OpenAI가 공식 오픈소스(Rust)로 공개한 터미널 코딩 에이전트입니다. GPT-5 계열 코딩 특화 모델을 사용하며, 파일 편집·명령 실행·웹 검색·MCP 통합을 지원합니다.

ClaudeRTOS-Insight는 `codex exec` 명령의 **headless(비대화형) 모드**를 사용해 FreeRTOS 디버깅 분석을 수행합니다.

```bash
# headless 실행 예시
codex exec "FreeRTOS 스택 오버플로 분석" \
  --json \                    # JSONL 이벤트 출력
  --full-auto \               # 자동 승인
  --skip-git-repo-check \     # Git 외 디렉터리 허용
  --ephemeral \               # 세션 파일 미저장
  --model gpt-5.3-codex
```

> **ChatGPT Plus/Pro 구독자**: 추가 비용 없이 Codex CLI 포함.

---

## 설치

### 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| Node.js | 18.0+ | 20.x LTS |
| Codex CLI | 최신 stable | 최신 stable |
| OS | Linux / macOS | Linux (WSL2 포함) |

> Windows는 WSL2 환경에서 사용을 권장합니다.

### Node.js 설치 (없는 경우)

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version   # v20.x.x 이상 확인
```

### Codex CLI 설치

```bash
# 전역 설치 (권장)
npm install -g @openai/codex

# 설치 확인
codex --version

# 또는 npx로 설치 없이 실행 (첫 실행 느림)
npx @openai/codex --version
```

> 최신 버전 확인: https://github.com/openai/codex

---

## 인증 설정

세 가지 방법 중 하나를 선택합니다.

### 방법 1: API Key (headless/CI 환경 권장)

```bash
# OpenAI API Key 발급: https://platform.openai.com/api-keys
export CODEX_API_KEY=sk-...    # codex exec 전용 인증 변수
# 또는
export OPENAI_API_KEY=sk-...   # 대체 인증 변수

# 확인
codex exec "Hello" --full-auto --skip-git-repo-check --ephemeral
```

### 방법 2: ChatGPT OAuth (Plus/Pro 구독, 비용 절감)

```bash
# 대화형 로그인 (브라우저 열림)
codex login

# 로그인 확인
codex exec "Hello" --full-auto --skip-git-repo-check --ephemeral
```

ChatGPT Plus/Pro/Business/Edu/Enterprise 구독자는 **추가 비용 없이** 사용 가능.

### 방법 3: npx (설치 없이 일시 사용)

```bash
export CODEX_API_KEY=sk-...
npx @openai/codex exec "Hello" --full-auto --skip-git-repo-check --ephemeral
```

---

## ClaudeRTOS에 연결

### 기본 설정

```bash
# 1. Provider 선택
export CLAUDERTOS_AI_PROVIDER=codex_cli

# 2. 인증 (방법 1: API Key)
export CODEX_API_KEY=sk-...
# 또는 방법 2: codex login 완료 후 환경 변수 불필요

# 3. 실행
python3 examples/integrated_demo.py --port jlink
```

### 고급 환경 변수

```bash
# 모델 선택 (기본값 아래 참조)
export CODEX_CLI_MODEL=gpt-5.3-codex        # Tier1 (기본)
export CODEX_CLI_MODEL_TIER2=codex-mini-latest  # Tier2 (기본)

# 타임아웃 (초, 기본 120)
export CODEX_CLI_TIMEOUT=180

# CLI 경로 직접 지정 (비표준 설치 위치)
export CODEX_CLI_PATH=/usr/local/bin/codex
```

### Python 코드에서 직접 사용

```python
from ai.providers.codex_cli_provider import CodexCLIProvider
from ai.providers.base import AITier

provider = CodexCLIProvider(
    api_key="sk-...",    # 선택 (OAuth 사용 시 불필요)
    timeout=180,
    full_auto=True,      # --full-auto (승인 없이 자동)
)

if provider.is_available():
    response = provider.generate(
        system="FreeRTOS 임베디드 디버깅 전문가로서 분석하라.",
        user=context_json_string,
        max_tokens=2048,
        tier=AITier.TIER1,
    )
    print(response.text)
    print(f"모델: {response.model}, 지연: {response.latency_ms}ms")
else:
    print("Codex CLI를 찾을 수 없습니다. 설치를 확인하세요.")
```

---

## 모델 선택

### 사용 가능한 모델 (2026-04 기준)

| 모델 | 용도 | 특징 |
|------|------|------|
| `gpt-5.3-codex` | Tier1 기본 | 최신 flagship, 코딩 + 추론 최고 품질 |
| `codex-mini-latest` | Tier2 기본 | 경량, 빠름, 단순 분석용 |

```bash
# Tier1 모델 변경
export CODEX_CLI_MODEL=gpt-5.3-codex

# 단일 실행에서 모델 지정 (--model 플래그)
codex exec "분석" --model gpt-5.3-codex --full-auto --skip-git-repo-check
```

---

## 비용 및 한도

### ChatGPT 구독 (OAuth 로그인)

| 구독 | 비용 | Codex CLI |
|------|------|---------|
| ChatGPT Plus | 월 $20 | **포함** |
| ChatGPT Pro | 월 $200 | **포함** |
| Business/Edu/Enterprise | 별도 | **포함** |

### OpenAI API Key (종량제)

```python
# 비용 추정 (gpt-5.3-codex 기준)
provider = CodexCLIProvider(api_key="sk-...")
cost = provider.estimate_cost(
    tokens_in=2000,
    tokens_out=800,
    tier=AITier.TIER1
)
# → 약 $0.026 / 분석 1회 (추정치)
```

> API Key 사용 시 임베디드 분석 세션(10~50회)당 약 $0.1~$0.5 예상 (추정치).

---

## 문제 해결

### codex: command not found

```bash
# 1. Node.js 버전 확인 (18+ 필요)
node --version

# 2. npm 전역 bin 경로 확인 및 PATH 추가
npm config get prefix
# → /usr/local

export PATH="/usr/local/bin:$PATH"
echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.bashrc

# 3. 재설치
npm install -g @openai/codex

# 4. npx로 임시 사용
npx @openai/codex exec "test" --full-auto --skip-git-repo-check --ephemeral
# 환경 변수로 npx 경로 지정
export CODEX_CLI_PATH=npx
```

### 인증 오류: "No credentials found"

```bash
# 방법 1: API Key 설정 확인
echo $CODEX_API_KEY
echo $OPENAI_API_KEY

# API Key 유효성 확인
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" | jq '.data[0].id'

# 방법 2: OAuth 재로그인
codex logout
codex login
```

### Git 저장소 오류: "Not inside a git repository"

```bash
# --skip-git-repo-check 플래그 자동 적용됨 (Provider 내부 처리)
# 직접 실행 시에는 명시적으로 추가
codex exec "분석" --full-auto --skip-git-repo-check --ephemeral
```

### 타임아웃 오류

```bash
# 타임아웃 늘리기 (기본 120초)
export CODEX_CLI_TIMEOUT=300

# 또는 Tier2(경량 모델) 사용
export CODEX_CLI_MODEL_TIER2=codex-mini-latest
export CLAUDERTOS_AI_TIER=2
```

### Windows / WSL2 환경

```bash
# WSL2 내에서 실행 권장
# Windows PATH의 node/codex를 WSL이 인식 못할 수 있음

# WSL 내에서 직접 설치
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
npm install -g @openai/codex

# Windows 파일은 /mnt/c/ 경로로 접근
```

### Docker 환경

```dockerfile
# Dockerfile에 Node.js + Codex CLI 추가
FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @openai/codex

# docker-compose.yml 환경 변수
# environment:
#   - CLAUDERTOS_AI_PROVIDER=codex_cli
#   - CODEX_API_KEY=${CODEX_API_KEY}
```

---

## 전체 Provider 비교

| 항목 | `anthropic` | `claude_agent` | `gemini_cli` | `codex_cli` | `ollama` |
|------|-------------|----------------|--------------|-------------|---------|
| 방식 | REST API | Agent SDK | CLI subprocess | CLI subprocess | REST API |
| 에이전트 루프 | ❌ | ✅ | 제한적 | ✅ | ❌ |
| 설치 | pip | pip | npm + Node.js | npm + Node.js | ollama |
| 무료 사용 | ❌ | ❌ | ✅ (OAuth) | ✅ (구독 포함) | ✅ |
| 오프라인 | ❌ | ❌ | ❌ | ❌ | ✅ |
| 코딩 특화 | 보통 | 보통 | 보통 | ✅ (코딩 특화) | 모델 의존 |
| 안정성 | 높음 | 높음 | CLI 버전 의존 | CLI 버전 의존 | 중간 |

### 선택 기준

```
목적별 권장:
  일반 디버깅 세션       → anthropic (안정, 검증됨)
  자율 에이전트 분석     → claude_agent (에이전트 루프)
  무료/개인 사용         → gemini_cli (OAuth 무료)
  OpenAI 구독 사용       → codex_cli (ChatGPT Plus 포함)
  코딩 특화 분석         → codex_cli (gpt-5.3-codex)
  폐쇄망/오프라인        → ollama
```

---

## 관련 문서

| 문서 | 내용 |
|------|------|
| [GEMINI_CLI_GUIDE.md](GEMINI_CLI_GUIDE.md) | Gemini CLI 설정 가이드 |
| [CLAUDE_AGENT_GUIDE.md](CLAUDE_AGENT_GUIDE.md) | Claude Agent SDK 가이드 |
| [AI_USAGE_GUIDE_ko.md](AI_USAGE_GUIDE_ko.md) | 전체 AI 사용 가이드 |
| [LOCAL_AI_GUIDE.md](LOCAL_AI_GUIDE.md) | Ollama 로컬 AI 설정 |
| [OFFLINE_GUIDE.md](OFFLINE_GUIDE.md) | 폐쇄망 운용 |

---

> 이 문서는 Codex CLI 최신 stable 기준으로 작성됐습니다.  
> 업데이트: https://github.com/openai/codex  
> CLI 업데이트 후 `--json` 이벤트 형식이 변경될 경우
> `codex_cli_provider.py`의 `_parse_output()` 메서드를 조정하세요.
