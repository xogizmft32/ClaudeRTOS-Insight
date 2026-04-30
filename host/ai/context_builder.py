#!/usr/bin/env python3
"""
context_builder.py — AI 진단을 위한 고품질 컨텍스트 생성기

기존 build_context()를 대체/보완한다.
AI 모델이 더 잘 추론할 수 있도록:
  1. 시스템 프로파일 (정적 컨텍스트) 고정
  2. 이슈 인과 관계 사전 구성
  3. 증거 기반 진단 힌트 삽입
  4. 우선순위 정렬 (Critical → High → Medium)
"""
from __future__ import annotations

import dataclasses
import json
import textwrap
from typing import Dict, List, Optional


# ── 시스템 프로파일 ──────────────────────────────────────────────

@dataclasses.dataclass
class SystemProfile:
    """
    타겟 시스템의 정적 특성.

    한 번 설정하면 모든 분석 세션에서 시스템 프롬프트에 고정 주입된다.

    Attributes
    ----------
    mcu          : MCU 모델 (예: "STM32F446RE")
    cpu_hz       : CPU 주파수 (Hz)
    total_ram    : 전체 RAM (바이트)
    rtos         : RTOS 이름 및 버전
    tick_rate_hz : FreeRTOS tick rate
    task_roles   : 태스크명 → 역할 설명 (예: {"CommTask": "UART 송수신"})
    stack_policy : 스택 크기 정책 ("tight"/"normal"/"generous")
    notes        : 추가 시스템 특이사항
    """
    mcu:          str  = "STM32F446RE (Cortex-M4 @ 180MHz)"
    cpu_hz:       int  = 180_000_000
    total_ram:    int  = 131_072   # 128KB
    rtos:         str  = "FreeRTOS v10.x"
    tick_rate_hz: int  = 1000
    task_roles:   Dict[str, str] = dataclasses.field(default_factory=dict)
    stack_policy: str  = "normal"  # "tight" / "normal" / "generous"
    notes:        str  = ""

    def to_system_prompt(self) -> str:
        """AI 시스템 프롬프트에 삽입할 정적 컨텍스트."""
        roles_str = ""
        if self.task_roles:
            roles_str = "\n태스크 역할:\n" + "\n".join(
                f"  {name}: {role}" for name, role in self.task_roles.items())
        return textwrap.dedent(f"""\
            [시스템 프로파일]
            MCU: {self.mcu}
            RAM: {self.total_ram // 1024}KB / RTOS: {self.rtos}
            Tick: {self.tick_rate_hz}Hz / 스택 정책: {self.stack_policy}{roles_str}
            {f'참고: {self.notes}' if self.notes else ''}""").strip()


# ── 인과 관계 사전 구성 ─────────────────────────────────────────

_CAUSAL_PATTERNS = {
    # (선행 이슈, 후행 이슈) → 인과 설명
    ('heap_exhaustion',      'hard_fault'):         "heap 고갈 → malloc 반환 NULL → NULL 역참조 → HardFault",
    ('stack_overflow_imminent','hard_fault'):       "스택 오버플로 → EXC_RETURN 훼손 → HardFault",
    ('priority_inversion',   'task_starvation'):   "우선순위 역전 → 고우선 태스크 기아",
    ('high_cpu',             'task_starvation'):   "CPU 포화 → 낮은 우선순위 태스크 기아",
    ('i2c_nack_storm',       'task_starvation'):   "I2C 반복 실패 → I2C 태스크 대기 → 기아",
    ('heap_leak_trend',      'heap_exhaustion'):   "메모리 누수 지속 → 힙 고갈 예정",
    ('cpu_creep_trend',      'cpu_overload'):      "CPU 점진 상승 → 과부하 예정",
    ('priority_inversion',   'hard_fault'):        "우선순위 역전 → 공유 자원 훼손 → Fault",
    ('bus_fault_precise',    'hard_fault'):        "버스 오류 → 잘못된 메모리 접근 → HardFault",
    ('isr_invalid_exc_return','hard_fault'):       "ISR 컨텍스트 오용 → EXC_RETURN 오류 → UsageFault",
}

