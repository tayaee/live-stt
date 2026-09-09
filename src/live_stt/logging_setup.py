"""로깅 셋업.

매 실행마다 ``logs/app.log`` 를 새로 만듬 (rotating 으로 백업 없음,
기존 파일 덮어쓰기). 모든 모듈은 ``logging.getLogger(__name__)`` 으로
logger 를 가져와 사용.

콘솔 (stderr) 에도 동시 출력하여 사용자가 라이브로 진행 상황을 볼 수 있게.

Usage
-----
``daemon.main`` 진입점에서 (가장 먼저)::

    from .logging_setup import setup_logging
    setup_logging()
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOGS_DIR = _PROJECT_ROOT / "logs"
_APP_LOG = _LOGS_DIR / "app.log"


_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """루트 logger 셋업. 매 실행 시 호출 (idempotent).

    - ``logs/app.log`` 를 truncate (rotate).
    - 파일 + stderr 동시 출력.
    - 외부 라이브러리 로그 레벨 조정.

    호출 시점: main 진입점 첫 줄 (다른 모듈 import 보다 먼저).
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if _APP_LOG.exists():
        try:
            _APP_LOG.unlink()
        except OSError:
            pass

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = RotatingFileHandler(
        _APP_LOG,
        maxBytes=10 * 1024 * 1024,
        backupCount=0,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    logging.getLogger("live_stt").setLevel(level)

    logging.getLogger(__name__).info(
        "logging initialized → %s (cwd=%s)", _APP_LOG, os.getcwd()
    )


def get_logger(name: str) -> logging.Logger:
    """편의 함수. ``logging.getLogger(name)`` 와 동일."""
    return logging.getLogger(name)
