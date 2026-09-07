"""live-stt 설정 (모델 ID, 오디오 파라미터, 키 코드).

API 키 로딩은 ``keypool.py``의 :func:`get_pool` 사용.
"""
# 모델 ID
# 공식 Live API 문서 (https://ai.google.dev/gemini-api/docs/live-api/live-transcribe)
# 의 예제와 같이 prefix 없이 사용:
GEMINI_LIVE_MODEL = "gemini-3.5-transcribe-live"
GEMINI_REWRITE_MODEL = "gemma-4-31b-it"

# 오디오 입력 파라미터
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1024
DTYPE = "int16"
INT16_MAX = 32768.0  # 게이지 정규화용 (실제로는 signed 16-bit 최대 32767)

# 재작성 컨텍스트: 아래쪽(clean) 버퍼 마지막 N자를 Gemini에 함께 전송
REWRITE_CONTEXT_TAIL = 500

# 키 코드 (Right Ctrl)
VK_RCONTROL = 0xA3

# Paste pacing (글자당 sleep ms)
PASTE_PACING_MS = 2.0

# Hook 안정화용 (Right Ctrl 두 번 인식 사이 최소 간격, ms)
DOUBLE_CTRL_MIN_INTERVAL_MS = 100