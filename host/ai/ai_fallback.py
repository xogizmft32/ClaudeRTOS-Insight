from __future__ import annotations

import logging
_log = logging.getLogger(__name__)

#!/usr/bin/env python3
"""
ai_fallback.py — AI 분석 실패 시 로컬 fallback 분석기
AI Provider(API 호출 실패, 네트워크 단절, 쿼터 초과 등)가 응답하지
않을 때 Rule-based 분석 결과만으로 구조화된 응답을 생성한다.
설계 원칙:
  - 네트워크 없이 항상 동작 (임베디드 현장 필수)
  - AI 응답과 동일한 딕셔너리 구조 반환 → 파이프라인 무변경
  - 신뢰도를 정직하게 낮게 표기 (AI 대비 추론 한계 명시)
  - 모든 이슈에 대해 즉각적인 next action 제공
"""
import time
from typing import List, Dict, Optional
# ── 규칙 기반 causal chain 생성 ──────────────────────────────
_CAUSAL_CHAINS: Dict[str, List[str]] = {
    'stack_overflow_imminent': [
        "태스크 스택 사용량이 위험 수위 도달",
        "Stack HWM(High Water Mark)가 임계값({hwm}W) 이하",
        "다음 ISR/함수 호출 시 스택 충돌 발생 가능",
    ],
    'heap_exhaustion': [
        "Heap 잔여 공간 위험 수준({free}B, {pct}%)",
        "추가 동적 할당 실패 → NULL 반환",
        "NULL 미검사 코드에서 HardFault 발생 가능",
    ],
    'priority_inversion': [
        "고우선순위 태스크({high}) BLOCKED",
        "저우선순위 태스크({low}) 실행 중 (mutex 보유)",
        "Priority Inheritance 미적용 시 무한 대기 가능",
    ],
    'high_cpu': [
        "CPU 사용률 {cpu}% — 정상 태스크 처리 지연",
        "Idle 태스크 실행 시간 부족 → watchdog 위험",
        "WFI 명령 실행 안됨 → 전력 소모 증가",
    ],
    'hard_fault': [
        "HardFault 발생 (CFSR={cfsr:#010x})",
        "실행 중단 → HardFault_Handler 진입",
        "CFSR 분석으로 원인 특정 필요",
    ],
    'gpio_glitch_storm': [
        "GPIO 글리치 {count}회 감지",
        "EXTI ISR 과다 호출 → CPU 부하 증가",
        "노이즈 차폐 또는 디바운스 필터 확인 필요",
    ],
    'i2c_nack_storm': [
        "I2C NACK {count}회 — 슬레이브 무응답",
        "HAL_I2C_Master_Transmit() 오류 반환",
        "슬레이브 주소, 전원, 풀업 저항 확인 필요",
    ],
    'spi_overrun': [
        "SPI 오버런 {count}회 — DR 미읽음",
        "데이터 손실 발생",
        "DMA 수신 모드 전환 필요",
    ],
}
_RECOMMENDED_ACTIONS: Dict[str, List[Dict]] = {
    'stack_overflow_imminent': [
        {'priority': 1, 'action': 'configMINIMAL_STACK_SIZE 또는 xTaskCreate() 스택 크기를 2배로 증가',
         'code_hint': 'xTaskCreate(fn, "name", 512, NULL, 5, &handle);  // 256→512'},
    ],
    'heap_exhaustion': [
        {'priority': 1, 'action': 'configTOTAL_HEAP_SIZE 증가 또는 동적 할당 제거',
         'code_hint': '#define configTOTAL_HEAP_SIZE ((size_t)(32 * 1024))'},
        {'priority': 2, 'action': 'pvPortMalloc() 반환값 NULL 검사 추가'},
    ],
    'priority_inversion': [
        {'priority': 1, 'action': 'Mutex에 Priority Inheritance 활성화',
         'code_hint': 'xSemaphoreCreateMutex()  // PI 자동 활성화'},
        {'priority': 2, 'action': 'mutex 보유 시간 최소화'},
    ],
    'high_cpu': [
        {'priority': 1, 'action': 'vTaskDelay() 또는 ulTaskNotifyTake()로 Yield 추가'},
        {'priority': 2, 'action': '해당 태스크 우선순위 조정'},
    ],
    'hard_fault': [
        {'priority': 1, 'action': 'HardFault_Handler에서 CFSR/MMFAR/BFAR 덤프 후 분석'},
        {'priority': 2, 'action': 'SCB->CFSR 값으로 원인 특정: IACCVIOL/DACCVIOL/BFARVALID'},
    ],
}
def _format_chain(issue_type: str, detail: dict) -> List[str]:
    """이슈 타입과 detail로 causal chain 문자열 생성."""
    template = _CAUSAL_CHAINS.get(issue_type, [
        f"이슈 유형: {issue_type}",
        "Rule-based 분석으로 탐지됨",
        "AI 분석 불가 시 수동 점검 필요",
    ])
    chain = []
    for step in template:
        try:
            chain.append(step.format(**detail))
        except (KeyError, ValueError):
            chain.append(step)
    return chain
