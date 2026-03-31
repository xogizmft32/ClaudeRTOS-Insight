#!/usr/bin/env python3
"""
Analysis Engine V3.4

AI 모드 추가:
  'offline'    — AI 완전 미호출. 로컬 Rule-based 탐지만.
                 실시간 제어 루프, 프로덕션 환경에 권장.
  'postmortem' — 기본값. 이슈가 3회 연속 감지되면 ai_ready=True.
                 세션 종료 후 일괄 AI 분석에 적합.
  'realtime'   — 이슈 첫 감지 즉시 ai_ready=True.
                 네트워크 레이턴시(~1-3s) 있음. 실시간 제어 루프 비적합.

핵심 원칙:
  - AnalysisEngine은 AI를 직접 호출하지 않음
  - ai_ready 플래그만 설정 → 호출자가 결정
  - Rule-based 탐지(< 1ms)와 AI 호출(~1-3s)은 항상 분리
"""

import time
from typing import List, Dict, Optional, Literal
from dataclasses import dataclass, field
from collections import deque
import statistics

# AI 모드 타입
AiMode = Literal['offline', 'postmortem', 'realtime']


# ── Issue ────────────────────────────────────────────────────────
@dataclass
class Issue:
    severity:       str
    issue_type:     str
    description:    str
    affected_tasks: List[str] = field(default_factory=list)
    timestamp_us:   int = 0
    detail:         Dict = field(default_factory=dict)
    ai_ready:       bool = False   # AI 호출 권장 여부 (호출자가 결정)

    def to_dict(self) -> Dict:
        return {
            'severity':       self.severity,
            'type':           self.issue_type,
            'description':    self.description,
            'affected_tasks': self.affected_tasks,
            'timestamp_us':   self.timestamp_us,
            'detail':         self.detail,
            'ai_ready':       self.ai_ready,
        }


# ── AI 응답 캐시 ─────────────────────────────────────────────────
class AIResponseCache:
    def __init__(self, ttl_seconds: float = 86400.0):
        self._ttl   = ttl_seconds
        self._store: Dict[str, tuple] = {}

    def _key(self, issue_type: str, task_name: str) -> str:
        return f"{issue_type}:{task_name}"

    def get(self, issue_type: str, task_name: str) -> Optional[str]:
        k = self._key(issue_type, task_name)
        entry = self._store.get(k)
        if not entry:
            return None
        response, ts = entry
        if time.time() - ts > self._ttl:
            del self._store[k]
            return None
        return response

    def put(self, issue_type: str, task_name: str, response: str) -> None:
        self._store[self._key(issue_type, task_name)] = (response, time.time())

    def invalidate(self, issue_type: str, task_name: str) -> None:
        self._store.pop(self._key(issue_type, task_name), None)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# ── 연속 감지 카운터 ─────────────────────────────────────────────
class ConsecutiveTracker:
    def __init__(self, threshold: int = 3):
        self._threshold = threshold
        self._counts:   Dict[str, int]  = {}
        self._notified: Dict[str, bool] = {}

    def _key(self, issue_type: str, task_name: str) -> str:
        return f"{issue_type}:{task_name}"

    def update(self, issue_type: str, task_name: str) -> bool:
        k = self._key(issue_type, task_name)
        self._counts[k] = self._counts.get(k, 0) + 1
        if (self._counts[k] >= self._threshold and
                not self._notified.get(k, False)):
            self._notified[k] = True
            return True
        return False

    def reset_absent(self, active_keys: set) -> None:
        for k in list(self._counts.keys()):
            if k not in active_keys:
                self._counts[k]   = 0
                self._notified[k] = False


