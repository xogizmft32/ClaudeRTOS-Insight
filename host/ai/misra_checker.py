"""
misra_checker.py — Option E: AI fix_code MISRA C:2012 정적 검사

AI가 생성한 fix_code 패치가 FreeRTOS/STM32 MISRA C:2012 핵심 규칙을
위반하지 않는지 패턴 기반으로 검사한다.

공식 MISRA 인증 도구(PC-lint Plus, Polyspace)를 대체하지 않는다.
개발 단계의 1차 스크리닝 용도로 사용한다.

사용 예
-------
from ai.misra_checker import MISRAChecker, MISRAViolation

checker = MISRAChecker()
violations = checker.check(fix_code_str)
report = checker.format_report(violations)
print(report)

# AgentResult에서 직접 검사
violations = checker.check_agent_result(agent_result)
"""

from __future__ import annotations

import re
import dataclasses
import logging
from typing import List, Optional

_log = logging.getLogger(__name__)

# ── 위반 심각도 ─────────────────────────────────────────────
SEVERITY_MANDATORY  = 'Mandatory'   # 예외 없이 준수 (SIL4 필수)
SEVERITY_REQUIRED   = 'Required'    # 합리적 이유 없이 위반 금지
SEVERITY_ADVISORY   = 'Advisory'    # 권고 — 위반 허용되나 문서화 필요


# ── 위반 항목 ────────────────────────────────────────────────
@dataclasses.dataclass
class MISRAViolation:
    """
    단일 MISRA C:2012 위반.

    Attributes
    ----------
    rule        : MISRA 규칙 번호 (예: "14.3")
    severity    : Mandatory / Required / Advisory
    description : 규칙 설명
    line        : 위반 행 번호 (1-based, -1이면 미확인)
    line_text   : 위반 행 원문 (공백 제거)
    suggestion  : 수정 방법 제안
    """
    rule:        str
    severity:    str
    description: str
    line:        int
    line_text:   str
    suggestion:  str

    def __str__(self) -> str:
        loc = f"L{self.line}" if self.line > 0 else "?"
        return (f"[{self.severity}] Rule {self.rule} @ {loc}: "
                f"{self.description}")


# ── 규칙 정의 ────────────────────────────────────────────────
@dataclasses.dataclass
class _Rule:
    number:      str
    severity:    str
    description: str
    pattern:     re.Pattern
    suggestion:  str
    negate:      bool = False   # True이면 패턴 미매칭 시 위반


