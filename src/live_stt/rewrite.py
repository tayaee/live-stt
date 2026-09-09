"""gemma-4-31b-it로 문장 재작성.

raw transcription 전체와 아래쪽(clean) 버퍼의 마지막 N자를 컨텍스트로
전달하여 자연스러운 한국어로 다듬은 텍스트 반환.

매 ``rewrite()`` 호출마다 :mod:`.keypool`에서 "rewrite" purpose용 키를
round-robin으로 1개 소비 (genai.Client 인스턴스도 함께 새로 생성).

호출 방식
---------
Google GenAI SDK에서 Gemma 모델은 ``client.chats.create()`` →
``chat.send_message()`` 패턴으로 호출해야 정상 동작한다. 동일 모델을
``client.models.generate_content()`` 로 호출하면 서버가 500 INTERNAL을
반환한다 (참조: ``e:\\src\\gemma-4-31b\\google-ai-chat.py``).

재시도 정책
-----------
키 failover는 **HTTP 429 (rate limit)** 에서만 수행한다. 그 외 모든 에러
(5xx 서버 에러, 4xx 클라이언트 에러, 네트워크 오류 등)는 즉시 raise하여
상위 호출자(daemon)가 사용자에게 알릴 수 있도록 한다. 의도: 500 INTERNAL이
모델 과부하/장애로 모든 키에서 동시에 발생해도 무의미한 키 순환을 중단.
"""
import logging

from google import genai

from .config import GEMINI_REWRITE_MODEL
from .keypool import get_pool

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """You are a Korean text editor. The user is dictating by voice. The raw transcription may contain speech disfluencies (um, uh, repetitions), false starts, and grammar errors.

Clean up the following raw transcription into natural, fluent Korean sentences. Preserve the user's original intent and wording where possible, but:
- Remove filler words (어, 음, 아, 그)
- Fix self-corrections (사용자가 말을 바꾸면 마지막 의도만 유지)
- Apply natural punctuation
- Keep technical terms and proper nouns verbatim

[Recent context (previous cleaned text, for continuity)]
{context}

[Raw transcription to clean]
{raw}

[Output]
Respond with ONLY the cleaned Korean text. No preamble, no explanation."""


def _http_status(exc: BaseException) -> int | None:
    """google-genai 예외 객체에서 HTTP status code 추출.

    ``APIError`` 계열은 ``code`` 속성에 int status code를 보관. 그 외 예외
    (네트워크 오류 등)는 ``None``. SDK 버전에 따라 ``status_code``일 수도 있어
    둘 다 시도.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


class Rewriter:
    """gemma-4-31b-it에 동기적으로 재작성 요청 (별도 스레드에서 호출 권장).

    키는 호출 시점에 풀에서 매번 새로 뽑으므로 ``__init__``에서 클라이언트를
    만들지 않음 (round-robin이 호출 단위로 동작).
    """

    def __init__(self) -> None:
        pass

    def rewrite(self, raw: str, clean_tail: str = "", max_retries: int | None = None) -> str:
        """raw 전체 + clean_tail을 받아 재작성된 텍스트 반환 (동기).

        재시도 정책: 429 (rate limit) 일 때만 다음 키로 failover. 다른 모든
        에러는 즉시 raise한다.

        호출 방식: Gemma 모델은 ``chats.create()`` → ``send_message()`` 패턴
        을 사용한다. (``models.generate_content()`` 는 500 INTERNAL을 반환.)
        """
        pool = get_pool()
        if max_retries is None:
            max_retries = min(len(pool), 5)

        prompt = REWRITE_PROMPT.format(
            context=clean_tail or "(이전 정리된 내용 없음)",
            raw=raw,
        )

        for attempt in range(1, max_retries + 1):
            api_key = pool.next("rewrite")
            key_mask = f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"
            client = genai.Client(api_key=api_key)

            logger.info(
                "Gemma rewrite request (attempt %d/%d, key=%s): sending transcribed text (%d chars) to model=%s",
                attempt,
                max_retries,
                key_mask,
                len(raw),
                GEMINI_REWRITE_MODEL,
            )
            try:
                chat = client.chats.create(model=GEMINI_REWRITE_MODEL)
                response = chat.send_message(prompt)
                result = (response.text or "").strip()
                logger.info("Gemma rewrite response (%d chars): %r", len(result), result[:200])
                return result
            except Exception as e:
                status = _http_status(e)
                logger.warning(
                    "Gemma rewrite attempt %d/%d failed with key=%s: %s: %s (status=%s)",
                    attempt,
                    max_retries,
                    key_mask,
                    type(e).__name__,
                    e,
                    status,
                )
                if status != 429:
                    logger.error(
                        "Gemma rewrite: non-429 error (status=%s) → raising without further retries",
                        status,
                    )
                    raise

        logger.error("Gemma rewrite completely failed after %d attempts (all 429)", max_retries)
        return ""