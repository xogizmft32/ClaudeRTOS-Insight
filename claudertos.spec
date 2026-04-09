# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 스펙 파일 — ClaudeRTOS-Insight Single-file Binary
#
# 빌드:
#   pip install pyinstaller
#   pyinstaller claudertos.spec
#
# 결과:
#   dist/claudertos          ← Linux/macOS 단일 실행 파일
#   dist/claudertos.exe      ← Windows 단일 실행 파일
#
# 특징:
#   - Python 인터프리터 포함 (대상 PC에 Python 불필요)
#   - host/ 소스 전체 번들링
#   - patterns/*.json 포함
#   - 약 40~60MB (Python + 의존성 포함)
#
# 주의:
#   - ANTHROPIC_API_KEY 등 환경 변수는 포함되지 않음
#     → 실행 시 환경 변수로 전달해야 함
#   - Ollama 모델 파일은 포함되지 않음
#   - 생성된 바이너리는 빌드 OS와 동일한 플랫폼에서만 실행됨
#     (Linux → Linux, Windows → Windows)

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 패턴 DB JSON 파일 포함
datas = [
    ('host/patterns/*.json',              'patterns'),
    ('host/patterns/peripheral/*.json',   'patterns/peripheral'),
]

# 분석기 모듈 전체 포함
hiddenimports = (
    collect_submodules('analysis')   +
    collect_submodules('ai')         +
    collect_submodules('parsers')    +
    collect_submodules('patterns')   +
    collect_submodules('local_analyzer') +
    ['numpy', 'anthropic', 'pyserial']
)

a = Analysis(
    ['host/claudertos_main.py'],
    pathex=['host'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'PIL', 'cv2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='claudertos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,   # 단일 파일로 패킹
)
