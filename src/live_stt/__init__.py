"""live-stt 패키지.

호환성 안전장치
---------------
google-genai SDK가 사용하는 ``websockets.speedups`` (C 확장)는 Windows
Python 3.11~3.14에서 tkinter mainloop와 충돌하는 사례가 관찰됨. 패키지
import 가장 이른 시점에 차단하여 pure-Python fallback 으로 강제.

이 모듈은 google.genai 보다 먼저 로드되어야 함 (daemon.py / live.py 등
에서 google.genai import 전).
"""
from __future__ import annotations

import sys

# websockets.speedups C 확장 비활성화 — Tk mainloop 진입 시
# PyEval_RestoreThread (NULL state) fatal 에러를 유발하는 사례 있음.
# 이 라인 이전에 websockets가 import 되지 않아야 함.
sys.modules.setdefault("websockets.speedups", None)  # type: ignore[arg-type]
