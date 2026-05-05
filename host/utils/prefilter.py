#!/usr/bin/env python3
"""
PreFilter — Claude API 호출 전 로컬 전처리

역할:
  1. 중복 이슈 제거     — 같은 타입·태스크의 이슈가 반복될 때 1개로 압축
  2. 이슈 병합          — 연관된 이슈를 묶어 1회 호출로 처리 가능하게
  3. 컨텍스트 압축      — 타임라인에서 패턴(원인→결과) 추출, 원본 이벤트 수 축소
  4. 자동 분류          — 알려진 패턴은 AI 없이 즉시 진단

N100 처리 시간: < 1ms (전부 Python 연산, API 호출 없음)
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from patterns.pattern_db import get_db, PatternDB, Pattern

# KNOWN_PATTERNS: 하위 호환용 — 실제로는 PatternDB를 사용
# 직접 접근이 필요하면 get_db().find_matches() 사용
KNOWN_PATTERNS: List[Dict] = [  # deprecated: use PatternDB
    {
        "id": "KP-001",
        "name": "Mutex Timeout → Priority Inversion",
        "trigger": lambda issues, tl: (
            any(i.get('type') == 'priority_inversion' for i in issues) and
            any(e.get('type') == 'mutex_timeout' for e in tl)
        ),
        "severity": "High",
        "summary": "Mutex 타임아웃이 우선순위 역전을 유발합니다.",
        "root_cause": "높은 우선순위 태스크가 낮은 우선순위 태스크가 보유한 Mutex 대기 중",
        "fix": "Priority Inheritance 활성화: configUSE_MUTEXES=1 + "
               "xSemaphoreCreateMutex() (Recursive 아닌 일반 Mutex 사용)",
        "prevention": "Mutex 보유 시간을 최소화하고 configUSE_MUTEXES로 우선순위 상속 활성화",
    },
    {
        "id": "KP-002",
        "name": "반복 Malloc → Heap 단편화",
        "trigger": lambda issues, tl: (
            any(i.get('type') in ('low_heap', 'heap_exhaustion') for i in issues) and
            sum(1 for e in tl if e.get('type') == 'malloc') >= 5
        ),
        "severity": "High",
        "summary": "반복적인 malloc/free로 Heap 단편화가 발생합니다.",
        "root_cause": "작은 크기의 동적 할당이 반복되어 단편화 → free heap 부족",
        "fix": "pvPortMalloc 대신 정적 할당(static 배열) 사용, "
               "또는 메모리 풀(Memory Pool) 패턴 도입",
        "prevention": "임베디드 시스템에서 동적 할당 최소화. "
                      "configUSE_HEAP_5로 여러 메모리 영역 활용",
    },
    {
        "id": "KP-003",
        "name": "Stack HWM < 20W → 즉각 오버플로우",
        "trigger": lambda issues, tl: (
            any(i.get('type') == 'stack_overflow_imminent' and
                (i.get('detail') or {}).get('stack_hwm_words', 99) < 20
                for i in issues)
        ),
        "severity": "Critical",
        "summary": "스택이 20 words 미만으로 오버플로우가 임박합니다.",
        "root_cause": "태스크 스택 크기 부족. xTaskCreate의 usStackDepth 파라미터 확인",
        "fix": "xTaskCreate() 스택 크기를 현재의 2배로 늘리거나 최소 "
               "uxTaskGetStackHighWaterMark() 반환값 + 64 words로 설정",
        "prevention": "configCHECK_FOR_STACK_OVERFLOW=2 설정으로 런타임 감지 활성화",
    },
    {
        "id": "KP-004",
        "name": "ISR + Malloc → 금지 패턴",
        "trigger": lambda issues, tl: (
            any(e.get('type') == 'isr_enter' for e in tl) and
            any(e.get('type') == 'malloc' for e in tl) and
            _isr_before_malloc(tl)
        ),
        "severity": "Critical",
        "summary": "ISR 컨텍스트에서 malloc 호출이 감지됩니다.",
        "root_cause": "pvPortMalloc/free는 ISR에서 호출 금지. "
                      "heap_4.c는 ISR-safe하지 않음",
        "fix": "ISR에서 동적 할당 제거. 정적 버퍼 또는 ISR-safe 큐 사용",
        "prevention": "ISR에서는 FreeRTOS ISR-safe API만 사용 (*FromISR 함수군)",
    },
]


def _isr_before_malloc(timeline: List[Dict]) -> bool:
    """타임라인에서 ISR_ENTER 직후 malloc 패턴 탐지."""
    in_isr = False
    for ev in timeline:
        et = ev.get('type', '')
        if et == 'isr_enter':
            in_isr = True
        elif et == 'isr_exit':
            in_isr = False
        elif et == 'malloc' and in_isr:
            return True
    return False


# ── 이슈 지문 (중복 감지용) ──────────────────────────────────
def _issue_fingerprint(issue: Dict) -> str:
    """이슈 타입 + 태스크명 + severity → 지문 해시."""
    key = (issue.get('type', ''),
           str(sorted(issue.get('affected_tasks', []))),
           issue.get('severity', ''))
    return hashlib.md5(str(key).encode()).hexdigest()[:8]


# ── PreFilter 클래스 ─────────────────────────────────────────
class PreFilter:
    """
    Claude API 호출 전 로컬 전처리기.

    사용 흐름:
        pre = PreFilter()

        result = pre.process(snap, issues, timeline)
        if result.skip_api:
            # 알려진 패턴 → 로컬 진단 바로 출력
            print(result.local_diagnosis)
        elif result.issues:
            # 압축된 이슈로 API 호출
            api_result = debugger.debug_snapshot(
                snap, result.issues, timeline_events=result.timeline)
    """

    def __init__(self, dedup_window_s: float = 3600.0,
                 chain_max_steps: int = 7):
        """
        dedup_window_s: 동일 이슈 재전송 억제 시간 (초)
                        기본 3600s = 1시간 세션 동안 1회
        """
        self._dedup_window    = dedup_window_s
        self._chain_max_steps = chain_max_steps
        self._seen: Dict[str, float] = {}   # fingerprint → last_seen_time

    def process(self, snap: Dict, issues: List[Dict],
                timeline: Optional[List[Dict]] = None) -> 'PreFilterResult':
        tl = timeline or []

        # Critical 이슈는 KP 우회 → 항상 Claude API 호출
        has_critical = any(i.get('severity') == 'Critical' for i in issues)

        # ── 1. 알려진 패턴 매칭 (Critical 없을 때만) ─────────
        if not has_critical:
         for pattern in KNOWN_PATTERNS:
            if pattern['trigger'](issues, tl):
                return PreFilterResult(
                    skip_api=True,
                    local_diagnosis=_format_local_diagnosis(pattern, issues),
                    pattern_id=pattern['id'],
                    issues=[],
                    timeline=[],
                    savings_note=f"KP 매칭 → API 호출 없음 (절감 ~$0.003–0.006)"
                )

         pass  # end for

        # ── 2. 중복 제거 ────────────────────────────────────
        now = time.time()
        fresh_issues = []
        for iss in issues:
            fp = _issue_fingerprint(iss)
            last = self._seen.get(fp, 0.0)
            if now - last > self._dedup_window:
                fresh_issues.append(iss)
                self._seen[fp] = now

        if not fresh_issues:
            return PreFilterResult(
                skip_api=True,
                local_diagnosis="[중복] 이미 분석된 이슈입니다. 캐시를 확인하세요.",
                issues=[],
                timeline=[],
                savings_note="중복 제거 → API 호출 없음"
            )

        # ── 3. 타임라인 압축 ────────────────────────────────
        compressed_tl = _compress_timeline(tl, fresh_issues)

        # ── 4. 이슈 병합 (같은 태스크의 여러 이슈를 1건으로) ─
        merged = _merge_issues(fresh_issues)

        return PreFilterResult(
            skip_api=False,
            issues=merged,
            timeline=compressed_tl,
            savings_note=(
                f"원본 {len(issues)}이슈 → 병합 {len(merged)}이슈, "
                f"타임라인 {len(tl)} → {len(compressed_tl)}이벤트"
            )
        )

    def reset(self) -> None:
        self._seen.clear()


# ── 타임라인 압축 ────────────────────────────────────────────
def _compress_timeline(timeline: List[Dict],
                       issues: List[Dict]) -> List[Dict]:
    """
    타임라인 이벤트를 최대 20개로 압축.
    우선순위:
      1. mutex_timeout (우선순위 역전 증거)
      2. 이슈 발생 직전 ±500ms 구간
      3. isr_enter (ISR 관련 이슈 시)
      4. 나머지는 균등 샘플링
    """
    if len(timeline) <= 20:
        return timeline

    important = []
    issue_ts   = [i.get('timestamp_us', 0) for i in issues if 'timestamp_us' in i]

    for ev in timeline:
        et = ev.get('type', '')
        ts = ev.get('t_us', 0)

        # 항상 포함: 중요 이벤트
        if et in ('mutex_timeout', 'isr_enter'):
            important.append(ev)
            continue

        # 이슈 발생 ±500ms 구간
        for its in issue_ts:
            if abs(ts - its) < 500_000:
                important.append(ev)
                break

    # 중복 제거
    seen_ts = set()
    unique = []
    for ev in important:
        k = (ev.get('type'), ev.get('t_us', 0))
        if k not in seen_ts:
            seen_ts.add(k)
            unique.append(ev)

    if len(unique) >= 20:
        return unique[:20]

    # 나머지 균등 샘플링
    remaining = [e for e in timeline if e not in unique]
    step = max(1, len(remaining) // (20 - len(unique)))
    sampled = remaining[::step][:(20 - len(unique))]

    result = sorted(unique + sampled, key=lambda e: e.get('t_us', 0))
    return result[:20]


# ── 이슈 병합 ────────────────────────────────────────────────
def _merge_issues(issues: List[Dict]) -> List[Dict]:
    """
    같은 태스크의 여러 이슈를 1개 이슈로 병합.
    예: DataProcessor의 stack_overflow + high_cpu → 1건 (더 심각한 severity 유지)

    단, Critical + High가 섞이면 Critical 유지.
    """
    by_task: Dict[str, List[Dict]] = defaultdict(list)
    for iss in issues:
        tasks = iss.get('affected_tasks', [])
        key   = tasks[0] if tasks else 'SYSTEM'
        by_task[key].append(iss)

    merged = []
    for task, iss_list in by_task.items():
        if len(iss_list) == 1:
            merged.append(iss_list[0])
            continue

        # 가장 심각한 것 기준
        sev_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        iss_list.sort(key=lambda i: sev_order.get(i.get('severity','Low'), 3))
        base = dict(iss_list[0])
        base['description'] += ' | '.join(
            f"[+{i.get('type','')}]" for i in iss_list[1:]
        )
        base['_merged_count'] = len(iss_list)
        merged.append(base)

    return merged


# ── 로컬 진단 포맷 ───────────────────────────────────────────
def _format_local_diagnosis_db(match: Dict, issues: List[Dict]) -> str:
    """PatternDB 매치 결과를 로컬 진단 텍스트로 변환."""
    pat = match['pattern']
    chain_str = ' → '.join(match.get('causal_chain', [])[:5])
    lines = [
        f"[LOCAL:{pat.id}] {pat.name}",
        f"SEVERITY:    {pat.severity}",
        f"SCENARIO:    {pat.category}",
        f"SUMMARY:     {pat.description}",
        f"CHAIN:       {chain_str}",
        f"ROOT_CAUSE:  {pat.diagnosis.get('root_cause','')}",
        f"FIX:         {pat.diagnosis.get('fix','')}",
        f"PREVENTION:  {pat.diagnosis.get('prevention','')}",
    ]
    if pat.references:
        lines.append(f"REFERENCES:  {', '.join(pat.references[:2])}")
    return '\n'.join(lines)


def _format_local_diagnosis(pattern: Dict, issues: List[Dict]) -> str:
    lines = [
        f"[LOCAL:{pattern['id']}] {pattern['name']}",
        f"SEVERITY: {pattern['severity']}",
        f"SUMMARY: {pattern['summary']}",
        f"ROOT_CAUSE: {pattern['root_cause']}",
        f"FIX: {pattern['fix']}",
        f"PREVENTION: {pattern['prevention']}",
    ]
    return "\n".join(lines)


# ── 결과 ─────────────────────────────────────────────────────
class PreFilterResult:
    def __init__(self, skip_api: bool, issues: List[Dict],
                 timeline: List[Dict], local_diagnosis: str = '',
                 pattern_id: str = '', savings_note: str = '',
                 causal_chain: Optional[List[str]] = None):
        self.skip_api        = skip_api
        self.issues          = issues
        self.timeline        = timeline
        self.local_diagnosis = local_diagnosis
        self.pattern_id      = pattern_id
        self.savings_note    = savings_note
        self.causal_chain    = causal_chain or []

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)
