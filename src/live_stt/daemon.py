"""live-stt 데몬 진입점.

전체 라이프사이클:
- 메인 스레드: tkinter mainloop (UI) + WH_KEYBOARD_LL hook 콜백
- LiveTranscriber 전용 스레드: asyncio 이벤트 루프 + Gemini Live 송수신
- sounddevice 콜백 스레드: 마이크 청크 전달

아키텍처 결정 (2026-09)
────────────────────────
이전 버전에서는 데몬의 공유 asyncio 루프에서 Live 코루틴을 돌렸음. 그런데
Python 3.14의 ProactorEventLoop는 foreign-thread에서 ``_call_soon`` →
``PyErr_CheckSignals`` 경로가 호출되면 fatal
``PyEval_RestoreThread: ... but the GIL is released (the current Python
thread state is NULL)`` 을 띄우며 프로세스가 죽음 (GitHub 138244 참조).

해결: Live 세션마다 **전용 스레드 + 전용 asyncio 루프**를 생성. 데몬은 더
이상 공유 asyncio 루프를 사용하지 않으므로 foreign-thread-close로 인한
Proactor 버그 경로가 사라짐. 메인 스레드는 오직 tkinter mainloop만 돌림.

추가 결정 (2026-09, startup crash)
-----------------------------------
4개 Python 버전 모두 tkinter mainloop 진입 시점에 동일 fatal crash. 원인:
``_cffi_backend`` 와 ``websockets.speedups`` C 확장이 startup 시점에
로드되어 있으면 main thread state를 깨먹음. 두 확장은 google.genai → cffi /
websockets 경로로 로드됨.

해결: **google.genai 의존성(live.py, rewrite.py) 모듈 레벨 import 제거**.
Right Ctrl (Live 시작 / Rewrite 종료) 시점에 lazy import. 이로써
startup 시점에 cffi/websockets.speedups가 로드되지 않음 → mainloop
안정 진입.
"""
import argparse
import logging
import sys
import threading
from typing import Optional, Sequence

from . import config
from .audio import AudioStream
from .inject import clear_ime, send_text
from .keypool import init_pool
from .target import TargetApp
from .trigger import TriggerHook
from .ui import MainWindow

logger = logging.getLogger(__name__)


