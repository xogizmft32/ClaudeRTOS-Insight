#!/usr/bin/env python3
"""
codex_cli_provider.py — OpenAI Codex CLI Provider

Codex CLI(Rust 기반)를 subprocess로 실행하여 AI 분석을 수행한다.
`codex exec` 명령의 --json JSONL 이벤트 스트림을 파싱해 AIResponse로 변환.

Codex CLI vs 기존 OpenAI API 차이:
  openai.py (기존) : REST API /chat/completions → 1회 응답
  codex_cli.py     : Codex CLI 에이전트 루프 → 도구 실행 포함 다회 추론
                     → 파일 편집, 명령 실행, 웹 검색, MCP 통합 가능

요구사항:
  Node.js 18+
  npm install -g @openai/codex    (또는 npx @openai/codex)

인증 방식 (택1):
  1. CODEX_API_KEY  — OpenAI API Key (CI/headless 권장)
  2. OPENAI_API_KEY — 대체 인증 (codex exec에서 자동 인식)
  3. ChatGPT OAuth  — `codex login` (Plus/Pro 구독 포함)

headless 명령:
  codex exec "prompt" --json --full-auto --skip-git-repo-check --ephemeral

--json JSONL 이벤트 형식 (stdout):
  {"type":"agent_message","content":"분석 결과 텍스트"}
  {"type":"reasoning","content":"추론 내용"}
  {"type":"command_execution","command":"ls","output":"..."}
  {"type":"file_change","path":"...","action":"modify"}
  {"type":"session_info","session_id":"...","model":"gpt-5.3-codex","usage":{...}}

공식 문서:
  https://developers.openai.com/codex/noninteractive
  https://developers.openai.com/codex/cli/reference
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


class CodexCLIProvider(AIProvider):
    """
    OpenAI Codex CLI headless 모드 기반 Provider.

    `codex exec` 명령을 subprocess로 실행하며 --json JSONL 스트림으로
    에이전트 이벤트를 수신하고 최종 응답을 AIResponse로 반환한다.

    모델 티어:
      TIER1 : gpt-5.3-codex   (최신 flagship, 코딩 특화)
      TIER2 : codex-mini-latest (경량, 단순 분석용)

    환경 변수:
      CODEX_API_KEY          — OpenAI API Key (codex exec 전용, 권장)
      OPENAI_API_KEY         — 대체 API Key
      CODEX_CLI_MODEL        — Tier1 모델 오버라이드
      CODEX_CLI_MODEL_TIER2  — Tier2 모델 오버라이드
      CODEX_CLI_PATH         — codex 바이너리 경로 오버라이드
      CODEX_CLI_TIMEOUT      — 최대 대기 시간(초, 기본 120)

    사용:
      export CLAUDERTOS_AI_PROVIDER=codex_cli
      export CODEX_API_KEY=sk-...     # 또는 codex login (ChatGPT OAuth)
      python3 examples/integrated_demo.py --port jlink

    주의:
      - codex exec은 Git 저장소 안에서만 동작 (--skip-git-repo-check로 우회)
      - 에이전트 루프로 동작 → 토큰 소비가 단순 API보다 많을 수 있음
      - ChatGPT Plus/Pro 구독 시 추가 비용 없음
    """

    # 기본 모델 (2026-04 최신 기준)
    _MODELS = {
        AITier.TIER1: os.getenv('CODEX_CLI_MODEL',       'gpt-5.3-codex'),
        AITier.TIER2: os.getenv('CODEX_CLI_MODEL_TIER2', 'codex-mini-latest'),
    }

    # OpenAI API 요금 (USD/M token, 참고용 — 구독 시 $0)
    _COST_IN  = {AITier.TIER1: 5.0,  AITier.TIER2: 1.5}
    _COST_OUT = {AITier.TIER1: 20.0, AITier.TIER2: 6.0}

    def __init__(self,
                 api_key:  Optional[str] = None,
                 timeout:  int = 120,
                 cli_path: Optional[str] = None,
                 full_auto: bool = True):
        """
        Parameters
        ----------
        api_key   : CODEX_API_KEY 오버라이드 (미지정 시 환경 변수 사용)
        timeout   : subprocess 최대 대기 시간(초), 기본 120
        cli_path  : codex 바이너리 경로 오버라이드
        full_auto : --full-auto 플래그 (승인 없이 자동 실행, 기본 True)
        """
        self._api_key   = api_key or \
                          os.getenv('CODEX_API_KEY',  '') or \
                          os.getenv('OPENAI_API_KEY', '')
        self._timeout   = int(os.getenv('CODEX_CLI_TIMEOUT', timeout))
        self._cli_path  = cli_path or os.getenv('CODEX_CLI_PATH', '')
        self._full_auto = full_auto

    # ── AIProvider 추상 메서드 구현 ───────────────────────────
    @property
    def name(self) -> str:
        return 'codex_cli'

    def model_for_tier(self, tier: AITier) -> str:
        return self._MODELS[tier]

    def estimate_cost(self, tokens_in: int, tokens_out: int,
                       tier: AITier) -> float:
        # ChatGPT 구독 사용 시 $0
        if not self._api_key:
            return 0.0
        return (tokens_in  * self._COST_IN[tier]  / 1_000_000 +
                tokens_out * self._COST_OUT[tier]  / 1_000_000)

    def is_available(self) -> bool:
        """Codex CLI 설치 여부 확인."""
        return bool(self._find_cli())

    def generate(self,
                 system:     str,
                 user:       str,
                 max_tokens: int,
                 tier:       AITier = AITier.TIER1) -> AIResponse:
        """
        Codex CLI headless 모드로 분석 실행.

        `codex exec "<prompt>" --json --full-auto --skip-git-repo-check`
        """
        t0    = time.time()
        model = self.model_for_tier(tier)

        # 프롬프트 구성 — system + user 통합
        prompt = (
            f"[SYSTEM CONTEXT — 임베디드 디버깅 전문가]\n{system}\n\n"
            f"[분석 요청]\n{user}\n\n"
            "위 FreeRTOS 디버그 데이터를 분석하고 JSON 형식으로 결과를 반환하라."
        )

        try:
            raw_output = self._call_cli(prompt, model)
        except FileNotFoundError:
            return AIResponse(
                text=(
                    "Codex CLI를 찾을 수 없습니다.\n"
                    "설치: npm install -g @openai/codex\n"
                    "확인: docs/CODEX_CLI_GUIDE.md"
                ),
                model=model, tokens_in=0, tokens_out=0,
                latency_ms=int((time.time()-t0)*1000), cached=False,
            )
        except subprocess.TimeoutExpired:
            return AIResponse(
                text=f"Codex CLI 타임아웃 ({self._timeout}초 초과)",
                model=model, tokens_in=0, tokens_out=0,
                latency_ms=self._timeout * 1000, cached=False,
            )
        except Exception as e:
            return AIResponse(
                text=f"Codex CLI 오류: {e}",
                model=model, tokens_in=0, tokens_out=0,
                latency_ms=int((time.time()-t0)*1000), cached=False,
            )

        latency_ms = int((time.time() - t0) * 1000)
        response_text, tokens_in, tokens_out = self._parse_output(raw_output)

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
        codex 바이너리 경로 탐색.
        우선순위: 명시 경로 > PATH > npx 폴백
        """
        if self._cli_path and shutil.which(self._cli_path):
            return self._cli_path
        if shutil.which('codex'):
            return 'codex'
        # npx 폴백 (설치 없이 사용 가능, 단 첫 실행 느림)
        if shutil.which('npx'):
            return 'npx'
        return ''

    def _call_cli(self, prompt: str, model: str) -> str:
        """
        Codex CLI subprocess 실행.

        codex exec "<prompt>" \
          --json \\                   # JSONL 이벤트 출력
          --full-auto \\              # 승인 없이 자동 실행
          --skip-git-repo-check \\    # Git 저장소 외 실행 허용
          --ephemeral \\              # 세션 파일 저장 안함
          --model <model>

        Returns
        -------
        CLI stdout 문자열 (JSONL 또는 텍스트)
        """
        cli = self._find_cli()
        if not cli:
            raise FileNotFoundError("codex CLI not found")

        if cli == 'npx':
            cmd = ['npx', '--yes', '@openai/codex', 'exec', prompt]
        else:
            cmd = [cli, 'exec', prompt]

        # 플래그 추가
        cmd += ['--json', '--skip-git-repo-check', '--ephemeral']
        if self._full_auto:
            cmd += ['--full-auto']
        cmd += ['--model', model]

        # 환경 변수 구성
        env = os.environ.copy()
        if self._api_key:
            # CODEX_API_KEY가 codex exec 전용 인증 키
            env['CODEX_API_KEY']  = self._api_key
            env['OPENAI_API_KEY'] = self._api_key

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._timeout,
            env=env,
        )

        # codex exec은 비정상 종료 시에도 stdout에 응답을 남길 수 있음
        if result.returncode != 0 and not result.stdout.strip():
            err = result.stderr.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Codex CLI 오류: {err[:300]}")

        return result.stdout

    def _parse_output(self, raw: str) -> tuple[str, int, int]:
        """
        Codex CLI --json JSONL 출력 파싱.

        이벤트 타입별 처리:
          agent_message  → 최종 응답 텍스트 (핵심)
          reasoning      → 무시 (내부 추론)
          session_info   → 토큰 사용량 추출
          file_change    → 무시 (파일 편집은 ClaudeRTOS에서 불필요)

        Falls back to:
          1. 단순 텍스트 (--json 미지원 버전)
          2. stdout 전체 텍스트
        """
        raw = raw.strip()
        if not raw:
            return '', 0, 0

        agent_messages = []
        tokens_in = tokens_out = 0

        # JSONL 파싱 시도
        lines = [l for l in raw.splitlines() if l.strip()]
        json_lines_parsed = 0

        for line in lines:
            try:
                obj = json.loads(line)
                json_lines_parsed += 1
                evt = obj.get('type', '')

                if evt == 'agent_message':
                    content = obj.get('content', '')
                    if content:
                        agent_messages.append(content)

                elif evt == 'session_info':
                    usage = obj.get('usage', {})
                    tokens_in  = usage.get('input_tokens',
                                  usage.get('prompt_tokens', 0))
                    tokens_out = usage.get('output_tokens',
                                  usage.get('completion_tokens', 0))

            except json.JSONDecodeError:
                # JSON이 아닌 줄(ANSI, 진행 로그 등) → 무시
                continue

        if json_lines_parsed > 0 and agent_messages:
            return '\n'.join(agent_messages), tokens_in, tokens_out

        # 폴백: JSON 미지원 버전 또는 단순 텍스트 응답
        # 마지막 비어있지 않은 줄이 최종 응답일 가능성이 높음
        non_empty = [l for l in lines if l.strip() and not l.startswith('{')]
        if non_empty:
            return '\n'.join(non_empty), 0, 0

        # 최종 폴백: 원본 그대로
        return raw, tokens_in, tokens_out