# ── Trend tracker ────────────────────────────────────────────────
class TrendTracker:
    def __init__(self, window: int = 15, min_samples: int = 7,
                 warm_up: int = 3):
        self._window      = window
        self._min_samples = min_samples
        self._warm_up     = warm_up
        self._vals: deque = deque(maxlen=window)
        self._total       = 0

    def push(self, v: float) -> None:
        self._total += 1
        if self._total > self._warm_up:
            self._vals.append(v)

    def trend(self) -> Optional[float]:
        n = len(self._vals)
        if n < self._min_samples:
            return None
        xs = list(range(n)); ys = list(self._vals)
        xm = sum(xs)/n; ym = sum(ys)/n
        num = sum((x-xm)*(y-ym) for x,y in zip(xs,ys))
        den = sum((x-xm)**2 for x in xs)
        return num/den if den != 0 else 0.0


# ════════════════════════════════════════════════════════════════
#  AnalysisEngine
# ════════════════════════════════════════════════════════════════
class AnalysisEngine:
    """
    AI 모드별 동작:

    offline    : ai_ready 항상 False. AI 키 없어도 안전하게 운영 가능.
                 실시간 제어 루프, CI, 임베디드 게이트웨이에 권장.

    postmortem : ai_ready = 동일 이슈 N회 연속 감지 후 1회 True.
                 세션 종료 후 ai_ready=True 이슈만 모아서 일괄 AI 분석.
                 기본값 — 대부분의 디버깅 세션에 적합.

    realtime   : ai_ready = 이슈 첫 감지 즉시 True.
                 AI 호출 레이턴시(~1-3s)가 발생하므로 실시간 제어 루프 비적합.
                 개발·테스트 환경에서 빠른 피드백이 필요할 때 사용.

    HardFault는 모든 모드에서 즉시 ai_ready=True
    (offline 모드 제외).
    """

    def __init__(self,
                 consecutive_threshold: int  = 3,
                 ai_cache_ttl: float         = 86400.0,
                 ai_mode: AiMode             = 'postmortem'):
        self._mode       = ai_mode
        self._snapshots: List[Dict] = []
        self._issues:    List[Issue] = []
        self._task_hist: Dict[str, deque] = {}

        self._heap_trend = TrendTracker(15, min_samples=7, warm_up=3)
        self._cpu_trend  = TrendTracker(15, min_samples=7, warm_up=3)
        self._consecutive = ConsecutiveTracker(threshold=consecutive_threshold)
        self.ai_cache     = AIResponseCache(ttl_seconds=ai_cache_ttl)

    @property
    def ai_mode(self) -> AiMode:
        return self._mode

    # ── Public API ───────────────────────────────────────────────
    def analyze_snapshot(self, snap: Dict) -> List[Issue]:
        self._snapshots.append(snap)
        self._update_trends(snap)

        issues: List[Issue] = []
        issues += self._check_stack(snap)
        issues += self._check_heap(snap)
        issues += self._check_cpu(snap)
        issues += self._check_priority_inversion(snap)
        issues += self._check_task_starvation(snap)
        issues += self._check_data_loss(snap)
        issues += self._check_heap_leak_trend(snap)
        issues += self._check_cpu_creep_trend(snap)

        # AI 모드별 ai_ready 설정
        active_keys: set = set()
        for iss in issues:
            task = iss.affected_tasks[0] if iss.affected_tasks else 'SYSTEM'
            k    = f"{iss.issue_type}:{task}"
            active_keys.add(k)

            if self._mode == 'offline':
                iss.ai_ready = False          # 완전 미호출

            elif self._mode == 'realtime':
                iss.ai_ready = True           # 즉시 호출

            else:  # postmortem (기본)
                if self._consecutive.update(iss.issue_type, task):
                    iss.ai_ready = True       # N회 연속 후 1회

        self._consecutive.reset_absent(active_keys)
        self._issues += issues
        return issues

    def analyze_fault(self, fault: Dict) -> List[Issue]:
        """HardFault: offline 제외하고 항상 ai_ready=True."""
        desc = (f"HardFault in task '{fault['active_task']['name']}': "
                f"{fault.get('fault_type', 'Unknown')}")
        issue = Issue(
            severity='Critical',
            issue_type='hard_fault',
            description=desc,
            affected_tasks=[fault['active_task']['name']],
            timestamp_us=fault.get('timestamp_us', 0),
            detail={
                'fault_type':   fault.get('fault_type'),
                'registers':    fault.get('registers'),
                'cfsr_decoded': fault.get('cfsr_decoded'),
            },
            ai_ready=(self._mode != 'offline'),
        )
        self._issues.append(issue)
        return [issue]

    def get_summary(self) -> Dict:
        counts = {'Critical':0,'High':0,'Medium':0,'Low':0}
        for i in self._issues:
            counts[i.severity] = counts.get(i.severity,0) + 1
        ai_ready_count = sum(1 for i in self._issues if i.ai_ready)
        return {
            'total_issues':                len(self._issues),
            'ai_ready_issues':             ai_ready_count,
            'by_severity':                 counts,
            'snapshots_analyzed':          len(self._snapshots),
            'heap_trend_bytes_per_sample': self._heap_trend.trend(),
            'cpu_trend_pct_per_sample':    self._cpu_trend.trend(),
            'ai_cache_size':               self.ai_cache.size,
            'ai_mode':                     self._mode,
        }

    def get_ai_ready_issues(self) -> List[Issue]:
        """postmortem 세션 종료 후 일괄 AI 분석용."""
        return [i for i in self._issues if i.ai_ready]

    # ── Private checks (변경 없음) ───────────────────────────────
    def _update_trends(self, snap: Dict) -> None:
        h = snap.get('heap', {})
        self._heap_trend.push(h.get('free', 0))
        self._cpu_trend.push(snap.get('cpu_usage', 0))
        for t in snap.get('tasks', []):
            name = t.get('name', f"Task{t['task_id']}")
            if name not in self._task_hist:
                self._task_hist[name] = deque(maxlen=20)
            self._task_hist[name].append(t)

    def _check_stack(self, snap: Dict) -> List[Issue]:
        issues = []
        for t in snap.get('tasks', []):
            name = t.get('name', f"Task{t['task_id']}")
            hwm  = t.get('stack_hwm', 9999)
            if hwm < 20:
                issues.append(Issue(
                    severity='Critical', issue_type='stack_overflow_imminent',
                    description=f"Task '{name}' has only {hwm} words left — overflow imminent",
                    affected_tasks=[name], timestamp_us=snap['timestamp_us'],
                    detail={'stack_hwm_words': hwm, 'priority': t.get('priority')},
                ))
            elif hwm < 50:
                issues.append(Issue(
                    severity='High', issue_type='low_stack',
                    description=f"Task '{name}' stack low: {hwm} words remaining",
                    affected_tasks=[name], timestamp_us=snap['timestamp_us'],
                    detail={'stack_hwm_words': hwm},
                ))
        return issues

    def _check_heap(self, snap: Dict) -> List[Issue]:
        issues = []
        h = snap.get('heap', {}); free = h.get('free',0); total = h.get('total',0)
        if total == 0: return issues
        pct = int(free * 100 / total)
        if pct < 5:
            issues.append(Issue(severity='Critical', issue_type='heap_exhaustion',
                description=f"Heap critically low: {free}B free ({pct}% of {total}B)",
                timestamp_us=snap['timestamp_us'],
                detail={'free':free,'total':total,'free_pct':pct}))
        elif pct < 15:
            issues.append(Issue(severity='High', issue_type='low_heap',
                description=f"Heap running low: {free}B free ({pct}% of {total}B)",
                timestamp_us=snap['timestamp_us'],
                detail={'free':free,'total':total,'free_pct':pct}))
        return issues

    def _check_cpu(self, snap: Dict) -> List[Issue]:
        issues = []; cpu = snap.get('cpu_usage', 0)
        if cpu > 95:
            issues.append(Issue(severity='Critical', issue_type='cpu_overload',
                description=f'CPU saturated at {cpu}%',
                timestamp_us=snap['timestamp_us'], detail={'cpu_pct':cpu}))
        elif cpu > 85:
            issues.append(Issue(severity='High', issue_type='high_cpu',
                description=f'CPU usage high: {cpu}%',
                timestamp_us=snap['timestamp_us'], detail={'cpu_pct':cpu}))
        return issues

    def _check_priority_inversion(self, snap: Dict) -> List[Issue]:
        running = [t for t in snap.get('tasks',[]) if t['state']==0]
        blocked  = [t for t in snap.get('tasks',[]) if t['state']==2]
        if not running or not blocked: return []
        mbp = max(t['priority'] for t in blocked)
        mrp = min(t['priority'] for t in running)
        if mbp > mrp:
            hb = [t['name'] for t in blocked  if t['priority']==mbp]
            lr = [t['name'] for t in running if t['priority']==mrp]
            return [Issue(severity='High', issue_type='priority_inversion',
                description=f"Priority inversion: {hb} blocked while {lr} runs",
                affected_tasks=hb+lr, timestamp_us=snap['timestamp_us'],
                detail={'high_pri':mbp,'low_pri':mrp})]
        return []

    def _check_task_starvation(self, snap: Dict) -> List[Issue]:
        issues = []
        for t in snap.get('tasks', []):
            name = t.get('name', f"Task{t['task_id']}")
            hist = list(self._task_hist.get(name, []))
            if len(hist) < 3: continue
            r = hist[-3:]
            if (all(h['state']==1 for h in r) and
                    all(h['runtime_us']==r[0]['runtime_us'] for h in r)):
                issues.append(Issue(severity='Medium', issue_type='task_starvation',
                    description=f"Task '{name}' (P{t['priority']}) ready but not scheduled",
                    affected_tasks=[name], timestamp_us=snap['timestamp_us'],
                    detail={'priority':t['priority']}))
        return issues

    def _check_data_loss(self, snap: Dict) -> List[Issue]:
        ps   = snap.get('_parser_stats', {})
        gaps = ps.get('sequence_gaps', 0)
        lost = ps.get('packets_lost', 0)
        if gaps > 0:
            return [Issue(severity='High', issue_type='data_loss_sequence_gap',
                description=f"{gaps} gap(s), ~{lost} packet(s) lost — analysis may be incomplete",
                timestamp_us=snap['timestamp_us'],
                detail={'gaps':gaps,'packets_lost':lost})]
        return []

    def _check_heap_leak_trend(self, snap: Dict) -> List[Issue]:
        trend = self._heap_trend.trend()
        if trend is None or trend >= 0: return []
        if trend < -1000:
            return [Issue(severity='High', issue_type='heap_leak_trend',
                description=f"Heap shrinking ~{abs(int(trend))}B/sample — possible memory leak",
                timestamp_us=snap['timestamp_us'],
                detail={'trend_bytes_per_sample':round(trend,1),
                        'sample_count':len(self._heap_trend._vals)})]
        elif trend < -300:
            return [Issue(severity='Medium', issue_type='heap_shrink',
                description=f"Heap slowly shrinking (~{abs(int(trend))}B/sample)",
                timestamp_us=snap['timestamp_us'],
                detail={'trend_bytes_per_sample':round(trend,1)})]
        return []

    def _check_cpu_creep_trend(self, snap: Dict) -> List[Issue]:
        trend = self._cpu_trend.trend()
        if trend is None or trend <= 0: return []
        if trend > 5.0:
            return [Issue(severity='High', issue_type='cpu_creep',
                description=f"CPU increasing ~{trend:.1f}%/sample — workload growing unbounded",
                timestamp_us=snap['timestamp_us'],
                detail={'trend_pct_per_sample':round(trend,2)})]
        return []
