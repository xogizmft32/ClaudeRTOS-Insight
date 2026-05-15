#!/usr/bin/env python3
"""
release_notes.py — CHANGELOG → GitHub Release body 변환기

CHANGELOG.md의 현재 버전 블록을 읽어 GitHub Release에
바로 붙여넣을 수 있는 Markdown 본문을 생성한다.

사용:
  python3 release_notes.py
  python3 release_notes.py --version 5.7.2
  python3 release_notes.py --out RELEASE_NOTES.md
  python3 release_notes.py --github-output
"""

from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path

ROOT = Path(__file__).parent


def read_version() -> str:
    vfile = ROOT / 'VERSION'
    if not vfile.exists():
        sys.exit('ERROR: VERSION 파일 없음')
    return vfile.read_text().strip()


def extract_block(version: str) -> str:
    changelog = ROOT / 'CHANGELOG.md'
    if not changelog.exists():
        sys.exit('ERROR: CHANGELOG.md 없음')
    text = changelog.read_text(encoding='utf-8')
    escaped = re.escape(version)
    pattern = rf'^(## \[{escaped}\][^\n]*\n.*?)(?=\n## \[|\Z)'
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not m:
        sys.exit(f'ERROR: CHANGELOG에서 v{version} 블록을 찾지 못함')
    return m.group(1).strip()


def build_release_body(version: str, block: str) -> str:
    lines = block.splitlines()
    header_line = lines[0] if lines else f'## [{version}]'
    date_match  = re.search(r'(\d{4}-\d{2}-\d{2})', header_line)
    sub_match   = re.search(r'\(([^)]+)\)', header_line)
    badge_match = re.search(r'(✅ PRODUCTION READY|⚠️ DEPRECATED)', header_line)

    date_str = date_match.group(1)  if date_match  else ''
    subtitle = sub_match.group(1)   if sub_match   else ''
    badge    = badge_match.group(1) if badge_match else ''

    proto_score = _extract_validation(block, r'Protocol\s+(\d+/\d+)')
    l2_score    = _extract_validation(block, r'Level\s+2\s+(\d+/\d+)')
    val_line    = _build_validation_line(proto_score, l2_score)
    body_lines  = _extract_body(lines[1:])
    install_block = _install_section(version)

    parts: list[str] = []

    title_parts = [f'## 🚀 ClaudeRTOS-Insight v{version}']
    if badge:
        title_parts.append(f'  `{badge}`')
    parts.append(' '.join(title_parts))

    meta: list[str] = []
    if date_str:
        meta.append(f'📅 **{date_str}**')
    if subtitle:
        meta.append(f'_{subtitle}_')
    if meta:
        parts.append('> ' + '  |  '.join(meta))
    parts.append('')

    if val_line:
        parts.append(val_line)
        parts.append('')

    parts.append('---')
    parts.append('')

    if body_lines:
        parts.extend(body_lines)
        parts.append('')

    parts.append('---')
    parts.append('')
    parts.extend(install_block)

    return '\n'.join(parts)


def _extract_validation(block: str, pattern: str) -> str:
    m = re.search(pattern, block)
    return m.group(1) if m else ''


def _build_validation_line(proto: str, l2: str) -> str:
    parts = []
    if proto:
        parts.append(f'![Protocol {proto}](https://img.shields.io/badge/Protocol-{proto.replace("/", "%2F")}%20PASS-brightgreen)')
    if l2:
        parts.append(f'![Level2 {l2}](https://img.shields.io/badge/Level2-{l2.replace("/", "%2F")}%20PASS-brightgreen)')
    return ' '.join(parts)


def _extract_body(lines: list[str]) -> list[str]:
    result: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^#{1,4}\s*(검증|Validation)', stripped):
            skip = True
        if skip and re.match(r'^#{1,3}\s+[^검증Vv]', stripped):
            skip = False
        if skip or stripped == '---':
            continue
        result.append(line.rstrip())
    while result and not result[-1].strip():
        result.pop()
    return result


def _install_section(version: str) -> list[str]:
    return [
        '## 📦 설치',
        '',
        '```bash',
        '# 아카이브 다운로드 후',
        f'tar -xzf ClaudeRTOS-Insight-v{version}.tar.gz',
        'cd ClaudeRTOS-Insight',
        'python3 install.py',
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


def write_github_output(version: str, body: str) -> None:
    output_file = os.environ.get('GITHUB_OUTPUT', '')
    if not output_file:
        print('WARNING: GITHUB_OUTPUT 환경변수 없음', file=sys.stderr)
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


def main() -> None:
    parser = argparse.ArgumentParser(description='CHANGELOG → GitHub Release body 변환기')
    parser.add_argument('--version', default='')
    parser.add_argument('--out', default='')
    parser.add_argument('--github-output', action='store_true')
    parser.add_argument('--block-only', action='store_true')
    args = parser.parse_args()

    version = args.version or read_version()
    block   = extract_block(version)
    output  = block if args.block_only else build_release_body(version, block)

    if args.github_output:
        write_github_output(version, output)

    if args.out:
        Path(args.out).write_text(output, encoding='utf-8')
        print(f'Release notes → {args.out}', file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
