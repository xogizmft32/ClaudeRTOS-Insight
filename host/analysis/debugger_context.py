#!/usr/bin/env python3
"""
debugger_context.py — AI에 전달할 구조화 JSON 컨텍스트 빌더

제안된 구조:
  {
    "session":   { uptime, transport, ai_mode, data_quality },
    "system":    { cpu, heap, trends },
    "tasks":     [ {name, priority, state, cpu, stack} ... ],
    "timeline":  [ {t_us, type, ...} ... ],   ← 이벤트 시계열
    "anomalies": [ {severity, type, task, detail} ... ],
    "crash":     { fault_type, registers, stack_dump, cfsr_bits } | null
  }

장점:
  - AI가 필드명으로 정확히 참조 가능 (tasks[1].name 처럼)
  - timeline이 있어 이벤트 순서 분석 가능
  - crash 구조체로 Fault 분석에 필요한 모든 정보 집약
  - None / 빈 배열은 직렬화에서 제외 → 토큰 절감
  - separators=(',',':') compact JSON → 추가 토큰 절감
"""

import json
import time
from typing import Optional, List, Dict, Any


# ── 이벤트 타입 이름 매핑 ─────────────────────────────────────
EVENT_TYPE_NAMES = {
    0x10: 'ctx_switch_in',
    0x11: 'ctx_switch_out',
    0x30: 'mutex_take',
    0x31: 'mutex_timeout',
    0x32: 'mutex_give',
    0x50: 'isr_enter',
    0x51: 'isr_exit',
    0x60: 'malloc',
    0x61: 'free',
}


def build_context(
    snap:           Optional[Dict],
    issues:         List[Dict],
    fault:          Optional[Dict]          = None,
    timeline_events: List[Dict]             = None,
    trends:         Optional[Dict]          = None,
    parser_stats:   Optional[Dict]          = None,
    ai_mode:        str                     = 'postmortem',
    transport:      str                     = 'unknown',
    max_timeline:   int                     = 50,
    isr_stats:      Optional[Dict]          = None,
    cpu_hz:         int                     = 180_000_000,
) -> str:
    """
    AI에 전달할 compact JSON 문자열을 생성한다.

    Parameters
    ----------
    snap          ParsedSnapshot.to_dict() 결과
    issues        Issue.to_dict() 리스트
    fault         ParsedFault.to_dict() 결과 (None = Fault 없음)
    timeline_events TraceEvent 딕셔너리 리스트 (가장 최근 순)
    trends        AnalysisEngine.get_summary() 의 trend 필드
    parser_stats  BinaryParserV3.get_stats()
    ai_mode       'offline' | 'postmortem' | 'realtime'
    transport     'ITM' | 'UART'
    max_timeline  타임라인에 포함할 최대 이벤트 수 (토큰 제한)

    Returns
    -------
    compact JSON 문자열
    """
    ctx: Dict[str, Any] = {}

    # ── session ──────────────────────────────────────────────
    session: Dict[str, Any] = {
        'uptime_s':  (snap or {}).get('uptime_ms', 0) // 1000,
        'transport': transport,
        'ai_mode':   ai_mode,
        'cpu_hz':    cpu_hz,
    }
    # ISR 통계 (DWT EXCCNT 기반, 오버헤드 0)
    if isr_stats:
        session['isr'] = {
            'count_per_sample': isr_stats.get('isr_count_delta', 0),
            'ctx_switches':     isr_stats.get('ctx_switch_count', 0),
            'mutex_timeouts':   isr_stats.get('mutex_timeout_count', 0),
            'trace_overflows':  isr_stats.get('overflow_count', 0),
        }
    # 데이터 유실 경고 (있을 때만)
    ps = parser_stats or (snap or {}).get('_parser_stats', {})
    gaps = ps.get('sequence_gaps', 0)
    if gaps > 0:
        session['data_loss'] = {
            'gaps':         gaps,
            'packets_lost': ps.get('packets_lost', 0),
            'warning':      'Analysis may be incomplete',
        }
    ctx['session'] = session

    # ── system ───────────────────────────────────────────────
    if snap:
        h = snap.get('heap', {})
        system: Dict[str, Any] = {
            'cpu_pct':  snap.get('cpu_usage', 0),
            'heap': {
                'free_bytes':  h.get('free',     0),
                'total_bytes': h.get('total',    0),
                'used_pct':    h.get('used_pct', 0),
                'min_ever_bytes': h.get('min',   0),
            },
        }
        if trends:
            ht = trends.get('heap_trend_bytes_per_sample')
            ct = trends.get('cpu_trend_pct_per_sample')
            if ht is not None or ct is not None:
                system['trends'] = {}
                if ht is not None:
                    system['trends']['heap_bytes_per_sample'] = round(ht, 1)
                if ct is not None:
                    system['trends']['cpu_pct_per_sample'] = round(ct, 2)
        ctx['system'] = system

    # ── tasks ────────────────────────────────────────────────
    if snap and snap.get('tasks'):
        ctx['tasks'] = [
            _task_entry(t) for t in snap['tasks']
        ]

    # ── timeline ─────────────────────────────────────────────
    if timeline_events:
        recent = (timeline_events[-max_timeline:]
                  if len(timeline_events) > max_timeline
                  else timeline_events)
        ctx['timeline'] = [_timeline_entry(e) for e in recent]

    # ── anomalies ────────────────────────────────────────────
    if issues:
        ctx['anomalies'] = [_anomaly_entry(iss) for iss in issues]

    # ── crash ────────────────────────────────────────────────
    if fault:
        ctx['crash'] = _crash_entry(fault)

    # compact JSON — separators=(',',':') 으로 공백 제거
    return json.dumps(ctx, separators=(',', ':'), ensure_ascii=False)


