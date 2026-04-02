#!/usr/bin/env python3
"""
causal_graph.py — DAG 기반 인과 관계 그래프

문제:
  현재 causal_chain = List[str] 는 선형이라
  "두 독립 원인이 합쳐져 하나의 결과" 표현 불가.

  예) ISR 과부하 ──┐
                   ├─→ mutex_timeout → priority_inversion
  메모리 압박 ────┘

해결:
  Directed Acyclic Graph (DAG):
    nodes: Event / Issue / State (통합 노드)
    edges: causes / correlated_with / precedes

기존 결과 → 그래프 변환:
  CorrelationEngine(CORR-001) → node(mutex_timeout)
    → edge(causes) → node(priority_inversion)
  ResourceGraph(RG-001) → node(deadlock_cycle)
    → edge(causes) → node(task_blocked)
  StateMachine(SM-001) → node(task_blocked)
    → edge(correlated_with) → node(mutex_timeout)

루트 원인 탐색:
  root nodes = 들어오는 엣지가 없는 노드 (in-degree == 0)
  → AI 컨텍스트에 root_causes 필드로 전달

N100 처리: < 0.5ms
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ── 엣지 타입 ─────────────────────────────────────────────────
class EdgeKind:
    CAUSES          = 'causes'          # A가 B의 직접 원인
    CORRELATED_WITH = 'correlated_with' # A와 B가 관련됨 (방향 없음)
    PRECEDES        = 'precedes'        # A가 시간적으로 B 이전
    AGGRAVATES      = 'aggravates'      # A가 B를 악화시킴


# ── 노드 ──────────────────────────────────────────────────────
@dataclass
class CausalNode:
    id:         str          # 고유 식별자
    kind:       str          # 'event' | 'issue' | 'state' | 'pattern'
    label:      str          # 사람이 읽는 이름
    severity:   str = 'Low'  # Critical / High / Medium / Low
    confidence: float = 0.5
    source:     str = ''     # 'rule' | 'correlation' | 'state_machine' | 'resource_graph'
    timestamp_us: int = 0
    metadata:   Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = {
            'id':         self.id,
            'kind':       self.kind,
            'label':      self.label,
            'severity':   self.severity,
            'confidence': round(self.confidence, 2),
            'source':     self.source,
        }
        if self.timestamp_us:
            d['timestamp_us'] = self.timestamp_us
        return d


# ── 엣지 ──────────────────────────────────────────────────────
@dataclass
class CausalEdge:
    from_id:    str
    to_id:      str
    kind:       str          # EdgeKind.*
    confidence: float = 0.7
    evidence:   List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'from': self.from_id,
            'to':   self.to_id,
            'kind': self.kind,
            'conf': round(self.confidence, 2),
        }


# ── DAG ───────────────────────────────────────────────────────
class CausalGraph:
    """
    분석 결과를 방향 비순환 그래프(DAG)로 구성.

    사용:
        cg = CausalGraph()
        cg.ingest_correlation(corr_results)
        cg.ingest_state_machine(sm_results)
        cg.ingest_resource_graph(rg_results)
        cg.ingest_rule_issues(rule_issues)

        roots   = cg.root_causes()          # in-degree=0 노드
        chains  = cg.longest_chains(top_n=3) # 가장 긴 인과 체인
        ctx     = cg.to_context_dict()      # AI 컨텍스트용
    """

    def __init__(self):
        self._nodes: Dict[str, CausalNode] = {}
        self._edges: List[CausalEdge]      = []
        # adjacency: from_id → list of to_id
        self._adj:   Dict[str, List[str]]  = defaultdict(list)
        # in-degree
        self._in_deg: Dict[str, int]       = defaultdict(int)

    # ── 노드/엣지 추가 ────────────────────────────────────────
    def add_node(self, node: CausalNode) -> None:
        if node.id not in self._nodes:
            self._nodes[node.id] = node
            self._in_deg[node.id]  # initialize

    def add_edge(self, edge: CausalEdge) -> None:
        # 사이클 방지 (DAG 유지)
        if self._would_create_cycle(edge.from_id, edge.to_id):
            return
        # 중복 엣지 방지
        for e in self._edges:
            if e.from_id == edge.from_id and e.to_id == edge.to_id:
                return
        self._edges.append(edge)
        self._adj[edge.from_id].append(edge.to_id)
        self._in_deg[edge.to_id] += 1
        # from_id 초기화
        if edge.from_id not in self._in_deg:
            self._in_deg[edge.from_id] = 0

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """to_id에서 from_id에 도달 가능하면 사이클."""
        if from_id == to_id:
            return True
        visited = set()
        stack   = [to_id]
        while stack:
            cur = stack.pop()
            if cur == from_id:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(self._adj.get(cur, []))
        return False

    # ── 인수 (기존 분석 결과 → 그래프 변환) ──────────────────
    def ingest_correlation(self, corr_results: List) -> None:
        """CorrelationResult 리스트 → 그래프 노드/엣지."""
        for cr in corr_results:
            # 패턴 노드
            n = CausalNode(
                id        = cr.pattern_id,
                kind      = 'pattern',
                label     = cr.description[:60],
                severity  = cr.severity,
                confidence= cr.confidence,
                source    = 'correlation',
                timestamp_us = cr.timestamp_us,
            )
            self.add_node(n)

            # causal_chain을 선형 엣지로 변환
            chain = cr.causal_chain
            for i in range(len(chain) - 1):
                step_id_a = f"{cr.pattern_id}_step{i}"
                step_id_b = f"{cr.pattern_id}_step{i+1}"
                self.add_node(CausalNode(
                    id=step_id_a, kind='event',
                    label=chain[i][:50], source='correlation'))
                self.add_node(CausalNode(
                    id=step_id_b, kind='event',
                    label=chain[i+1][:50], source='correlation'))
                self.add_edge(CausalEdge(
                    from_id=step_id_a, to_id=step_id_b,
                    kind=EdgeKind.PRECEDES,
                    confidence=cr.confidence * 0.9,
                ))

            # 패턴 → 마지막 체인 스텝
            if chain:
                last_id = f"{cr.pattern_id}_step{len(chain)-1}"
                self.add_edge(CausalEdge(
                    from_id=f"{cr.pattern_id}_step0",
                    to_id=cr.pattern_id,
                    kind=EdgeKind.CAUSES,
                    confidence=cr.confidence,
                ))

    def ingest_state_machine(self, sm_results: List) -> None:
        """SMResult 리스트 → 그래프 노드/엣지."""
        for sr in sm_results:
            n = CausalNode(
                id        = sr.pattern_id,
                kind      = 'state',
                label     = sr.description[:60],
                severity  = sr.severity,
                confidence= sr.confidence,
                source    = 'state_machine',
            )
            self.add_node(n)

            # SM-001(long blocked) → 원인으로 CORR-001(mutex_timeout) 연결 시도
            if sr.pattern_id == 'SM-001' and 'CORR-001' in self._nodes:
                self.add_edge(CausalEdge(
                    from_id   = 'CORR-001',
                    to_id     = 'SM-001',
                    kind      = EdgeKind.CAUSES,
                    confidence= 0.75,
                    evidence  = ['mutex_timeout → task_blocked'],
                ))

    def ingest_resource_graph(self, rg_results: List) -> None:
        """GraphResult 리스트 → 그래프 노드/엣지."""
        for rr in rg_results:
            n = CausalNode(
                id        = rr.pattern_id,
                kind      = 'issue',
                label     = rr.description[:60],
                severity  = rr.severity,
                confidence= rr.confidence,
                source    = 'resource_graph',
            )
            self.add_node(n)

            # RG-001(deadlock) → SM-001(blocked) 연결
            if rr.pattern_id == 'RG-001' and 'SM-001' in self._nodes:
                self.add_edge(CausalEdge(
                    from_id   = 'RG-001',
                    to_id     = 'SM-001',
                    kind      = EdgeKind.CAUSES,
                    confidence= 0.85,
                    evidence  = ['deadlock_cycle → task_blocked'],
                ))
            # RG-001 → CORR-001 연결 (같은 deadlock 현상)
            if rr.pattern_id == 'RG-001' and 'CORR-001' in self._nodes:
                self.add_edge(CausalEdge(
                    from_id   = 'RG-001',
                    to_id     = 'CORR-001',
                    kind      = EdgeKind.CORRELATED_WITH,
                    confidence= 0.90,
                ))

    def ingest_rule_issues(self, issues: List[Dict]) -> None:
        """Rule 이슈 딕셔너리 리스트 → 그래프 노드."""
        for iss in issues:
            itype = iss.get('type', iss.get('issue_type', 'unknown'))
            nid   = f"rule_{itype}"
            n = CausalNode(
                id        = nid,
                kind      = 'issue',
                label     = iss.get('description', itype)[:60],
                severity  = iss.get('severity', 'Medium'),
                confidence= 0.75,
                source    = 'rule',
                timestamp_us = iss.get('timestamp_us', 0),
            )
            self.add_node(n)

    # ── 분석 ──────────────────────────────────────────────────
    def root_causes(self) -> List[CausalNode]:
        """
        루트 원인 = 들어오는 CAUSES 엣지가 없는 노드.
        (in-degree == 0 for CAUSES edges)
        """
        causes_to: Set[str] = {
            e.to_id for e in self._edges
            if e.kind == EdgeKind.CAUSES
        }
        roots = [
            n for n_id, n in self._nodes.items()
            if n_id not in causes_to
        ]
        # severity 순 정렬
        sev = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        roots.sort(key=lambda n: (sev.get(n.severity, 3), -n.confidence))
        return roots

    def longest_chains(self, top_n: int = 3) -> List[List[str]]:
        """
        가장 긴 인과 체인 top_n개 반환.
        각 체인은 루트 원인부터 최종 결과까지의 노드 label 리스트.
        """
        roots = [n.id for n in self.root_causes()]
        all_chains: List[List[str]] = []

        def dfs(node_id: str, path: List[str]) -> None:
            path.append(self._nodes[node_id].label
                         if node_id in self._nodes else node_id)
            nexts = [
                e.to_id for e in self._edges
                if e.from_id == node_id and e.kind == EdgeKind.CAUSES
            ]
            if not nexts:
                all_chains.append(list(path))
            else:
                for nxt in nexts:
                    if nxt not in path:   # DAG이지만 안전장치
                        dfs(nxt, path)
            path.pop()

        for root in roots[:5]:   # 상위 5개 루트만
            dfs(root, [])

        all_chains.sort(key=len, reverse=True)
        return all_chains[:top_n]

    def to_context_dict(self, max_nodes: int = 15) -> Dict:
        """
        AI 컨텍스트용 딕셔너리 변환.
        토큰 예산을 위해 max_nodes 개 노드만 포함.
        """
        # severity + confidence 순으로 상위 노드 선택
        sev = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        top_nodes = sorted(
            self._nodes.values(),
            key=lambda n: (sev.get(n.severity, 3), -n.confidence)
        )[:max_nodes]
        top_ids = {n.id for n in top_nodes}

        # 선택된 노드 간 엣지만 포함
        top_edges = [
            e for e in self._edges
            if e.from_id in top_ids and e.to_id in top_ids
        ]

        roots   = self.root_causes()[:3]
        chains  = self.longest_chains(top_n=2)

        return {
            'nodes':       [n.to_dict() for n in top_nodes],
            'edges':       [e.to_dict() for e in top_edges[:20]],
            'root_causes': [n.label for n in roots],
            'causal_chains': chains[:2],
            'node_count':  len(self._nodes),
            'edge_count':  len(self._edges),
        }

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)
