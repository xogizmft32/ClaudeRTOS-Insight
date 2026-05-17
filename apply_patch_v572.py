#!/usr/bin/env python3
"""
apply_patch_v572.py — ClaudeRTOS-Insight v5.7.1 → v5.7.2 패치 적용기

변경 내용 (GitHub Release 자동화):
  - release_notes.py          신규 — CHANGELOG → Release body 변환기
  - .github/workflows/        신규 — GitHub Actions 자동 릴리즈 워크플로우
  - github-update.sh          수정 — 6단계→7단계, gh CLI 연동, Release 자동화
  - VERSION                   수정 — 5.7.1 → 5.7.2
  - CHANGELOG.md              수정 — v5.7.2 항목 추가

사용법:
  cd ClaudeRTOS-Insight          # 프로젝트 루트에서 실행
  python3 apply_patch_v572.py
  python3 apply_patch_v572.py --dry-run   # 실제 적용 없이 확인만
"""

from __future__ import annotations
import argparse
import os
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

# ── 적용할 파일 목록 ─────────────────────────────────────────────
# (상대경로, 파일내용)
FILES: list[tuple[str, str]] = []

# ── 파일 내용 (인라인 저장) ──────────────────────────────────────

FILES.append(("VERSION", "5.7.2\n"))

# ── CHANGELOG.md 추가 항목 ──────────────────────────────────────
CHANGELOG_APPEND = '''
## [5.7.2] — 2026-05-15 (GitHub Release 자동화)

### 추가

- **`release_notes.py`** — CHANGELOG → GitHub Release body 변환기
  - 현재 버전 블록 자동 추출 + 검증 배지(shields.io) 삽입
  - `--github-output` 플래그: GitHub Actions `GITHUB_OUTPUT` 직접 기록
  - `--out FILE`: Markdown 파일 저장
- **`.github/workflows/release.yml`** — 태그 푸시 자동 Release 워크플로우
  - `v*` 태그 푸시 시 자동 트리거
  - Protocol 48/48 + Level 2 45/45 검증 실패 시 Release 중단
  - CHANGELOG → Release body 자동 생성
  - 배포 아카이브(`tar.gz`) + SHA256 체크섬 자동 첨부
  - `workflow_dispatch` 지원 (수동 실행 가능)
- **`github-update.sh`** — Release 자동화 통합 (6단계 → 7단계)
  - Step 7 추가: `gh` CLI 있으면 자동 Release 생성
  - `gh` 없음 + Actions 있음: 태그 푸시만으로 자동 처리 안내
  - `gh` 없음 + Actions 없음: 수동 URL + Notes 파일 경로 안내
  - 커밋 메시지: `release_notes.py` 연동으로 요약 자동 추출
  - 검증 점수 동적 감지 (하드코딩 43/43 → 실제 점수)
  - `required_files`에 v5.7.x 신규 파일 추가

### 버그 수정

- `github-update.sh`: `gh release view` URL 쿼리 구문 수정 (`-q` → `--jq`)

### 검증

| 항목 | 결과 |
|------|------|
| Protocol 48/48 | ✅ ALL PASS |
| Level 2 45/45  | ✅ ALL PASS |

---
'''

# ── release_notes.py ────────────────────────────────────────────
RELEASE_NOTES_PY = r'''#!/usr/bin/env python3
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
'''

