#!/usr/bin/env python3
"""
LocalLLM — N100에서 실행하는 경량 로컬 모델 (선택 사항)

목적:
  Claude API 호출 전 1차 필터링 및 요약.
  로컬에서 처리 가능한 이슈는 Claude를 부르지 않아 비용 절감.

지원 백엔드:
  1. llama-cpp-python  — llama.cpp Python 바인딩 (권장)
  2. ollama            — REST API (ollama가 실행 중일 때)
  3. transformers      — HuggingFace (메모리 사용량 높음)

N100 권장 모델 (정확도·속도 균형):
  - Qwen2.5-1.5B-Instruct Q4_K_M  (1.1GB, ~30 tok/s)
  - Phi-3-mini-4k-instruct Q4_K_M (2.3GB, ~15 tok/s)

설치:
  pip install llama-cpp-python   (CPU 빌드)
  # 또는
  pip install ollama

사용법:
  llm = LocalLLM(backend='ollama', model='qwen2.5:1.5b')
  result = llm.triage(issues, timeline)
  if result.needs_cloud_ai:
      # Claude API 호출
  else:
      print(result.diagnosis)

주의:
  - 로컬 LLM은 Claude보다 품질이 낮을 수 있음
  - Critical 이슈는 항상 Claude로 escalate 권장
  - N100에서 8B 이상 모델은 너무 느림 (>30초/응답)
"""

from __future__ import annotations

import json
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LocalLLMResult:
    needs_cloud_ai: bool        # True → Claude API 호출 필요
    diagnosis:      str         # 로컬 진단 텍스트 (needs_cloud_ai=False 시 사용)
    confidence:     float       # 0.0 ~ 1.0 (낮으면 Claude로 escalate)
    latency_ms:     float       # 추론 시간
    model_used:     str         # 사용된 모델 이름
    tokens_used:    int         # 로컬 처리 토큰 수 (참고용)


# ── 로컬 추론 프롬프트 (짧게 유지 — N100 속도 최적화) ─────────
_TRIAGE_SYSTEM = """\
You are an RTOS debugging assistant. Given issue data, respond with JSON only:
{"needs_cloud": true|false, "confidence": 0.0-1.0, "diagnosis": "brief diagnosis or empty"}
needs_cloud=true if: multiple Critical issues, HardFault, or confidence<0.7.
Be brief. Max 50 words in diagnosis."""

def _build_triage_prompt(issues: List[Dict], timeline: List[Dict]) -> str:
    """짧은 트리아지 프롬프트 — 토큰 최소화."""
    issue_summary = "; ".join(
        f"{i.get('severity','?')}/{i.get('type','?')}"
        for i in issues[:5]
    )
    tl_summary = ", ".join(
        e.get('type','?') for e in timeline[-5:]
    )
    return (f"issues: [{issue_summary}]\n"
            f"recent_events: [{tl_summary}]\n"
            "Triage:")


# ── Backend: llama-cpp-python ─────────────────────────────────
class LlamaCppBackend:
    def __init__(self, model_path: str, n_threads: int = 4):
        """
        model_path: GGUF 모델 파일 경로
          예: /models/qwen2.5-1.5b-instruct-q4_k_m.gguf
        """
        self._path     = model_path
        self._threads  = n_threads
        self._llm      = None
        self._model    = model_path.split('/')[-1]

    def _load(self):
        if self._llm is not None:
            return
        try:
            from llama_cpp import Llama
            self._llm = Llama(
                model_path=self._path,
                n_ctx=512,           # 컨텍스트 짧게 — N100 메모리 절약
                n_threads=self._threads,
                n_gpu_layers=0,      # CPU only (N100 iGPU는 CUDA 지원 없음)
                verbose=False,
            )
            logger.info("Loaded %s", self._model)
        except ImportError:
            raise RuntimeError("pip install llama-cpp-python")
        except Exception as e:
            raise RuntimeError(f"Model load failed: {e}")

    def generate(self, system: str, user: str,
                 max_tokens: int = 80) -> tuple[str, int]:
        self._load()
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]
        resp = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,   # 결정적 출력
        )
        text = resp['choices'][0]['message']['content']
        tok  = resp['usage']['total_tokens']
        return text, tok


