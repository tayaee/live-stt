"""PyAudio 마이크 입력 검증 도구.

2초 녹음 + RMS(음압) 계산 + 디바이스 목록 출력.

진단 기준
- 출력에 Default input device 이름 + index 표시
- RMS > 100 : 정상 (마이크가 소리 받음)
- RMS < 50  : 침묵 (마이크 mute, 잘못된 디바이스, OS 권한 등)
"""
from __future__ import annotations

import struct
import sys
import time

import pyaudio

_FORMAT = pyaudio.paInt16
_RATE = 16000
_CHANNELS = 1
_BLOCKSIZE = 1024
_DURATION_SEC = 2.0


def main() -> int:
    pa = pyaudio.PyAudio()

    print("=" * 60)
    print(" Devices:")
    print("=" * 60)
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(
                f"  [{i:2d}] in={info['maxInputChannels']:>2} "
                f"{info['name']}  (host={info['hostApi']})"
            )

    print()
    try:
        default_in = pa.get_default_input_device_info()
        print(f"Default input device:")
        print(f"  [{default_in['index']:2d}] {default_in['name']}")
    except OSError as e:
        print(f"[FAIL] no default input device: {e}", file=sys.stderr)
        pa.terminate()
        return 1

    print()
    print("=" * 60)
    print(f" Recording {_DURATION_SEC} sec, speak or clap now!")
    print("=" * 60)

    stream = pa.open(
        format=_FORMAT,
        channels=_CHANNELS,
        rate=_RATE,
        input=True,
        input_device_index=default_in["index"],
        frames_per_buffer=_BLOCKSIZE,
    )

    chunks: list[bytes] = []
    rms_samples: list[float] = []
    start = time.time()
    try:
        while time.time() - start < _DURATION_SEC:
            data = stream.read(_BLOCKSIZE, exception_on_overflow=False)
            chunks.append(data)
            n = len(data) // 2
            if n > 0:
                samples = struct.unpack(f"<{n}h", data)
                rms = (sum(s * s for s in samples) / n) ** 0.5
                rms_samples.append(rms)
                sys.stdout.write(f"  RMS={rms:7.0f}\r")
                sys.stdout.flush()
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        stream.close()
        pa.terminate()
        return 1
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    print()
    avg_rms = sum(rms_samples) / len(rms_samples) if rms_samples else 0
    peak_rms = max(rms_samples) if rms_samples else 0
    total_bytes = sum(len(c) for c in chunks)
    print(f"Total: {total_bytes} bytes in {len(chunks)} chunks")
    print(f"  avg RMS = {avg_rms:.0f}")
    print(f"  peak RMS = {peak_rms:.0f}")
    print()
    if peak_rms < 50:
        print("⚠  peak RMS < 50 — mic is silent or wrong device")
        print("   1) OS 마이크 권한 (Win10: Settings → Privacy → Microphone)")
        print("   2) 헤드셋이 default input 인지 확인")
        print("   3) 헤드셋 mute/잠금 해제")
        return 2
    print("✓  mic is capturing audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())