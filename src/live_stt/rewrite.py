"""Gemini 4 31b IT로 문장 재작성.

raw transcription 전체와 아래쪽(clean) 버퍼의 마지막 N자를 컨텍스트로
전달하여 자연스러운 한국어로 다듬은 텍스트 반환.
"""
from google import genai

from .config import GEMINI_REWRITE_MODEL, get_api_key

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
    """Gemini 4 31b IT에 동기적으로 재작성 요청 (별도 스레드에서 호출 권장)."""

    def __init__(self) -> None:
        self.client = genai.Client(api_key=get_api_key())

    def rewrite(self, raw: str, clean_tail: str = "") -> str:
        """raw 전체 + clean_tail을 받아 재작성된 텍스트 반환 (동기)."""
        prompt = REWRITE_PROMPT.format(
            context=clean_tail or "(이전 정리된 내용 없음)",
            raw=raw,
        )
        response = self.client.models.generate_content(
            model=GEMINI_REWRITE_MODEL,
            contents=prompt,
        )
        return response.text.strip()