# ── Backend: Ollama REST API ──────────────────────────────────
class OllamaBackend:
    def __init__(self, model: str = 'qwen2.5:1.5b',
                 host: str = 'http://localhost:11434'):
        self._model = model
        self._host  = host

    def generate(self, system: str, user: str,
                 max_tokens: int = 80) -> tuple[str, int]:
        try:
            import urllib.request
            payload = json.dumps({
                "model":  self._model,
                "system": system,
                "prompt": user,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.1},
            }).encode()
            req = urllib.request.Request(
                f"{self._host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            text = resp.get('response', '')
            tok  = resp.get('eval_count', 0) + resp.get('prompt_eval_count', 0)
            return text, tok
        except Exception as e:
            raise RuntimeError(f"Ollama request failed: {e}")


# ── LocalLLM 메인 클래스 ──────────────────────────────────────
class LocalLLM:
    """
    로컬 LLM 트리아지 필터.

    Critical 이슈 또는 confidence < threshold 이면
    needs_cloud_ai=True 반환 → 호출자가 Claude API 호출.

    이 클래스 자체는 Claude API를 호출하지 않는다.
    """

    def __init__(self,
                 backend: str           = 'ollama',
                 model:   str           = 'qwen2.5:1.5b',
                 model_path: str        = '',
                 confidence_threshold: float = 0.7,
                 n_threads: int         = 4):
        """
        backend:
          'ollama'    — Ollama REST (가장 쉬운 설치)
          'llamacpp'  — llama-cpp-python (GGUF 파일 직접)
          'disabled'  — 로컬 LLM 비활성 (항상 Claude 사용)

        confidence_threshold:
          이 값 미만이면 needs_cloud_ai=True (Claude로 escalate)
        """
        self._backend_name = backend
        self._threshold    = confidence_threshold
        self._backend      = None

        if backend == 'ollama':
            self._backend = OllamaBackend(model=model)
        elif backend == 'llamacpp':
            if not model_path:
                raise ValueError("model_path required for llamacpp backend")
            self._backend = LlamaCppBackend(model_path, n_threads=n_threads)
        elif backend == 'disabled':
            pass   # 항상 Claude로 escalate
        else:
            raise ValueError(f"Unknown backend: {backend}")

        self._model_name = model or model_path.split('/')[-1] if model_path else 'none'

    def triage(self, issues: List[Dict],
               timeline: Optional[List[Dict]] = None) -> LocalLLMResult:
        """
        이슈 트리아지 — 로컬에서 처리 가능한지 판단.

        Returns:
          needs_cloud_ai=False → 로컬 진단 사용 (Claude API 불필요)
          needs_cloud_ai=True  → Claude API 호출 필요
        """
        if not issues:
            return LocalLLMResult(False, "No issues", 1.0, 0.0, 'none', 0)

        # disabled 모드
        if self._backend is None:
            return LocalLLMResult(True, '', 0.0, 0.0, 'disabled', 0)

        # Critical이 있으면 즉시 Claude로
        if any(i.get('severity') == 'Critical' for i in issues):
            return LocalLLMResult(
                needs_cloud_ai=True,
                diagnosis='',
                confidence=0.0,
                latency_ms=0.0,
                model_used=self._model_name,
                tokens_used=0,
            )

        # 로컬 추론
        prompt = _build_triage_prompt(issues, timeline or [])
        t0 = time.perf_counter()
        try:
            text, tokens = self._backend.generate(
                system=_TRIAGE_SYSTEM,
                user=prompt,
                max_tokens=80,
            )
        except Exception as e:
            logger.warning("Local LLM failed: %s → escalating to Cloud", e)
            return LocalLLMResult(True, '', 0.0, 0.0, self._model_name, 0)

        latency = (time.perf_counter() - t0) * 1000

        # JSON 파싱
        try:
            # 모델이 마크다운 코드블록으로 감쌀 수 있음
            text_clean = text.strip().lstrip('`').rstrip('`').strip()
            if text_clean.startswith('json'):
                text_clean = text_clean[4:].strip()
            result = json.loads(text_clean)
            needs_cloud  = bool(result.get('needs_cloud', True))
            confidence   = float(result.get('confidence', 0.5))
            diagnosis    = str(result.get('diagnosis', ''))
        except Exception:
            # 파싱 실패 → 안전하게 Cloud로
            needs_cloud = True
            confidence  = 0.0
            diagnosis   = ''

        # confidence 임계값 미달 → Cloud
        if confidence < self._threshold:
            needs_cloud = True

        return LocalLLMResult(
            needs_cloud_ai=needs_cloud,
            diagnosis=diagnosis,
            confidence=confidence,
            latency_ms=latency,
            model_used=self._model_name,
            tokens_used=tokens,
        )

    @property
    def is_available(self) -> bool:
        """로컬 LLM 사용 가능 여부 (연결 테스트)."""
        if self._backend is None:
            return False
        try:
            _, _ = self._backend.generate("ping", "ok?", max_tokens=5)
            return True
        except Exception:
            return False
