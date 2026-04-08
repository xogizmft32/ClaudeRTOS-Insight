#!/usr/bin/env python3
"""
trend_analyzer.py — 시계열 이상 감지 + Anomaly Scoring

임베디드 디버깅 특화 AI 분석기 고도화 (즉시 적용 분):

① Trend Analysis: CPU/Heap의 슬로프(기울기) 계산
    "3분간 CPU 0.8%/s 상승 → 2분 내 포화 예측"

② Anomaly Scoring: Z-score 기반 이상 수치화
    binary(초과/미초과) → "anomaly_score: 3.2σ" (AI 가설 품질 향상)

③ Root Cause Correlation ID: 동일 원인 이슈 그룹화
    stack_overflow + heap_exhaustion → group_id: memory_pressure

이 세 기능은 AI 컨텍스트(debugger_context.py)에 자동 삽입되어
AI가 더 정확한 근본 원인 가설을 생성하도록 돕는다.

설계 원칙:
  - numpy 의존: numpy는 이미 requirements.txt에 포함
  - numpy 없이도 기본 동작 (순수 Python 폴백)
  - 슬라이딩 윈도우: 최근 N 스냅샷만 유지 (메모리 보호)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ── 트렌드 분석 ──────────────────────────────────────────────
@dataclass
class TrendResult:
    """단일 메트릭의 트렌드 분석 결과."""
    metric:       str
    slope_per_s:  float    # 초당 변화량 (양수=증가, 음수=감소)
    r_squared:    float    # 선형 적합도 (0~1, 1=완전 선형)
    predicted_at: Dict[str, float] = field(default_factory=dict)
    # 예: {'saturation_s': 120.0} → 120초 후 포화 예측
    # 예: {'exhaustion_s':  90.0} → 90초 후 고갈 예측
    anomaly:      bool = False
    description:  str  = ''

    def to_context(self) -> Dict:
        d = {
            'slope_per_s': round(self.slope_per_s, 4),
            'r_squared':   round(self.r_squared, 2),
            'anomaly':     self.anomaly,
        }
        if self.description:
            d['description'] = self.description
        d.update(self.predicted_at)
        return d


class TrendAnalyzer:
    """
    슬라이딩 윈도우 기반 시계열 트렌드 분석.

    사용:
        ta = TrendAnalyzer(window=10, sample_interval_s=3.0)
        ta.push(snap_dict)
        trends = ta.analyze()
        # → {'cpu': TrendResult(...), 'heap_free': TrendResult(...)}
    """

    # 이상 감지 임계값
    _CPU_SLOPE_ALARM   = 0.5    # %/s 이상 상승 = 이상
    _HEAP_SLOPE_ALARM  = -100.0 # B/s 이상 감소 = 이상
    _R2_THRESHOLD      = 0.7    # 선형성 최소값 (낮으면 불규칙)

    def __init__(self, window: int = 10, sample_interval_s: float = 3.0):
        self._window   = window
        self._interval = sample_interval_s
        # (timestamp_s, value) 쌍 저장
        self._cpu:       deque = deque(maxlen=window)
        self._heap_free: deque = deque(maxlen=window)
        self._heap_pct:  deque = deque(maxlen=window)

    def push(self, snap: Dict) -> None:
        """스냅샷 추가."""
        ts = snap.get('timestamp_us', 0) / 1_000_000.0
        self._cpu.append((ts, snap.get('cpu_usage', 0)))
        h = snap.get('heap', {})
        self._heap_free.append((ts, h.get('free', 0)))
        self._heap_pct.append((ts,  h.get('used_pct', 0)))

    def analyze(self) -> Dict[str, TrendResult]:
        """현재 윈도우 기반 트렌드 분석."""
        results: Dict[str, TrendResult] = {}

        if len(self._cpu) < 3:
            return results   # 데이터 부족

        results['cpu']       = self._analyze_metric('cpu',       self._cpu)
        results['heap_free'] = self._analyze_metric('heap_free', self._heap_free)
        results['heap_pct']  = self._analyze_metric('heap_pct',  self._heap_pct)
        return results

    def _analyze_metric(self, name: str,
                         data: deque) -> TrendResult:
        xs = [d[0] for d in data]
        ys = [d[1] for d in data]

        # 기준점 정규화
        x0 = xs[0]
        xs_n = [x - x0 for x in xs]

        slope, r2 = self._linear_fit(xs_n, ys)

        result = TrendResult(
            metric=name,
            slope_per_s=round(slope, 4),
            r_squared=round(r2, 3),
        )

        # 포화/고갈 예측
        last_val = ys[-1]
        if name == 'cpu' and slope > 0.01:
            remaining = 100.0 - last_val
            if remaining > 0 and slope > 0:
                eta_s = remaining / slope
                result.predicted_at['saturation_s'] = round(eta_s, 1)
                if eta_s < 300:   # 5분 내 포화
                    result.anomaly = True
                    result.description = (
                        f"CPU {last_val:.1f}% → +{slope:.2f}%/s 상승 중, "
                        f"포화까지 약 {eta_s:.0f}초")

        elif name == 'heap_free' and slope < -10:
            if last_val > 0 and slope < 0:
                eta_s = last_val / (-slope)
                result.predicted_at['exhaustion_s'] = round(eta_s, 1)
                if eta_s < 300:
                    result.anomaly = True
                    result.description = (
                        f"Heap {last_val:.0f}B → {slope:.0f}B/s 감소, "
                        f"고갈까지 약 {eta_s:.0f}초")

        # 이상 판정
        if name == 'cpu' and abs(slope) > self._CPU_SLOPE_ALARM and r2 > self._R2_THRESHOLD:
            result.anomaly = True
        if name == 'heap_free' and slope < self._HEAP_SLOPE_ALARM and r2 > self._R2_THRESHOLD:
            result.anomaly = True

        return result

    @staticmethod
    def _linear_fit(xs: List[float], ys: List[float]) -> Tuple[float, float]:
        """최소제곱 선형 회귀. slope, r_squared 반환."""
        n = len(xs)
        if n < 2:
            return 0.0, 0.0

        if _HAS_NUMPY:
            try:
                coeffs = np.polyfit(xs, ys, 1)
                slope = float(coeffs[0])
                y_pred = np.polyval(coeffs, xs)
                ss_res = float(np.sum((np.array(ys) - y_pred) ** 2))
                ss_tot = float(np.sum((np.array(ys) - np.mean(ys)) ** 2))
                r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
                return slope, max(0.0, r2)
            except Exception:
                pass

        # 순수 Python 폴백
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        slope = num / den if abs(den) > 1e-9 else 0.0
        y_pred = [slope * x + (my - slope * mx) for x in xs]
        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
        return slope, max(0.0, r2)


# ── Anomaly Scoring ──────────────────────────────────────────
@dataclass
class AnomalyScore:
    """단일 메트릭의 Z-score 기반 이상 점수."""
    metric:   str
    value:    float
    z_score:  float    # 표준 편차 단위 이탈
    mean:     float
    std:      float
    is_anomaly: bool   # |z| > threshold
    direction: str     # 'high' | 'low' | 'normal'

    def to_context(self) -> Dict:
        return {
            'value':       round(self.value, 2),
            'z_score':     round(self.z_score, 2),
            'anomaly':     self.is_anomaly,
            'direction':   self.direction,
        }

    def description(self) -> str:
        if not self.is_anomaly:
            return ''
        d = 'high' if self.z_score > 0 else 'low'
        return (f"{self.metric} {self.value:.1f} "
                f"(평균 {self.mean:.1f}±{self.std:.1f}, {self.z_score:+.1f}σ)")


class AnomalyScorer:
    """
    Z-score 기반 이상 점수.

    이진 임계값(초과/미초과) 대신 "얼마나 비정상인가"를 수치화.
    AI 컨텍스트에 anomaly_score를 포함시켜 더 정확한 가설 생성.

    사용:
        scorer = AnomalyScorer(window=20, z_threshold=2.5)
        scorer.push(snap)
        scores = scorer.score(snap)
        # → {'cpu': AnomalyScore(z_score=3.2, is_anomaly=True), ...}
    """

    def __init__(self, window: int = 20, z_threshold: float = 2.5):
        self._window      = window
        self._z_threshold = z_threshold
        self._history:    Dict[str, deque] = {
            'cpu':       deque(maxlen=window),
            'heap_pct':  deque(maxlen=window),
            'stack_hwm': deque(maxlen=window),
        }

    def push(self, snap: Dict) -> None:
        self._history['cpu'].append(snap.get('cpu_usage', 0))
        self._history['heap_pct'].append(
            snap.get('heap', {}).get('used_pct', 0))
        # 태스크 중 최소 HWM
        tasks = snap.get('tasks', [])
        min_hwm = min((t.get('stack_hwm', 9999) for t in tasks), default=9999)
        if min_hwm < 9999:
            self._history['stack_hwm'].append(min_hwm)

    def score(self, snap: Dict) -> Dict[str, AnomalyScore]:
        results = {}
        current = {
            'cpu':       snap.get('cpu_usage', 0),
            'heap_pct':  snap.get('heap', {}).get('used_pct', 0),
            'stack_hwm': min(
                (t.get('stack_hwm', 9999) for t in snap.get('tasks', [])),
                default=9999),
        }
        for metric, hist in self._history.items():
            if len(hist) < 5:
                continue
            vals = list(hist)
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            std  = variance ** 0.5

            val = current.get(metric, mean)
            z   = (val - mean) / std if std > 1e-9 else 0.0
            is_anom = abs(z) > self._z_threshold
            direction = 'high' if z > 0 else ('low' if z < 0 else 'normal')

            results[metric] = AnomalyScore(
                metric=metric, value=val, z_score=round(z, 2),
                mean=round(mean, 2), std=round(std, 2),
                is_anomaly=is_anom, direction=direction,
            )
        return results

    def anomaly_summary(self, snap: Dict) -> List[str]:
        """이상 감지된 메트릭의 설명 리스트."""
        scores = self.score(snap)
        return [s.description() for s in scores.values()
                if s.is_anomaly and s.description()]


# ── Root Cause Correlation ID ────────────────────────────────
_ISSUE_GROUPS: Dict[str, str] = {
    # (이슈 타입) → 그룹 ID (근본 원인 카테고리)
    'stack_overflow_imminent': 'memory_pressure',
    'low_stack':               'memory_pressure',
    'heap_exhaustion':         'memory_pressure',
    'low_heap':                'memory_pressure',
    'priority_inversion':      'scheduler_anomaly',
    'task_starvation':         'scheduler_anomaly',
    'deadlock':                'scheduler_anomaly',
    'high_cpu':                'cpu_saturation',
    'cpu_creep':               'cpu_saturation',
    'cpu_overload':            'cpu_saturation',
    'hard_fault':              'system_fault',
    'data_loss_sequence_gap':  'data_integrity',
}


def group_issues_by_root_cause(
        issues: List[Dict]) -> Dict[str, List[Dict]]:
    """
    이슈 목록을 근본 원인 그룹으로 분류.

    동일 근본 원인에서 비롯된 이슈들을 묶어
    AI가 "이 두 이슈는 같은 원인"임을 인식하도록 돕는다.

    반환:
        {
          'memory_pressure': [stack_overflow_issue, heap_exhaustion_issue],
          'scheduler_anomaly': [priority_inversion_issue],
        }
    """
    groups: Dict[str, List[Dict]] = {}
    for iss in issues:
        itype = iss.get('type', iss.get('issue_type', ''))
        group = _ISSUE_GROUPS.get(itype, 'uncategorized')
        groups.setdefault(group, []).append(iss)
    return groups


def enrich_context_with_analysis(ctx_dict: Dict,
                                   trend_results: Dict[str, TrendResult],
                                   anomaly_scores: Dict[str, AnomalyScore],
                                   issue_groups: Dict[str, List]) -> Dict:
    """
    AI 컨텍스트에 트렌드/이상/그룹 정보를 삽입.
    build_context() 결과 딕셔너리를 받아 enriched 버전 반환.
    """
    import copy
    ctx = copy.deepcopy(ctx_dict)

    # 트렌드 정보
    trend_ctx = {}
    for metric, tr in trend_results.items():
        if tr.anomaly or abs(tr.slope_per_s) > 0.01:
            trend_ctx[metric] = tr.to_context()
    if trend_ctx:
        ctx.setdefault('analysis', {})['trends'] = trend_ctx

    # 이상 점수
    anomaly_ctx = {}
    for metric, sc in anomaly_scores.items():
        if sc.is_anomaly:
            anomaly_ctx[metric] = sc.to_context()
    if anomaly_ctx:
        ctx.setdefault('analysis', {})['anomalies'] = anomaly_ctx

    # 근본 원인 그룹
    if issue_groups:
        group_summary = {
            group: [i.get('type','?') for i in issues]
            for group, issues in issue_groups.items()
            if group != 'uncategorized'
        }
        if group_summary:
            ctx.setdefault('analysis', {})['root_cause_groups'] = group_summary

    return ctx
