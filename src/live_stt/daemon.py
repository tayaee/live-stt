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
Right Ctrl (Live), Right Shift (Rewrite) 시점에 lazy import. 이로써
startup 시점에 cffi/websockets.speedups가 로드되지 않음 → tkinter mainloop
안정 진입.
"""
import argparse
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
            on_shift=self._on_shift_pressed,
        )

        self.is_listening = False
        self.transcriber: Optional["LiveTranscriber"] = None
        self.rewriter: Optional["Rewriter"] = None  # lazy
        self._live_interim = False  # 이전 응답이 interim 이었는지

        self.ui.set_quit_callback(self._shutdown)

    # ── 라이프사이클 ─
    def start(self) -> None:
        """전체 데몬 시작. 메인 스레드에서 호출.

        이 시점에 google.genai는 아직 import되지 않음 → cffi/websockets.speedups
        미로드 상태에서 tkinter mainloop 진입.
        """
        # 1) 키보드 hook 설치 (메인 스레드)
        try:
            self.trigger.install()
        except RuntimeError as e:
            print(f"[Hook Error] {e}", file=sys.stderr)
            self.ui.set_status(f"⚠ Hook 실패: {e}")
            # 그래도 UI는 살아있게 (사용자가 직접 종료 가능)

        # 2) UI 시작 (메인 스레드 블로킹). 종료 시 _shutdown 자동 호출.
        self.ui.set_status("대기 중 — Right Ctrl로 시작")
        self.ui.run()

    def _shutdown(self) -> None:
        """데몬 종료."""
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

    # ── 콜백 (hook / sounddevice / LiveTranscriber 스레드에서 호출) ─
    def _on_audio_chunk(self, chunk: bytes) -> None:
        """sounddevice 콜백 스레드에서 호출."""
        if self.transcriber:
            self.transcriber.enqueue_audio(chunk)

    def _on_audio_level(self, pct: int) -> None:
        """PortAudio 콜백 스레드에서 호출 → 메인 스레드로 UI 갱신 예약."""
        self.ui.schedule(self.ui.set_level, pct)

    def _on_ctrl_pressed(self) -> None:
        """Right Ctrl — 토글 (hook 콜백, 메인 스레드)."""
        # mainloop 안에서 호출되지만 안전을 위해 schedule
        self.ui.schedule(self._toggle_listening)

    def _on_shift_pressed(self) -> None:
        """Right Shift — 재작성 (hook 콜백, 메인 스레드)."""
        if not self.is_listening:
            return
        self.ui.schedule(self._do_rewrite)

    def _on_live_text(self, text: str) -> None:
        """Gemini Live 텍스트 도착 (LiveTranscriber 전용 스레드).

        interim 인 경우 UI 의 raw 버퍼 전체 교체 (모델이 best-guess 를
        계속 갱신). final 인 경우 newline 과 함께 append (lock-in).
        """
        print(
            f"[daemon] _on_live_text: {text!r} (interim_flag={self._live_interim})",
            flush=True,
        )
        if not text:
            return
        if getattr(self, "_live_interim", False):
            # interim: 버퍼 OVERWRITE
            self.ui.schedule(self.ui.set_raw, text)
        else:
            # final: newline 으로 lock-in
            self.ui.schedule(self.ui.append_raw, text + "\n")

    def _on_live_interim(self, text: str) -> None:
        """interim transcription (저지연 부분 결과) → flag ON."""
        print(f"[daemon] _on_live_interim: {text!r}", flush=True)
        self._live_interim = True
        self.ui.schedule(self.ui.set_raw, text)

    # ── 메인 로직 (메인 스레드에서 실행) ─
    def _toggle_listening(self) -> None:
        print(f"[daemon] toggle_listening (was listening={self.is_listening})", flush=True)
        if self.is_listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        print("[daemon] _start_listening", flush=True)
        if not self.target.capture():
            self.ui.set_status("⚠ 활성 윈도우 캡처 실패")
            return

        self.ui.clear_raw()
        self.audio.start()
        self.is_listening = True
        self.ui.set_status("🔴 받아쓰기 진행 중")

        # LiveTranscriber는 자체 스레드/루프에서 동작.
        # Lazy import: live.py → google.genai → cffi (이 시점에 처음 로드됨).
        # 이미 tkinter mainloop는 통과한 상태이므로 안전.
        def _init_session() -> None:
            print("[daemon] _init_session thread start", flush=True)
            try:
                from .live import LiveTranscriber  # lazy
                tr = LiveTranscriber(
                    on_text=self._on_live_text,
                    on_interim=self._on_live_interim,
                )
                print("[daemon] LiveTranscriber created, calling start()...", flush=True)
                tr.start(ready_timeout=10.0)  # 동기: ready 이벤트까지 대기
                print("[daemon] LiveTranscriber ready (session open)", flush=True)
                self.transcriber = tr
                self._live_interim = False
            except BaseException as e:  # 넓게 잡아 데몬은 살려둠
                print(f"[daemon] [Live Error] {type(e).__name__}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                self.ui.schedule(
                    self.ui.set_status, f"⚠ Gemini Live 실패: {type(e).__name__}"
                )
                # 실패 시 상태 복구
                try:
                    self.audio.stop()
                except Exception:
                    pass
                self.is_listening = False

        threading.Thread(target=_init_session, daemon=True, name="LiveInit").start()

    def _stop_listening(self) -> None:
        print("[daemon] _stop_listening", flush=True)
        if not self.is_listening:
            return

        # 1) 마이크 정리
        try:
            self.audio.stop()
        except Exception:
            pass

        # 2) Gemini Live 종료 (전용 스레드의 루프를 정리)
        if self.transcriber:
            print("[daemon] transcriber.stop()", flush=True)
            try:
                self.transcriber.stop()
            except Exception:
                pass
        self.transcriber = None

        # 3) Paste (clean 버퍼 전체)
        clean = self.ui.get_clean()
        if clean and self.target.is_valid():
            self.ui.set_status("📤 Paste 중...")
            self.target.restore_focus()
            clear_ime(self.target.hwnd)
            send_text(clean)
            self.ui.set_status("✅ Paste 완료 — 대기 중")
        else:
            self.ui.set_status("⏹️ 받아쓰기 종료 — 대기 중")

        self.is_listening = False

    def _do_rewrite(self) -> None:
        raw = self.ui.get_raw()
        if not raw.strip():
            return
        clean_tail = self.ui.get_clean_tail(config.REWRITE_CONTEXT_TAIL)

        self.ui.set_status("✍️ 재작성 중...")

        # Lazy import: rewrite.py → google.genai → cffi (이 시점에 로드).
        def _do() -> None:
            try:
                from .rewrite import Rewriter  # lazy
                if self.rewriter is None:
                    self.rewriter = Rewriter()
                rewritten = self.rewriter.rewrite(raw, clean_tail)
                if rewritten:
                    self.ui.schedule(self.ui.append_clean, rewritten)
                    self.ui.schedule(self.ui.set_status, "🔴 받아쓰기 진행 중")
                else:
                    self.ui.schedule(self.ui.set_status, "⚠ 재작성 결과 비어있음")
            except Exception as e:
                print(f"[Rewrite Error] {e}", file=sys.stderr)
                self.ui.schedule(self.ui.set_status, f"⚠ 재작성 실패: {e}")

        threading.Thread(target=_do, daemon=True).start()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI 인자 파싱."""
    parser = argparse.ArgumentParser(
        prog="live-stt",
        description="Windows 음성 받아쓰기 데몬 (Right Ctrl 시작/종료, Right Shift 재작성)",
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
    args = _parse_args(argv)

    # 키 풀 초기화 (CLI 옵션 반영). 실패 시 여기서 예외로 종료.
    init_pool(cli_csv=args.gemini_api_keys)

    daemon = Daemon()
    daemon.start()


if __name__ == "__main__":
    main()