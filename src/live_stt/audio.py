"""마이크 스트림 (sounddevice.RawInputStream).

16kHz 16-bit PCM mono 청크를 on_chunk 콜백 또는 큐에 전달.
sounddevice 콜백은 별도 스레드에서 호출됨.
"""
from typing import Callable, Optional
import queue

import sounddevice as sd

from .config import SAMPLE_RATE, CHANNELS, BLOCKSIZE, DTYPE


class AudioStream:
    """마이크에서 PCM 청크를 받아 콜백 또는 큐에 전달."""

    def __init__(self, on_chunk: Optional[Callable[[bytes], None]] = None) -> None:
        self.on_chunk = on_chunk  # callable: bytes -> None (콜백 모드)
        self.queue: queue.Queue[bytes] = queue.Queue()  # 큐 모드 폴백
        self.stream: Optional[sd.RawInputStream] = None

    def _callback(self, indata, _frames, _time_info, _status) -> None:
        # _status 플래그는 일단 무시 (overflow 등은 추후 로깅 가능)
        chunk = bytes(indata)
        if self.on_chunk:
            self.on_chunk(chunk)
        else:
            self.queue.put_nowait(chunk)

    def start(self) -> None:
        if self.stream is not None:
            return
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._callback,
        )
        stream.start()
        self.stream = stream

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        # 큐 비우기
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

    def get_chunk(self, timeout: float = 0.1) -> Optional[bytes]:
        """큐 모드일 때 청크 가져오기. 콜백 모드에선 None."""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None