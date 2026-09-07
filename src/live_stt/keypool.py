"""Gemini API 키 round-robin 풀.

키 로딩 우선순위 (위에서 아래 순서로 시도, 처음 발견된 소스 사용):
    1. CLI 옵션 ``--gemini-api-keys "k1,k2,k3"`` (CSV 형식, 인라인)
    2. 현재 디렉터리 ``./gemini-api-keys.txt``
    3. 홈 디렉터리 ``~/.config/google-ai/gemini-api-keys.txt``

파일 형식
---------
- 한 줄에 키 1개
- '#'로 시작하는 줄은 주석
- 빈 줄 / 공백-only 줄은 무시
- UTF-8

예시 (파일)::

    # ~/.config/google-ai/gemini-api-keys.txt
    AIzaSyA...key1
    AIzaSyB...key2
    AIzaSyC...key3

세 소스 모두에서 키를 찾지 못하면 ``RuntimeError`` 발생 (env var fallback 없음).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

# ── 키 파일 경로 상수 ──────────────────────────────────────────────
# 2순위: 현재 디렉터리
CWD_KEYS_FILE: Path = Path.cwd() / "gemini-api-keys.txt"

# 3순위: 홈 디렉터리 설정
HOME_KEYS_FILE: Path = Path.home() / ".config" / "google-ai" / "gemini-api-keys.txt"

# 호출 목적 (전사 / 재작성) — 각각 독립 카운터
Purpose = Literal["live", "rewrite"]


def _load_keys_file(path: Path) -> list[str]:
    """텍스트 파일에서 주석/빈 줄을 제외한 키 목록 반환.

    파일이 없거나 읽기 실패 시 빈 리스트 반환.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.append(line)
    return keys


def _parse_csv_keys(csv: str) -> list[str]:
    """CSV 문자열을 트림해서 키 리스트로 변환. 빈 항목 제거."""
    return [k.strip() for k in csv.split(",") if k.strip()]


class KeyPool:
    """다중 Gemini API 키 round-robin 풀.

    ``next("live")`` / ``next("rewrite")``로 호출하면 해당 purpose의
    카운터를 1 증가시키고 키를 반환. 두 카운터는 독립적으로 동작.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise RuntimeError("API 키가 비어 있습니다.")
        self._keys: list[str] = keys
        self._counters: dict[Purpose, int] = {"live": 0, "rewrite": 0}
        self._lock = threading.Lock()

    @classmethod
    def load(
        cls,
        *,
        cli_csv: str | None = None,
    ) -> "KeyPool":
        """우선순위대로 키 로딩: CLI(CSV) > CWD 파일 > 홈 파일.

        Parameters
        ----------
        cli_csv
            1순위: ``--gemini-api-keys``로 받은 CSV 문자열.
            비어있는 항목은 무시. ``None``이면 CLI 소스 스킵.

        Raises
        ------
        RuntimeError
            세 소스 모두에서 키를 찾지 못한 경우.
        """
        # 1순위: CLI (CSV) — 명시적이므로 fallback 없이 그 결과로 결정
        if cli_csv is not None:
            keys = _parse_csv_keys(cli_csv)
            if not keys:
                raise RuntimeError(
                    "--gemini-api-keys 옵션에 유효한 키가 없습니다 "
                    "(콤마 구분 CSV, 각 항목은 비어있지 않아야 함)."
                )
            instance = cls(keys)
            instance._source = f"CLI: --gemini-api-keys ({len(keys)}개)"  # type: ignore[attr-defined]
            return instance

        # 2순위: CWD
        keys = _load_keys_file(CWD_KEYS_FILE)
        if keys:
            instance = cls(keys)
            instance._source = f"CWD: {CWD_KEYS_FILE}"  # type: ignore[attr-defined]
            return instance

        # 3순위: 홈 디렉터리
        keys = _load_keys_file(HOME_KEYS_FILE)
        if keys:
            instance = cls(keys)
            instance._source = f"home: {HOME_KEYS_FILE}"  # type: ignore[attr-defined]
            return instance

        raise RuntimeError(
            "API 키를 찾을 수 없습니다. 다음 중 하나를 사용하세요:\n"
            "  1) CLI: live-stt --gemini-api-keys 'k1,k2,k3'\n"
            f"  2) CWD 파일: {CWD_KEYS_FILE}\n"
            f"  3) 홈 파일:  {HOME_KEYS_FILE}\n"
            "  파일 형식: 한 줄에 키 1개, '#' 시작은 주석"
        )

    def next(self, purpose: Purpose) -> str:
        """해당 purpose의 다음 키를 round-robin으로 반환 (스레드 안전)."""
        if purpose not in self._counters:
            raise ValueError(f"unknown purpose: {purpose!r}")
        with self._lock:
            idx = self._counters[purpose] % len(self._keys)
            self._counters[purpose] += 1
            return self._keys[idx]

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def size(self) -> int:
        return len(self._keys)

    @property
    def source(self) -> str:
        return getattr(self, "_source", "unknown")


# ── 모듈 레벨 싱글톤 (지연 초기화, 스레드 안전) ─────────────────────
_pool: KeyPool | None = None
_pool_init_lock = threading.Lock()


def init_pool(*, cli_csv: str | None = None) -> KeyPool:
    """싱글톤 풀을 명시적으로 초기화. CLI 옵션 반영.

    진입점(``daemon.main``, ``live-stt.py``)에서 한 번 호출 권장.
    이미 초기화된 풀이 있으면 교체.
    """
    global _pool
    with _pool_init_lock:
        _pool = KeyPool.load(cli_csv=cli_csv)
    return _pool


def get_pool() -> KeyPool:
    """싱글톤 풀 반환. 아직 없으면 기본 위치(CWD → home)에서 로딩."""
    global _pool
    if _pool is None:
        with _pool_init_lock:
            if _pool is None:
                _pool = KeyPool.load()
    return _pool


def reset_pool() -> None:
    """싱글톤 풀 해제 (테스트용). 다음 ``get_pool()`` 호출 시 재초기화."""
    global _pool
    with _pool_init_lock:
        _pool = None