# ── 내부 빌더 ─────────────────────────────────────────────────

def _task_entry(t: Dict) -> Dict:
    entry: Dict = {
        'name':     t.get('name', f"Task{t.get('task_id',0)}"),
        'priority': t.get('priority', 0),
        'state':    t.get('state_name', '?'),
        'cpu_pct':  t.get('cpu_pct', 0),
        'stack_hwm_words': t.get('stack_hwm', 0),
    }
    # 위험 플래그 (AI가 명시적으로 볼 수 있게)
    hwm = t.get('stack_hwm', 9999)
    if hwm < 20:
        entry['stack_risk'] = 'CRITICAL'
    elif hwm < 50:
        entry['stack_risk'] = 'HIGH'
    return entry


def _timeline_entry(e: Dict) -> Dict:
    entry: Dict = {
        't_us': e.get('timestamp_us', 0),
        'type': EVENT_TYPE_NAMES.get(e.get('event_type', 0),
                                      f"0x{e.get('event_type',0):02X}"),
    }
    etype = e.get('event_type', 0)
    data  = e.get('data', {})

    if etype in (0x10, 0x11):   # context switch
        ctx = data.get('ctx', {})
        if ctx.get('prev_task_id'): entry['from_task'] = ctx['prev_task_id']
        if ctx.get('next_task_id'): entry['to_task']   = ctx['next_task_id']

    elif etype in (0x30, 0x31, 0x32):  # mutex
        mutex = data.get('mutex', {})
        if mutex.get('mutex_addr'):
            entry['mutex'] = f"0x{mutex['mutex_addr']:08X}"
        if mutex.get('name'):
            entry['mutex_name'] = mutex['name']
        if etype == 0x31:
            entry['timeout'] = True
        if mutex.get('wait_ticks', 0) > 0:
            entry['wait_ticks'] = mutex['wait_ticks']

    elif etype in (0x50, 0x51):  # ISR
        if data.get('isr', {}).get('irq_num') is not None:
            entry['irq'] = data['isr']['irq_num']

    elif etype == 0x60:  # malloc
        mem = data.get('mem', {})
        entry['size']  = mem.get('size', 0)
        entry['ptr']   = f"0x{mem.get('ptr', 0):08X}"

    elif etype == 0x61:  # free
        mem = data.get('mem', {})
        entry['ptr'] = f"0x{mem.get('ptr', 0):08X}"

    task_id = e.get('task_id', 0)
    if task_id != 0xFF:
        entry['task_id'] = task_id

    return {k: v for k, v in entry.items() if v is not None}


