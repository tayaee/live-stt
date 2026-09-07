@echo off
setlocal

rem ----------------------------------------------------------------------
rem run.bat — launches the installed live-stt binary.
rem
rem First-time setup: run install.bat once to register the CLI via uv.
rem
rem Override Gemini keys inline:
rem     live-stt --gemini-api-keys "k1,k2,k3"
rem
rem Key loading priority (top wins):
rem   1. --gemini-api-keys CSV on the CLI
rem   2. .\gemini-api-keys.txt  (current directory)
rem   3. %USERPROFILE%\.config\google-ai\gemini-api-keys.txt
rem ----------------------------------------------------------------------

where live-stt >nul 2>&1
if errorlevel 1 (
    echo [run.bat] 'live-stt' not found on PATH.
    echo           Running install.bat ...
    call "%~dp0install.bat"
    if errorlevel 1 (
        echo [run.bat] install failed. Aborting.
        pause
        exit /b 1
    )
)

live-stt %*
exit /b %errorlevel%
endlocal