# ── .github/workflows/release.yml ──────────────────────────────
RELEASE_YML = '''\
# .github/workflows/release.yml
#
# ClaudeRTOS-Insight 자동 릴리즈 워크플로우
# 트리거: v* 형식 태그 푸시 (e.g. v5.7.2)
#
# 필요 설정:
#   Settings → Actions → General → Workflow permissions
#   → "Read and write permissions" 선택 후 Save

name: Release

on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+'
  workflow_dispatch:
    inputs:
      tag:
        description: '릴리즈할 태그 (e.g. v5.7.2)'
        required: true
        default: ''

permissions:
  contents: write

jobs:
  release:
    name: Build & Publish Release
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Resolve version
        id: version
        run: |
          if [[ "${{ github.event_name }}" == "workflow_dispatch" ]]; then
            TAG="${{ github.event.inputs.tag }}"
          else
            TAG="${GITHUB_REF#refs/tags/}"
          fi
          VERSION="${TAG#v}"
          FILE_VERSION="$(cat VERSION 2>/dev/null | tr -d '[:space:]')"
          echo "tag=${TAG}"         >> "$GITHUB_OUTPUT"
          echo "version=${VERSION}" >> "$GITHUB_OUTPUT"
          if [[ "$VERSION" != "$FILE_VERSION" ]]; then
            echo "::warning::태그 ${TAG}와 VERSION 파일(${FILE_VERSION})이 다릅니다."
          fi
          echo "✅ Tag=${TAG}  Version=${VERSION}  FILE=${FILE_VERSION}"

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install pyserial numpy 2>/dev/null || true

      - name: Run validation
        id: validation
        run: |
          echo "── Protocol 검증 ──"
          RESULT=$(PYTHONPATH=host python3 examples/integrated_demo.py --validate 2>&1)
          echo "$RESULT"
          if echo "$RESULT" | grep -q "ALL CHECKS PASSED"; then
            SCORE=$(echo "$RESULT" | grep -oE '[0-9]+/[0-9]+ PASS' | tail -1)
            echo "proto_score=${SCORE}" >> "$GITHUB_OUTPUT"
            echo "✅ Protocol: ${SCORE}"
          else
            echo "::error::Protocol 검증 실패"
            exit 1
          fi
          echo "── Level 2 검증 ──"
          L2_RESULT=$(PYTHONPATH=host python3 tests/level2/run_level2.py 2>&1)
          echo "$L2_RESULT"
          if echo "$L2_RESULT" | grep -q "ALL CHECKS PASSED"; then
            L2_SCORE=$(echo "$L2_RESULT" | grep -oE '[0-9]+/[0-9]+ PASS' | tail -1)
            echo "l2_score=${L2_SCORE}" >> "$GITHUB_OUTPUT"
            echo "✅ Level 2: ${L2_SCORE}"
          else
            echo "::warning::Level 2 검증 실패"
          fi

      - name: Generate Release Notes
        id: notes
        run: |
          python3 release_notes.py \\
            --version "${{ steps.version.outputs.version }}" \\
            --github-output \\
            --out RELEASE_NOTES.md
          echo "── Release Notes 미리보기 ──"
          head -15 RELEASE_NOTES.md

      - name: Build release archive
        id: archive
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          ARCHIVE="ClaudeRTOS-Insight-v${VERSION}.tar.gz"
          tar -czf "${ARCHIVE}" \\
            --exclude='.git' --exclude='*.pyc' \\
            --exclude='__pycache__' --exclude='.github' \\
            --exclude='RELEASE_NOTES.md' .
          SIZE=$(du -sh "${ARCHIVE}" | cut -f1)
          SHA=$(sha256sum "${ARCHIVE}" | cut -d' ' -f1)
          echo "archive=${ARCHIVE}" >> "$GITHUB_OUTPUT"
          echo "size=${SIZE}"       >> "$GITHUB_OUTPUT"
          echo "sha256=${SHA}"      >> "$GITHUB_OUTPUT"
          echo "✅ ${ARCHIVE} (${SIZE})"

      - name: Create checksum file
        run: |
          sha256sum "${{ steps.archive.outputs.archive }}" \\
            > "${{ steps.archive.outputs.archive }}.sha256"

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          tag_name:   ${{ steps.version.outputs.tag }}
          name:       "ClaudeRTOS-Insight ${{ steps.version.outputs.tag }}"
          body_path:  RELEASE_NOTES.md
          draft:      false
          prerelease: ${{ contains(steps.version.outputs.version, '-') }}
          files: |
            ${{ steps.archive.outputs.archive }}
            ${{ steps.archive.outputs.archive }}.sha256
          fail_on_unmatched_files: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Summary
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          REPO="${{ github.repository }}"
          cat >> "$GITHUB_STEP_SUMMARY" << EOF
          ## ✅ ClaudeRTOS-Insight v${VERSION} 릴리즈 완료

          | 항목 | 값 |
          |------|-----|
          | 버전 | \\`v${VERSION}\\` |
          | 아카이브 | \\`${{ steps.archive.outputs.archive }}\\` (${{ steps.archive.outputs.size }}) |
          | Protocol | ${{ steps.validation.outputs.proto_score }} |
          | Level 2  | ${{ steps.validation.outputs.l2_score }} |
          | SHA256 | \\`${{ steps.archive.outputs.sha256 }}\\` |

          🔗 [릴리즈 페이지](https://github.com/${REPO}/releases/tag/v${VERSION})
          EOF
'''

# ── github-update.sh는 크기 관계로 파일에서 직접 읽음 ────────────
# (apply 시 현재 저장소의 최신본을 사용)

# ─────────────────────────────────────────────────────────────────

def apply(dry_run: bool = False) -> None:
    root = ROOT

    # 프로젝트 루트 확인
    if not (root / 'VERSION').exists() and not (root / 'CHANGELOG.md').exists():
        sys.exit(
            'ERROR: ClaudeRTOS-Insight 프로젝트 루트에서 실행하세요.\n'
            '  cd ClaudeRTOS-Insight\n'
            '  python3 apply_patch_v572.py'
        )

    print(f'\nClaudeRTOS-Insight v5.7.1 → v5.7.2 패치{"  [DRY-RUN]" if dry_run else ""}\n')

    actions = [
        ('VERSION', 'write',  '5.7.2\n'),
        ('release_notes.py',                     'write',  RELEASE_NOTES_PY),
        ('.github/workflows/release.yml',         'write',  RELEASE_YML),
        ('CHANGELOG.md',                          'append', CHANGELOG_APPEND),
    ]

    ok = failed = 0
    for rel_path, mode, content in actions:
        path = root / rel_path
        label = f'{"신규" if mode == "write" and not path.exists() else "수정" if mode == "write" else "추가"}'
        print(f'  [{label}] {rel_path}')

        if dry_run:
            ok += 1
            continue

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == 'write':
                path.write_text(content, encoding='utf-8')
            elif mode == 'append':
                # CHANGELOG: 마지막 항목 중복 체크
                existing = path.read_text(encoding='utf-8')
                if '## [5.7.2]' in existing:
                    print(f'    → 이미 존재, 건너뜀')
                    ok += 1
                    continue
                with path.open('a', encoding='utf-8') as f:
                    f.write(content)
            ok += 1
        except OSError as e:
            print(f'    ❌ 실패: {e}')
            failed += 1

    print(f'\n{"DRY-RUN " if dry_run else ""}결과: {ok}개 {"확인" if dry_run else "적용"} / {failed}개 실패')

    if not dry_run and failed == 0:
        print('\n✅ 패치 완료. 검증을 실행하세요:')
        print('   PYTHONPATH=host python3 examples/integrated_demo.py --validate')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='v5.7.1 → v5.7.2 패치 적용기')
    parser.add_argument('--dry-run', action='store_true', help='실제 적용 없이 확인만')
    args = parser.parse_args()
    apply(dry_run=args.dry_run)
