@echo off
setlocal

REM ─────────────────────────────────────────────────────────────
REM Python 버전별 venv 준비 + 테스트
REM
REM 이 스크립트는 venv만 만들고 설치한 다음, 각 버전에 대해
REM 실행할 명령을 echo로 알려줍니다. 직접 Ctrl+C로 종료한 다음
REM 다음 버전으로 넘어가세요.
REM ─────────────────────────────────────────────────────────────

set VERSIONS=3.14 3.13 3.12 3.11

for %%V in (%VERSIONS%) do (
    echo.
    echo ══════════════════════════════════════════════════════════
    echo   Setting up Python %%V
    echo ══════════════════════════════════════════════════════════

    if exist .venv-%%V (
        echo [clean] removing old .venv-%%V
        rmdir /s /q .venv-%%V
    )

    echo [1/4] creating venv .venv-%%V
    uv venv .venv-%%V --python %%V
    if errorlevel 1 (
        echo [skip] uv venv failed for %%V ^- skipping
        goto :nextver
    )

    echo [2/4] installing project
    uv pip install --python .venv-%%V -e .
    if errorlevel 1 (
        echo [fail] pip install failed for %%V ^- skipping
        goto :nextver
    )

    echo [3/4] copying gemini-api-keys.txt into venv
    if exist gemini-api-keys.txt (
        copy /Y gemini-api-keys.txt .venv-%%V\ >nul
    )

    echo [4/4] venv ready. Try:
    echo.
    echo     .venv-%%V\Scripts\python.exe -m live_stt.daemon
    echo.
    echo   ^(Ctrl+C to quit after observing behavior^)

    :nextver
)

echo.
echo ══════════════════════════════════════════════════════════
echo   All venvs ready.
echo ══════════════════════════════════════════════════════════

endlocal