#!/bin/bash
# build_binary.sh — ClaudeRTOS-Insight Single-file Binary 빌드
#
# 사용:
#   ./build_binary.sh              # 현재 OS용 바이너리
#   ./build_binary.sh --docker     # Docker로 Linux 바이너리
#
# 결과: dist/claudertos (또는 dist/claudertos.exe)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 버전 단일 진실 공급원: README.md 배지 (make_patch.sh와 동일) ──
VERSION=$(cat VERSION 2>/dev/null | tr -d '[:space:]')
if [[ -z "$VERSION" ]]; then
  echo "ERROR: README.md 에서 버전 배지를 찾을 수 없습니다." >&2
  echo "  VERSION 파일에 X.Y.Z 형식으로 버전을 입력하세요." >&2
  exit 1
fi
echo "=== ClaudeRTOS-Insight v${VERSION} Binary Build ==="

USE_DOCKER=0
for arg in "$@"; do
  if [[ "$arg" == "--docker" ]]; then USE_DOCKER=1; fi
done

if [[ $USE_DOCKER -eq 1 ]]; then
  echo "→ Docker 기반 Linux 바이너리 빌드"
  docker run --rm \
    -v "$SCRIPT_DIR":/app \
    -w /app \
    python:3.11-slim \
    bash -c "
      pip install pyinstaller anthropic pyserial numpy --quiet
      pyinstaller claudertos.spec --distpath dist/linux --workpath build/linux
      echo '✅ Linux 바이너리: dist/linux/claudertos'
    "
else
  echo "→ 로컬 빌드 ($(uname -s))"

  # PyInstaller 설치 확인
  if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller 설치 중..."
    pip install pyinstaller --quiet
  fi

  # 빌드
  python3 -m PyInstaller claudertos.spec \
    --distpath dist \
    --workpath build \
    --clean \
    --noconfirm

  BINARY="dist/claudertos"
  if [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "win32" ]]; then
    BINARY="dist/claudertos.exe"
  fi

  if [[ -f "$BINARY" ]]; then
    SIZE=$(du -h "$BINARY" | cut -f1)
    echo "✅ 바이너리: $BINARY ($SIZE)"
    echo ""
    echo "검증:"
    "$BINARY" --validate
  else
    echo "❌ 빌드 실패"
    exit 1
  fi
fi

echo ""
echo "=== 배포 방법 ==="
echo "  Linux/macOS : dist/claudertos 파일 하나만 전달"
echo "  Windows     : dist/claudertos.exe 파일 하나만 전달"
echo "  실행 예:    ./claudertos --port jlink"
