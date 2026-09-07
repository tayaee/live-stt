# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "google-genai",
#     "sounddevice",
#     "numpy",
# ]
# ///
"""live-stt: Windows 음성 받아쓰기 데몬 (개발용 단독 실행 스크립트).

설치된 ``live-stt`` 바이너리(``uv tool install -e .``)와 충돌을 피하기 위해
이름을 ``dev-launcher.py``로 변경했습니다. CWD에 ``live-stt.py``가 있으면
Windows가 PATH의 ``live-stt.exe`` 대신 .py를 우선 실행하는 문제 회피.

실행::

    uv run dev-launcher.py [--gemini-api-keys CSV]

CLI 옵션은 ``live_stt.daemon.main``과 동일.
"""
import argparse
import sys
from pathlib import Path
from typing import Sequence


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="live-stt",
        description="Windows 음성 받아쓰기 데몬 (단독 실행)",
    )
    parser.add_argument(
        "--gemini-api-keys",
        dest="gemini_api_keys",
        default=None,
        metavar="CSV",
        help="Gemini API 키 (콤마 구분 CSV). 1순위.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    # live_stt 패키지가 import 경로에 있어야 함
    # (uv run은 PEP 723 deps만 격리, src/live_stt는 PYTHONPATH로 노출 가정)
    try:
        from live_stt.keypool import init_pool
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from live_stt.keypool import init_pool

    init_pool(cli_csv=args.gemini_api_keys)

    from live_stt.daemon import Daemon

    Daemon().start()


if __name__ == "__main__":
    main()