def infer_causal_chain(issues: List[Dict]) -> List[str]:
    """이슈 목록에서 인과 관계를 추론해 문장 리스트로 반환."""
    issue_types = {i.get('issue_type', i.get('type', '')) for i in issues}
    chains = []
    for (src, dst), explanation in _CAUSAL_PATTERNS.items():
        if src in issue_types and dst in issue_types:
            chains.append(f"⚠ 인과 관계: {explanation}")
    # 단독 발생 패턴도 힌트 제공
    if 'heap_exhaustion' in issue_types and 'hard_fault' not in issue_types:
        chains.append("→ heap_exhaustion 방치 시 hard_fault 발생 가능")
    if 'stack_overflow_imminent' in issue_types and 'hard_fault' not in issue_types:
        chains.append("→ stack_overflow_imminent 방치 시 crash 발생 가능")
    return chains


# ── 증거 기반 진단 힌트 ─────────────────────────────────────────

def build_diagnostic_hints(snap: Dict, issues: List[Dict],
                            trends: Optional[Dict] = None) -> str:
    """
    AI가 바로 활용할 수 있는 진단 힌트 블록 생성.

    단순 수치 나열이 아니라 이미 분석된 결론을 전달한다.
    """
    hints = []

    # 심각도별 이슈 분류
    by_sev = {'Critical': [], 'High': [], 'Medium': [], 'Low': []}
    for iss in issues:
        sev = iss.get('severity', 'Low')
        by_sev.setdefault(sev, []).append(iss)

    if by_sev['Critical']:
        names = [i.get('issue_type', i.get('type','?')) for i in by_sev['Critical']]
        hints.append(f"🔴 Critical({len(by_sev['Critical'])}건): {', '.join(names)}")
    if by_sev['High']:
        names = [i.get('issue_type', i.get('type','?')) for i in by_sev['High']]
        hints.append(f"🟠 High({len(by_sev['High'])}건): {', '.join(names)}")

    # 트렌드 힌트
    if trends:
        cpu_t = trends.get('cpu')
        heap_t = trends.get('heap')
        if cpu_t and abs(cpu_t.slope_per_s) > 2:
            direction = "상승" if cpu_t.slope_per_s > 0 else "하강"
            hints.append(
                f"📈 CPU {direction} 추세: {cpu_t.slope_per_s:+.1f}%/s "
                f"(현재 {snap.get('cpu_usage')}% → "
                f"60초 후 예상 {min(100, snap.get('cpu_usage',0)+cpu_t.slope_per_s*60):.0f}%)")
        if heap_t and heap_t.slope_per_s < -10:
            hints.append(
                f"📉 Heap 감소 추세: {heap_t.slope_per_s:.1f}bytes/s "
                f"(현재 여유 {snap.get('heap',{}).get('free',0)}B → "
                f"소진까지 약 {abs(snap.get('heap',{}).get('free',0)/heap_t.slope_per_s):.0f}초)")

    # 최장 Blocked 태스크
    blocked = [t for t in snap.get('tasks',[]) if t.get('state_name') == 'Blocked']
    if blocked:
        hi_pri_blocked = max(blocked, key=lambda t: t.get('priority', 0))
        hints.append(
            f"⏸ 최고우선순위 Blocked: '{hi_pri_blocked['name']}' "
            f"(priority={hi_pri_blocked.get('priority',0)}, "
            f"hwm={hi_pri_blocked.get('stack_hwm','?')}words)")

    # 인과 관계
    causal = infer_causal_chain(issues)
    hints.extend(causal)

    return "\n".join(hints) if hints else "이슈 없음 — 정상 동작 중"


# ── 메인 컨텍스트 빌더 ─────────────────────────────────────────

