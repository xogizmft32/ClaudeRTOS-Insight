#!/usr/bin/env python3
"""
release_notes.py — CHANGELOG → GitHub Release body 변환기

CHANGELOG.md의 현재 버전 블록을 읽어 GitHub Release에
바로 붙여넣을 수 있는 Markdown 본문을 생성한다.

사용:
  # 현재 버전(VERSION 파일) 릴리즈 노트 출력
  python3 release_notes.py

  # 특정 버전 지정
  python3 release_notes.py --version 5.7.1

  # 파일로 저장
  python3 release_notes.py --out RELEASE_NOTES.md

  # GitHub Actions 환경변수로 출력 (GITHUB_OUTPUT)
  python3 release_notes.py --github-output

출력 형식:
  ## 🚀 ClaudeRTOS-Insight v5.7.1
  > MISRA 감사 수정 — ...
  ...
  **검증:** Protocol 48/48 · Level 2 45/45 ALL PASS
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent


# ── CHANGELOG 파싱 ───────────────────────────────────────────────

def read_version() -> str:
    """VERSION 파일에서 현재 버전 읽기."""
    vfile = ROOT / 'VERSION'
    if not vfile.exists():
        sys.exit('ERROR: VERSION 파일 없음')
    return vfile.read_text().strip()


def extract_block(version: str) -> str:
    """
    CHANGELOG.md에서 지정 버전 블록 추출.
    ## [X.Y.Z] 부터 다음 ## [ 까지.
    """
    changelog = ROOT / 'CHANGELOG.md'
    if not changelog.exists():
        sys.exit('ERROR: CHANGELOG.md 없음')

    text = changelog.read_text(encoding='utf-8')

    # 버전 헤더 패턴: ## [5.7.1] — ... 또는 ## [5.7.1] ...
    escaped = re.escape(version)
    pattern = rf'^(## \[{escaped}\][^\n]*\n.*?)(?=\n## \[|\Z)'
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not m:
        sys.exit(f'ERROR: CHANGELOG에서 v{version} 블록을 찾지 못함')
    return m.group(1).strip()


# ── Release body 생성 ────────────────────────────────────────────

def build_release_body(version: str, block: str) -> str:
    """
    CHANGELOG 블록을 GitHub Release body Markdown으로 변환.

    구조:
      헤더 (버전·날짜·부제목)
      검증 배지 라인
      변경 내용 (원본 그대로)
      푸터 (설치·링크)
    """
    lines = block.splitlines()

    # ── 헤더 파싱 ────────────────────────────────────────────────
    # ## [5.7.1] — 2026-05-13 (MISRA 감사 수정)
    header_line = lines[0] if lines else f'## [{version}]'
    date_match    = re.search(r'(\d{4}-\d{2}-\d{2})', header_line)
    sub_match     = re.search(r'\(([^)]+)\)', header_line)
    badge_match   = re.search(r'(✅ PRODUCTION READY|⚠️ DEPRECATED)', header_line)

    date_str    = date_match.group(1)   if date_match  else ''
    subtitle    = sub_match.group(1)    if sub_match   else ''
    badge       = badge_match.group(1)  if badge_match else ''

    # ── 검증 결과 추출 ───────────────────────────────────────────
    # | Protocol 48/48 | ✅ ALL PASS | 같은 테이블 행
    proto_score = _extract_validation(block, r'Protocol\s+(\d+/\d+)')
    l2_score    = _extract_validation(block, r'Level\s+2\s+(\d+/\d+)')
    val_line    = _build_validation_line(proto_score, l2_score)

    # ── 변경 내용 (헤더 행 제외, 검증 테이블 이전까지) ──────────
    body_lines = _extract_body(lines[1:])

    # ── 설치 명령 ────────────────────────────────────────────────
    install_block = _install_section(version)

    # ── 조합 ────────────────────────────────────────────────────
    parts: list[str] = []

    # 제목
    title_parts = [f'## 🚀 ClaudeRTOS-Insight v{version}']
    if badge:
        title_parts.append(f'  `{badge}`')
    parts.append(' '.join(title_parts))

    # 메타
    meta: list[str] = []
    if date_str:
        meta.append(f'📅 **{date_str}**')
    if subtitle:
        meta.append(f'_{subtitle}_')
    if meta:
        parts.append('> ' + '  |  '.join(meta))

    parts.append('')

    # 검증 배지
    if val_line:
        parts.append(val_line)
        parts.append('')

    # 구분선
    parts.append('---')
    parts.append('')

    # 변경 내용
    if body_lines:
        parts.extend(body_lines)
        parts.append('')

    # 설치 안내
    parts.append('---')
    parts.append('')
    parts.extend(install_block)

    return '\n'.join(parts)


def _extract_validation(block: str, pattern: str) -> str:
    """검증 테이블에서 점수 추출. e.g. '48/48'"""
    m = re.search(pattern, block)
    return m.group(1) if m else ''


def _build_validation_line(proto: str, l2: str) -> str:
    """검증 뱃지 라인 생성."""
    parts = []
    if proto:
        parts.append(f'![Protocol {proto}](https://img.shields.io/badge/Protocol-{proto.replace("/", "%2F")}%20PASS-brightgreen)')
    if l2:
        parts.append(f'![Level2 {l2}](https://img.shields.io/badge/Level2-{l2.replace("/", "%2F")}%20PASS-brightgreen)')
    if not parts:
        return ''
    return ' '.join(parts)


def _extract_body(lines: list[str]) -> list[str]:
    """
    변경 내용 섹션 추출.
    '### 검증' 이후 테이블은 제외 (Release body에는 배지로 대체).
    '---' 구분선도 제외.
    """
    result: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        # 검증 섹션 시작 → 스킵
        if re.match(r'^#{1,4}\s*(검증|Validation)', stripped):
            skip = True
        # 다음 ### 섹션 시작 → 스킵 해제
        if skip and re.match(r'^#{1,3}\s+[^검증Vv]', stripped):
            skip = False
        if skip:
            continue
        if stripped == '---':
            continue
        result.append(line.rstrip())

    # 말미 빈 줄 정리
    while result and not result[-1].strip():
        result.pop()
    return result


def _install_section(version: str) -> list[str]:
    """설치·링크 안내 블록."""
    return [
        '## 📦 설치',
        '',
        '```bash',
        f'# 아카이브 다운로드 후',
        f'tar -xzf ClaudeRTOS-Insight-v{version}.tar.gz',
        f'cd ClaudeRTOS-Insight',
        f'python3 install.py',
        '```',
        '',
        '> **빠른 검증**',
        '> ```bash',
        '> PYTHONPATH=host python3 examples/integrated_demo.py --validate',
        '> ```',
        '',
        '📖 **문서:** `docs/01_start/GETTING_STARTED.md`  ',
        '🔄 **CHANGELOG:** [전체 이력](CHANGELOG.md)',
    ]


# ── GitHub Actions 출력 ──────────────────────────────────────────

def write_github_output(version: str, body: str) -> None:
    """
    GITHUB_OUTPUT 파일에 멀티라인 값 기록.
    GitHub Actions 표준 방식: heredoc 구문.

    출력 변수:
      release_version  — e.g. "5.7.1"
      release_tag      — e.g. "v5.7.1"
      release_name     — e.g. "ClaudeRTOS-Insight v5.7.1"
      release_body     — Release Markdown body
    """
    output_file = os.environ.get('GITHUB_OUTPUT', '')
    if not output_file:
        print('WARNING: GITHUB_OUTPUT 환경변수 없음 (Actions 외부 실행)', file=sys.stderr)
        return

    delimiter = 'EOF_RELEASE_BODY'
    with open(output_file, 'a', encoding='utf-8') as f:
        f.write(f'release_version={version}\n')
        f.write(f'release_tag=v{version}\n')
        f.write(f'release_name=ClaudeRTOS-Insight v{version}\n')
        f.write(f'release_body<<{delimiter}\n')
        f.write(body)
        f.write(f'\n{delimiter}\n')

    print(f'GitHub Output 기록 완료 → {output_file}', file=sys.stderr)


# ── CLI ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='CHANGELOG → GitHub Release body 변환기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', default='',
                        help='버전 지정 (기본: VERSION 파일)')
    parser.add_argument('--out', default='',
                        help='출력 파일 경로 (기본: stdout)')
    parser.add_argument('--github-output', action='store_true',
                        help='GITHUB_OUTPUT 환경변수에 기록 (Actions 전용)')
    parser.add_argument('--block-only', action='store_true',
                        help='CHANGELOG 원본 블록만 출력 (변환 없음)')
    args = parser.parse_args()

    version = args.version or read_version()
    block   = extract_block(version)

    if args.block_only:
        output = block
    else:
        output = build_release_body(version, block)

    # GitHub Actions 출력
    if args.github_output:
        write_github_output(version, output)

    # 파일 또는 stdout
    if args.out:
        Path(args.out).write_text(output, encoding='utf-8')
        print(f'Release notes → {args.out}', file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