_RULES: List[_Rule] = [
    # ── Mandatory ────────────────────────────────────────────
    _Rule(
        number='1.3', severity=SEVERITY_MANDATORY,
        description='정의되지 않은 동작(UB) — 초기화되지 않은 변수 사용 의심',
        pattern=re.compile(
            r'\b(?:int|uint8_t|uint16_t|uint32_t|char|float|double)\s+'
            r'(\w+)\s*;(?!\s*/\*.*initialized)',
            re.MULTILINE,
        ),
        suggestion='모든 변수를 선언 시 초기화하라: int x = 0;',
    ),
    _Rule(
        number='8.4', severity=SEVERITY_MANDATORY,
        description='외부 링키지 객체/함수에 프로토타입 선언 없음',
        pattern=re.compile(
            r'^(?!static\s|extern\s|//)(?:void|int|uint\w+|BaseType_t)\s+'
            r'(?!main\b)(\w+)\s*\([^)]*\)\s*\{',
            re.MULTILINE,
        ),
        suggestion='헤더 파일에 함수 프로토타입을 선언하거나 static을 붙여라.',
    ),
    _Rule(
        number='14.3', severity=SEVERITY_MANDATORY,
        description='불변 조건 — 항상 참/거짓인 제어 표현식',
        pattern=re.compile(
            r'\bif\s*\(\s*(?:1|0|true|false|TRUE|FALSE)\s*\)',
            re.MULTILINE,
        ),
        suggestion='if(1) / if(0) 등 불변 조건을 제거하라.',
    ),
    _Rule(
        number='17.3', severity=SEVERITY_MANDATORY,
        description='묵시적 함수 선언 (implicit function declaration)',
        pattern=re.compile(
            r'(?<!\w)(?:pvPortMalloc|vPortFree|xTaskCreate|'
            r'xQueueSend|xQueueReceive|xSemaphore\w+)\s*\(',
        ),
        suggestion='FreeRTOS API를 사용하려면 반드시 #include "FreeRTOS.h" 등을 명시하라.',
        negate=True,   # include가 없는데 FreeRTOS API 호출 시 위반
    ),

    # ── Required ─────────────────────────────────────────────
    _Rule(
        number='10.3', severity=SEVERITY_REQUIRED,
        description='표현식 값을 다른 본질 타입에 대입 — 암묵적 캐스팅 의심',
        pattern=re.compile(
            r'uint\d+_t\s+\w+\s*=\s*-\d',   # unsigned에 음수 대입
            re.MULTILINE,
        ),
        suggestion='uint32_t 변수에 음수를 대입하지 말라. 부호있는 타입을 사용하라.',
    ),
    _Rule(
        number='11.3', severity=SEVERITY_REQUIRED,
        description='객체 포인터와 다른 타입 포인터 간 캐스팅',
        pattern=re.compile(
            r'\((?:void\s*\*|uint8_t\s*\*|char\s*\*)\)\s*&?\w+',
            re.MULTILINE,
        ),
        suggestion='포인터 타입 변환은 memcpy를 사용해 안전하게 처리하라.',
    ),
    _Rule(
        number='12.1', severity=SEVERITY_REQUIRED,
        description='연산자 우선순위 — 괄호 없는 복합 표현식',
        pattern=re.compile(
            r'(?<!=)(?<![<>!])(?:\w+\s*[&|^]\s*\w+\s*[+\-\*\/]\s*\w+|'
            r'\w+\s*[+\-\*\/]\s*\w+\s*[&|^]\s*\w+)',
            re.MULTILINE,
        ),
        suggestion='연산자 우선순위가 불명확한 표현식에 괄호를 추가하라.',
    ),
    _Rule(
        number='13.2', severity=SEVERITY_REQUIRED,
        description='부작용 있는 표현식 순서 평가 의존',
        pattern=re.compile(
            r'\b(\w+)\+\+\s*(?:&&|\|\|)|(?:&&|\|\|)\s*(\w+)\+\+',
            re.MULTILINE,
        ),
        suggestion='논리 연산자와 증감 연산자를 같은 표현식에 혼용하지 말라.',
    ),
    _Rule(
        number='15.5', severity=SEVERITY_REQUIRED,
        description='함수에 여러 개의 return 문 — FreeRTOS ISR 안전 위험',
        pattern=re.compile(
            r'(?s)(?:void|BaseType_t|uint\w+)\s+\w+ISR\w*\s*\([^)]*\)\s*\{'
            r'(?:(?!\}).)*?\breturn\b.*?\breturn\b',
        ),
        suggestion='ISR 함수에는 단일 출구(return)를 사용하라.',
    ),
    _Rule(
        number='18.1', severity=SEVERITY_REQUIRED,
        description='배열 인덱스 범위 검사 없이 접근',
        pattern=re.compile(
            r'\b(\w+)\[(\w+)\](?!\s*;)(?!.*\bif\b.*\2\s*<)',
            re.MULTILINE,
        ),
        suggestion='배열 접근 전에 인덱스 범위를 명시적으로 검사하라.',
    ),

    # ── Advisory ─────────────────────────────────────────────
    _Rule(
        number='2.2', severity=SEVERITY_ADVISORY,
        description='도달 불가능 코드',
        pattern=re.compile(
            r'\breturn\b[^;]*;\s*\n\s*(?!//|/\*)(?:[\w\(\{])',
            re.MULTILINE,
        ),
        suggestion='return 이후의 도달 불가능 코드를 제거하라.',
    ),
    _Rule(
        number='5.1', severity=SEVERITY_ADVISORY,
        description='식별자 길이 — 31자 초과 (C90 호환 문제)',
        pattern=re.compile(
            r'\b([A-Za-z_]\w{31,})\b',
            re.MULTILINE,
        ),
        suggestion='식별자는 31자 이하로 제한하라 (C90 호환).',
    ),
    _Rule(
        number='20.9', severity=SEVERITY_ADVISORY,
        description='#define 없이 미정의 매크로 사용 가능성',
        pattern=re.compile(
            r'#\s*if\s+(?!defined)\w+',
            re.MULTILINE,
        ),
        suggestion='#if 조건에서 defined() 연산자를 명시적으로 사용하라.',
    ),
]

# ISR에서 FreeRTOS API 직접 호출 (FROM_ISR 버전 필요)
_ISR_UNSAFE_API = re.compile(
    r'\b(xQueueSend|xQueueReceive|xSemaphoreGive|xSemaphoreTake|'
    r'xTaskResumeFromISR|vTaskNotifyGiveFromISR)\s*\(',
)
_ISR_FUNC = re.compile(r'\bvoid\s+\w+ISR\w*\s*\(', re.MULTILINE)
_FREERTOS_INCLUDE = re.compile(r'#\s*include\s*[<"](?:FreeRTOS\.h|task\.h|queue\.h|semphr\.h)[">]')