def build_enhanced_context(
    snap:     Dict,
    issues:   List[Dict],
    *,
    profile:  Optional[SystemProfile] = None,
    trends:   Optional[Dict] = None,
    timeline: Optional[List] = None,
    rg_results: Optional[List] = None,
    max_tokens: int = 8000,
) -> str:
    """
    고품질 AI 컨텍스트 생성.

    기존 build_context()보다:
    - 인과 관계 사전 추론 포함
    - 트렌드 예측 힌트 포함
    - 우선순위 기반 정렬
    - 시스템 프로파일 포함

    Parameters
    ----------
    snap        : ParsedSnapshot.to_dict()
    issues      : AnalysisEngine 결과
    profile     : 시스템 정적 프로파일 (없으면 기본값)
    trends      : TrendAnalyzer 결과
    timeline    : 타임라인 이벤트
    rg_results  : ResourceGraph 결과
    max_tokens  : 최대 토큰 (초과 시 낮은 우선순위 항목 잘라냄)

    Returns
    -------
    AI 프롬프트에 삽입할 컨텍스트 문자열
    """
    profile = profile or SystemProfile()
    parts   = []

    # §1. 시스템 프로파일
    parts.append(f"## 시스템 정보\n{profile.to_system_prompt()}")

    # §2. 현재 상태 요약
    heap = snap.get('heap', {})
    parts.append(
        f"## 현재 시스템 상태\n"
        f"Uptime: {snap.get('uptime_ms',0)/1000:.1f}s | "
        f"CPU: {snap.get('cpu_usage',0)}% | "
        f"Heap: {heap.get('free',0)}B 여유 ({heap.get('used_pct',0)}% 사용) | "
        f"태스크: {len(snap.get('tasks',[]))}개"
    )

    # §3. 진단 힌트 (AI가 바로 활용)
    hints = build_diagnostic_hints(snap, issues, trends)
    parts.append(f"## 진단 힌트\n{hints}")

    # §4. 이슈 목록 (우선순위 정렬)
    if issues:
        sev_rank = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        sorted_issues = sorted(issues, key=lambda i: sev_rank.get(i.get('severity','Low'), 3))
        iss_lines = []
        for iss in sorted_issues:
            sev  = iss.get('severity', 'Low')
            itype= iss.get('issue_type', iss.get('type', '?'))
            desc = iss.get('description', '')[:100]
            tasks= iss.get('affected_tasks', [])
            iss_lines.append(
                f"  [{sev}] {itype}"
                + (f" | 태스크: {tasks}" if tasks else "")
                + (f"\n    {desc}" if desc else ""))
        parts.append(f"## 감지된 이슈 ({len(issues)}건)\n" + "\n".join(iss_lines))

    # §5. 태스크 상태 (스택 위험 태스크 강조)
    tasks = snap.get('tasks', [])
    if tasks:
        task_lines = []
        for t in sorted(tasks, key=lambda x: x.get('stack_hwm', 9999)):
            hwm   = t.get('stack_hwm', 0)
            state = t.get('state_name', '?')
            cpu   = t.get('cpu_pct', 0)
            warn  = " ⚠ STACK LOW" if hwm < 50 else ""
            task_lines.append(
                f"  {t['name']:16s} pri={t.get('priority',0):2d} "
                f"{state:8s} cpu={cpu:3d}% hwm={hwm:4d}w{warn}")
        parts.append(f"## 태스크 상태\n" + "\n".join(task_lines))

    # §6. 데드락/경합 (ResourceGraph)
    if rg_results:
        rg_lines = []
        for r in rg_results:
            rg_lines.append(f"  {r.pattern_id} [{r.severity}]: {r.description}")
            for c in r.causal_chain[:3]:
                rg_lines.append(f"    → {c}")
        parts.append("## 데드락/경합 탐지\n" + "\n".join(rg_lines))

    # §7. 타임라인 요약
    if timeline:
        tl_summary = []
        for ev in timeline[-10:]:  # 최근 10개
            etype = ev.get('type','?')
            mutex = ev.get('mutex_name', ev.get('mutex','?'))
            tid   = ev.get('task_id','?')
            t_us  = ev.get('t_us', 0)
            tl_summary.append(f"  {t_us/1000:.0f}ms: Task{tid} {etype} {mutex}")
        parts.append("## 최근 타임라인 이벤트\n" + "\n".join(tl_summary))

    # 토큰 제한 적용
    full_ctx = "\n\n".join(parts)
    char_limit = max_tokens * 4
    if len(full_ctx) > char_limit:
        full_ctx = full_ctx[:char_limit] + "\n[... 컨텍스트 잘림]"

    return full_ctx
