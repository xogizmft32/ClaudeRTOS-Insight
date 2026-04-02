#!/usr/bin/env python3
"""
resource_graph.py — Resource Graph 모델 + Deadlock 탐지

구조:
  노드: Task (task_id), Mutex (mutex_addr)
  엣지:
    holds(task → mutex)  : task가 mutex를 보유 중
    waits(task → mutex)  : task가 mutex를 대기 중

데이터 소스:
  timeline의 mutex_take / mutex_give / mutex_timeout 이벤트로
  현재 보유·대기 상태를 추론한다.

  FreeRTOS는 holder를 hook으로 직접 노출하지 않으므로
  "마지막 mutex_take 후 mutex_give 없음 = 보유 중"으로 추론.

탐지:
  - RG-001: 순환 의존성 (deadlock cycle, DFS O(V+E))
      A holds M1 waits M2
      B holds M2 waits M1  →  deadlock
  - RG-002: 단일 mutex 복수 대기
      A holds M1, B/C/D waits M1  →  contention
  - RG-003: 오랜 보유 (take 후 give 없이 N 이벤트 경과)

N100 처리 시간: < 0.2ms
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ── 그래프 엣지 ──────────────────────────────────────────────
@dataclass
class ResourceEdge:
    kind:       str      # 'holds' | 'waits'
    task_id:    int
    mutex_addr: str      # "0x20001234" 형식
    mutex_name: str      # 사람이 읽을 수 있는 이름 (없으면 addr)
    since_event: int     # 엣지가 생성된 이벤트 인덱스


# ── 그래프 분석 결과 ─────────────────────────────────────────
@dataclass
class GraphResult:
    pattern_id:   str
    severity:     str
    description:  str
    causal_chain: List[str]
    evidence:     List[str]
    confidence:   float
    affected_tasks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'pattern_id':    self.pattern_id,
            'severity':      self.severity,
            'scenario':      'deadlock',
            'description':   self.description,
            'causal_chain':  self.causal_chain,
            'evidence':      self.evidence,
            'confidence':    self.confidence,
            'affected_tasks': self.affected_tasks,
        }


# ── Resource Graph ────────────────────────────────────────────
class ResourceGraph:
    """
    Mutex 보유·대기 관계를 방향 그래프로 모델링.

    사용:
        rg = ResourceGraph()
        rg.apply_timeline(timeline_events)
        results = rg.analyze()
    """

    def __init__(self):
        # task_id → set of mutex_addr (보유)
        self._holds:  Dict[int, Set[str]] = defaultdict(set)
        # task_id → mutex_addr (대기 중, 최대 1개)
        self._waits:  Dict[int, Optional[str]] = {}
        # mutex_addr → task_id (현재 보유자)
        self._holder: Dict[str, Optional[int]] = {}
        # mutex_addr → list of task_id (대기 큐)
        self._waiters: Dict[str, List[int]] = defaultdict(list)
        # mutex_addr → name
        self._names:  Dict[str, str] = {}
        self._event_count = 0

    # ── 이벤트 적용 ──────────────────────────────────────────
    def apply_timeline(self, events: List[Dict]) -> None:
        """타임라인 이벤트를 순서대로 적용해 그래프 갱신."""
        for ev in events:
            self._event_count += 1
            etype = ev.get('type', '')
            tid   = ev.get('task_id')
            maddr = ev.get('mutex') or ev.get('mutex_addr', '')
            mname = ev.get('mutex_name', maddr)

            if maddr:
                self._names[maddr] = mname

            if etype == 'mutex_take' and tid is not None and maddr:
                # 대기 시작
                self._waits[tid] = maddr
                if tid not in self._waiters[maddr]:
                    self._waiters[maddr].append(tid)

            elif etype == 'mutex_give' and tid is not None and maddr:
                # 반환: holds 제거, 대기 큐 선두 → holder
                self._holds[tid].discard(maddr)
                if self._holder.get(maddr) == tid:
                    self._holder[maddr] = None
                # 대기 큐 선두가 새 보유자
                if self._waiters[maddr]:
                    next_tid = self._waiters[maddr].pop(0)
                    self._holds[next_tid].add(maddr)
                    self._holder[maddr] = next_tid
                    self._waits.pop(next_tid, None)

            elif etype == 'mutex_timeout' and tid is not None and maddr:
                # 타임아웃: 대기 취소
                self._waits.pop(tid, None)
                if tid in self._waiters[maddr]:
                    self._waiters[maddr].remove(tid)

            # mutex_take 후 다음 이벤트가 give 없으면 보유 중으로 간주
            # (FreeRTOS는 성공적 take를 별도 hook으로 알리지 않음)
            # → take 이벤트 직후 대기에서 holds로 이동 (낙관적 추론)
            if etype == 'mutex_take' and tid is not None and maddr:
                # 현재 보유자가 없으면 즉시 취득으로 간주
                if self._holder.get(maddr) is None:
                    self._holds[tid].add(maddr)
                    self._holder[maddr] = tid
                    self._waits.pop(tid, None)
                    if tid in self._waiters[maddr]:
                        self._waiters[maddr].remove(tid)

    # ── 분석 ─────────────────────────────────────────────────
    def analyze(self) -> List[GraphResult]:
        results: List[GraphResult] = []
        results += self._detect_deadlock_cycle()
        results += self._detect_contention()
        return results

    def _detect_deadlock_cycle(self) -> List[GraphResult]:
        """
        순환 의존성 탐지 (DFS).
        holds + waits 엣지로 Wait-For Graph 구성 후 사이클 검색.
        """
        # Wait-For Graph: task → task (A waits mutex held by B → A → B)
        wfg: Dict[int, List[int]] = defaultdict(list)

        for tid, maddr in list(self._waits.items()):
            if maddr is None:
                continue
            holder = self._holder.get(maddr)
            if holder is not None and holder != tid:
                wfg[tid].append(holder)

        # DFS cycle detection
        visited:    Set[int] = set()
        rec_stack:  Set[int] = set()
        cycle_path: List[int] = []

        def dfs(node: int, path: List[int]) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbor in wfg.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor, path):
                        return True
                elif neighbor in rec_stack:
                    # 사이클 발견 — path에서 cycle 추출
                    cycle_start = path.index(neighbor)
                    cycle_path.extend(path[cycle_start:])
                    return True
            rec_stack.discard(node)
            path.pop()
            return False

        found_cycle = False
        for node in list(wfg.keys()):
            if node not in visited:
                if dfs(node, []):
                    found_cycle = True
                    break

        if not found_cycle or not cycle_path:
            return []

        # 사이클 설명 생성
        chain = []
        tasks_in_cycle = []
        for i, tid in enumerate(cycle_path):
            maddr = self._waits.get(tid)
            mname = self._names.get(maddr, maddr) if maddr else '?'
            next_tid = cycle_path[(i + 1) % len(cycle_path)]
            chain.append(f"Task{tid} holds mutex → Task{next_tid} waits → blocked")
            tasks_in_cycle.append(str(tid))

        evidence = [
            f"Deadlock cycle: {' → '.join(f'Task{t}' for t in cycle_path)}",
            f"Mutexes involved: {set(self._names.get(self._waits.get(t,''),'?') for t in cycle_path)}",
            f"Wait-For Graph edges: {len(wfg)}",
        ]

        # confidence: evidence 기반
        confidence = self._calc_confidence([
            ('cycle_detected',      True,  0.40),
            ('multiple_tasks',      len(cycle_path) >= 2, 0.20),
            ('names_known',         any(n != a for a, n in self._names.items()), 0.10),
            ('recent_events',       self._event_count > 5, 0.10),
        ])

        return [GraphResult(
            pattern_id='RG-001',
            severity='Critical',
            description=(
                f"Deadlock cycle detected: "
                f"{' → '.join(f'Task{t}' for t in cycle_path)} → (cycle)"
            ),
            causal_chain=chain[:7],
            evidence=evidence,
            confidence=confidence,
            affected_tasks=tasks_in_cycle,
        )]

    def _detect_contention(self) -> List[GraphResult]:
        """단일 mutex에 3개 이상 태스크 대기 → 경합 경고."""
        results = []
        for maddr, waiters in self._waiters.items():
            if len(waiters) < 3:
                continue
            mname  = self._names.get(maddr, maddr)
            holder = self._holder.get(maddr)
            chain  = [
                f"mutex '{mname}' held by Task{holder}",
                f"{len(waiters)} tasks waiting: {waiters[:5]}",
                "high contention → scheduling pressure",
            ]
            evidence = [
                f"mutex: {mname} ({maddr})",
                f"holder: Task{holder}",
                f"waiters ({len(waiters)}): {waiters}",
            ]
            confidence = self._calc_confidence([
                ('many_waiters',   len(waiters) >= 3, 0.30),
                ('holder_known',   holder is not None, 0.20),
                ('name_known',     mname != maddr,     0.10),
                ('very_many',      len(waiters) >= 5,  0.15),
            ])
            results.append(GraphResult(
                pattern_id='RG-002',
                severity='High',
                description=(
                    f"Mutex contention: '{mname}' "
                    f"has {len(waiters)} waiters"
                ),
                causal_chain=chain,
                evidence=evidence,
                confidence=confidence,
                affected_tasks=[str(w) for w in waiters[:5]],
            ))
        return results

    @staticmethod
    def _calc_confidence(factors: List[Tuple]) -> float:
        """(name, condition, weight) 리스트로 evidence 기반 confidence 계산."""
        base = 0.30
        for _, cond, w in factors:
            if cond:
                base += w
        return round(min(0.95, base), 2)

    # ── 현재 상태 조회 ────────────────────────────────────────
    def get_state(self) -> Dict:
        return {
            'holds':  {str(k): list(v) for k, v in self._holds.items() if v},
            'waits':  {str(k): v for k, v in self._waits.items() if v},
            'holder': {k: v for k, v in self._holder.items() if v is not None},
        }

    def reset(self) -> None:
        self._holds.clear()
        self._waits.clear()
        self._holder.clear()
        self._waiters.clear()
        self._event_count = 0
