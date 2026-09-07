"""live-stt 데몬 진입점.

전체 라이프사이클:
- 메인 스레드: tkinter mainloop (UI) + WH_KEYBOARD_LL hook 콜백
- 백그라운드 스레드: asyncio 이벤트 루프 (Gemini Live 송수신)
- sounddevice 콜백 스레드: 마이크 청크 전달

흐름:
1. Right Alt 1번째 → hwnd 캡처 + 마이크 ON + Gemini Live 시작 + 위쪽 버퍼 클리어
2. Gemini Live 텍스트 청크 도착 → 위쪽 Text 위젯에 append
3. Right Shift → 위쪽 raw + 아래쪽 마지막 N자 → Gemini 4 31b IT 재작성 → 아래쪽 append
4. Right Alt 2번째 → 마이크 OFF + Gemini Live 종료 + 아래쪽 clean → SendInput으로 Paste
"""
import sys
import threading
import asyncio
from typing import Optional

from . import config
from .audio import AudioStream
from .inject import clear_ime, send_text
from .live import LiveTranscriber
from .rewrite import Rewriter
from .target import TargetApp
from .trigger import TriggerHook
from .ui import MainWindow


class Daemon:
    """live-stt 데몬: UI + 단축키 hook + Gemini Live + 재작성 + Paste."""

    def __init__(self) -> None:
        self.ui = MainWindow()
        self.target = TargetApp()
        self.rewriter = Rewriter()

        self.audio = AudioStream(on_chunk=self._on_audio_chunk)
        self.trigger = TriggerHook(
            on_alt=self._on_alt_pressed,
            on_shift=self._on_shift_pressed,
        )

        self.is_listening = False
        self.transcriber: Optional[LiveTranscriber] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._asyncio_thread: Optional[threading.Thread] = None

        self.ui.set_quit_callback(self._shutdown)

    # ── 라이프사이클 ─
    def start(self) -> None:
        """전체 데몬 시작. 메인 스레드에서 호출."""
        # 1) asyncio 백그라운드 스레드 시작
        self.loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self._asyncio_thread = threading.Thread(target=_run_loop, daemon=True)
        self._asyncio_thread.start()

        # 2) 키보드 hook 설치 (메인 스레드)
        try:
            self.trigger.install()
        except RuntimeError as e:
            print(f"[Hook Error] {e}", file=sys.stderr)
            self.ui.set_status(f"⚠ Hook 실패: {e}")
            # 그래도 UI는 살아있게 (사용자가 직접 종료 가능)

        # 3) UI 시작 (메인 스레드 블로킹)
        self.ui.set_status("대기 중 — Right Alt로 시작")
        try:
            self.ui.run()
        finally:
            self._shutdown()

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
            if self.transcriber and self.loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.transcriber.stop(), self.loop
                    ).result(timeout=2.0)
                except Exception:
                    pass

        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass

    # ── 콜백 (hook / sounddevice / asyncio에서 호출) ─
    def _on_audio_chunk(self, chunk: bytes) -> None:
        """sounddevice 콜백 스레드에서 호출."""
        if self.transcriber:
            self.transcriber.enqueue_audio(chunk)

    def _on_alt_pressed(self) -> None:
        """Right Alt — 토글 (hook 콜백, 메인 스레드)."""
        # mainloop 안에서 호출되지만 안전을 위해 schedule
        self.ui.schedule(self._toggle_listening)

    def _on_shift_pressed(self) -> None:
        """Right Shift — 재작성 (hook 콜백, 메인 스레드)."""
        if not self.is_listening:
            return
        self.ui.schedule(self._do_rewrite)

    def _on_live_text(self, text: str) -> None:
        """Gemini Live 텍스트 도착 (asyncio 스레드)."""
        self.ui.schedule(self.ui.append_raw, text)

    # ── 메인 로직 (메인 스레드에서 실행) ─
    def _toggle_listening(self) -> None:
        if self.is_listening:
            self._stop_listening()
        else:
            self._start_listening()

    def _start_listening(self) -> None:
        if not self.target.capture():
            self.ui.set_status("⚠ 활성 윈도우 캡처 실패")
            return

        self.ui.clear_raw()
        self.audio.start()
        self.is_listening = True
        self.ui.set_status("🔴 받아쓰기 진행 중")

        # Gemini Live 시작 (백그라운드)
        async def _init() -> None:
            self.transcriber = LiveTranscriber(on_text=self._on_live_text)
            try:
                await self.transcriber.start()
            except Exception as e:
                print(f"[Live Error] {e}", file=sys.stderr)
                self.ui.schedule(self.ui.set_status, f"⚠ Gemini Live 실패: {e}")

        if self.loop:
            asyncio.run_coroutine_threadsafe(_init(), self.loop)

    def _stop_listening(self) -> None:
        if not self.is_listening:
            return

        # 1) 마이크 정리
        try:
            self.audio.stop()
        except Exception:
            pass

        # 2) Gemini Live 종료
        if self.transcriber and self.loop:
            async def _stop() -> None:
                if self.transcriber:
                    await self.transcriber.stop()

            try:
                asyncio.run_coroutine_threadsafe(_stop(), self.loop).result(timeout=2.0)
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

        def _do() -> None:
            try:
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


def main() -> None:
    """진입점."""
    daemon = Daemon()
    daemon.start()


if __name__ == "__main__":
    main()