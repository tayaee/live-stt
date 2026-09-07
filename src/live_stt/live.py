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
import logging
import threading
from typing import Callable, Optional

from google import genai
from google.genai import types
from google.genai.types import HttpOptions

from .config import GEMINI_LIVE_MODEL, SAMPLE_RATE
from .keypool import get_pool

logger = logging.getLogger(__name__)


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
        """Finalize 스트림 신호 → tasks cancel → running=False."""
        if not self._running and self.session is None:
            return
        # 1) finalize: audio 끝났음을 서버에 알려 최종 transcript 가 도착하게 함
        if self.session is not None:
            try:
                await self.session.send_realtime_input(audio_stream_end=True)
            except Exception:
                pass
        # 2) 모델이 finalize 후 final transcript 를 보내는 시간 확보.
        #    매우 짧은 발음(예: 0.5~1초)에서는 모델이 finalize 전에 응답을
        #    emit 하기 어려워 final 0개 → 2초 정도 기다림.
        await asyncio.sleep(2.0)
        # 3) 송수신 태스크 명시적 cancel — 그렇지 않으면 루프 종료 시
        #    "Task was destroyed but it is pending!" 경고 발생.
        for task_attr in ("_send_task", "_recv_task"):
            task: Optional[asyncio.Task] = getattr(self, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        self._send_task = None
        self._recv_task = None
        # 4) running flag — _setup_and_run 의 while 루프 종료
        self._running = False
        # 5) 세션 close 는 _setup_and_run.finally 의 cm.__aexit__ 가 처리

    def _thread_main(self) -> None:
        """전용 스레드 entrypoint."""
        logger.info("thread starting (model=%s)", GEMINI_LIVE_MODEL)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._setup_and_run())
        except BaseException as e:
            logger.exception("thread crashed: %s: %s", type(e).__name__, e)
            self._setup_error = e
            self._ready.set()
        finally:
            logger.info("thread exiting")
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
        logger.info("creating genai.Client...")
        self.client = genai.Client(api_key=self._api_key)
        logger.info("client OK (key=***%s)", self._api_key[-4:])
        self._audio_queue = asyncio.Queue()

        logger.info("calling client.aio.live.connect()...")
        cm = self.client.aio.live.connect(
            model=GEMINI_LIVE_MODEL,
            config=self._config,
        )
        try:
            logger.info("awaiting cm.__aenter__()...")
            self.session = await cm.__aenter__()
            logger.info("session OPENED ✓")
            self._running = True
            self._send_task = asyncio.create_task(self._send_loop())
            self._recv_task = asyncio.create_task(self._recv_loop())
            self._ready.set()  # 메인 스레드 해제
            logger.info("enter run loop (waiting on _running)")
            while self._running:
                await asyncio.sleep(0.05)
            logger.info("_running=False → leaving run loop")
        finally:
            self._running = False
            if self.session is not None:
                try:
                    logger.info("closing session via __aexit__...")
                    await cm.__aexit__(None, None, None)
                    logger.info("session closed")
                except Exception as e:
                    logger.warning("__aexit__ error: %s", e)
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
                    logger.info("first audio chunk sent (%d bytes)", len(chunk))
                    first_send = False
                elif sent_count % 50 == 0:
                    logger.info(
                        "sent %d chunks (%d bytes total)",
                        sent_count, sent_count * len(chunk),
                    )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("send error: %s: %s", type(e).__name__, e)
                break
        logger.info("send_loop ended (sent %d chunks total)", sent_count)

    async def _recv_loop(self) -> None:
        recv_count = 0
        try:
            logger.info("recv_loop waiting for responses...")
            async for response in self.session.receive():
                if not self._running:
                    logger.info("recv_loop: _running=False, break")
                    break
                recv_count += 1
                sc = response.server_content
                interim = sc.interim_input_transcription.text if (sc and sc.interim_input_transcription is not None) else None
                final = sc.input_transcription.text if (sc and sc.input_transcription is not None) else None
                # 모든 응답을 찍어 모델이 진짜 침묵인지 / 응답 형태가 예상과 다른지 확인
                if recv_count <= 3 or interim or final or recv_count % 20 == 0:
                    logger.info(
                        "recv #%d sc=%r interim=%r final=%r",
                        recv_count, sc, interim, final,
                    )
                    if recv_count == 1:
                        # 첫 응답은 전체 dump
                        logger.info("FULL first response: %r", response)
                        try:
                            logger.info("FULL type: %s", type(response).__name__)
                        except Exception:
                            pass
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
            logger.info("recv_loop cancelled (got %d responses)", recv_count)
        except Exception as e:
            err_str = str(e)
            # WebSocket 정상 close 는 에러로 raise 됨 (APIError 1000) — 정상 종료
            if "1000" in err_str or "ConnectionClosedOK" in err_str:
                logger.info(
                    "recv_loop: connection closed normally (%d responses)",
                    recv_count,
                )
            else:
                logger.exception("recv_loop error: %s: %s", type(e).__name__, e)
        logger.info("recv_loop ended (total %d responses)", recv_count)