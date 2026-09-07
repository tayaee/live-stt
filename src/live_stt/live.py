"""Gemini Live API 클라이언트 (실시간 STT).

오디오 청크를 Gemini Live에 전송하고, input_transcription 응답을
콜백으로 전달.
"""
import asyncio
from typing import Callable, Optional

from google import genai
from google.genai import types

from .config import GEMINI_LIVE_MODEL, SAMPLE_RATE, get_api_key


class LiveTranscriber:
    """Gemini Live 세션 관리. input_transcription 텍스트를 콜백으로 전달."""

    def __init__(self, on_text: Callable[[str], None]) -> None:
        self.client = genai.Client(api_key=get_api_key())
        self.on_text = on_text  # callable: str -> None
        self.session = None
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._running = False
        self._audio_queue: Optional[asyncio.Queue[bytes]] = None
        self._config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def start(self) -> None:
        """Live 세션 시작. 송신/수신 백그라운드 태스크 시작."""
        self._running = True
        self._audio_queue = asyncio.Queue()
        self.session = await self.client.aio.live.connect(
            model=GEMINI_LIVE_MODEL,
            config=self._config,
        )
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def stop(self) -> None:
        """세션 종료."""
        self._running = False
        for t in (self._send_task, self._recv_task):
            if t:
                t.cancel()
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        self._send_task = None
        self._recv_task = None
        self._audio_queue = None

    def enqueue_audio(self, chunk: bytes) -> None:
        """오디오 청크를 Live 세션에 enqueue (별도 스레드에서 호출 가능)."""
        if self._audio_queue is not None:
            try:
                self._audio_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    async def _send_loop(self) -> None:
        """오디오 청크를 Gemini에 전송."""
        while self._running and self.session:
            try:
                chunk = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=0.1
                )
                await self.session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def _recv_loop(self) -> None:
        """input_transcription 응답 수신."""
        try:
            async for response in self.session.receive():
                if not self._running:
                    break
                sc = response.server_content
                if sc and sc.input_transcription:
                    text = sc.input_transcription.text
                    if text and self.on_text:
                        try:
                            self.on_text(text)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass