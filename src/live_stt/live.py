"""Gemini Live API 클라이언트 (실시간 STT).

오디오 청크를 Gemini Live (`gemini-3.5-transcribe-live`) 에 전송하고,
input_transcription 응답을 콜백으로 전달.

참조: https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
       https://aistudio.google.com/docs/models/gemini-3.5-transcribe

매 세션마다 :mod:`.keypool`에서 "live" purpose용 키를 round-robin으로 1개 소비.

아키텍처
--------
각 Live 세션은 **전용 스레드 + 전용 asyncio 루프**에서 동작. 데몬은 공유
asyncio 루프를 사용하지 않으므로 Python 3.14 ProactorEventLoop의
foreign-thread-close 버그 경로를 우회.

세션 사양
---------
- 모델: ``models/gemini-3.5-transcribe-live`` (Live API 전용 변종)
- Audio: 16kHz 16-bit mono PCM, ``audio/pcm;rate=16000``
- 입력 언어: ``language_codes=[]`` → 자동 감지 (다국어/코드스위칭 처리)
- 응답: ``response_modalities=["TEXT"]``
- 두 종류의 transcription 응답:
    - ``interim_input_transcription``: 저지연 부분 결과 (덮어쓰기)
    - ``input_transcription``: 최종 확정 (append)
- Stop 시 ``audio_stream_end=True`` 로 finalize
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from google import genai
from google.genai import types
from google.genai.types import HttpOptions

from .config import GEMINI_LIVE_MODEL, SAMPLE_RATE
from .keypool import get_pool


class LiveTranscriber:
    """전용 스레드/루프에서 동작하는 Gemini Live 세션."""

    def __init__(
        self,
        on_text: Callable[[str], None],
        on_interim: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._api_key = get_pool().next("live")
        self.on_text = on_text
        self.on_interim = on_interim
        self.client: Optional[genai.Client] = None
        self.session = None
        self._audio_queue: Optional[asyncio.Queue[bytes]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ready = threading.Event()  # setup 완료 시 set
        self._setup_error: Optional[BaseException] = None
        self._config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[],  # 자동 감지
            ),
        )

    def start(self, ready_timeout: float = 10.0) -> None:
        """전용 스레드 시작 + Live 연결. setup 완료 또는 타임아웃까지 대기."""
        self._thread = threading.Thread(
            target=self._thread_main, name="LiveTranscriber", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=ready_timeout):
            raise TimeoutError("Live 세션 setup 타임아웃")
        if self._setup_error is not None:
            raise self._setup_error

    def enqueue_audio(self, chunk: bytes) -> None:
        """오디오 청크 enqueue (호출 스레드 무관 — thread-safe)."""
        if self._audio_queue is None or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, chunk)
        except RuntimeError:
            pass

    def stop(self, timeout: float = 2.0) -> None:
        """전용 스레드/루프 종료. 세션에 audio_stream_end 보내 finalize."""
        if self._loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)
            future.result(timeout=timeout)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None

    async def _cleanup(self) -> None:
        """Finalize 스트림 신호 → running=False."""
        if not self._running and self.session is None:
            return
        # 1) finalize: audio 끝났음을 서버에 알려 최종 transcript 가 도착하게 함
        if self.session is not None:
            try:
                await self.session.send_realtime_input(audio_stream_end=True)
            except Exception:
                pass
        # 2) running flag — _setup_and_run 의 while 루프 종료
        self._running = False
        # 3) 송수신 태스크는 cooperative cancellation 으로 종료
        #    세션 close 는 _setup_and_run.finally 의 cm.__aexit__ 가 처리

    def _thread_main(self) -> None:
        """전용 스레드 entrypoint."""
        print(f"[live] thread starting (model={GEMINI_LIVE_MODEL})", flush=True)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._setup_and_run())
        except BaseException as e:
            print(f"[live] thread crashed: {type(e).__name__}: {e}", flush=True)
            self._setup_error = e
            self._ready.set()
        finally:
            print("[live] thread exiting", flush=True)
            asyncio.set_event_loop(None)
            try:
                self._loop.close()
            except Exception:
                pass

    async def _setup_and_run(self) -> None:
        """setup → run_forever → cleanup.

        ``client.aio.live.connect(...)`` 는 ``_AsyncGeneratorContextManager``
        이므로 ``async with`` (또는 수동 ``__aenter__/__aexit__``) 로 진입.
        외부에서 세션 종료 시키기 위해 수동 진입 패턴 사용.
        """
        print("[live] creating genai.Client...", flush=True)
        self.client = genai.Client(api_key=self._api_key)
        print(f"[live] client OK (key=***{self._api_key[-4:]})", flush=True)
        self._audio_queue = asyncio.Queue()

        print("[live] calling client.aio.live.connect()...", flush=True)
        cm = self.client.aio.live.connect(
            model=GEMINI_LIVE_MODEL,
            config=self._config,
        )
        try:
            print("[live] awaiting cm.__aenter__()...", flush=True)
            self.session = await cm.__aenter__()
            print("[live] session OPENED ✓", flush=True)
            self._running = True
            self._send_task = asyncio.create_task(self._send_loop())
            self._recv_task = asyncio.create_task(self._recv_loop())
            self._ready.set()  # 메인 스레드 해제
            print("[live] enter run loop (waiting on _running)", flush=True)
            while self._running:
                await asyncio.sleep(0.05)
            print("[live] _running=False → leaving run loop", flush=True)
        finally:
            self._running = False
            if self.session is not None:
                try:
                    print("[live] closing session via __aexit__...", flush=True)
                    await cm.__aexit__(None, None, None)
                    print("[live] session closed", flush=True)
                except Exception as e:
                    print(f"[live] __aexit__ error: {e}", flush=True)
                self.session = None

    async def _send_loop(self) -> None:
        sent_count = 0
        first_send = True
        while self._running and self.session:
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.1)
                await self.session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )
                sent_count += 1
                if first_send:
                    print(f"[live] first audio chunk sent ({len(chunk)} bytes)", flush=True)
                    first_send = False
                elif sent_count % 50 == 0:
                    print(f"[live] sent {sent_count} chunks ({sent_count * len(chunk)} bytes total)", flush=True)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[live] send error: {type(e).__name__}: {e}", flush=True)
                break
        print(f"[live] send_loop ended (sent {sent_count} chunks total)", flush=True)

    async def _recv_loop(self) -> None:
        recv_count = 0
        try:
            print("[live] recv_loop waiting for responses...", flush=True)
            async for response in self.session.receive():
                if not self._running:
                    print("[live] recv_loop: _running=False, break", flush=True)
                    break
                recv_count += 1
                sc = response.server_content
                if sc is None:
                    if recv_count == 1:
                        print(f"[live] first response has no server_content: {response}", flush=True)
                    elif recv_count % 50 == 0:
                        print(f"[live] recv_count={recv_count}", flush=True)
                    continue
                interim = sc.interim_input_transcription.text if sc.interim_input_transcription is not None else None
                final = sc.input_transcription.text if sc.input_transcription is not None else None
                if interim or final:
                    print(
                        f"[live] recv #{recv_count} interim={interim!r} final={final!r}",
                        flush=True,
                    )
                    # 첫 응답은 전체 response 디버그 출력
                    if recv_count == 1:
                        print(f"[live] full first response: {response}", flush=True)
                # Interim (저지연 부분 결과) — UI 덮어쓰기
                if interim and self.on_interim:
                    try:
                        self.on_interim(interim)
                    except Exception:
                        pass
                # Final (최종 확정) — UI append
                if final and self.on_text:
                    try:
                        self.on_text(final)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            print(f"[live] recv_loop cancelled (got {recv_count} responses)", flush=True)
        except Exception as e:
            err_str = str(e)
            # WebSocket 정상 close 는 에러로 raise 됨 (APIError 1000) — 정상 종료
            if "1000" in err_str or "ConnectionClosedOK" in err_str:
                print(
                    f"[live] recv_loop: connection closed normally ({recv_count} responses)",
                    flush=True,
                )
            else:
                print(f"[live] recv_loop error: {type(e).__name__}: {e}", flush=True)
                import traceback
                traceback.print_exc()
        print(f"[live] recv_loop ended (total {recv_count} responses)", flush=True)