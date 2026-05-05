#!/usr/bin/env python3
"""
TokenOptimizer — 컨텍스트 전송 전 최종 토큰 최적화

역할:
  - 이미 PreFilter를 통과한 이슈·타임라인을 Claude에 보내기 전
    마지막으로 토큰을 줄이는 후처리.

방법:
  1. 필드 선택적 포함 (verbose 필드 제거)
  2. 타임라인 최종 슬라이싱
  3. 숫자 반올림 (소수점 절약)
  4. 빈 값 제거 (null, [], {} 제거)
  5. 토큰 예산 초과 시 이슈 우선순위에 따라 트리밍

목표: 200 tokens 이내 (현재 평균 26 tokens → 추가 여유 확보)
"""

import json
from typing import Dict, List, Optional


# Claude에 절대 빼면 안 되는 필드
_REQUIRED_SNAP_FIELDS  = {'cpu_usage', 'heap', 'tasks', '_parser_stats'}
_REQUIRED_TASK_FIELDS  = {'name', 'priority', 'state', 'state_name',
                          'cpu_pct', 'stack_hwm', 'task_id'}
_REQUIRED_ISSUE_FIELDS = {'severity', 'type', 'description', 'affected_tasks'}


def optimize_snapshot(snap: Dict,
                      max_tasks: int = 8,
                      drop_runtime: bool = True) -> Dict:
    """스냅샷 딕셔너리에서 AI 분석에 불필요한 필드 제거."""
    out: Dict = {}

    # 필수 필드만
    for k in _REQUIRED_SNAP_FIELDS:
        if k in snap:
            out[k] = snap[k]

    # 선택 필드 (있으면 포함)
    for k in ('timestamp_us', 'sequence', 'uptime_ms'):
        if k in snap and snap[k]:
            out[k] = snap[k]

    # 태스크: 필수 필드만, runtime_us 제거 (AI가 활용 안 함)
    if 'tasks' in out:
        trimmed_tasks = []
        for t in out['tasks'][:max_tasks]:
            task_out = {k: t[k] for k in _REQUIRED_TASK_FIELDS if k in t}
            if not drop_runtime and 'runtime_us' in t:
                task_out['runtime_us'] = t['runtime_us']
            trimmed_tasks.append(task_out)
        out['tasks'] = trimmed_tasks

    # heap: 소수점 없는 정수로
    if 'heap' in out:
        h = out['heap']
        out['heap'] = {
            'free':     int(h.get('free', 0)),
            'total':    int(h.get('total', 0)),
            'used_pct': int(h.get('used_pct', 0)),
        }
        # min_ever는 트렌드 분석에 유용하면 포함
        if h.get('min', 0) < h.get('free', 0):
            out['heap']['min'] = int(h['min'])

    return out


def optimize_issues(issues: List[Dict]) -> List[Dict]:
    """이슈에서 AI가 사용하지 않는 detail 필드 제거."""
    result = []
    for iss in issues:
        o: Dict = {}
        for k in _REQUIRED_ISSUE_FIELDS:
            if k in iss:
                o[k] = iss[k]
        # detail: 핵심 키만
        detail = iss.get('detail', {})
        if detail:
            keep = {k: v for k, v in detail.items()
                    if k in ('stack_hwm_words', 'cpu_pct', 'free_pct',
                              'free', 'total', 'high_pri', 'low_pri',
                              'gaps', 'packets_lost')}
            if keep:
                o['detail'] = keep
        result.append(o)
    return result


def optimize_timeline(timeline: List[Dict],
                      max_events: int = 15) -> List[Dict]:
    """타임라인 최대 max_events개로 압축 (중요도 기준)."""
    if len(timeline) <= max_events:
        return timeline

    PRIORITY_EVENTS = {'mutex_timeout', 'isr_enter', 'malloc'}
    important = [e for e in timeline if e.get('type') in PRIORITY_EVENTS]
    others    = [e for e in timeline if e.get('type') not in PRIORITY_EVENTS]

    budget = max_events - len(important)
    if budget > 0 and others:
        step    = max(1, len(others) // budget)
        sampled = others[::step][:budget]
    else:
        sampled = []

    result = sorted(important + sampled, key=lambda e: e.get('t_us', 0))
    return result[:max_events]


def estimate_json_tokens(obj) -> int:
    """JSON 직렬화 후 토큰 수 근사."""
    s = json.dumps(obj, separators=(',', ':'))
    return int(len(s.split()) * 1.3)


class TokenOptimizer:
    """
    파이프라인에서 마지막으로 토큰을 최적화하는 클래스.

    사용:
        opt = TokenOptimizer(token_budget=150)
        snap_opt, issues_opt, tl_opt = opt.optimize(snap, issues, timeline)
        # → Claude에 전달할 최적화된 컨텍스트
    """

    def __init__(self, token_budget: int = 150,
                 max_tasks: int = 8,
                 max_timeline: int = 15):
        self._budget   = token_budget
        self._max_tasks = max_tasks
        self._max_tl    = max_timeline

    def optimize(self, snap: Dict, issues: List[Dict],
                 timeline: Optional[List[Dict]] = None
                 ) -> tuple:
        """
        Returns (snap_opt, issues_opt, timeline_opt, token_estimate)
        """
        tl = timeline or []

        # 각 요소 최적화
        snap_opt   = optimize_snapshot(snap, max_tasks=self._max_tasks)
        issues_opt = optimize_issues(issues)
        tl_opt     = optimize_timeline(tl, max_events=self._max_tl)

        # 토큰 추정
        tokens = (estimate_json_tokens(snap_opt) +
                  estimate_json_tokens(issues_opt) +
                  estimate_json_tokens(tl_opt))

        # 예산 초과 시 타임라인부터 줄이기
        if tokens > self._budget and tl_opt:
            tl_opt = tl_opt[:max(5, self._max_tl // 2)]
            tokens = (estimate_json_tokens(snap_opt) +
                      estimate_json_tokens(issues_opt) +
                      estimate_json_tokens(tl_opt))

        return snap_opt, issues_opt, tl_opt, tokens