def _anomaly_entry(iss: Dict) -> Dict:
    entry: Dict = {
        'severity': iss.get('severity', 'Unknown'),
        'type':     iss.get('type',     'unknown'),
        'desc':     iss.get('description', ''),
    }
    tasks = iss.get('affected_tasks', [])
    if tasks:
        entry['tasks'] = tasks
    detail = iss.get('detail', {})
    if detail:
        # 핵심 detail만 포함 (verbose 필드 제외)
        compact = {}
        for k, v in detail.items():
            if k in ('stack_hwm_words', 'cpu_pct', 'free', 'total',
                     'free_pct', 'high_pri', 'low_pri', 'gaps',
                     'packets_lost', 'trend_bytes_per_sample',
                     'trend_pct_per_sample', 'sample_count'):
                compact[k] = v
        if compact:
            entry['detail'] = compact
    return entry


def _crash_entry(fault: Dict) -> Dict:
    crash: Dict = {
        'fault_type': fault.get('fault_type', 'Unknown'),
        'task':       fault.get('active_task', {}).get('name', '?'),
    }

    # 레지스터
    regs = fault.get('registers', {})
    if regs:
        crash['registers'] = {
            'PC':    regs.get('PC',    '?'),
            'LR':    regs.get('LR',    '?'),
            'SP':    regs.get('SP',    '?'),
            'CFSR':  regs.get('CFSR',  '?'),
            'HFSR':  regs.get('HFSR',  '?'),
            'MMFAR': regs.get('MMFAR', '?'),
            'BFAR':  regs.get('BFAR',  '?'),
        }

    # CFSR 비트 디코드 (활성 비트만)
    cfsr_decoded = fault.get('cfsr_decoded', {})
    active_bits: Dict[str, List[str]] = {}
    for cls, bits in cfsr_decoded.items():
        active = [k for k, v in bits.items() if v]
        if active:
            active_bits[cls] = active
    if active_bits:
        crash['cfsr_bits'] = active_bits

    # 스택 덤프 (유효할 때만)
    if fault.get('stack_dump_valid') and fault.get('stack_dump'):
        dump = fault['stack_dump']
        crash['stack_dump'] = [f"0x{w:08X}" for w in dump[:16]]
        crash['stack_dump_note'] = \
            "Words at SP+0..+63 at time of fault (frame+offset)"

    return crash


# ── 시스템 프롬프트 (구조화 JSON용) ──────────────────────────
SYSTEM_PROMPT_JSON = """
FreeRTOS/ARM Cortex-M expert debugger.

Input: compact JSON with session, system, tasks[], timeline[], anomalies[], crash?.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "issues": [
    {
      "id": 1,
      "severity": "Critical|High|Medium",
      "type": "<anomaly type>",
      "task": "<task name or SYSTEM>",
      "scenario": "memory|timing|deadlock|general",
      "summary": "<한 줄 요약, 한국어>",
      "confidence": 0.0-1.0,
      "causal_chain": ["event1", "event2", "result"],
      "root_cause_candidates": [
        {
          "hypothesis": "<기술적 원인>",
          "confidence": 0.0-1.0,
          "evidence": ["<timeline 또는 anomaly에서 근거>"]
        }
      ],
      "recommended_actions": [
        {
          "priority": 1,
          "action": "<action description>",
          "fix": {
            "file": "<filename>",
            "line": <line number or null>,
            "before": "<old code>",
            "after": "<new code>"
          },
          "reason": "<why this works>"
        }
      ],
      "prevention": "<재발 방지 1문장>"
    }
  ],
  "session_summary": "<전체 세션 한국어 요약 1문장>",
  "overall_confidence": 0.0-1.0
}

Rules:
- stack_hwm_words = words REMAINING (<20=Critical, <50=High)
- causal_chain: chronological event sequence leading to the issue
- scenario: memory=heap/stack, timing=ISR/latency, deadlock=mutex/block, general=other
- For crash: use CFSR bits and PC/LR for root cause
- If timeline has mutex_timeout before priority_inversion, include in causal_chain
- confidence: 0.9=certain, 0.7=likely, 0.5=possible, 0.3=speculative
"""


# ── 편의 함수 ─────────────────────────────────────────────────
def context_token_estimate(ctx_json: str) -> int:
    """compact JSON 토큰 수 근사 (word * 1.3)"""
    return int(len(ctx_json.split()) * 1.3)


def pretty_print_context(ctx_json: str) -> None:
    """디버그용 pretty print"""
    try:
        obj = json.loads(ctx_json)
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    except Exception:
        print(ctx_json)
