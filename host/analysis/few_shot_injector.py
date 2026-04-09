#!/usr/bin/env python3
"""
few_shot_injector.py — AI 컨텍스트 Few-shot Example 주입

문제:
  현재 AI에게 "지금 상태" 만 전달.
  AI는 유사 사례를 모르므로 일반적인 가설만 생성.

해결:
  과거 해결된 세션에서 유사 사례를 찾아 컨텍스트에 포함.
  "유사 케이스: 2026-03-15, AppMutex 데드락 → Mutex 획득 순서 고정으로 해결"
  → AI가 구체적이고 프로젝트 특화된 수정 방법 제안

데이터 소스:
  1. session_logger.py 생성 .jsonl 파일 (해결 표시된 이슈)
  2. custom_patterns.json (session_learner 학습 결과)
  3. 수동 정의 examples.json

유사도 판단:
  - issue_type 일치 (1순위)
  - severity 일치 (2순위)
  - task_name 유사 (3순위, 같은 task 이름이면 같은 프로젝트)

사용:
    injector = FewShotInjector(log_dir="logs/", max_examples=3)
    examples = injector.find_similar(current_issues)
    ctx['few_shot_examples'] = examples
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class FewShotInjector:
    """
    과거 세션 로그에서 유사 사례를 찾아 AI 컨텍스트에 주입.
    """

    def __init__(self,
                 log_dir:      str = 'logs',
                 examples_file: Optional[str] = None,
                 max_examples: int = 3):
        self._log_dir      = Path(log_dir)
        self._examples_file = Path(examples_file) if examples_file else None
        self._max          = max_examples
        self._examples:    List[Dict] = []
        self._load()

    def _load(self) -> None:
        """세션 로그(.jsonl) + 수동 examples.json 에서 사례 로드."""
        # 1. 수동 정의 예시
        if self._examples_file and self._examples_file.exists():
            try:
                data = json.loads(self._examples_file.read_text('utf-8'))
                self._examples.extend(data.get('examples', []))
            except Exception:
                pass

        # 2. 세션 로그 — 해결된 이슈 추출
        if self._log_dir.exists():
            for f in sorted(self._log_dir.glob('*.jsonl'))[-10:]:  # 최근 10세션
                try:
                    for line in f.read_text('utf-8').splitlines():
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        # AI 결과 레코드 중 이슈가 있는 것
                        if rec.get('type') == 'ai_result' and rec.get('issue_count', 0) > 0:
                            self._examples.append({
                                'source':     'session_log',
                                'session_id': f.stem,
                                'issue_type': rec.get('issue_type', '?'),
                                'severity':   rec.get('severity', '?'),
                                'summary':    rec.get('summary', ''),
                                'resolved':   rec.get('resolved', False),
                            })
                except Exception:
                    pass

    def find_similar(self, current_issues: List[Dict],
                      resolved_only: bool = False) -> List[Dict]:
        """
        현재 이슈와 유사한 과거 사례 반환.

        반환 형식 (AI 컨텍스트 삽입용):
        [
          {
            "issue_type": "priority_inversion",
            "summary":    "AppMutex 데드락 → Mutex 획득 순서 고정으로 해결",
            "resolved":   true,
            "source":     "session_20260315"
          }, ...
        ]
        """
        if not current_issues or not self._examples:
            return []

        current_types = {
            i.get('type', i.get('issue_type', ''))
            for i in current_issues
        }
        current_sevs = {i.get('severity', '') for i in current_issues}

        scored: List[tuple] = []
        for ex in self._examples:
            if resolved_only and not ex.get('resolved', False):
                continue
            score = 0
            if ex.get('issue_type') in current_types:
                score += 3
            if ex.get('severity') in current_sevs:
                score += 1
            if ex.get('summary'):
                score += 1
            if score > 0:
                scored.append((score, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:self._max]]

    def to_context(self, issues: List[Dict]) -> Optional[Dict]:
        """
        AI 컨텍스트에 직접 삽입할 few_shot_examples 딕셔너리 반환.
        유사 사례 없으면 None.
        """
        examples = self.find_similar(issues)
        if not examples:
            return None
        return {
            'count':    len(examples),
            'note':     '유사 과거 사례 (참고용, 맹목적 적용 금지)',
            'examples': [
                {k: v for k, v in ex.items()
                 if k in ('issue_type','summary','resolved','source')}
                for ex in examples
            ],
        }

    def add_example(self, issue_type: str, summary: str,
                     resolved: bool = True,
                     severity: str = 'High') -> None:
        """수동으로 사례 추가."""
        self._examples.append({
            'source':     'manual',
            'issue_type': issue_type,
            'severity':   severity,
            'summary':    summary,
            'resolved':   resolved,
        })

    def save_examples(self, path: str) -> None:
        """현재 사례를 JSON 파일로 저장."""
        Path(path).write_text(
            json.dumps({'examples': self._examples}, indent=2,
                       ensure_ascii=False),
            encoding='utf-8')
