#!/usr/bin/env python3
"""
claudertos_main.py — ClaudeRTOS-Insight CLI 진입점

PyInstaller 또는 직접 실행:
    python3 host/claudertos_main.py --help
    python3 host/claudertos_main.py --validate
    python3 host/claudertos_main.py --port jlink
    python3 host/claudertos_main.py --port uart:/dev/ttyUSB0
    python3 host/claudertos_main.py --ai-mode offline --port jlink
"""

import sys
import os

# PyInstaller 실행 시 패키지 경로 조정
if getattr(sys, 'frozen', False):
    # PyInstaller 번들 실행
    _base = sys._MEIPASS
    sys.path.insert(0, _base)
else:
    # 개발 환경 실행
    _base = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _base)

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog='claudertos',
        description='ClaudeRTOS-Insight — AI-Assisted FreeRTOS Debugger',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  claudertos --validate                      # 환경 검증 (하드웨어 불필요)
  claudertos --port jlink                    # J-Link ITM 연결
  claudertos --port uart:/dev/ttyUSB0        # UART 연결
  claudertos --ai-mode offline --port jlink  # AI 없이 로컬 분석만
  claudertos --profile LITE --port jlink     # 저사양 프로파일

Environment Variables:
  ANTHROPIC_API_KEY       Claude API 키
  CLAUDERTOS_AI_PROVIDER  anthropic|openai|google|ollama
  CLAUDERTOS_MASK_LEVEL   none|names|addresses|strict
  CLAUDERTOS_SECRETS_FILE 민감 정보 목록 JSON 경로
        """)

    parser.add_argument('--validate',
        action='store_true',
        help='환경 검증 실행 (하드웨어 불필요)')
    parser.add_argument('--port',
        default=None,
        metavar='PORT',
        help='연결 포트: jlink | uart:/dev/ttyUSB0 | uart:COM3')
    parser.add_argument('--ai-mode',
        choices=['offline','postmortem','realtime'],
        default='postmortem',
        help='AI 분석 모드 (기본: postmortem)')
    parser.add_argument('--profile',
        choices=['LITE','STANDARD','EXPERT'],
        default='STANDARD',
        help='디버깅 프로파일 (기본: STANDARD)')
    parser.add_argument('--provider',
        choices=['anthropic','openai','google','ollama'],
        default=None,
        help='AI Provider (기본: CLAUDERTOS_AI_PROVIDER 환경 변수)')
    parser.add_argument('--mask-level',
        choices=['none','names','addresses','strict'],
        default=None,
        help='민감 정보 마스킹 수준')
    parser.add_argument('--log-dir',
        default='logs',
        help='세션 로그 저장 디렉터리 (기본: logs/)')
    parser.add_argument('--report',
        default=None,
        metavar='PATH',
        help='분석 보고서 저장 경로 (.md)')
    parser.add_argument('--version',
        action='version',
        version='ClaudeRTOS-Insight 4.9.0')

    args = parser.parse_args()

    # 환경 변수 적용
    if args.provider:
        os.environ['CLAUDERTOS_AI_PROVIDER'] = args.provider
    if args.mask_level:
        os.environ['CLAUDERTOS_MASK_LEVEL'] = args.mask_level

    if args.validate:
        _run_validate()
    elif args.port:
        _run_debug(args)
    else:
        parser.print_help()
        sys.exit(0)


def _run_validate():
    """환경 검증 — integrated_demo.py --validate 동작."""
    print("ClaudeRTOS-Insight — 환경 검증\n")
    try:
        from examples import integrated_demo
        integrated_demo.run_validation()
    except ImportError:
        # 직접 검증
        import subprocess
        result = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__),
                          '..', 'examples', 'integrated_demo.py'),
             '--validate', '--simulate-switch'],
            capture_output=False)
        sys.exit(result.returncode)


def _run_debug(args):
    """실제 디버깅 세션 실행."""
    print(f"ClaudeRTOS-Insight — 디버깅 시작")
    print(f"  포트: {args.port}")
    print(f"  AI 모드: {args.ai_mode}")
    print(f"  프로파일: {args.profile}")
    print(f"  로그: {args.log_dir}/\n")

    try:
        from analysis.debugger_context import init_session_analyzers
        from analysis.session_logger   import SessionLogger
        from analysis.debug_report     import DebugReportGenerator

        init_session_analyzers()

        logger   = SessionLogger(log_dir=args.log_dir)
        reporter = DebugReportGenerator(profile=args.profile)

        logger.start()
        print("세션 시작. Ctrl+C로 종료.\n")

        # 포트 연결 및 수신 루프 (실제 구현은 integrated_demo.py 참조)
        print("→ 연결 중...")
        print("→ 호스트 분석 파이프라인 초기화 완료")
        print(f"→ AI: {os.environ.get('CLAUDERTOS_AI_PROVIDER','anthropic')}")
        print("\n실제 하드웨어 수신 루프는 examples/integrated_demo.py 참조")

    except KeyboardInterrupt:
        print("\n\n세션 종료.")
        summary = logger.stop()
        if args.report:
            reporter.save(args.report)
            print(f"보고서 저장: {args.report}")
        print(f"이슈: {summary.issue_count}개, Critical: {summary.critical_count}개")


if __name__ == '__main__':
    main()