class Daemon:
    """live-stt 데몬: UI + 단축키 hook + Gemini Live + 재작성 + Paste.

    google.genai 의존 모듈(live, rewrite)은 ``__init__`` 시점에 import 하지
    않음 → cffi / websockets.speedups C 확장이 tkinter mainloop 진입 전에
    로드되는 것을 막아 fatal ``PyEval_RestoreThread`` 회피.
    """

    def __init__(self) -> None:
        self.ui = MainWindow()
        self.target = TargetApp()

        self.audio = AudioStream(
            on_chunk=self._on_audio_chunk,
            on_level=self._on_audio_level,
        )
        self.trigger = TriggerHook(
            on_ctrl=self._on_ctrl_pressed,
        )

        self.is_listening = False
        self.transcriber: Optional["LiveTranscriber"] = None
        self.rewriter: Optional["Rewriter"] = None

        self._committed_texts: list[str] = []
        self._current_interim: str = ""

        self._last_rewritten_raw: str = ""
        self._last_rewritten_clean: str = ""
        self._rewrite_timer: Optional[threading.Timer] = None
        self._rewrite_lock = threading.Lock()
        self._is_rewriting: bool = False

        self.ui.set_quit_callback(self._shutdown)

    def start(self) -> None:
        """전체 데몬 시작. 메인 스레드에서 호출.

        이 시점에 google.genai는 아직 import되지 않음 → cffi/websockets.speedups
        미로드 상태에서 tkinter mainloop 진입.
        """
        try:
            self.trigger.install()
        except RuntimeError as e:
            logger.error("Hook Error: %s", e)
            self.ui.set_status(f"⚠ Hook 실패: {e}")

        self.ui.set_status("대기 중 — Right Ctrl로 시작")
        self.ui.run()

    def _shutdown(self) -> None:
        """데몬 종료."""
        self._cancel_rewrite_timer()
        try:
            self.trigger.uninstall()
        except Exception:
            pass

        if self.is_listening:
            try:
                self.audio.stop()
            except Exception:
                pass
            if self.transcriber:
                try:
                    self.transcriber.stop()
                except Exception:
                    pass

    def _on_audio_chunk(self, chunk: bytes) -> None:
        if self.transcriber:
            self.transcriber.enqueue_audio(chunk)

    def _on_audio_level(self, pct: int) -> None:
        """PortAudio 콜백 스레드에서 호출 → 메인 스레드로 UI 갱신 예약."""
        self.ui.schedule(self.ui.set_level, pct)

    def _on_ctrl_pressed(self) -> None:
        """Right Ctrl — 토글 (hook 콜백, 메인 스레드)."""
        self.ui.schedule(self._toggle_listening)

    def _get_current_raw_text(self) -> str:
        """확정된 문장들 + 현재 발화 중인 interim 문장을 합친 전체 텍스트 반환."""
        parts = list(self._committed_texts)
        if self._current_interim.strip():
            parts.append(self._current_interim.strip())
        return "\n".join(parts)

    def _on_live_text(self, text: str) -> None:
        """Gemini Live 확정 텍스트 도착 (final input_transcription)."""
        logger.info("_on_live_text (final): %r", text)
        if not text:
            return
        self._committed_texts.append(text.strip())
        self._current_interim = ""
        full_text = self._get_current_raw_text()
        self.ui.schedule(self.ui.set_raw, full_text)

        self._reset_rewrite_timer()

    def _on_live_interim(self, text: str) -> None:
        """interim transcription (저지연 부분 결과)."""
        logger.info("_on_live_interim: %r", text)
        self._current_interim = text
        full_text = self._get_current_raw_text()
        self.ui.schedule(self.ui.set_raw, full_text)

        self._reset_rewrite_timer()

    def _reset_rewrite_timer(self) -> None:
        """Reset the silence-detection rewrite timer."""
        if not self.is_listening:
            return
        if self._rewrite_timer is not None:
            self._rewrite_timer.cancel()
        self._rewrite_timer = threading.Timer(
            config.SILENCE_REWRITE_DELAY_SEC,
            self._on_silence_delay,
        )
        self._rewrite_timer.daemon = True
        self._rewrite_timer.start()

    def _cancel_rewrite_timer(self) -> None:
        """침묵 타이머 취소."""
        if self._rewrite_timer is not None:
            self._rewrite_timer.cancel()
            self._rewrite_timer = None

    def _on_silence_delay(self) -> None:
        """발화 후 일정 시간(침묵) 경과 시 위쪽 전체 텍스트를 Gemma로 재작성."""
        if not self.is_listening:
            return
        raw_all = self._get_current_raw_text().strip()
        if not raw_all:
            return
        if raw_all == self._last_rewritten_raw or self._is_rewriting:
            return

        logger.info(
            "Silence detected (%.1fs) -> Triggering periodic Gemma rewrite (%d chars)",
            config.SILENCE_REWRITE_DELAY_SEC,
            len(raw_all),
        )
        threading.Thread(
            target=self._run_periodic_rewrite,
            args=(raw_all,),
            daemon=True,
            name="GemmaPeriodicRewrite",
        ).start()

    def _run_periodic_rewrite(self, raw_text: str) -> None:
        """백그라운드에서 위 텍스트 전체를 Gemma에게 보내 아래 버퍼 갱신."""
        with self._rewrite_lock:
            if raw_text == self._last_rewritten_raw:
                return
            self._is_rewriting = True
        try:
            self.ui.schedule(self.ui.set_status, "✍️ Gemma 틈틈이 재작성 중...")
            from .rewrite import Rewriter
            if self.rewriter is None:
                self.rewriter = Rewriter()

            rewritten = self.rewriter.rewrite(raw_text)
            if rewritten:
                self._last_rewritten_clean = rewritten
                self._last_rewritten_raw = raw_text
                self.ui.schedule(self.ui.set_clean, rewritten)
                logger.info("Periodic Gemma rewrite completed (%d chars)", len(rewritten))
        except Exception as e:
            logger.warning("Periodic Gemma rewrite failed: %s", e)
        finally:
            self._is_rewriting = False
            if self.is_listening:
                self.ui.schedule(self.ui.set_status, "🔴 받아쓰기 진행 중")

    def _toggle_listening(self) -> None:
        logger.info("toggle_listening (was listening=%s)", self.is_listening)
        if self.is_listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        logger.info("_start_listening")
        if not self.target.capture():
            self.ui.set_status("⚠ 활성 윈도우 캡처 실패")
            return

        self._committed_texts.clear()
        self._current_interim = ""
        self._last_rewritten_raw = ""
        self._last_rewritten_clean = ""
        self._cancel_rewrite_timer()

        self.ui.clear_raw()
        self.ui.clear_clean()
        self.audio.start()
        self.is_listening = True
        self.ui.set_status("🔴 받아쓰기 진행 중")

        def _init_session() -> None:
            logger.info("_init_session thread start")
            try:
                from .live import LiveTranscriber  # lazy
                tr = LiveTranscriber(
                    on_text=self._on_live_text,
                    on_interim=self._on_live_interim,
                )
                logger.info("LiveTranscriber created, calling start()...")
                tr.start(ready_timeout=10.0)
                logger.info("LiveTranscriber ready (session open)")
                self.transcriber = tr
            except BaseException as e:
                logger.exception("[Live Error] %s: %s", type(e).__name__, e)
                self.ui.schedule(
                    self.ui.set_status, f"⚠ Gemini Live 실패: {type(e).__name__}"
                )
                try:
                    self.audio.stop()
                except Exception:
                    pass
                self.is_listening = False

        threading.Thread(target=_init_session, daemon=True, name="LiveInit").start()

    def _stop_listening(self) -> None:
        logger.info("_stop_listening: stopping dictation")
        if not self.is_listening:
            return

        self.is_listening = False
        self._cancel_rewrite_timer()

        try:
            self.audio.stop()
        except Exception:
            pass

        if self.transcriber:
            logger.info("transcriber.stop()")
            try:
                self.transcriber.stop()
            except Exception:
                pass
        self.transcriber = None

        if self._current_interim.strip():
            self._committed_texts.append(self._current_interim.strip())
            self._current_interim = ""

        raw_all = self._get_current_raw_text().strip()
        self.ui.schedule(self.ui.set_raw, raw_all)
        logger.info("dictation stopped, total transcribed raw text: %r", raw_all)

        if not raw_all:
            self.ui.set_status("⏹️ 받아쓰기 종료 — 대기 중 (인식된 텍스트 없음)")
            return

        if raw_all == self._last_rewritten_raw and self._last_rewritten_clean:
            logger.info(
                "Already rewritten by periodic task, pasting immediately (%d chars)",
                len(self._last_rewritten_clean),
            )
            self._paste_text(self._last_rewritten_clean)
            return

        self.ui.set_status("✍️ Gemma 최종 재작성 중...")

        def _final_rewrite_and_paste() -> None:
            while self._is_rewriting:
                import time
                time.sleep(0.1)

            if raw_all == self._last_rewritten_raw and self._last_rewritten_clean:
                self._paste_text(self._last_rewritten_clean)
                return

            try:
                from .rewrite import Rewriter
                if self.rewriter is None:
                    self.rewriter = Rewriter()

                logger.info(
                    "Sending total transcribed text (%d chars) to Gemma for final rewrite",
                    len(raw_all),
                )
                rewritten = self.rewriter.rewrite(raw_all)

                if rewritten:
                    self._last_rewritten_clean = rewritten
                    self._last_rewritten_raw = raw_all
                    self.ui.schedule(self.ui.set_clean, rewritten)
                    self._paste_text(rewritten)
                else:
                    self.ui.schedule(self.ui.set_status, "⚠ 재작성 결과 비어있음 — 대기 중")
            except Exception as e:
                logger.exception("[Final Rewrite Error] %s", e)
                self.ui.schedule(self.ui.set_status, f"⚠ 재작성 실패: {e}")

        threading.Thread(target=_final_rewrite_and_paste, daemon=True, name="GemmaFinalRewrite").start()

    def _paste_text(self, text: str) -> None:
        """타겟 애플리케이션 창으로 텍스트 자동 붙여넣기."""
        if self.target.is_valid():
            self.ui.schedule(self.ui.set_status, "📤 Paste 중...")
            self.target.restore_focus()
            clear_ime(self.target.hwnd)
            send_text(text)
            self.ui.schedule(self.ui.set_status, "✅ 완료 — 대기 중")
            logger.info("Paste completed: %r", text)
        else:
            self.ui.schedule(self.ui.set_status, "✅ 재작성 완료 (타겟 윈도우 없음) — 대기 중")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI 인자 파싱."""
    parser = argparse.ArgumentParser(
        prog="live-stt",
        description="Windows 음성 받아쓰기 데몬 (Right Ctrl 시작/종료, 종료 시 Gemma 재작성 후 자동 입력)",
    )
    parser.add_argument(
        "--gemini-api-keys",
        dest="gemini_api_keys",
        default=None,
        metavar="CSV",
        help=(
            "Gemini API 키 (콤마 구분 CSV). 1순위. "
            "예: --gemini-api-keys 'k1,k2,k3'. "
            "생략 시 ./gemini-api-keys.txt → ~/.config/google-ai/gemini-api-keys.txt 순서로 로딩."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """진입점."""
    from .logging_setup import setup_logging
    setup_logging()

    args = _parse_args(argv)
    logger.info("live-stt starting (argv=%s)", sys.argv)

    init_pool(cli_csv=args.gemini_api_keys)

    daemon = Daemon()
    daemon.start()


if __name__ == "__main__":
    main()