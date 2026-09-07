"""마이크 스트림 (PyAudio 기반).

16kHz 16-bit PCM mono 청크를 ``on_chunk`` 콜백으로 전달.
추가로 ``on_level`` 콜백으로 RMS 기반 음압(0~100)을 매 청크마다 통지 →
UI의 수직 게이지가 반응함.

PyAudio는 ctypes 바인딩을 사용하므로 cffi C 확장 (sounddevice)와 달리
``_cffi_backend`` 를 로드하지 않음 → tkinter mainloop 진입 시 fatal
``PyEval_RestoreThread`` 회피.

PyAudio 콜백은 별도 PortAudio 스레드에서 호출됨.
"""
from __future__ import annotations

import logging
import struct
from typing import Callable, Optional

import pyaudio

from .config import SAMPLE_RATE, CHANNELS, BLOCKSIZE, DTYPE, INT16_MAX

logger = logging.getLogger(__name__)

_FORMAT = pyaudio.paInt16  # 16-bit signed int


def _pyaudio_format(dtype: str) -> int:
    if dtype == "int16":
        return pyaudio.paInt16
    if dtype == "int32":
        return pyaudio.paInt32
    if dtype == "float32":
        return pyaudio.paFloat32
    if dtype == "uint8":
        return pyaudio.paUint8
    raise ValueError(f"unsupported dtype: {dtype!r}")


def _rms_to_pct(rms: float) -> int:
    """RMS → 0~100 게이지 값. 사람이 들을 수 있는 영역을 시각적으로 잘 보이도록 스케일 보정.

    정상 음성 RMS 약 200~3000. 1%라도 표시되도록 log-ish 한 스케일.
        ratio = rms / INT16_MAX
    게이지 = clamp(ratio * 10 * 100, 0, 100)
    """
    if rms <= 0.0:
        return 0
    ratio = rms / INT16_MAX
    pct = int(ratio * 10.0 * 100.0)  # 10배 증폭 (시각화용)
    if pct < 1 and rms > 0:
        pct = 1
    return max(0, min(100, pct))


class AudioStream:
    """마이크에서 PCM 청크를 받아 콜백으로 전달."""

    def __init__(
        self,
        on_chunk: Optional[Callable[[bytes], None]] = None,
        on_level: Optional[Callable[[int], None]] = None,
    ) -> None:
        """
        Parameters
        ----------
        on_chunk
            PCM 청크 받을 때 (PortAudio 스레드).
        on_level
            RMS 기반 음압 (0~100) 콜백. UI 게이지 갱신용.
        """
        self.on_chunk = on_chunk
        self.on_level = on_level
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None

    def _callback(self, in_data: bytes, _frame_count, _time_info, _status) -> tuple:
        """PyAudio 입력 콜백 (PortAudio 스레드에서 호출)."""
        # 1) RMS 계산 → 음압 콜백 (가시화용)
        rms = 0.0
        if self.on_level:
            try:
                n = len(in_data) // 2  # int16 = 2 bytes/sample
                if n > 0:
                    samples = struct.unpack(f"<{n}h", in_data)
                    sumsq = 0
                    for s in samples:
                        sumsq += s * s
                    rms = (sumsq / n) ** 0.5
                    self.on_level(_rms_to_pct(rms))
            except BaseException:
                pass

        # 2) 청크 콜백 (실제 STT 전송용)
        if self.on_chunk:
            try:
                self.on_chunk(in_data)
            except BaseException:
                # 콜백 스레드 예외가 PortAudio 내부 상태를 깨지 않도록 흡수
                pass
        return (None, pyaudio.paContinue)

    def start(self) -> None:
        if self._stream is not None:
            return
        if self._pa is None:
            self._pa = pyaudio.PyAudio()
            try:
                default_in = self._pa.get_default_input_device_info()
                logger.info(
                    "mic device: [%s] %s",
                    default_in['index'],
                    default_in['name'],
                )
            except Exception as e:
                logger.warning("get_default_input_device failed: %s", e)
        self._stream = self._pa.open(
            format=_pyaudio_format(DTYPE),
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=BLOCKSIZE,
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        logger.info(
            "stream opened: %dHz, %dch, blocksize=%d",
            SAMPLE_RATE, CHANNELS, BLOCKSIZE,
        )

    def stop(self) -> None:
        if self._stream is not None:
            logger.info("closing stream")
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        # 게이지 0으로 (다음 받을 때까지 0 표시)
        if self.on_level:
            try:
                self.on_level(0)
            except BaseException:
                pass