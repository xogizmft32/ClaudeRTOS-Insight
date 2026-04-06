#!/usr/bin/env python3
"""
response_cache.py — Multi-layer AI 응답 캐시 (v2)

v2 개선:
  - L1 (메모리): 소수 항목, 초고속 조회
  - L2 (파일):   세션 간 지속, 대용량
  - Context-aware Key: 이슈 + 스냅샷 컨텍스트 결합
  - Similarity 기반 캐시: 유사 이슈 재활용 (semantic bucket)
  - TTL × Confidence: 신뢰도 높은 응답은 TTL 연장
  - AI 결과 검증: confidence 임계값 미달 시 캐시 저장 거부

캐시 키 설계:
  L1 key = SHA256(issue_type + task + severity_bucket + context_bucket)[:16]
  L2 key = 동일 (파일 저장)

Semantic Bucket (Similarity):
  hwm=14W → "danger"   ─┐ 같은 L1/L2 키 공유
  hwm=15W → "danger"   ─┘
  hwm=45W → "warning"     별도 키

TTL × Confidence:
  base_ttl = 24h
  effective_ttl = base_ttl × (1 + confidence)
  → confidence=0.95 → 46.8h TTL
  → confidence=0.50 → 36h TTL

AI 결과 검증:
  put() 시 confidence < 0.5 이면 저장 거부
  → 불확실한 응답의 캐시 오염 방지
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DEFAULT_CACHE_DIR  = Path.home() / '.claudertos_cache'
_DEFAULT_CACHE_FILE = _DEFAULT_CACHE_DIR / 'ai_responses.json'

# L1 메모리 캐시 크기
_L1_MAX_ENTRIES = 20
# L2 파일 캐시 크기
_L2_MAX_ENTRIES = 200
# 기본 TTL (초)
_BASE_TTL = 86_400   # 24시간
# Critical: 빠른 갱신 (장애 상황 변화 대응)
_CRITICAL_BASE_TTL = 3_600   # 1시간
# 최소 저장 confidence
_MIN_CONFIDENCE = 0.50


@dataclass
class CacheEntry:
    key:             str
    response_text:   str
    response_dict:   Dict
    semantic_key:    str      # 디버그용
    created_at:      float
    ttl_s:           float
    confidence:      float    # AI 응답 confidence
    hit_count:       int   = 0
    cost_saved:      float = 0.0
    severity:        str   = 'High'

    @property
    def is_valid(self) -> bool:
        return (time.time() - self.created_at) < self.ttl_s

    def to_json(self) -> Dict:
        return {k: getattr(self, k) for k in
                ['key','response_text','response_dict','semantic_key',
                 'created_at','ttl_s','confidence','hit_count',
                 'cost_saved','severity']}

    @classmethod
    def from_json(cls, d: Dict) -> 'CacheEntry':
        valid = {k for k in d if hasattr(cls, k) or k in
                 ['key','response_text','response_dict','semantic_key',
                  'created_at','ttl_s','confidence','hit_count',
                  'cost_saved','severity']}
        return cls(**{k: d[k] for k in valid if k in d})


class SemanticKeyBuilder:
    """
    Context-aware Semantic Key 생성.

    이슈 + 스냅샷 컨텍스트를 결합해
    의미적으로 유사한 이슈는 같은 버킷을 공유한다.
    """

    # stack_hwm 버킷 (words remaining)
    _STACK_BUCKETS = [
        (10,  'stack_critical'),
        (20,  'stack_danger'),
        (50,  'stack_warning'),
        (999, 'stack_ok'),
    ]
    # heap 버킷 (free bytes / total ratio)
    _HEAP_BUCKETS = [
        (5,   'heap_critical'),   # < 5% free
        (15,  'heap_danger'),
        (30,  'heap_warning'),
        (100, 'heap_ok'),
    ]
    # CPU 버킷
    _CPU_BUCKETS = [
        (85, 'cpu_high'),
        (60, 'cpu_moderate'),
        (0,  'cpu_ok'),
    ]

    def build(self, issue: Dict,
               snap: Optional[Dict] = None) -> Tuple[str, str]:
        """
        Returns: (hash_key, human_readable_semantic_key)
        """
        itype    = issue.get('type', issue.get('issue_type', 'unknown'))
        task     = (issue.get('affected_tasks') or ['SYSTEM'])[0]
        severity = issue.get('severity', 'Medium')

        # Stack bucket
        hwm     = (issue.get('detail') or {}).get('stack_hwm_words', 9999)
        stack_b = self._bucket(hwm, self._STACK_BUCKETS)

        # Context buckets (스냅샷 있을 때)
        heap_b = cpu_b = 'na'
        if snap:
            h      = snap.get('heap', {})
            total  = max(h.get('total', 1), 1)
            free_p = h.get('free', total) * 100 // total
            heap_b = self._bucket(100 - free_p, [
                (5,  'heap_critical'),
                (15, 'heap_danger'),
                (30, 'heap_warning'),
                (100,'heap_ok'),
            ])
            cpu_b  = self._bucket(snap.get('cpu_usage', 0), [
                (85, 'cpu_high'),
                (60, 'cpu_moderate'),
                (0,  'cpu_ok'),
            ])

        semantic = f"{itype}::{task}::{severity}::{stack_b}::{heap_b}::{cpu_b}"
        key      = hashlib.sha256(semantic.encode()).hexdigest()[:16]
        return key, semantic

    @staticmethod
    def _bucket(value: int, buckets: List) -> str:
        for threshold, label in buckets:
            if value <= threshold:
                return label
        return buckets[-1][1]


def _calc_ttl(base_ttl: float, confidence: float) -> float:
    """
    TTL × Confidence: 신뢰도 높은 응답은 TTL 연장.
    effective = base × (1 + confidence)
    confidence=0.95 → 1.95× TTL
    confidence=0.50 → 1.50× TTL
    """
    return base_ttl * (1.0 + max(0.0, min(1.0, confidence)))


class AIResponseCache:
    """
    Multi-layer AI 응답 캐시.

    L1 (메모리, OrderedDict, 20개):
      가장 최근 접근된 항목. 조회 속도: O(1).
    L2 (파일, JSON Lines, 200개):
      세션 간 지속. 시작 시 로드, 종료 시 저장.

    조회 순서: L1 → L2 → miss
    저장 순서: L1에 추가, 세션 종료 시 L1+L2를 L2 파일로 병합 저장.
    """

    def __init__(self,
                 cache_file:   Path  = _DEFAULT_CACHE_FILE,
                 l1_max:       int   = _L1_MAX_ENTRIES,
                 l2_max:       int   = _L2_MAX_ENTRIES,
                 min_confidence: float = _MIN_CONFIDENCE):
        self._file          = cache_file
        self._l1_max        = l1_max
        self._l2_max        = l2_max
        self._min_confidence = min_confidence
        self._kb            = SemanticKeyBuilder()

        # L1: 메모리 캐시 (최근 l1_max개)
        self._l1: OrderedDict[str, CacheEntry] = OrderedDict()
        # L2: 파일 캐시 (전체)
        self._l2: OrderedDict[str, CacheEntry] = OrderedDict()

        self._stats = {
            'l1_hits': 0, 'l2_hits': 0, 'misses': 0, 'puts': 0,
            'rejected_low_confidence': 0,
            'l1_evictions': 0, 'l2_evictions': 0,
            'expired': 0,
            'total_cost_saved': 0.0,
        }
        self._load_l2()

    # ── 조회 ─────────────────────────────────────────────────
    def get(self, issue: Dict,
             snap: Optional[Dict] = None) -> Optional[CacheEntry]:
        """L1 → L2 순서로 조회. 만료 항목 자동 제거."""
        key, _ = self._kb.build(issue, snap)

        # L1 조회
        entry = self._l1.get(key)
        if entry:
            if entry.is_valid:
                self._l1.move_to_end(key)
                entry.hit_count += 1
                self._stats['l1_hits'] += 1
                return entry
            else:
                del self._l1[key]
                self._stats['expired'] += 1

        # L2 조회
        entry = self._l2.get(key)
        if entry:
            if entry.is_valid:
                # L2 히트 → L1으로 승격
                self._promote_to_l1(key, entry)
                entry.hit_count += 1
                self._stats['l2_hits'] += 1
                return entry
            else:
                del self._l2[key]
                self._stats['expired'] += 1

        self._stats['misses'] += 1
        return None

    def _promote_to_l1(self, key: str, entry: CacheEntry) -> None:
        """L2 항목을 L1으로 승격."""
        self._l1[key] = entry
        self._l1.move_to_end(key)
        if len(self._l1) > self._l1_max:
            self._l1.popitem(last=False)
            self._stats['l1_evictions'] += 1

    # ── 저장 ─────────────────────────────────────────────────
    def put(self, issue: Dict, snap: Optional[Dict],
             response_text:  str,
             response_dict:  Dict,
             cost_usd:       float = 0.0,
             severity:       str   = 'High',
             confidence:     float = 0.7) -> bool:
        """
        AI 응답 저장.
        confidence < min_confidence 이면 저장 거부 (오염 방지).
        Returns: True=저장됨, False=거부됨
        """
        # AI 결과 검증
        if confidence < self._min_confidence:
            self._stats['rejected_low_confidence'] += 1
            return False

        key, semantic = self._kb.build(issue, snap)

        base_ttl = _CRITICAL_BASE_TTL if severity == 'Critical' else _BASE_TTL
        ttl      = _calc_ttl(base_ttl, confidence)

        entry = CacheEntry(
            key=key, response_text=response_text,
            response_dict=response_dict, semantic_key=semantic,
            created_at=time.time(), ttl_s=ttl,
            confidence=confidence, severity=severity,
        )

        # 기존 항목이면 통계 누산
        existing = self._l1.get(key) or self._l2.get(key)
        if existing:
            entry.hit_count  = existing.hit_count
            entry.cost_saved = existing.cost_saved + cost_usd
        else:
            entry.cost_saved = cost_usd

        # L1에 저장
        self._l1[key] = entry
        self._l1.move_to_end(key)
        while len(self._l1) > self._l1_max:
            old_key, old_entry = self._l1.popitem(last=False)
            self._l2[old_key]  = old_entry   # L1 넘침 → L2로
            self._stats['l1_evictions'] += 1

        # L2에도 동기화
        self._l2[key] = entry
        self._l2.move_to_end(key)
        while len(self._l2) > self._l2_max:
            self._l2.popitem(last=False)
            self._stats['l2_evictions'] += 1

        self._stats['puts'] += 1
        self._stats['total_cost_saved'] += cost_usd
        return True

    # ── 영속화 ───────────────────────────────────────────────
    def save(self) -> None:
        """L1 + L2를 파일로 저장."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            merged = {**self._l2, **self._l1}   # L1 우선
            data = {
                'version':  '2.0',
                'saved_at': time.time(),
                'stats':    self._stats,
                'entries':  [e.to_json() for e in merged.values()
                             if e.is_valid],
            }
            self._file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8')
        except Exception:
            pass

    def _load_l2(self) -> None:
        try:
            if not self._file.exists():
                return
            data = json.loads(self._file.read_text('utf-8'))
            prev = data.get('stats', {})
            self._stats['total_cost_saved'] = prev.get('total_cost_saved', 0.0)
            for ed in data.get('entries', []):
                try:
                    e = CacheEntry.from_json(ed)
                    if e.is_valid:
                        self._l2[e.key] = e
                except Exception:
                    pass
        except Exception:
            pass

    # ── 무효화 ───────────────────────────────────────────────
    def invalidate(self, pattern: Optional[str] = None) -> int:
        """
        패턴 매칭으로 캐시 무효화.
        pattern=None: 전체 무효화
        pattern='stack_*': semantic_key에 'stack_' 포함 항목만
        """
        count = 0
        if pattern is None:
            count = len(self._l1) + len(self._l2)
            self._l1.clear(); self._l2.clear()
        else:
            for cache in [self._l1, self._l2]:
                to_del = [k for k, e in cache.items()
                          if pattern.rstrip('*') in e.semantic_key]
                for k in to_del:
                    del cache[k]; count += 1
        return count

    # ── 통계 ─────────────────────────────────────────────────
    def stats(self) -> Dict:
        total_hits  = self._stats['l1_hits'] + self._stats['l2_hits']
        total_calls = total_hits + self._stats['misses']
        hit_rate    = total_hits / total_calls if total_calls > 0 else 0.0
        return {
            **self._stats,
            'l1_size':    len(self._l1),
            'l2_size':    len(self._l2),
            'hit_rate':   round(hit_rate, 3),
            'hit_rate_pct': f"{hit_rate*100:.1f}%",
            'l1_hit_rate': f"{self._stats['l1_hits']/max(total_calls,1)*100:.1f}%",
        }

    def clear(self) -> None:
        self._l1.clear(); self._l2.clear()
        if self._file.exists():
            self._file.unlink()

    def __len__(self) -> int:
        return len(self._l1) + len(self._l2)
