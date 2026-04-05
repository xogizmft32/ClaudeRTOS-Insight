#!/usr/bin/env python3
"""
response_cache.py — AI 응답 Semantic Cache

현재 prefilter._seen 의 한계:
  - 중복 AI 호출 억제만 (응답 자체 저장 없음)
  - cache_key = issue_type+task_name (유사 이슈 구분 못 함)
  - 세션 간 지속 없음 (재시작하면 초기화)
  - LRU 없음, 메모리 무제한

이 모듈의 역할:
  - AI 응답(ParsedResponse)을 Semantic Key로 캐시
  - 유사 이슈(hwm=14 vs hwm=15)를 같은 버킷으로 처리
  - 세션 간 지속: ~/.claudertos_cache/ai_responses.json
  - LRU + 최대 항목 수 제한
  - 히트율 측정으로 비용 절감 가시화

Semantic Bucket 정의:
  (issue_type, task_name, severity_bucket, context_bucket) → cache_key
  
  severity_bucket:
    stack_hwm < 10  → "critical"
    stack_hwm < 20  → "danger"
    stack_hwm < 50  → "warning"
    
  context_bucket:
    heap_used_pct > 90 → "heap_critical"
    heap_used_pct > 70 → "heap_warn"
    cpu > 85          → "cpu_high"
    else              → "normal"
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# 기본 캐시 파일 위치
_DEFAULT_CACHE_DIR  = Path.home() / '.claudertos_cache'
_DEFAULT_CACHE_FILE = _DEFAULT_CACHE_DIR / 'ai_responses.json'

# 캐시 설정
_MAX_ENTRIES  = 200       # 최대 항목 수 (LRU 교체)
_DEFAULT_TTL  = 86_400    # 기본 TTL: 24시간 (초)
_CRITICAL_TTL = 3_600     # Critical 이슈: 1시간 (빠른 무효화)


@dataclass
class CacheEntry:
    key:         str
    response_text: str       # ParsedResponse.format_human() 또는 raw text
    response_dict: Dict      # ParsedResponse.to_dict()
    semantic_key: str        # 디버그용: 버킷 구성 요소
    created_at:  float
    ttl_s:       float
    hit_count:   int = 0
    cost_saved:  float = 0.0   # 이 캐시로 절감된 누적 비용 (USD)
    severity:    str = 'High'

    @property
    def is_valid(self) -> bool:
        return (time.time() - self.created_at) < self.ttl_s

    def to_json(self) -> Dict:
        return {
            'key':          self.key,
            'response_text': self.response_text,
            'response_dict': self.response_dict,
            'semantic_key': self.semantic_key,
            'created_at':   self.created_at,
            'ttl_s':        self.ttl_s,
            'hit_count':    self.hit_count,
            'cost_saved':   self.cost_saved,
            'severity':     self.severity,
        }

    @classmethod
    def from_json(cls, d: Dict) -> 'CacheEntry':
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


class SemanticKeyBuilder:
    """
    이슈 + 스냅샷 컨텍스트 → 의미 버킷 기반 캐시 키 생성.

    목표: hwm=14와 hwm=15는 같은 버킷, hwm=45는 다른 버킷.
    """

    # stack_hwm 버킷
    _STACK_BUCKETS = [(10, 'critical'), (20, 'danger'),
                      (50, 'warning'), (999, 'ok')]

    # heap 버킷
    _HEAP_BUCKETS  = [(90, 'heap_critical'), (70, 'heap_warn'),
                      (0, 'heap_ok')]

    # CPU 버킷
    _CPU_BUCKETS   = [(85, 'cpu_high'), (60, 'cpu_moderate'), (0, 'cpu_ok')]

    def build(self, issue: Dict, snap: Optional[Dict] = None) -> str:
        """의미 기반 캐시 키 반환."""
        itype     = issue.get('type', issue.get('issue_type', 'unknown'))
        task      = (issue.get('affected_tasks') or ['SYSTEM'])[0]
        severity  = issue.get('severity', 'Medium')

        # stack 버킷
        hwm = (issue.get('detail') or {}).get('stack_hwm_words', 999)
        stack_b = self._bucket(hwm, self._STACK_BUCKETS)

        # heap 버킷 (snap 있을 때)
        heap_b = 'heap_na'
        cpu_b  = 'cpu_na'
        if snap:
            heap_pct = snap.get('heap', {}).get('used_pct', 0)
            heap_b   = self._bucket(100 - heap_pct, [(30,'heap_critical'),
                                                       (10,'heap_warn'),
                                                       (0, 'heap_ok')])
            cpu_b    = self._bucket(100 - snap.get('cpu_usage', 0),
                                     [(15,'cpu_high'),(40,'cpu_moderate'),
                                      (0,'cpu_ok')])

        key_str = f"{itype}::{task}::{severity}::{stack_b}::{heap_b}::{cpu_b}"
        return hashlib.sha256(key_str.encode()).hexdigest()[:16], key_str

    @staticmethod
    def _bucket(value: int, buckets: List) -> str:
        for threshold, label in buckets:
            if value <= threshold:
                return label
        return buckets[-1][1]


class AIResponseCache:
    """
    AI 응답 Semantic LRU Cache.

    사용:
        cache = AIResponseCache()   # 세션 시작 시 1회, 파일에서 로드

        # AI 호출 전 확인
        hit = cache.get(issue, snap)
        if hit:
            return hit.response_dict   # AI 호출 없음

        # AI 호출 후 저장
        cache.put(issue, snap, response_text, response_dict,
                  cost_usd=0.0085, severity='Critical')

        # 세션 종료 시 저장
        cache.save()

        # 통계
        print(cache.stats())
    """

    def __init__(self,
                 cache_file: Path = _DEFAULT_CACHE_FILE,
                 max_entries: int = _MAX_ENTRIES,
                 default_ttl: float = _DEFAULT_TTL):
        self._file       = cache_file
        self._max        = max_entries
        self._default_ttl = default_ttl
        # OrderedDict: LRU (가장 최근 접근이 마지막)
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._key_builder = SemanticKeyBuilder()
        self._stats = {
            'hits': 0, 'misses': 0, 'puts': 0,
            'evictions': 0, 'expired': 0,
            'total_cost_saved': 0.0,
        }
        self._load()

    # ── 조회 ─────────────────────────────────────────────────
    def get(self, issue: Dict,
             snap: Optional[Dict] = None) -> Optional[CacheEntry]:
        """
        캐시에서 응답 조회.
        Returns: CacheEntry (유효) 또는 None (miss/expired)
        """
        key, _ = self._key_builder.build(issue, snap)
        entry   = self._cache.get(key)

        if entry is None:
            self._stats['misses'] += 1
            return None

        if not entry.is_valid:
            del self._cache[key]
            self._stats['expired'] += 1
            self._stats['misses'] += 1
            return None

        # LRU 갱신 (맨 뒤로 이동)
        self._cache.move_to_end(key)
        entry.hit_count += 1
        self._stats['hits'] += 1
        return entry

    # ── 저장 ─────────────────────────────────────────────────
    def put(self, issue: Dict, snap: Optional[Dict],
             response_text: str, response_dict: Dict,
             cost_usd: float = 0.0,
             severity:  str  = 'High') -> None:
        """AI 응답 캐시에 저장."""
        key, semantic_key = self._key_builder.build(issue, snap)

        ttl = _CRITICAL_TTL if severity == 'Critical' else self._default_ttl

        entry = CacheEntry(
            key=key,
            response_text=response_text,
            response_dict=response_dict,
            semantic_key=semantic_key,
            created_at=time.time(),
            ttl_s=ttl,
            severity=severity,
        )

        # 기존 항목 업데이트
        if key in self._cache:
            existing = self._cache[key]
            entry.hit_count   = existing.hit_count
            entry.cost_saved  = existing.cost_saved + cost_usd
        else:
            entry.cost_saved  = cost_usd

        self._cache[key] = entry
        self._cache.move_to_end(key)
        self._stats['puts'] += 1
        self._stats['total_cost_saved'] += cost_usd

        # LRU 교체
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
            self._stats['evictions'] += 1

    # ── 영속화 ───────────────────────────────────────────────
    def save(self) -> None:
        """현재 캐시를 파일에 저장."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'version':  '1.0',
                'saved_at': time.time(),
                'stats':    self._stats,
                'entries': [
                    e.to_json() for e in self._cache.values()
                    if e.is_valid
                ],
            }
            self._file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8')
        except Exception as e:
            pass   # 캐시 저장 실패는 무시

    def _load(self) -> None:
        """파일에서 캐시 로드 (TTL 체크 포함)."""
        try:
            if not self._file.exists():
                return
            data = json.loads(self._file.read_text('utf-8'))
            loaded = prev_stats = data.get('stats', {})
            # 이전 세션 비용 절감액 누산
            self._stats['total_cost_saved'] = \
                prev_stats.get('total_cost_saved', 0.0)
            for ed in data.get('entries', []):
                try:
                    entry = CacheEntry.from_json(ed)
                    if entry.is_valid:
                        self._cache[entry.key] = entry
                except Exception:
                    pass
        except Exception:
            pass

    def stats(self) -> Dict:
        total = self._stats['hits'] + self._stats['misses']
        hit_rate = self._stats['hits'] / total if total > 0 else 0.0
        return {
            **self._stats,
            'size':      len(self._cache),
            'hit_rate':  round(hit_rate, 3),
            'hit_rate_pct': f"{hit_rate*100:.1f}%",
        }

    def clear(self) -> None:
        self._cache.clear()
        if self._file.exists():
            self._file.unlink()

    def __len__(self) -> int:
        return len(self._cache)
