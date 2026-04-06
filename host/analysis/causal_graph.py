#!/usr/bin/env python3
"""
causal_graph.py — DAG 기반 인과 관계 그래프 (v2: Global Session Graph)

v2 개선:
  - GlobalCausalGraph: 세션 전체를 아우르는 누산 그래프
      매 스냅샷마다 new CausalGraph() 대신 세션 동안 동일 인스턴스 유지
  - 노드 병합(node merging): 동일 현상 반복 시 occurrence_count 증가
  - 의미 기반 자동 연결(semantic edge): 패턴 ID 하드코딩 제거
      카테고리 + 시간 창으로 인과 관계 자동 추론
  - 시간적 연속성: "스냅샷 #5 heap_leak → 스냅샷 #10 heap_exhaustion"
  - 노드 상한(max_nodes): 메모리 & AI 토큰 예산 보호

EdgeKind:
  CAUSES:          A가 B의 직접 원인
  CORRELATED_WITH: 시간·카테고리로 관련됨
  PRECEDES:        A가 시간적으로 B 이전
  AGGRAVATES:      A가 B를 악화시킴

N100 처리 시간: < 0.5ms / 스냅샷
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
import time


class EdgeKind:
    CAUSES          = 'causes'
    CORRELATED_WITH = 'correlated_with'
    PRECEDES        = 'precedes'
    AGGRAVATES      = 'aggravates'


@dataclass
class CausalNode:
    id:              str
    kind:            str      # 'event' | 'issue' | 'state' | 'pattern'
    label:           str
    severity:        str = 'Low'
    confidence:      float = 0.5
    source:          str = ''
    timestamp_us:    int = 0
    occurrence_count: int = 1   # 반복 발생 횟수 (v2 추가)
    first_seen_us:   int = 0    # 최초 발생 (v2 추가)
    category:        str = 'general'   # memory/timing/deadlock/general

    def merge(self, other: 'CausalNode') -> None:
        """동일 노드 반복 발생 시 병합."""
        self.occurrence_count += 1
        # 신뢰도: 반복될수록 높아짐 (max 0.95)
        self.confidence = min(0.95, self.confidence + 0.05)
        # 심각도는 더 높은 쪽 유지
        sev = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        if sev.get(other.severity, 3) < sev.get(self.severity, 3):
            self.severity = other.severity
        self.timestamp_us = other.timestamp_us   # 최신 타임스탬프

    def to_dict(self) -> Dict:
        d = {
            'id':         self.id,
            'kind':       self.kind,
            'label':      self.label,
            'severity':   self.severity,
            'confidence': round(self.confidence, 2),
            'source':     self.source,
            'category':   self.category,
        }
        if self.occurrence_count > 1:
            d['occurrences'] = self.occurrence_count
        if self.timestamp_us:
            d['timestamp_us'] = self.timestamp_us
        if self.context_type != 'task':
            d['context_type'] = self.context_type
        return d


@dataclass
class CausalEdge:
    from_id:    str
    to_id:      str
    kind:       str
    confidence: float = 0.7
    evidence:   List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'from': self.from_id,
            'to':   self.to_id,
            'kind': self.kind,
            'conf': round(self.confidence, 2),
        }


# ── 의미 기반 자동 연결 규칙 ──────────────────────────────────
# (from_category, to_category, max_time_gap_us, edge_kind, confidence)
_SEMANTIC_RULES: List[Tuple] = [
    ('deadlock', 'timing',  60_000_000, EdgeKind.CAUSES,       0.75),
    ('deadlock', 'memory',  60_000_000, EdgeKind.AGGRAVATES,   0.60),
    ('memory',   'memory',  120_000_000, EdgeKind.CAUSES,      0.70),
    ('timing',   'timing',  30_000_000, EdgeKind.CORRELATED_WITH, 0.60),
    ('timing',   'memory',  60_000_000, EdgeKind.AGGRAVATES,   0.55),
]

# 심각도 순서
_SEV_ORDER = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}


class CausalGraph:
    """
    단일 분석 사이클용 DAG (기존 v1 호환).
    GlobalCausalGraph에서 내부적으로 사용.
    """

    def __init__(self, max_nodes: int = 200):
        self._nodes:   Dict[str, CausalNode] = {}
        self._edges:   List[CausalEdge] = []
        self._adj:     Dict[str, List[str]] = defaultdict(list)
        self._in_deg:  Dict[str, int] = defaultdict(int)
        self._max_nodes = max_nodes

    # ── 노드/엣지 추가 ────────────────────────────────────────
    def add_node(self, node: CausalNode) -> bool:
        """
        노드 추가. 이미 존재하면 병합(merge).
        Returns: True=추가됨, False=병합됨
        """
        if node.id in self._nodes:
            self._nodes[node.id].merge(node)
            return False
        if len(self._nodes) >= self._max_nodes:
            # 공간 부족: Low severity 노드 제거 후 추가
            self._evict_lowest()
        self._nodes[node.id] = node
        self._in_deg[node.id]   # initialize
        if node.first_seen_us == 0:
            node.first_seen_us = node.timestamp_us
        return True

    def _evict_lowest(self) -> None:
        """Low severity + 낮은 confidence 노드 1개 제거."""
        candidates = [
            n for n in self._nodes.values()
            if n.severity == 'Low' and n.occurrence_count == 1
        ]
        if candidates:
            worst = min(candidates, key=lambda n: n.confidence)
            del self._nodes[worst.id]
            self._edges = [e for e in self._edges
                           if e.from_id != worst.id and e.to_id != worst.id]

    def add_edge(self, edge: CausalEdge) -> bool:
        if edge.from_id not in self._nodes or edge.to_id not in self._nodes:
            return False
        if self._would_create_cycle(edge.from_id, edge.to_id):
            return False
        for e in self._edges:
            if e.from_id == edge.from_id and e.to_id == edge.to_id:
                return False
        self._edges.append(edge)
        self._adj[edge.from_id].append(edge.to_id)
        self._in_deg[edge.to_id] += 1
        return True

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        if from_id == to_id:
            return True
        visited: Set[str] = set()
        stack = [to_id]
        while stack:
            cur = stack.pop()
            if cur == from_id:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(self._adj.get(cur, []))
        return False

    # ── ingest (분석 결과 → 그래프) ──────────────────────────
    def ingest_all(self,
                   corr_results: List = None,
                   sm_results:   List = None,
                   rg_results:   List = None,
                   rule_issues:  List[Dict] = None) -> None:
        """모든 분석 결과를 한 번에 ingest (v2: 통합 메서드)."""
        ts_now = int(time.time() * 1_000_000)

        for cr in (corr_results or []):
            cat = cr.scenario if hasattr(cr, 'scenario') else 'general'
            n = CausalNode(
                id=cr.pattern_id, kind='pattern',
                label=cr.description[:60], severity=cr.severity,
                confidence=cr.confidence, source='correlation',
                timestamp_us=getattr(cr, 'timestamp_us', ts_now),
                category=cat,
            )
            self.add_node(n)
            # causal_chain → 선형 엣지
            chain = cr.causal_chain
            for i in range(len(chain) - 1):
                nid_a = f"{cr.pattern_id}_s{i}"
                nid_b = f"{cr.pattern_id}_s{i+1}"
                self.add_node(CausalNode(nid_a, 'event', chain[i][:40],
                                          source='correlation', category=cat))
                self.add_node(CausalNode(nid_b, 'event', chain[i+1][:40],
                                          source='correlation', category=cat))
                self.add_edge(CausalEdge(nid_a, nid_b, EdgeKind.PRECEDES,
                                          confidence=cr.confidence * 0.9))
            if chain:
                self.add_edge(CausalEdge(
                    f"{cr.pattern_id}_s0", cr.pattern_id,
                    EdgeKind.CAUSES, confidence=cr.confidence))

        for sr in (sm_results or []):
            cat = sr.to_dict().get('scenario', 'timing')
            self.add_node(CausalNode(
                sr.pattern_id, 'state', sr.description[:60],
                sr.severity, sr.confidence, 'state_machine',
                category=cat))

        for rr in (rg_results or []):
            self.add_node(CausalNode(
                rr.pattern_id, 'issue', rr.description[:60],
                rr.severity, rr.confidence, 'resource_graph',
                category='deadlock'))

        for iss in (rule_issues or []):
            itype = iss.get('type', iss.get('issue_type', 'unknown'))
            nid = f"rule_{itype}"
            self.add_node(CausalNode(
                nid, 'issue', iss.get('description', itype)[:60],
                iss.get('severity', 'Medium'), 0.75, 'rule',
                iss.get('timestamp_us', ts_now),
                category=self._infer_category(itype)))

        # 의미 기반 자동 연결
        self._apply_semantic_edges()

    def _apply_semantic_edges(self) -> None:
        """
        _SEMANTIC_RULES를 적용해 카테고리+시간 조건으로 자동 연결.
        패턴 ID 하드코딩 없음.
        """
        nodes = list(self._nodes.values())
        for i, n_from in enumerate(nodes):
            for n_to in nodes[i+1:]:
                if n_from.id == n_to.id:
                    continue
                dt = abs(n_to.timestamp_us - n_from.timestamp_us)
                for (cat_f, cat_t, max_dt, kind, conf) in _SEMANTIC_RULES:
                    if (n_from.category == cat_f and n_to.category == cat_t
                            and dt <= max_dt):
                        earlier = n_from if n_from.timestamp_us <= n_to.timestamp_us else n_to
                        later   = n_to   if n_from.timestamp_us <= n_to.timestamp_us else n_from
                        self.add_edge(CausalEdge(
                            earlier.id, later.id, kind, conf,
                            evidence=[f"time_gap={dt//1000}ms, "
                                      f"categories={cat_f}→{cat_t}"]))
                        break   # 규칙 하나만 적용

    # ── 분석 ──────────────────────────────────────────────────
    def root_causes(self) -> List[CausalNode]:
        causes_to: Set[str] = {
            e.to_id for e in self._edges if e.kind == EdgeKind.CAUSES}
        roots = [n for nid, n in self._nodes.items() if nid not in causes_to]
        roots.sort(key=lambda n: (_SEV_ORDER.get(n.severity, 3), -n.confidence))
        return roots

    def longest_chains(self, top_n: int = 3) -> List[List[str]]:
        roots = [n.id for n in self.root_causes()]
        all_chains: List[List[str]] = []

        def dfs(node_id: str, path: List[str]) -> None:
            node = self._nodes.get(node_id)
            path.append(node.label if node else node_id)
            nexts = [e.to_id for e in self._edges
                     if e.from_id == node_id and e.kind == EdgeKind.CAUSES]
            if not nexts:
                all_chains.append(list(path))
            else:
                for nxt in nexts:
                    if nxt not in path:
                        dfs(nxt, path)
            path.pop()

        for root in roots[:5]:
            dfs(root, [])
        all_chains.sort(key=len, reverse=True)
        return all_chains[:top_n]

    def to_context_dict(self, max_nodes: int = 15) -> Dict:
        top_nodes = sorted(
            self._nodes.values(),
            key=lambda n: (_SEV_ORDER.get(n.severity, 3),
                            -n.confidence, -n.occurrence_count)
        )[:max_nodes]
        top_ids = {n.id for n in top_nodes}
        top_edges = [e for e in self._edges
                     if e.from_id in top_ids and e.to_id in top_ids]
        roots  = self.root_causes()[:3]
        chains = self.longest_chains(top_n=2)
        return {
            'nodes':        [n.to_dict() for n in top_nodes],
            'edges':        [e.to_dict() for e in top_edges[:20]],
            'root_causes':  [n.label for n in roots],
            'causal_chains': chains[:2],
            'node_count':   len(self._nodes),
            'edge_count':   len(self._edges),
        }


    def to_mermaid(self, max_nodes: int = 20) -> str:
        """
        Mermaid 다이어그램 문자열 반환.

        사용:
            md = gcg.to_mermaid()
            with open("causal_graph.mermaid", "w") as f:
                f.write(md)
            # GitHub / Notion / VS Code에서 렌더링

        출력 예:
            ```mermaid
            graph TD
                RG-001["🔴 Deadlock cycle<br/>conf=0.95"]
                SM-001["🟠 HighTask blocked<br/>conf=0.70"]
                RG-001 -->|causes| SM-001
            ```
        """
        sev_emoji = {
            'Critical': '🔴', 'High': '🟠', 'Medium': '🟡', 'Low': '⚪'}
        top_nodes = sorted(
            self._nodes.values(),
            key=lambda n: (_SEV_ORDER.get(n.severity, 3), -n.confidence)
        )[:max_nodes]
        top_ids = {n.id for n in top_nodes}

        lines = ["```mermaid", "graph TD"]

        # 노드
        for n in top_nodes:
            emoji = sev_emoji.get(n.severity, '⚪')
            safe_id = n.id.replace('-','_').replace('.','_')
            label   = n.label[:40].replace('"', "'")
            occ     = f"<br/>×{n.occurrence_count}" if n.occurrence_count > 1 else ""
            ctx     = f"<br/>[{n.context_type}]" if n.context_type != 'task' else ""
            lines.append(
                f'    {safe_id}["{emoji} {label}<br/>'
                f'conf={n.confidence:.2f}{occ}{ctx}"]')

        # 엣지
        edge_labels = {
            EdgeKind.CAUSES:          '-->|causes|',
            EdgeKind.CORRELATED_WITH: '-.->|corr|',
            EdgeKind.PRECEDES:        '-->|precedes|',
            EdgeKind.AGGRAVATES:      '-->|aggravates|',
        }
        for e in self._edges:
            if e.from_id in top_ids and e.to_id in top_ids:
                fid = e.from_id.replace('-','_').replace('.','_')
                tid = e.to_id.replace('-','_').replace('.','_')
                arrow = edge_labels.get(e.kind, '-->')
                lines.append(f"    {fid} {arrow} {tid}")

        # 루트 원인 강조 (굵은 테두리)
        roots = {n.id.replace('-','_').replace('.','_')
                 for n in self.root_causes()[:3]}
        if roots:
            lines.append("    %% Root causes")
            for r in roots:
                lines.append(f"    style {r} stroke:#f00,stroke-width:3px")

        lines.append("```")
        return "\n".join(lines)

    @property
    def node_count(self) -> int: return len(self._nodes)
    @property
    def edge_count(self) -> int: return len(self._edges)

    @staticmethod
    def _infer_category(issue_type: str) -> str:
        mem  = {'stack_overflow_imminent','low_stack','heap_exhaustion','low_heap'}
        time_ = {'high_cpu','cpu_overload','task_starvation','cpu_creep'}
        dead = {'priority_inversion','hard_fault'}
        if issue_type in mem:  return 'memory'
        if issue_type in time_: return 'timing'
        if issue_type in dead: return 'deadlock'
        return 'general'


# ── Global Session Graph (v2 핵심) ────────────────────────────
class GlobalCausalGraph(CausalGraph):
    """
    세션 전체를 아우르는 누산 그래프.

    사용:
        gcg = GlobalCausalGraph()   # 세션 시작 시 1회 생성

        # 매 스냅샷마다
        gcg.update(corr_results, sm_results, rg_results, rule_issues)

        # 언제든 조회
        roots  = gcg.root_causes()
        trends = gcg.get_trends()   # 반복 발생 패턴
        ctx    = gcg.to_context_dict()
    """

    def __init__(self, max_nodes: int = 200):
        super().__init__(max_nodes=max_nodes)
        self._snapshot_count = 0
        self._session_start  = int(time.time() * 1_000_000)

    def update(self,
               corr_results: List = None,
               sm_results:   List = None,
               rg_results:   List = None,
               rule_issues:  List[Dict] = None) -> None:
        """
        새 스냅샷 분석 결과를 글로벌 그래프에 누산.
        노드가 이미 있으면 병합(occurrence_count 증가).
        """
        self._snapshot_count += 1
        self.ingest_all(corr_results, sm_results, rg_results, rule_issues)

    def get_trends(self) -> List[Dict]:
        """
        occurrence_count > 1인 노드 = 반복 발생 패턴.
        Returns: severity 순 정렬된 반복 패턴 리스트
        """
        repeated = [
            {
                'id':          n.id,
                'label':       n.label,
                'severity':    n.severity,
                'occurrences': n.occurrence_count,
                'confidence':  round(n.confidence, 2),
                'category':    n.category,
            }
            for n in self._nodes.values()
            if n.occurrence_count > 1
        ]
        repeated.sort(key=lambda r: (_SEV_ORDER.get(r['severity'], 3),
                                      -r['occurrences']))
        return repeated

    def to_context_dict(self, max_nodes: int = 15) -> Dict:
        d = super().to_context_dict(max_nodes)
        d['session_snapshots'] = self._snapshot_count
        d['repeated_patterns'] = self.get_trends()[:5]
        return d

    def reset(self) -> None:
        """세션 종료 시 리셋."""
        self._nodes.clear()
        self._edges.clear()
        self._adj.clear()
        self._in_deg.clear()
        self._snapshot_count = 0
        self._session_start  = int(time.time() * 1_000_000)
