"""Qt6 (PySide6) 메인 윈도우 + 위/아래 2버퍼 (QSplitter).

tkinter → PySide6 교체 이유 (2026-09)
──────────────────────────────────────
이 환경의 Python 3.11~3.14에서 ``tkinter.Tk().mainloop()`` 진입 시점에
이미 다른 C 확장이 로드되어 있으면 메인 스레드 상태가 NULL이 되는 fatal
``PyEval_RestoreThread`` 가 발생. ``_tkinter`` 자체 결함이 아니라,
tkinter mainloop과 특정 C 확장 (cffi / websockets.speedups / pyaudio /
portaudio 등) 의 비호환성.

Qt의 mainloop은 Tcl/Tk와 완전히 다른 스레딩 모델을 사용하므로 동일 패턴의
crash가 재현되지 않을 가능성이 높음.

스레딩 정책
- 메인 스레드: QApplication mainloop ( ``app.exec()`` )
- 다른 스레드에서 UI 조작: ``schedule(func, *args)`` ( queue-based polling )
- ``QTimer.singleShot(0, callable)`` 은 **호출한 스레드**에서 lambda 를
  실행하므로 cross-thread 용으로 부적합. Signal/QueuedConnection 도 환경에
  따라 조용히 실패해 신뢰 불가 → 사용 안 함.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

# ── 모듈 레벨 QApplication 싱글톤 ────────────────────────────────
_app: Optional[QApplication] = None


def _qapp() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance()  # type: ignore[assignment]
        if _app is None:
            _app = QApplication(sys.argv)
    return _app  # type: ignore[return-value]


# ── 음압 게이지 색상 헬퍼 ────────────────────────────────────────
def _level_color(value: int) -> str:
    """값(0~100)에 따라 녹→황→적 색상 코드를 반환 (QProgressBar 용)."""
    if value < 60:
        return "#4caf50"  # green
    if value < 80:
        return "#ff9800"  # orange
    return "#f44336"      # red


# ── 스레드 간 안전한 UI 갱신 ────────────────────────────────
# PySide6 native Signal/Slot 으로 cross-thread dispatch 가 이 환경에서
# 조용히 실패하는 현상이 관측됨 ( ``[ui-disp]`` 로그도 안 찍힘 ).
# → 더 안정적 패턴: Python ``queue.Queue`` 를 메인 스레드의 QTimer 로 polling.
import queue as _queue

_invoke_queue: "_queue.Queue[tuple]" = _queue.Queue()
_poll_timer: Optional["QTimer"] = None


def _pump_queue() -> None:
    """메인 스레드 컨텍스트에서만 실행됨 ( ``_poll_timer.timeout`` 슬롯)."""
    import threading as _t
    drained = 0
    while True:
        try:
            func, args = _invoke_queue.get_nowait()
        except _queue.Empty:
            break
        try:
            func(*args)
            drained += 1
        except Exception as e:
            import traceback
            logger.exception("schedule error: %s", e)
    if drained:
        logger.debug("drained %d (tid=%s)", drained, _t.get_ident())


def _start_pump_once() -> None:
    """메인 스레드에서 1회만 호출되어 폴링 타이머 시작."""
    global _poll_timer
    if _poll_timer is not None:
        return
    _poll_timer = QTimer()
    _poll_timer.setInterval(33)  # ~30 Hz (UI 갱신 부드러운 정도)
    _poll_timer.timeout.connect(_pump_queue)  # type: ignore[arg-type]
    _poll_timer.start()


def schedule(func: Callable, *args) -> None:
    """어느 스레드에서 호출되어도 main thread 에서 ``func(*args)`` 실행.

    내부적으로 Python ``queue.Queue`` 에 (func, args) 를 put 하고,
    메인 스레드의 QTimer 가 33ms 마다 drain 해서 main thread 컨텍스트에서
    실행. Qt native signal 보다 안정적 ( cross-thread 시그널이 환경에 따라
    조용히 실패하는 케이스 회피 ).
    """
    _invoke_queue.put((func, args))


class MainWindow:
    """PySide6 메인 윈도우. tkinter 버전과 동일한 public API."""

    def __init__(self) -> None:
        qt_app = _qapp()  # mainloop 시작 전에 호출되어도 안전

        # main thread 에서 queue-polling QTimer 시작
        # (이후 어느 스레드에서 ``schedule(...)`` 호출되어도 메인 스레드 컨텍스트에서 실행)
        _start_pump_once()

        self._window = QMainWindow()
        self._window.setWindowTitle("live-stt")
        self._window.resize(800, 600)

        central = QWidget()
        self._window.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ─ 상단 컨트롤 바 ─
        control = QHBoxLayout()
        control.addWidget(QLabel("단축키:"))
        control.addWidget(QLabel("Right Ctrl (시작/종료)"))
        control.addWidget(QLabel("  상태:"))
        self.status_label = QLabel("대기 중")
        control.addWidget(self.status_label)
        control.addStretch()
        root.addLayout(control)

        # ─ 본문: [음압 게이지 | 위/아래 2버퍼] ─
        body = QHBoxLayout()

        # 세로 음압 게이지 (왼쪽)
        self.level_bar = QProgressBar()
        self.level_bar.setOrientation(Qt.Vertical)
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedWidth(28)
        self.level_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #888; background: #222; border-radius: 2px; }"
            "QProgressBar::chunk { background-color: #4caf50; }"
        )
        # 게이지 라벨 (M / 0..100)
        level_col = QVBoxLayout()
        level_col.setContentsMargins(0, 0, 0, 0)
        level_col.setSpacing(2)
        level_col.addWidget(QLabel("🎤"), alignment=Qt.AlignCenter)
        level_col.addWidget(self.level_bar, stretch=1)
        body.addLayout(level_col)

        # 위/아래 2버퍼 (QSplitter, drag 로 분할 변경 가능)
        splitter = QSplitter(Qt.Vertical)

        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setReadOnly(True)
        self.raw_edit.setPlaceholderText("위쪽 — Raw (Gemini Live 실시간)")
        splitter.addWidget(self.raw_edit)

        self.clean_edit = QPlainTextEdit()
        self.clean_edit.setReadOnly(True)
        self.clean_edit.setPlaceholderText("아래쪽 — Clean (gemma-4-31b-it 재작성)")
        splitter.addWidget(self.clean_edit)

        splitter.setSizes([400, 400])
        body.addWidget(splitter, stretch=1)
        root.addLayout(body, stretch=1)

        # ─ 사용법 ─
        usage = QLabel(
            "Right Ctrl: 받아쓰기 시작/종료 (종료 시 Gemma 재작성 후 자동 Paste) | "
            "Ctrl+Z: 마지막 재작성 취소"
        )
        usage.setStyleSheet("color: gray;")
        root.addWidget(usage)

        # ─ 종료 ─
        self.quit_btn = QPushButton("종료")
        self.quit_btn.clicked.connect(self._on_quit)
        root.addWidget(self.quit_btn)

        # ─ 키 단축키 ─
        QShortcut(QKeySequence("Ctrl+Z"), self._window, activated=self.undo_last_clean)
        QShortcut(QKeySequence("Escape"), self._window, activated=self._on_quit)

        self._on_quit_callback: Optional[Callable[[], None]] = None
        self._window.closeEvent = self._on_close_event  # type: ignore[method-assign]

        # UI 요소가 다 만들어진 후에 show (다른 스레드에서 호출되면 안 되지만 안전용)
        self._window.show()
        qt_app.processEvents()  # 즉시 paint 가 적용되도록

    # ── 라이프사이클 ─
    def run(self) -> None:
        """QApplication mainloop 진입 (블로킹). Qt의 표준 진입점."""
        sys.exit(_qapp().exec())

    def quit(self) -> None:
        _qapp().quit()

    # ── 콜백 등록 ─
    def set_quit_callback(self, callback: Callable[[], None]) -> None:
        self._on_quit_callback = callback

    # ── UI 업데이트 (메인 스레드에서 호출되어야 함) ─
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def clear_raw(self) -> None:
        self.raw_edit.clear()

    def append_raw(self, text: str) -> None:
        logger.info("append_raw: %r", text)
        # QPlainTextEdit.appendPlainText 는 항상 새 줄로 시작 → cursor 이동으로 동일 효과
        cursor = self.raw_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.raw_edit.setTextCursor(cursor)
        self.raw_edit.ensureCursorVisible()

    def set_raw(self, text: str) -> None:
        """raw 버퍼 전체 교체. interim transcription 갱신용."""
        logger.info("set_raw: %r", text)
        self.raw_edit.setPlainText(text)
        cursor = self.raw_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.raw_edit.setTextCursor(cursor)
        self.raw_edit.ensureCursorVisible()

    def get_raw(self) -> str:
        return self.raw_edit.toPlainText().rstrip("\n")

    def append_clean(self, text: str) -> None:
        self.clean_edit.appendPlainText(text.rstrip())

    def get_clean(self) -> str:
        return self.clean_edit.toPlainText().rstrip("\n")

    def get_clean_tail(self, n_chars: int) -> str:
        text = self.get_clean()
        return text[-n_chars:] if len(text) > n_chars else text

    def undo_last_clean(self) -> None:
        text = self.clean_edit.toPlainText()
        lines = text.splitlines()
        if len(lines) > 1:
            self.clean_edit.setPlainText("\n".join(lines[:-1]))

    # ── 음압 게이지 (PortAudio 스레드에서 호출) ─
    def set_level(self, pct: int) -> None:
        """음압(0~100) 설정. 메인 스레드에서 호출되어야 함 (schedule 경유 권장)."""
        pct = max(0, min(100, pct))
        # 너무 빈번하면 console 잠수. 5% 단위로 양자화.
        # print는 첫 감지시에만.
        if not hasattr(self, "_last_logged_pct") or abs(pct - self._last_logged_pct) >= 5:
            logger.debug("set_level: %d", pct)
            self._last_logged_pct = pct
        self.level_bar.setValue(pct)
        self.level_bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid #888; background: #222; "
            f"border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {_level_color(pct)}; }}"
        )

    # ── 스레드 안전 ─
    def schedule(self, func: Callable, *args) -> None:
        """다른 스레드에서 호출 가능 — main thread 에서 ``func(*args)`` 실행.

        모듈 레벨 :func:`schedule` 로 위임. 내부적으로 Qt 시그널의
        QueuedConnection 을 사용해 cross-thread 안전.
        ``QTimer.singleShot(0, callable)`` 은 호출 스레드(PortAudio /
        LiveTranscriber / daemon worker) 에서 lambda 가 실행되어 UI 갱신이
        안 되는 버그 → 사용 안 함.
        """
        schedule(func, *args)

    # ── 내부 ─
    def _on_quit(self) -> None:
        if self._on_quit_callback:
            try:
                self._on_quit_callback()
            except Exception:
                pass
        _qapp().quit()

    def _on_close_event(self, event: QCloseEvent) -> None:
        """윈도우 X 버튼 / Alt+F4 등."""
        self._on_quit()
        event.accept()