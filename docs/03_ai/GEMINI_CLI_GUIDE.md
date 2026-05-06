# Gemini CLI 연동 가이드 — ClaudeRTOS-Insight
# Gemini CLI Integration Guide

> Use Google Gemini CLI as a free AI provider for ClaudeRTOS-Insight. Personal Google accounts get free quota via OAuth; API key mode suits server deployments.

Gemini CLI headless 모드를 사용한 AI 분석 Provider 설정 안내.

---

## 목차

1. [Gemini CLI란?](#gemini-cli란)
2. [설치](#설치)
3. [인증 설정](#인증-설정)
4. [ClaudeRTOS에 연결](#claudertos에-연결)
5. [모델 선택](#모델-선택)
6. [비용 및 한도](#비용-및-한도)
7. [문제 해결](#문제-해결)
8. [claude_agent vs gemini_cli 비교](#provider-비교)

---

## Gemini CLI란?
*What Is Gemini CLI?*

Gemini CLI는 Google이 공식 오픈소스로 공개한 터미널 AI 에이전트로, Gemini 모델을 터미널에서 직접 사용할 수 있게 해줍니다. Google Search 기반 정보 검색, 파일 조작, 셸 명령 실행, MCP(Model Context Protocol) 지원을 내장하고 있습니다.

ClaudeRTOS-Insight는 이 CLI의 **headless 모드**(`--output-format json`)를 사용해 FreeRTOS 디버깅 분석을 수행합니다.

> **무료 개인 사용 가능**: Google 계정으로 OAuth 로그인 시 별도 결제 없이 사용 가능합니다.

---

## 설치
*Installation*

### 요구사항 / Requirements

| 항목 | 최소 | 권장 |
|------|------|------|
| Node.js | 18.0+ | 20.x LTS |
| Gemini CLI | 0.37.x | 최신 stable |
| OS | Linux / macOS / Windows | Linux |

### Node.js 설치 (없는 경우) / Node.js Installation (if missing)

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# macOS (Homebrew)
brew install node

# 버전 확인
node --version   # v20.x.x 이상
```

### Gemini CLI 설치 / Gemini CLI Installation

```bash
# 방법 1: 전역 설치 (권장)
npm install -g @google/gemini-cli

# 방법 2: npx (설치 없이 사용, 단 첫 실행 느림)
npx @google/gemini-cli --version

# 설치 확인
gemini --version
# 출력: 0.37.x
```

> **최신 버전 확인**: https://github.com/google-gemini/gemini-cli/releases

---

## 인증 설정
*Authentication Setup — Three Methods*

세 가지 방법 중 하나를 선택합니다. **개인 사용자는 방법 1(OAuth) 권장**입니다.

### 방법 1: Google OAuth (무료, 개인 권장) / Method 1: Google OAuth (Free, Personal)

```bash
# 대화형 로그인 (브라우저 열림)
gemini login

# 로그인 성공 확인
gemini -p "Hello" --output-format json
# {"response": "Hello! ...", "stats": {...}}
```

무료 한도: **60 요청/분, 1,000 요청/일**

### 방법 2: Gemini API Key (유료, 서버 배포 권장) / Method 2: Gemini API Key (Paid, Server)

```bash
# 1. Google AI Studio에서 API Key 발급
#    https://aistudio.google.com/apikey

# 2. 환경 변수 설정
export GOOGLE_API_KEY=AIzaSy...

# 3. ClaudeRTOS에도 동일하게 적용
export GOOGLE_API_KEY=AIzaSy...

# 확인
gemini -p "Hello" --output-format json
```

### 방법 3: Google Cloud Vertex AI (기업 환경) / Method 3: Vertex AI (Enterprise)

```bash
# Google Cloud 서비스 계정 인증
gcloud auth application-default login

# Vertex AI 모드 활성화
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=my-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

# 확인
gemini -p "Hello" --output-format json
```

---

## ClaudeRTOS에 연결
*Connecting to ClaudeRTOS-Insight*

### 기본 설정 / Basic Configuration

```bash
# 1. Provider 선택
export CLAUDERTOS_AI_PROVIDER=gemini_cli

# 2. 인증 (방법 1: OAuth — gemini login 완료 후 불필요)
#    또는
export GOOGLE_API_KEY=AIzaSy...  # 방법 2: API Key

# 3. 실행
python3 examples/integrated_demo.py --port jlink
```

### 고급 환경 변수 / Advanced Environment Variables

```bash
# 모델 선택 (기본값 아래 참조)
export GEMINI_CLI_MODEL=gemini-2.5-pro       # Tier1 (기본)
export GEMINI_CLI_MODEL_TIER2=gemini-2.0-flash  # Tier2 (기본)

# 타임아웃 (초, 기본 120)
export GEMINI_CLI_TIMEOUT=180

# CLI 경로 직접 지정 (비표준 설치 위치)
export GEMINI_CLI_PATH=/usr/local/bin/gemini
```

### Python 코드에서 직접 사용 / Direct Use in Python Code

```python
from ai.providers.gemini_cli_provider import GeminiCLIProvider
from ai.providers.base import AITier

provider = GeminiCLIProvider(
    api_key="AIzaSy...",   # 선택 (OAuth 사용 시 불필요)
    timeout=180,            # 응답 대기 최대 시간(초)
)

# 사용 가능 여부 확인
if provider.is_available():
    response = provider.generate(
        system="FreeRTOS 임베디드 디버깅 전문가로서 분석하라.",
        user=context_json_string,
        max_tokens=2048,
        tier=AITier.TIER1,
    )
    print(response.text)
else:
    print("Gemini CLI를 찾을 수 없습니다. 설치 확인 필요.")
```

### RTOSDebuggerV3와 함께 사용 / Use with RTOSDebuggerV3

```python
from ai.rtos_debugger import RTOSDebuggerV3

# 방법 A: 환경 변수로 설정 (권장)
# export CLAUDERTOS_AI_PROVIDER=gemini_cli
debugger = RTOSDebuggerV3()

# 방법 B: 코드에서 직접 지정
debugger = RTOSDebuggerV3(provider='gemini_cli')

result = debugger.debug_snapshot(snap, issues, timeline)
print(result['issues'])
print(result['_verification']['summary']['trust_score'])
```

---

## 모델 선택
*Model Selection*

### 사용 가능한 모델 (2026-04 기준) / Available Models

| 모델 | 용도 | 특징 | 비용(API Key) |
|------|------|------|------------|
| `gemini-2.5-pro` | Tier1 기본 | 최고 품질, 1M context | $1.25/M in |
| `gemini-2.0-flash` | Tier2 기본 | 균형형, 빠름 | $0.075/M in |
| `gemini-2.5-flash` | 균형형 | 빠름+품질 균형 | $0.15/M in |

> **OAuth(무료 티어) 사용 시**: 비용 없음. 한도 내에서 모든 모델 사용 가능.

### 모델 변경 / Changing Models

```bash
# Tier1(고품질) 모델 변경
export GEMINI_CLI_MODEL=gemini-2.5-flash

# Tier2(빠른) 모델 변경
export GEMINI_CLI_MODEL_TIER2=gemini-2.0-flash
```

---

## 비용 및 한도
*Cost and Rate Limits*

### OAuth 무료 티어 (Google 계정 로그인)

| 항목 | 한도 |
|------|------|
| 요청/분 | 60회 |
| 요청/일 | 1,000회 |
| 비용 | **$0** |

임베디드 디버깅 세션에서 일반적으로 세션당 10~50회 요청이 발생합니다.

### Gemini API Key 유료 사용

```python
# 비용 추정 (코드에서)
provider = GeminiCLIProvider(api_key="AIza...")
cost = provider.estimate_cost(
    tokens_in=1000,
    tokens_out=500,
    tier=AITier.TIER1  # gemini-2.5-pro
)
# → ~$0.00000125 + ~$0.000005 ≈ $0.0000063 (매우 저렴)
```

---

## 문제 해결
*Troubleshooting*

### gemini: command not found

```bash
# 1. Node.js 버전 확인
node --version   # 18.0 이상 필요

# 2. npm 전역 설치 경로 확인
npm root -g
# → /usr/local/lib/node_modules

# 3. PATH에 npm 전역 bin 추가
export PATH="$(npm bin -g):$PATH"
echo 'export PATH="$(npm bin -g):$PATH"' >> ~/.bashrc

# 4. npx로 임시 실행
npx @google/gemini-cli --version
# 환경 변수로 npx 사용 강제
export GEMINI_CLI_PATH=npx
```

### 인증 오류: "Could not load credentials"

```bash
# 확인 1: OAuth 로그인 재시도
gemini logout
gemini login

# 확인 2: API Key 유효성 확인
curl -H "x-goog-api-key: $GOOGLE_API_KEY" \
  "https://generativelanguage.googleapis.com/v1beta/models"

# 확인 3: 환경 변수 확인
echo $GOOGLE_API_KEY
echo $GOOGLE_GENAI_USE_VERTEXAI
```

### 응답이 비어있거나 파싱 오류

```bash
# CLI 직접 실행으로 확인
gemini -p "Hello" --output-format json

# 예상 출력:
# {"response": "Hello! ...", "stats": {"total_tokens": 50, ...}}

# 출력이 다른 형식이면 gemini CLI 버전 확인
gemini --version
# 0.37.x 이상 권장

# 버전 업데이트
npm update -g @google/gemini-cli
```

### 타임아웃 오류

```bash
# 타임아웃 늘리기
export GEMINI_CLI_TIMEOUT=300  # 5분

# 또는 네트워크 연결 확인
ping generativelanguage.googleapis.com
```

### Docker 환경에서 사용

```bash
# Dockerfile에 Node.js + Gemini CLI 추가
# FROM python:3.11-slim
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @google/gemini-cli

# docker-compose.yml에 환경 변수 추가
# environment:
#   - CLAUDERTOS_AI_PROVIDER=gemini_cli
#   - GOOGLE_API_KEY=${GOOGLE_API_KEY}
```

---

## Provider 비교
*Provider Comparison — Gemini vs Claude vs Local*

| 항목 | `anthropic` | `claude_agent` | `gemini_cli` | `ollama` |
|------|-------------|----------------|--------------|---------|
| 방식 | REST API | Agent SDK | CLI subprocess | REST API |
| 에이전트 루프 | ❌ | ✅ | 제한적 | ❌ |
| 설치 | pip | pip | npm + Node.js | ollama 설치 |
| 무료 사용 | ❌ | ❌ | ✅ (OAuth) | ✅ |
| 오프라인 | ❌ | ❌ | ❌ | ✅ |
| 안정성 | 높음 | 높음 | CLI 버전 의존 | 중간 |
| 추천 환경 | 일반 | 에이전트 분석 | 무료 사용 | 폐쇄망 |

### 선택 기준

```
목적별 권장:
  일반 디버깅 세션       → anthropic (안정, 검증됨)
  자율 에이전트 분석     → claude_agent (에이전트 루프)
  무료/개인 사용         → gemini_cli (OAuth 무료)
  폐쇄망/오프라인        → ollama (llama3.1:8b 권장)
```

---

## 관련 문서
*Related Documentation*

| 문서 | 내용 |
|------|------|
| [AI_USAGE_GUIDE_ko.md](AI_USAGE_GUIDE_ko.md) | 전체 AI 사용 가이드 |
| [LOCAL_AI_GUIDE.md](LOCAL_AI_GUIDE.md) | Ollama 로컬 AI 설정 |
| [OFFLINE_GUIDE.md](OFFLINE_GUIDE.md) | 폐쇄망 운용 |

---

> 이 문서는 Gemini CLI v0.37.x 기준으로 작성됐습니다.  
> CLI 업데이트 후 출력 형식 변경 시 `gemini_cli_provider.py`의 `_parse_output()` 메서드를 조정하세요.
