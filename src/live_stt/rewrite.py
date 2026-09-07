"""gemma-4-31b-it로 문장 재작성.

raw transcription 전체와 아래쪽(clean) 버퍼의 마지막 N자를 컨텍스트로
전달하여 자연스러운 한국어로 다듬은 텍스트 반환.

매 ``rewrite()`` 호출마다 :mod:`.keypool`에서 "rewrite" purpose용 키를
round-robin으로 1개 소비 (genai.Client 인스턴스도 함께 새로 생성).
"""
import logging

from google import genai
from google.genai import types

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


class Rewriter:
    """gemma-4-31b-it에 동기적으로 재작성 요청 (별도 스레드에서 호출 권장).

    키는 호출 시점에 풀에서 매번 새로 뽑으므로 ``__init__``에서 클라이언트를
    만들지 않음 (round-robin이 호출 단위로 동작).
    """

    def __init__(self) -> None:
        # 의도적으로 클라이언트 보관 안 함 — 호출마다 풀에서 키 + 클라이언트 생성
        pass

    def rewrite(self, raw: str, clean_tail: str = "", max_retries: int | None = None) -> str:
        """raw 전체 + clean_tail을 받아 재작성된 텍스트 반환 (동기).

        실패 시 키 풀의 다음 키로 round-robin 순회하며 최대 N회 재시도(failover).
        """
        pool = get_pool()
        if max_retries is None:
            max_retries = min(len(pool), 5)

        prompt = REWRITE_PROMPT.format(
            context=clean_tail or "(이전 정리된 내용 없음)",
            raw=raw,
        )

        last_err: Exception | None = None
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
                response = client.models.generate_content(
                    model=GEMINI_REWRITE_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )
                result = (response.text or "").strip()
                logger.info("Gemma rewrite response (%d chars): %r", len(result), result[:200])
                return result
            except Exception as e:
                last_err = e
                logger.warning(
                    "Gemma rewrite attempt %d/%d failed with key=%s: %s: %s",
                    attempt,
                    max_retries,
                    key_mask,
                    type(e).__name__,
                    e,
                )

        logger.error("Gemma rewrite completely failed after %d attempts", max_retries)
        if last_err:
            raise last_err
        return ""