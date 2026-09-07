"""live-stt 설정 (모델 ID, 오디오 파라미터, 키 코드).

환경변수 GEMINI_API_KEY 로딩.
"""
import os


# 모델 ID
GEMINI_LIVE_MODEL = "models/gemini-3.5-transcribe-live"
GEMINI_REWRITE_MODEL = "models/gemini-4-31b-it"

# 오디오 입력 파라미터
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1024
DTYPE = "int16"

# 재작성 컨텍스트: 아래쪽(clean) 버퍼 마지막 N자를 Gemini에 함께 전송
REWRITE_CONTEXT_TAIL = 500

# 키 코드 (Right Alt, Right Shift)
VK_RMENU = 0xA5
VK_RSHIFT = 0xA1

# Paste pacing (글자당 sleep ms)
PASTE_PACING_MS = 2.0

# Hook 안정화용 (Right Alt 두 번 인식 사이 최소 간격, ms)
DOUBLE_ALT_MIN_INTERVAL_MS = 100


def get_api_key() -> str:
    """GEMINI_API_KEY 환경변수 로딩. 없으면 예외."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY 환경변수가 설정되지 않았습니다.\n"
            "PowerShell: $env:GEMINI_API_KEY = 'your-key'\n"
            "cmd: set GEMINI_API_KEY=your-key"
        )
    return key