class MISRAChecker:
    """
    AI fix_code에 대한 MISRA C:2012 패턴 기반 1차 검사기.

    참고: 이 검사기는 공식 MISRA 인증 도구를 대체하지 않는다.
    SIL4 인증에는 PC-lint Plus, Polyspace 등 공인 도구를 사용하라.
    """

    def __init__(self):
        self._rules = _RULES

    def check(self, code: str) -> List[MISRAViolation]:
        """
        코드 문자열에서 MISRA 위반을 탐지해 반환.

        Parameters
        ----------
        code : 검사할 C 코드 문자열 (fix_code 등)

        Returns
        -------
        위반 항목 목록 (빈 리스트 = 위반 없음)
        """
        if not code or not code.strip():
            return []

        lines  = code.splitlines()
        result: List[MISRAViolation] = []

        for rule in self._rules:
            if rule.negate:
                # negate 규칙: FreeRTOS API 사용했는데 include 없는 경우
                if _ISR_UNSAFE_API.search(code) and not _FREERTOS_INCLUDE.search(code):
                    result.append(MISRAViolation(
                        rule=rule.number, severity=rule.severity,
                        description=rule.description,
                        line=-1, line_text='(전체 파일)',
                        suggestion=rule.suggestion,
                    ))
                continue

            for m in rule.pattern.finditer(code):
                # 행 번호 계산
                line_no  = code[:m.start()].count('\n') + 1
                line_txt = lines[line_no - 1].strip() if line_no <= len(lines) else ''

                # 주석 행 제외
                if line_txt.startswith('//') or line_txt.startswith('*'):
                    continue

                result.append(MISRAViolation(
                    rule=rule.number, severity=rule.severity,
                    description=rule.description,
                    line=line_no, line_text=line_txt[:120],
                    suggestion=rule.suggestion,
                ))

        # ISR에서 비ISR API 호출 검사 (FreeRTOS 전용)
        if _ISR_FUNC.search(code) and _ISR_UNSAFE_API.search(code):
            for m in _ISR_UNSAFE_API.finditer(code):
                line_no  = code[:m.start()].count('\n') + 1
                line_txt = lines[line_no - 1].strip() if line_no <= len(lines) else ''
                result.append(MISRAViolation(
                    rule='17.1',
                    severity=SEVERITY_MANDATORY,
                    description=(f"ISR에서 비ISR FreeRTOS API 호출: {m.group().strip()}"
                                 " — FromISR 버전 사용 필요"),
                    line=line_no, line_text=line_txt[:120],
                    suggestion=(f"{m.group().strip()}을 "
                                f"{m.group().strip().replace('(', 'FromISR(')} 로 변경하라."),
                ))

        # 중복 제거 (rule + line 기준)
        seen: set = set()
        unique: List[MISRAViolation] = []
        for v in result:
            key = (v.rule, v.line)
            if key not in seen:
                seen.add(key)
                unique.append(v)

        # Mandatory → Required → Advisory 순 정렬
        _order = {SEVERITY_MANDATORY: 0, SEVERITY_REQUIRED: 1, SEVERITY_ADVISORY: 2}
        unique.sort(key=lambda v: (_order.get(v.severity, 9), v.line))

        _log.info("[MISRAChecker] %d 위반 감지 (Mandatory:%d Required:%d Advisory:%d)",
                  len(unique),
                  sum(1 for v in unique if v.severity == SEVERITY_MANDATORY),
                  sum(1 for v in unique if v.severity == SEVERITY_REQUIRED),
                  sum(1 for v in unique if v.severity == SEVERITY_ADVISORY))
        return unique

    def check_agent_result(self, agent_result) -> List[MISRAViolation]:
        """
        AgentResult.fix_code를 검사.
        fix_code가 None이면 빈 리스트를 반환.
        """
        code = getattr(agent_result, 'fix_code', None)
        if not code:
            return []
        return self.check(code)

    def format_report(self, violations: List[MISRAViolation]) -> str:
        """사람이 읽기 쉬운 위반 보고서 생성."""
        if not violations:
            return "✅ MISRA C:2012 위반 없음 (패턴 기반 1차 검사)"

        mandatory = [v for v in violations if v.severity == SEVERITY_MANDATORY]
        required  = [v for v in violations if v.severity == SEVERITY_REQUIRED]
        advisory  = [v for v in violations if v.severity == SEVERITY_ADVISORY]

        lines = [
            "## MISRA C:2012 1차 검사 보고서",
            f"총 위반: {len(violations)}건  "
            f"(Mandatory {len(mandatory)} · Required {len(required)} · Advisory {len(advisory)})",
            "",
            "> ⚠️ 이 검사는 패턴 기반 1차 스크리닝입니다.",
            "> SIL4 인증에는 PC-lint Plus, Polyspace 등 공인 도구를 사용하세요.",
            "",
        ]

        for sev, items in [
            (SEVERITY_MANDATORY, mandatory),
            (SEVERITY_REQUIRED,  required),
            (SEVERITY_ADVISORY,  advisory),
        ]:
            if not items:
                continue
            lines.append(f"### {sev} ({len(items)}건)")
            for v in items:
                loc = f"L{v.line}" if v.line > 0 else "?"
                lines.append(f"- **Rule {v.rule}** @ {loc}: {v.description}")
                lines.append(f"  ```\n  {v.line_text}\n  ```")
                lines.append(f"  → {v.suggestion}")
            lines.append("")

        return '\n'.join(lines)

    @staticmethod
    def severity_counts(violations: List[MISRAViolation]) -> Dict:
        """심각도별 집계 반환."""
        return {
            'mandatory': sum(1 for v in violations if v.severity == SEVERITY_MANDATORY),
            'required':  sum(1 for v in violations if v.severity == SEVERITY_REQUIRED),
            'advisory':  sum(1 for v in violations if v.severity == SEVERITY_ADVISORY),
            'total':     len(violations),
        }
