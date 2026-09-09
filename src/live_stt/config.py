"""live-stt 설정 (모델 ID, 오디오 파라미터, 키 코드).

API 키 로딩은 ``keypool.py``의 :func:`get_pool` 사용.
"""
GEMINI_LIVE_MODEL = "gemini-3.5-transcribe-live"
GEMINI_REWRITE_MODEL = "gemma-4-31b-it"

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1024
DTYPE = "int16"
INT16_MAX = 32768.0

REWRITE_CONTEXT_TAIL = 500

SILENCE_REWRITE_DELAY_SEC = 1.5

VK_RCONTROL = 0xA3

PASTE_PACING_MS = 2.0

DOUBLE_CTRL_MIN_INTERVAL_MS = 100