class AIFallbackAnalyzer:
    """
    AI 응답 실패 시 Rule-based 결과를 AI 응답 형식으로 변환.
    사용:
        fallback = AIFallbackAnalyzer()
        result = fallback.analyze(snap, issue_dicts, reason="API timeout")
        # result 구조: RTOSDebuggerV3.debug_snapshot()과 동일
    """
    VERSION = "local-fallback-v1"
    def analyze(self,
                snap:        dict,
                issue_dicts: List[Dict],
                reason:      str = "AI unavailable") -> Dict:
        """
        Rule-based 이슈 리스트를 AI 응답 형식으로 변환.
        Parameters
        ----------
        snap        : 현재 스냅샷 딕셔너리
        issue_dicts : AnalysisEngine.analyze_snapshot() 결과 (to_dict() 변환)
        reason      : AI 비가용 이유 (로깅용)
        Returns
        -------
        AI 응답과 동일한 구조의 dict
        """
        t_start = time.time()
        structured_issues = []
        for iss in issue_dicts:
            itype    = iss.get('issue_type', 'unknown')
            severity = iss.get('severity', 'Low')
            detail   = self._extract_detail(iss, snap)
            structured_issues.append({
                'type':     itype,
                'severity': severity,
                'task':     (iss.get('affected_tasks') or ['?'])[0],
                'summary':  iss.get('description', itype),
                'causal_chain': _format_chain(itype, detail),
                'confidence':   self._confidence(severity),
                'recommended_actions': _RECOMMENDED_ACTIONS.get(itype, [
                    {'priority': 1, 'action': f'{itype} 수동 점검 필요'}
                ]),
                '_fallback': True,
            })
        crits  = [i for i in structured_issues if i['severity'] == 'Critical']
        highs  = [i for i in structured_issues if i['severity'] == 'High']
        summary = self._session_summary(snap, crits, highs, reason)
        return {
            'issues':              structured_issues,
            'session_summary':     summary,
            'overall_confidence':  self._overall_confidence(structured_issues),
            '_fallback':           True,
            '_fallback_reason':    reason,
            '_fallback_version':   self.VERSION,
            '_analysis_ms':        int((time.time() - t_start) * 1000),
        }
    # ── 내부 헬퍼 ────────────────────────────────────────────
    def _extract_detail(self, iss: dict, snap: dict) -> dict:
        """이슈 딕셔너리와 스냅샷에서 포매팅용 변수 추출."""
        d: dict = {}
        tasks = snap.get('tasks', [])
        heap  = snap.get('heap', {})
        # 공통
        d['cpu']  = snap.get('cpu_usage', 0)
        d['free'] = heap.get('free', 0)
        d['pct']  = heap.get('used_pct', 0)
        # 태스크별
        affected = iss.get('affected_tasks', [])
        if affected and tasks:
            for t in tasks:
                if t.get('name') in affected:
                    d['hwm']  = t.get('stack_hwm', '?')
                    d['task'] = t.get('name', '?')
                    break
        # 우선순위 역전
        blocked  = [t.get('name','?') for t in tasks if t.get('state_name')=='Blocked']
        running  = [t.get('name','?') for t in tasks if t.get('state_name')=='Running']
        d['high'] = blocked[0]  if blocked else '?'
        d['low']  = running[0]  if running else '?'
        # fault
        d['cfsr'] = iss.get('detail', {}).get('cfsr', 0)
        # peripheral
        peri     = snap.get('peripheral', {})
        gpio     = peri.get('gpio_pins', [])
        d['count'] = iss.get('detail', {}).get('glitch_count') or \
                     iss.get('detail', {}).get('nack_count')   or \
                     iss.get('detail', {}).get('overrun_count') or \
                     len(gpio)
        return d
    @staticmethod
    def _confidence(severity: str) -> float:
        """Rule-based 신뢰도 — AI 대비 낮게 표기."""
        return {'Critical': 0.75, 'High': 0.60, 'Medium': 0.45, 'Low': 0.30}.get(severity, 0.30)
    @staticmethod
    def _overall_confidence(issues: list) -> float:
        if not issues: return 0.0
        return round(sum(i['confidence'] for i in issues) / len(issues), 2)
    @staticmethod
    def _session_summary(snap: dict, crits: list, highs: list, reason: str) -> str:
        cpu  = snap.get('cpu_usage', 0)
        free = snap.get('heap', {}).get('free', 0)
        n_crit = len(crits)
        n_high = len(highs)
        crit_names = ', '.join(i['type'] for i in crits[:2])
        return (
            f"[AI 비가용: {reason}] Rule-based 분석 결과 — "
            f"Critical {n_crit}건({crit_names}), High {n_high}건 | "
            f"CPU {cpu}%, Heap {free}B 여유 | "
            "AI 분석 가능 시 재실행 권장"
        )