#!/usr/bin/env python3
"""
debug_report.py — 분석 결과 자동 문서화 보고서 (3번 항목)

세션 종료 후 "이 세션에서 무슨 일이 있었나?"를
사람이 읽을 수 있는 Markdown 보고서로 자동 생성.

생성 내용:
  1. 세션 요약 (시간, 이슈 횟수, 심각도별 분류)
  2. Critical/High 이슈 상세 (근본 원인, 수정 코드, 인과 체인)
  3. 리소스 추이 (ASCII 막대그래프)
  4. Mermaid 인과관계 다이어그램
  5. 미해결 항목 체크리스트
  6. 다음 세션 권장 사항

출력 형식: Markdown (.md)

사용:
    reporter = DebugReportGenerator(project_name="MyRTOS", cpu_hz=180_000_000)
    reporter.add_snapshot(snap)
    reporter.add_issue(issue_dict)
    reporter.add_ai_result(ai_result_dict)
    reporter.set_causal_graph(gcg)
    report_path = reporter.save("reports/debug_report.md")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ReportIssue:
    issue_type:   str
    severity:     str
    task_name:    str
    description:  str
    causal_chain: List[str] = field(default_factory=list)
    fix_file:     str = ''
    fix_before:   str = ''
    fix_after:    str = ''
    confidence:   float = 0.0
    resolved:     bool = False
    timestamp_s:  float = 0.0


class DebugReportGenerator:
    """
    디버깅 세션의 분석 결과를 Markdown 보고서로 자동 생성.

    사용:
        gen = DebugReportGenerator(project_name="MyProject")
        gen.add_snapshot(snap_dict)          # 세션 중
        gen.add_issue(issue_dict)            # 이슈 감지 시
        gen.add_ai_result(ai_response_dict)  # AI 분석 후
        gen.set_causal_graph(gcg)            # 선택
        gen.save("debug_report_20260408.md")
    """

    def __init__(self, project_name: str = "MyProject",
                 cpu_hz: int = 180_000_000,
                 profile: str = 'STANDARD'):
        self._project    = project_name
        self._cpu_hz     = cpu_hz
        self._profile    = profile
        self._start_time = time.time()
        self._end_time   = 0.0

        self._snapshots:  List[Dict]  = []
        self._issues:     List[ReportIssue] = []
        self._ai_results: List[Dict]  = []
        self._gcg         = None

        # 통계
        self._sev_count: Dict[str, int] = {
            'Critical': 0, 'High': 0, 'Medium': 0, 'Low': 0}

    # ── 데이터 수집 ───────────────────────────────────────────
    def add_snapshot(self, snap: Dict) -> None:
        self._snapshots.append(snap)

    def add_issue(self, issue: Dict) -> None:
        sev = issue.get('severity', 'Low')
        self._sev_count[sev] = self._sev_count.get(sev, 0) + 1
        self._issues.append(ReportIssue(
            issue_type  = issue.get('type', issue.get('issue_type', '?')),
            severity    = sev,
            task_name   = (issue.get('affected_tasks') or ['?'])[0],
            description = issue.get('description', ''),
            timestamp_s = time.time(),
        ))

    def add_ai_result(self, ai_dict: Dict) -> None:
        """AI 응답 딕셔너리에서 이슈 상세 정보 추출."""
        self._ai_results.append(ai_dict)
        for ai_iss in ai_dict.get('issues', []):
            ri = ReportIssue(
                issue_type   = ai_iss.get('type', '?'),
                severity     = ai_iss.get('severity', 'Medium'),
                task_name    = ai_iss.get('task', '?'),
                description  = ai_iss.get('summary', ''),
                causal_chain = ai_iss.get('causal_chain', []),
                confidence   = ai_iss.get('confidence', 0.0),
                timestamp_s  = time.time(),
            )
            # 수정 코드
            actions = ai_iss.get('recommended_actions', [])
            if actions:
                fix = actions[0].get('fix', {})
                ri.fix_file   = fix.get('file', '')
                ri.fix_before = fix.get('before', '')
                ri.fix_after  = fix.get('after', '')
            # 기존 이슈와 병합 (같은 타입이면 갱신)
            merged = False
            for existing in self._issues:
                if existing.issue_type == ri.issue_type:
                    existing.causal_chain = ri.causal_chain
                    existing.confidence   = ri.confidence
                    existing.fix_file     = ri.fix_file
                    existing.fix_before   = ri.fix_before
                    existing.fix_after    = ri.fix_after
                    merged = True
                    break
            if not merged:
                self._issues.append(ri)

    def set_causal_graph(self, gcg) -> None:
        self._gcg = gcg

    def mark_resolved(self, issue_type: str) -> None:
        for iss in self._issues:
            if iss.issue_type == issue_type:
                iss.resolved = True

    # ── 보고서 생성 ───────────────────────────────────────────
    def generate(self) -> str:
        self._end_time = time.time()
        duration = self._end_time - self._start_time
        n_snaps  = len(self._snapshots)
        n_issues = len(self._issues)
        unresolved = [i for i in self._issues if not i.resolved]

        lines = []

        # ── 헤더 ─────────────────────────────────────────────
        lines += [
            f"# 디버그 분석 보고서 — {self._project}",
            f"\n생성: {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"| 프로파일: `{self._profile}` "
            f"| 세션: {duration:.0f}초\n",
        ]

        # ── 1. 세션 요약 ──────────────────────────────────────
        lines += [
            "---",
            "## 1. 세션 요약",
            "",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 분석 스냅샷 수 | {n_snaps} |",
            f"| 감지된 이슈 | {n_issues} |",
            f"| 🔴 Critical | {self._sev_count.get('Critical', 0)} |",
            f"| 🟠 High | {self._sev_count.get('High', 0)} |",
            f"| 🟡 Medium | {self._sev_count.get('Medium', 0)} |",
            f"| 미해결 이슈 | {len(unresolved)} |",
            f"| AI 분석 호출 | {len(self._ai_results)} |",
            "",
        ]

        # ── 2. 이슈 상세 (Critical/High 우선) ─────────────────
        lines += ["---", "## 2. 이슈 상세", ""]
        priority_issues = sorted(
            self._issues,
            key=lambda i: {'Critical':0,'High':1,'Medium':2,'Low':3}.get(i.severity, 3))

        for idx, iss in enumerate(priority_issues, 1):
            emoji = {'Critical':'🔴','High':'🟠','Medium':'🟡','Low':'⚪'}.get(iss.severity,'⚪')
            status = "~~해결됨~~" if iss.resolved else "**미해결**"
            lines += [
                f"### {idx}. {emoji} {iss.issue_type} — {iss.task_name}",
                f"**심각도**: {iss.severity} | **상태**: {status}",
                f"| 신뢰도 | {iss.confidence:.0%} |" if iss.confidence else "",
                "",
                f"**설명**: {iss.description}" if iss.description else "",
                "",
            ]
            if iss.causal_chain:
                lines.append("**인과 체인**:")
                for step in iss.causal_chain:
                    lines.append(f"  → {step}")
                lines.append("")
            if iss.fix_file:
                lines += [
                    "**수정 방법**:",
                    f"```",
                    f"파일: {iss.fix_file}",
                    f"Before: {iss.fix_before}" if iss.fix_before else "",
                    f"After:  {iss.fix_after}" if iss.fix_after else "",
                    "```",
                    "",
                ]

        # ── 3. 리소스 추이 (ASCII) ────────────────────────────
        lines += ["---", "## 3. 리소스 추이", ""]
        if self._snapshots:
            cpus  = [s.get('cpu_usage', 0) for s in self._snapshots[-20:]]
            heaps = [s.get('heap',{}).get('used_pct',0)
                     for s in self._snapshots[-20:]]

            lines.append("**CPU 사용률 추이** (최근 20 스냅샷, ▪=5%):")
            lines.append("```")
            for i, c in enumerate(cpus):
                bar = '▪' * (c // 5)
                lines.append(f"  [{i+1:2d}] {bar:<20} {c:.0f}%")
            lines.append("```\n")

            lines.append("**Heap 사용률 추이** (▪=5%):")
            lines.append("```")
            for i, h in enumerate(heaps):
                bar = '▪' * (h // 5)
                danger = " ← ⚠" if h > 85 else ""
                lines.append(f"  [{i+1:2d}] {bar:<20} {h:.0f}%{danger}")
            lines.append("```\n")

        # ── 4. Mermaid 인과관계 다이어그램 ───────────────────
        if self._gcg is not None:
            try:
                mermaid = self._gcg.to_mermaid(max_nodes=8)
                lines += [
                    "---",
                    "## 4. 인과관계 다이어그램",
                    "",
                    mermaid.replace('\\n', '\n'),
                    "",
                ]
            except Exception:
                pass

        # ── 5. 미해결 체크리스트 ──────────────────────────────
        lines += ["---", "## 5. 미해결 항목 체크리스트", ""]
        if unresolved:
            for iss in unresolved:
                emoji = {'Critical':'🔴','High':'🟠','Medium':'🟡','Low':'⚪'}.get(iss.severity,'⚪')
                fix_hint = f" → `{iss.fix_file}`" if iss.fix_file else ""
                lines.append(f"- [ ] {emoji} **{iss.issue_type}** ({iss.task_name}){fix_hint}")
        else:
            lines.append("✅ 모든 이슈 해결됨")
        lines.append("")

        # ── 6. 다음 세션 권장 사항 ───────────────────────────
        lines += ["---", "## 6. 다음 세션 권장 사항", ""]
        recommendations = self._generate_recommendations()
        for rec in recommendations:
            lines.append(f"- {rec}")
        lines.append("")

        # ── 푸터 ─────────────────────────────────────────────
        lines += [
            "---",
            f"> 이 보고서는 ClaudeRTOS-Insight가 자동 생성했습니다.  ",
            f"> 프로젝트: **{self._project}** | CPU: {self._cpu_hz:,} Hz",
        ]

        return '\n'.join(l for l in lines)

    def _generate_recommendations(self) -> List[str]:
        recs = []
        crits = [i for i in self._issues
                 if i.severity == 'Critical' and not i.resolved]
        if crits:
            recs.append(f"**즉시**: Critical 이슈 {len(crits)}개 수정 후 재테스트")
        has_stack = any(i.issue_type == 'stack_overflow_imminent'
                        for i in self._issues)
        has_heap  = any(i.issue_type in ('heap_exhaustion','low_heap')
                        for i in self._issues)
        if has_stack and has_heap:
            recs.append("메모리 압박 복합 증상 — 정적 할당 검토 권장")
        if len(self._snapshots) < 10:
            recs.append("스냅샷 수 부족 (< 10) — 더 긴 세션으로 재분석 권장")
        if not recs:
            recs.append("현재 세션에서 특이 사항 없음 — 다음 릴리즈 시 리소스 추이 재확인")
        return recs

    def save(self, path: str) -> str:
        """보고서를 파일로 저장. 저장된 경로 반환."""
        content = self.generate()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding='utf-8')
        return str(out)
