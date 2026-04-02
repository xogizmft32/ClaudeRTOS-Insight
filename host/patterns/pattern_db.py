#!/usr/bin/env python3
"""
pattern_db.py — 확장 가능한 패턴 DB 로더 & 매처

설계 원칙:
  - JSON 파일 기반 패턴 정의 (하드코딩 없음)
  - 사용자 정의 패턴 오버레이 지원 (custom_patterns.json)
  - 런타임 패턴 추가/비활성화
  - 카테고리·심각도·태그 기반 필터링
  - 확장성: trigger 함수 대신 JSON 조건으로 선언적 매칭

파일 우선순위:
  1. known_patterns.json  (기본 DB)
  2. custom_patterns.json (사용자 정의, 없으면 스킵)
  3. RuntimePatternDB.add_pattern() (코드 레벨 추가)
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)

# ── 기본 패턴 DB 경로 ────────────────────────────────────────
_DEFAULT_DB   = Path(__file__).parent / "known_patterns.json"
_CUSTOM_DB    = Path(__file__).parent / "custom_patterns.json"


# ── 패턴 데이터 클래스 ───────────────────────────────────────
@dataclass
class Pattern:
    id:          str
    name:        str
    category:    str            # memory / timing / deadlock / general
    severity:    str
    enabled:     bool
    description: str
    match:       Dict           # 매칭 조건 (JSON 선언적)
    causal_chain_template: List[str]
    diagnosis:   Dict           # root_cause / fix / prevention
    references:  List[str] = field(default_factory=list)
    # 코드 레벨 추가 패턴을 위한 커스텀 trigger
    custom_trigger: Optional[Callable] = field(default=None, repr=False)

    def to_prefilter_dict(self) -> Dict:
        """PreFilter가 사용하는 레거시 딕셔너리 포맷으로 변환."""
        return {
            'id':          self.id,
            'name':        self.name,
            'severity':    self.severity,
            'summary':     self.description,
            'root_cause':  self.diagnosis.get('root_cause', ''),
            'fix':         self.diagnosis.get('fix', ''),
            'prevention':  self.diagnosis.get('prevention', ''),
            'category':    self.category,
            'causal_chain_template': self.causal_chain_template,
        }


# ── 매칭 엔진 ────────────────────────────────────────────────
class ConstraintChecker:
    """
    Pattern.constraints 조건을 평가하는 엔진.

    지원 constraint 타입:
      pair      : open/close 이벤트 쌍 검사 (mutex_take/give 균형)
      temporal  : 이벤트 지속 시간 상한
      monotonic : 지표의 단조 방향 검사
      ratio     : 두 이벤트 수의 비율
      threshold : 지표 임계값
      forbidden_context: 특정 컨텍스트에서 이벤트 금지
      rate      : 지표 변화율
    """

    def check(self, constraints: list,
               issues: list, timeline: list, snap: dict = None) -> list:
        """
        조건 위반 목록 반환. 빈 리스트 = 모두 통과.
        """
        violations = []
        snap = snap or {}
        for c in constraints:
            ctype = c.get('type', '')
            if ctype == 'pair':
                v = self._check_pair(c, timeline)
            elif ctype == 'temporal':
                v = self._check_temporal(c, timeline)
            elif ctype == 'monotonic':
                v = self._check_monotonic(c, snap)
            elif ctype == 'ratio':
                v = self._check_ratio(c, timeline)
            elif ctype == 'threshold':
                v = self._check_threshold(c, issues)
            elif ctype == 'forbidden_context':
                v = self._check_forbidden_context(c, timeline)
            elif ctype == 'rate':
                v = self._check_rate(c, snap)
            else:
                continue
            if v:
                violations.append({'constraint': c, 'violation': v})
        return violations

    @staticmethod
    def _check_pair(c, tl):
        opens  = sum(1 for e in tl if e.get('type') == c.get('open'))
        closes = sum(1 for e in tl if e.get('type') == c.get('close'))
        if opens > closes:
            return f"{opens} opens vs {closes} closes (imbalance: {opens-closes})"
        return None

    @staticmethod
    def _check_temporal(c, tl):
        evt = c.get('event')
        max_d = c.get('max_duration_ticks', 0)
        if not max_d:
            return None
        # take 후 give 없이 max_d 이상 이벤트가 지나면 위반
        take_idx = None
        for i, e in enumerate(tl):
            if e.get('type') == evt:
                take_idx = i
            elif take_idx is not None and i - take_idx > max_d:
                return f"{evt} held for {i-take_idx} events (max {max_d})"
        return None

    @staticmethod
    def _check_monotonic(c, snap):
        metric = c.get('metric', '')
        direction = c.get('direction', 'non_decreasing')
        # heap_free: snap에서 추세 확인
        if metric == 'heap_free':
            heap = snap.get('heap', {})
            free = heap.get('free', 0)
            total = heap.get('total', 1)
            used_pct = heap.get('used_pct', 0)
            if direction == 'non_decreasing' and used_pct > 90:
                return f"heap_free at {100-used_pct:.0f}% — monotonically decreasing"
        return None

    @staticmethod
    def _check_ratio(c, tl):
        num_evt = c.get('numerator', '').replace('_count', '')
        den_evt = c.get('denominator', '').replace('_count', '')
        max_r   = c.get('max_ratio', 3.0)
        num = sum(1 for e in tl if e.get('type') == num_evt)
        den = max(1, sum(1 for e in tl if e.get('type') == den_evt))
        ratio = num / den
        if ratio > max_r:
            return f"{num_evt}/{den_evt} ratio={ratio:.1f} (max {max_r})"
        return None

    @staticmethod
    def _check_threshold(c, issues):
        metric = c.get('metric', '')
        min_v  = c.get('min_value')
        if min_v is None:
            return None
        for iss in issues:
            val = (iss.get('detail') or {}).get(metric)
            if val is not None and val < min_v:
                return f"{metric}={val} < min {min_v}"
        return None

    @staticmethod
    def _check_forbidden_context(c, tl):
        evt     = c.get('event', '')
        ctx     = c.get('forbidden_in', '')
        in_ctx  = False
        for e in tl:
            etype = e.get('type', '')
            if ctx == 'isr' and etype == 'isr_enter':
                in_ctx = True
            elif ctx == 'isr' and etype == 'isr_exit':
                in_ctx = False
            elif etype == evt and in_ctx:
                return f"{evt} called from {ctx} context"
        return None

    @staticmethod
    def _check_rate(c, snap):
        metric = c.get('metric', '')
        max_r  = c.get('max_trend_per_sample', 0)
        if metric == 'cpu_pct':
            cpu = snap.get('cpu_usage', 0)
            if cpu > 90:   # 간단 근사
                return f"cpu_pct={cpu}% trending high"
        return None


class PatternMatcher:
    """
    Pattern.match 조건을 평가하는 선언적 매처.

    지원 조건:
      require_issues:   이슈 타입 중 하나 이상 존재
      require_events:   이벤트 타입 중 하나 이상 존재
      event_sequence:   이벤트가 이 순서로 등장 (부분 시퀀스)
      event_count_min:  특정 이벤트가 최소 N회 이상
      issue_detail:     이슈 detail 필드 조건 (lt/gt/eq)
      exclude_issues:   이 이슈 타입이 없어야 함
      min_confidence:   (예약, 향후 사용)
    """

    def matches(self, pattern: Pattern,
                issues: List[Dict],
                timeline: List[Dict]) -> bool:
        # 비활성 패턴 스킵
        if not pattern.enabled:
            return False

        # 커스텀 trigger가 있으면 우선 사용
        if pattern.custom_trigger is not None:
            return pattern.custom_trigger(issues, timeline)

        m = pattern.match
        issue_types = {i.get('type', '') for i in issues}
        event_types = [e.get('type', '') for e in timeline]

        # require_issues: AND 조건 (모두 있어야)
        for req in m.get('require_issues', []):
            if req not in issue_types:
                return False

        # require_events: OR 조건 (하나라도 있으면)
        req_evts = m.get('require_events', [])
        if req_evts and not any(e in event_types for e in req_evts):
            return False

        # event_sequence: 순서 매칭
        seq = m.get('event_sequence', [])
        if seq and not self._has_sequence(event_types, seq):
            return False

        # event_count_min
        for evt_type, min_count in m.get('event_count_min', {}).items():
            if event_types.count(evt_type) < min_count:
                return False

        # exclude_issues
        for excl in m.get('exclude_issues', []):
            if excl in issue_types:
                return False

        # issue_detail 조건
        detail_cond = m.get('issue_detail', {})
        if detail_cond:
            if not self._check_detail(issues, detail_cond):
                return False

        return True

    @staticmethod
    def _has_sequence(event_types: List[str], seq: List[str]) -> bool:
        idx = 0
        for et in event_types:
            if idx < len(seq) and et == seq[idx]:
                idx += 1
            if idx == len(seq):
                return True
        return False

    @staticmethod
    def _check_detail(issues: List[Dict], cond: Dict) -> bool:
        for field_name, ops in cond.items():
            for iss in issues:
                val = (iss.get('detail') or {}).get(field_name)
                if val is None:
                    continue
                for op, threshold in ops.items():
                    if op == 'lt'  and not (val < threshold):  return False
                    if op == 'gt'  and not (val > threshold):  return False
                    if op == 'eq'  and not (val == threshold): return False
                    if op == 'lte' and not (val <= threshold): return False
                    if op == 'gte' and not (val >= threshold): return False
        return True


# ── 인과 체인 렌더링 ─────────────────────────────────────────
class ChainRenderer:
    """
    causal_chain_template의 {변수}를 실제 값으로 치환.

    지원 변수:
      {mutex_name}, {irq_num}, {size}, {task_name},
      {wait_ticks}, {hwm}, {malloc_count}, {heap_free_pct},
      {high_task}, {low_task}, {cpu_trend}, {heap_trend}, {eta_min}
    """

    def render(self, template: List[str], issues: List[Dict],
               timeline: List[Dict],
               max_steps: int = 7) -> List[str]:
        vars_ = self._extract_vars(issues, timeline)
        rendered = []
        for step in template[:max_steps]:
            try:
                rendered.append(step.format_map(_SafeDict(vars_)))
            except Exception:
                rendered.append(step)
        return rendered

    @staticmethod
    def _extract_vars(issues: List[Dict],
                       timeline: List[Dict]) -> Dict:
        v: Dict[str, Any] = {}

        # 타임라인에서
        for e in timeline:
            if e.get('type') == 'mutex_timeout':
                v['mutex_name']  = e.get('mutex_name', e.get('mutex', '?'))
                v['wait_ticks']  = e.get('wait_ticks', '?')
            if e.get('type') == 'mutex_take':
                v.setdefault('mutex_name', e.get('mutex_name', '?'))
            if e.get('type') == 'isr_enter':
                v['irq_num'] = e.get('irq', '?')
            if e.get('type') == 'malloc':
                v['size'] = e.get('size', '?')
                v['malloc_count'] = v.get('malloc_count', 0) + 1

        # 이슈에서
        for i in issues:
            detail = i.get('detail') or {}
            tasks  = i.get('affected_tasks', [])
            itype  = i.get('type', '')

            if itype in ('stack_overflow_imminent', 'low_stack'):
                v['task_name'] = tasks[0] if tasks else '?'
                v['hwm']       = detail.get('stack_hwm_words', '?')
                v['stack_size'] = '256'   # 기본값 (실제는 main.c에서)
            if itype == 'priority_inversion':
                v['high_task'] = tasks[0] if tasks else '?'
                v['low_task']  = tasks[1] if len(tasks) > 1 else '?'
            if itype in ('low_heap', 'heap_exhaustion'):
                d = detail
                pct = d.get('free_pct', d.get('used_pct', '?'))
                v['heap_free_pct'] = pct

        return v


class _SafeDict(dict):
    """KeyError 시 {key} 그대로 반환 (format_map용)."""
    def __missing__(self, key):
        return f'{{{key}}}'


# ── 패턴 DB 클래스 ───────────────────────────────────────────
class PatternDB:
    """
    JSON 파일 기반 패턴 DB.

    사용:
        db = PatternDB()
        db.load()                          # 파일 로드
        db.add_pattern(Pattern(...))       # 런타임 추가
        db.find_matches(issues, timeline)  # 매칭
    """

    def __init__(self,
                 db_path: Path        = _DEFAULT_DB,
                 custom_path: Path    = _CUSTOM_DB,
                 chain_max_steps: int = 7):
        self._db_path     = db_path
        self._custom_path = custom_path
        self._patterns:   List[Pattern] = []
        self._matcher     = PatternMatcher()
        self._renderer    = ChainRenderer()
        self.chain_max_steps = chain_max_steps
        self._meta: Dict  = {}

    # ── 로드 ─────────────────────────────────────────────────
    def load(self) -> 'PatternDB':
        self._patterns = []
        self._load_file(self._db_path)
        if self._custom_path.exists():
            self._load_file(self._custom_path, override=True)
        logger.info("PatternDB loaded: %d patterns", len(self._patterns))
        return self

    def _load_file(self, path: Path, override: bool = False) -> None:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            logger.debug("Pattern file not found: %s", path)
            return
        except json.JSONDecodeError as e:
            logger.error("Pattern file JSON error: %s — %s", path, e)
            return

        if not override:
            self._meta = data.get('_meta', {})

        for raw in data.get('patterns', []):
            pat = self._build_pattern(raw)
            if pat is None:
                continue
            if override:
                # 동일 ID가 있으면 교체, 없으면 추가
                self._patterns = [p for p in self._patterns if p.id != pat.id]
            self._patterns.append(pat)

    @staticmethod
    def _build_pattern(raw: Dict) -> Optional[Pattern]:
        try:
            return Pattern(
                id          = raw['id'],
                name        = raw.get('name', raw['id']),
                category    = raw.get('category', 'general'),
                severity    = raw.get('severity', 'High'),
                enabled     = raw.get('enabled', True),
                description = raw.get('description', ''),
                match       = raw.get('match', {}),
                causal_chain_template = raw.get('causal_chain_template', []),
                diagnosis   = raw.get('diagnosis', {}),
                references  = raw.get('references', []),
            )
        except KeyError as e:
            logger.warning("Pattern missing required field: %s", e)
            return None

    # ── 런타임 추가 ──────────────────────────────────────────
    def add_pattern(self, pattern: Pattern,
                     save_to_custom: bool = False) -> None:
        """런타임 패턴 추가. save_to_custom=True면 custom_patterns.json에 영속화."""
        self._patterns = [p for p in self._patterns if p.id != pattern.id]
        self._patterns.append(pattern)
        if save_to_custom:
            self._append_to_custom(pattern)
        logger.info("Pattern added: %s", pattern.id)

    def _append_to_custom(self, pattern: Pattern) -> None:
        data: Dict = {'patterns': []}
        if self._custom_path.exists():
            try:
                data = json.loads(self._custom_path.read_text('utf-8'))
            except Exception:
                pass
        data['patterns'] = [p for p in data.get('patterns', [])
                             if p.get('id') != pattern.id]
        data['patterns'].append({
            'id': pattern.id, 'name': pattern.name,
            'category': pattern.category, 'severity': pattern.severity,
            'enabled': pattern.enabled, 'description': pattern.description,
            'match': pattern.match,
            'causal_chain_template': pattern.causal_chain_template,
            'diagnosis': pattern.diagnosis,
        })
        self._custom_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

    def disable_pattern(self, pattern_id: str) -> bool:
        for p in self._patterns:
            if p.id == pattern_id:
                p.enabled = False
                return True
        return False

    # ── 매칭 ─────────────────────────────────────────────────
    def find_matches(self, issues: List[Dict],
                      timeline: List[Dict],
                      categories: Optional[List[str]] = None,
                      severity_min: Optional[str]     = None
                      ) -> List[Dict]:
        """
        매칭된 패턴을 PreFilter 호환 딕셔너리 리스트로 반환.

        Returns: [{'pattern': Pattern, 'causal_chain': [...], ...}]
        """
        results = []
        sev_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        min_sev   = sev_order.get(severity_min or 'Low', 3)

        for pat in self._patterns:
            if categories and pat.category not in categories:
                continue
            if sev_order.get(pat.severity, 3) > min_sev:
                continue
            if not self._matcher.matches(pat, issues, timeline):
                continue

            chain = self._renderer.render(
                pat.causal_chain_template, issues, timeline,
                max_steps=self.chain_max_steps)

            results.append({
                'pattern':     pat,
                'causal_chain': chain,
                'severity':    pat.severity,
                'category':    pat.category,
                **pat.to_prefilter_dict(),
            })

        # Critical 먼저 정렬
        results.sort(key=lambda r: sev_order.get(r['severity'], 3))
        return results

    # ── 정보 ─────────────────────────────────────────────────
    @property
    def pattern_count(self) -> int:
        return len(self._patterns)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._patterns if p.enabled)

    def summary(self) -> Dict:
        cats: Dict[str, int] = {}
        for p in self._patterns:
            cats[p.category] = cats.get(p.category, 0) + 1
        return {
            'version':       self._meta.get('version', '?'),
            'total':         self.pattern_count,
            'active':        self.active_count,
            'by_category':   cats,
            'chain_max_steps': self.chain_max_steps,
        }


# ── 싱글턴 (기본 DB) ─────────────────────────────────────────
_default_db: Optional[PatternDB] = None

def get_db(chain_max_steps: int = 7) -> PatternDB:
    """전역 기본 DB 인스턴스 반환. 처음 호출 시 로드."""
    global _default_db
    if _default_db is None:
        _default_db = PatternDB(chain_max_steps=chain_max_steps).load()
    return _default_db

def reload_db() -> PatternDB:
    """DB 재로드 (파일 변경 후)."""
    global _default_db
    _default_db = None
    return get_db()
