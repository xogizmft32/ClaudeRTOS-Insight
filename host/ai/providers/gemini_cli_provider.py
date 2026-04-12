#!/usr/bin/env python3
"""
gemini_cli_provider.py — Gemini CLI Headless Mode Provider

Gemini CLI를 subprocess로 실행하여 AI 분석을 수행한다.
--output-format json 플래그로 구조화된 응답을 파싱한다.

Gemini CLI vs Google API Provider 차이:
  google.py (기존): google-generativeai SDK → REST API 직접 호출
  gemini_cli.py  : Gemini CLI 바이너리 → headless subprocess
                   → 에이전트 루프, 파일 접근, MCP 통합 가능
                   → 무료 티어: 60 req/min, 1,000 req/day

요구사항:
  Node.js 18+
  npm install -g @google/gemini-cli   (또는 npx @google/gemini-cli)

인증 방식 (택1):
  1. GOOGLE_API_KEY    — Gemini API Key (Google AI Studio 발급)
  2. Google OAuth      — gemini login (Google 계정 로그인, 무료 티어)
  3. Vertex AI         — GOOGLE_GENAI_USE_VERTEXAI=true + GCP 서비스 계정

공식 문서:
  https://github.com/google-gemini/gemini-cli
  https://geminicli.com/docs/cli/headless/

headless JSON 출력 형식 (v0.37.1 기준):
  {
    "response": "분석 텍스트",
    "stats": {
      "total_tokens": 250,
      "input_tokens": 50,
      "output_tokens": 200,
      "duration_ms": 1200
    },
    "error": null
  }
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from .base import AIProvider, AIResponse, AITier


class GeminiCLIProvider(AIProvider):
    """
    Gemini CLI headless 모드 기반 Provider.

    Gemini CLI를 subprocess로 실행하며 --output-format json으로
    구조화된 응답을 받아 AIResponse로 변환한다.

    모델 티어:
      TIER1: gemini-2.5-pro  (고품질, 무료 티어 한도 있음)
      TIER2: gemini-2.0-flash (균형형, 기본)

    환경 변수:
      GOOGLE_API_KEY            — Gemini API Key (선택, OAuth 대안)
      GEMINI_CLI_MODEL          — Tier1 모델 오버라이드
      GEMINI_CLI_MODEL_TIER2    — Tier2 모델 오버라이드
      GEMINI_CLI_PATH           — gemini 바이너리 경로 오버라이드
      GEMINI_CLI_TIMEOUT        — 최대 대기 시간(초, 기본 120)

    사용:
      export CLAUDERTOS_AI_PROVIDER=gemini_cli
      export GOOGLE_API_KEY=AIza...  (또는 gemini login 완료 후 불필요)
      python3 examples/integrated_demo.py --port jlink

    주의:
      - Gemini CLI 버전별로 출력 형식이 변경될 수 있음
        → 파싱 실패 시 자동으로 텍스트 폴백 처리
      - 무료 티어 사용 시 분당 60회 제한 존재
      - 에이전트 루프 기능은 현재 headless 모드에서 제한적
    """

    # 기본 모델 (환경 변수로 오버라이드 가능)
    _MODELS = {
        AITier.TIER1: os.getenv('GEMINI_CLI_MODEL',       'gemini-2.5-pro'),
        AITier.TIER2: os.getenv('GEMINI_CLI_MODEL_TIER2', 'gemini-2.0-flash'),
    }

    # Gemini API 요금 (USD/M token, 2026-04 기준, 참고용)
    # 무료 티어 사용 시 $0
    _COST_IN  = {AITier.TIER1: 1.25,  AITier.TIER2: 0.075}
    _COST_OUT = {AITier.TIER1: 10.0,  AITier.TIER2: 0.30}

    def __init__(self,
                 api_key:  Optional[str] = None,
                 timeout:  int = 120,
                 cli_path: Optional[str] = None):
        """
        Parameters
        ----------
        api_key  : GOOGLE_API_KEY 오버라이드
                   (미지정 시 환경 변수 또는 OAuth 세션 사용)
        timeout  : subprocess 최대 대기 시간(초), 기본 120
        cli_path : gemini 바이너리 경로 오버라이드
                   (미지정 시 PATH에서 자동 탐색, npx 폴백)
        """
        self._api_key  = api_key or os.getenv('GOOGLE_API_KEY', '')
        self._timeout  = int(os.getenv('GEMINI_CLI_TIMEOUT', timeout))
        self._cli_path = cli_path or os.getenv('GEMINI_CLI_PATH', '')

    # ── AIProvider 추상 메서드 구현 ───────────────────────────
    @property
    def name(self) -> str:
        return 'gemini_cli'

    def model_for_tier(self, tier: AITier) -> str:
        return self._MODELS[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        # 무료 티어(OAuth)는 $0, API 키 사용 시 아래 요금
        if not self._api_key:
            return 0.0
        return (tokens_in  * self._COST_IN[tier]  / 1_000_000 +
                tokens_out * self._COST_OUT[tier]  / 1_000_000)

    def is_available(self) -> bool:
        """Gemini CLI 설치 여부 + 인증 가능 여부 확인."""
        return bool(self._find_cli())

    def generate(self,
                 system:     str,
                 user:       str,
                 max_tokens: int,
                 tier:       AITier = AITier.TIER1) -> AIResponse:
        """
        Gemini CLI headless 모드로 분석 실행.

        gemini -p "<prompt>" --output-format json
        """
        t0    = time.time()
        model = self.model_for_tier(tier)

        # 프롬프트 구성
        prompt = (
            f"[SYSTEM]\n{system}\n\n"
            f"[EMBEDDED DEBUG REQUEST]\n{user}"
        )

        try:
            result = self._call_cli(prompt, model)
        except FileNotFoundError:
            return AIResponse(
                text=(
                    "Gemini CLI를 찾을 수 없습니다.\n"
                    "설치: npm install -g @google/gemini-cli\n"
                    "확인: docs/GEMINI_CLI_GUIDE.md"
                ),
                model=model,
                tokens_in=0, tokens_out=0,
                latency_ms=int((time.time()-t0)*1000),
                cached=False,
            )
        except subprocess.TimeoutExpired:
            return AIResponse(
                text=f"Gemini CLI 타임아웃 ({self._timeout}초 초과)",
                model=model,
                tokens_in=0, tokens_out=0,
                latency_ms=self._timeout * 1000,
                cached=False,
            )
        except Exception as e:
            return AIResponse(
                text=f"Gemini CLI 오류: {e}",
                model=model,
                tokens_in=0, tokens_out=0,
                latency_ms=int((time.time()-t0)*1000),
                cached=False,
            )

        latency_ms = int((time.time() - t0) * 1000)

        # JSON 파싱 — 출력 형식이 변경되면 텍스트 폴백
        response_text, tokens_in, tokens_out = self._parse_output(result)

        return AIResponse(
            text=response_text,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cached=False,
        )

    # ── 내부 메서드 ───────────────────────────────────────────
    def _find_cli(self) -> str:
        """
        gemini 바이너리 경로 탐색.
        우선순위: 명시 경로 > PATH > npx 폴백
        """
        if self._cli_path and shutil.which(self._cli_path):
            return self._cli_path
        if shutil.which('gemini'):
            return 'gemini'
        # npx 폴백 (설치 없이 사용 가능, 단 첫 실행 느림)
        if shutil.which('npx'):
            return 'npx'
        return ''

    def _call_cli(self, prompt: str, model: str) -> str:
        """
        Gemini CLI subprocess 실행.

        Returns
        -------
        CLI stdout 문자열 (JSON 또는 텍스트)
        """
        cli = self._find_cli()
        if not cli:
            raise FileNotFoundError("gemini CLI not found")

        # 명령어 구성
        if cli == 'npx':
            cmd = ['npx', '--yes', '@google/gemini-cli',
                   '-p', prompt,
                   '--model', model,
                   '--output-format', 'json']
        else:
            cmd = [cli,
                   '-p', prompt,
                   '--model', model,
                   '--output-format', 'json']

        # 환경 변수 구성
        env = os.environ.copy()
        if self._api_key:
            env['GOOGLE_API_KEY'] = self._api_key
        # 텔레메트리 비활성화 (프라이버시)
        env['GEMINI_CLI_NO_TELEMETRY'] = '1'

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._timeout,
            env=env,
        )

        if result.returncode != 0:
            # stderr에 오류 메시지가 있으면 반환
            err = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Gemini CLI exit {result.returncode}: {err[:200]}")

        return result.stdout

    def _parse_output(self, raw: str) -> tuple[str, int, int]:
        """
        CLI 출력을 파싱하여 (text, tokens_in, tokens_out) 반환.

        Gemini CLI v0.37.1 JSON 형식:
          {"response": "...", "stats": {"input_tokens": N, "output_tokens": M}}

        파싱 실패 시: (raw_text, 0, 0) 폴백
        """
        raw = raw.strip()
        if not raw:
            return '', 0, 0

        # JSON 파싱 시도
        try:
            data = json.loads(raw)

            # 오류 확인
            if data.get('error'):
                return f"Gemini 오류: {data['error']}", 0, 0

            response  = data.get('response', '')
            stats     = data.get('stats', {})
            tokens_in  = stats.get('input_tokens',  0)
            tokens_out = stats.get('output_tokens', 0)
            return response, tokens_in, tokens_out

        except json.JSONDecodeError:
            pass

        # JSONL (스트리밍 형식) 폴백
        try:
            lines = [l for l in raw.splitlines() if l.strip()]
            texts = []
            tokens_in = tokens_out = 0
            for line in lines:
                obj = json.loads(line)
                if obj.get('type') == 'message' and obj.get('role') == 'assistant':
                    texts.append(obj.get('content', ''))
                elif obj.get('type') == 'result':
                    s = obj.get('stats', {})
                    tokens_in  = s.get('input_tokens',  0)
                    tokens_out = s.get('output_tokens', 0)
            if texts:
                return '\n'.join(texts), tokens_in, tokens_out
        except Exception:
            pass

        # 최종 폴백 — 텍스트 그대로 반환
        return raw, 0, 0
