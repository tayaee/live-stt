# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "google-genai",
#     "sounddevice",
#     "numpy",
# ]
# ///
"""live-stt: Windows 음성 받아쓰기 데몬 (단독 실행 스크립트).

`uv run live-stt.py`로 패키지 설치 없이 바로 실행 가능.
Phase 3에서 실제 구현 (현재는 임시 진입점).
"""


def main():
    print("Hello from live-stt (standalone)! uv run live-stt.py")


if __name__ == "__main__":
    main()