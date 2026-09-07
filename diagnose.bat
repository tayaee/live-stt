@echo off
setlocal

REM ─────────────────────────────────────────────────────────────
REM 진단 스크립트 — 5단계로 원인을 좁힘
REM
REM Test 1: tkinter 단독 mainloop (deps 안 거침)
REM Test 2: tkinter + sounddevice + numpy (deps 중 cffi 빼고)
REM Test 3: tkinter + cffi 명시 로드
REM Test 4: websockets.speedups BLOCKED 후 tkinter
REM Test 5: live_stt 패키지 전체 import + Tk() (mainloop 없이)
REM ─────────────────────────────────────────────────────────────

set VENV_DIR=.venv-diag
if exist %VENV_DIR% (
    echo [setup] reusing %VENV_DIR%
) else (
    echo [setup] creating %VENV_DIR% with Python 3.13
    uv venv %VENV_DIR% --python 3.13
    if errorlevel 1 (
        echo [setup FAIL] could not create venv
        exit /b 1
    )
)

echo [setup] installing tkinter, sounddevice, numpy, cffi, websockets only
%VENV_DIR%\Scripts\python.exe -m pip install --quiet --disable-pip-version-check tk sounddevice numpy cffi websockets 2>nul

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 1: tkinter only
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import tkinter as tk; r=tk.Tk(); r.title('T1'); r.update(); r.destroy(); print('[T1 OK] minimal tkinter survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 2: tkinter + mainloop (1 second via .after)
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import tkinter as tk; r=tk.Tk(); r.title('T2'); r.after(800, r.destroy); r.mainloop(); print('[T2 OK] tkinter + mainloop survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 3: tkinter + sounddevice + numpy + mainloop
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import sounddevice, numpy; import tkinter as tk; r=tk.Tk(); r.title('T3'); r.after(800, r.destroy); r.mainloop(); print('[T3 OK] tkinter + audio deps survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 4: tkinter + cffi + mainloop
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import cffi; import tkinter as tk; r=tk.Tk(); r.title('T4'); r.after(800, r.destroy); r.mainloop(); print('[T4 OK] tkinter + cffi survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 5: tkinter + websockets (with speedups) + mainloop
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import websockets; import websockets.asyncio; import tkinter as tk; r=tk.Tk(); r.title('T5'); r.after(800, r.destroy); r.mainloop(); print('[T5 OK] tkinter + websockets.speedups survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 6: tkinter + websockets.speedups BLOCKED + mainloop
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -c "import sys; sys.modules['websockets.speedups']=None; import websockets; import websockets.asyncio; import tkinter as tk; r=tk.Tk(); r.title('T6'); r.after(800, r.destroy); r.mainloop(); print('[T6 OK] websockets.speedups DISABLED + tkinter survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 7: full live_stt import + Tk() (no mainloop)
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -e . 2>nul
%VENV_DIR%\Scripts\python.exe -c "from live_stt.daemon import Daemon; d = Daemon(); print('[T7 OK] live_stt import + Tk() survives')"

echo.
echo ══════════════════════════════════════════════════════════
echo   TEST 8: full daemon with mainloop (real test)
echo ══════════════════════════════════════════════════════════
start /b "" cmd /c "%VENV_DIR%\Scripts\python.exe -m live_stt.daemon > diag-output.txt 2>&1"
timeout /t 5 /nobreak >nul
taskkill /f /im python.exe /fi "PID gt 0" 2>nul
echo [T8] inspect diag-output.txt:
type diag-output.txt

echo.
echo ══════════════════════════════════════════════════════════
echo   Tests done. Read output above to find which test fails.
echo ══════════════════════════════════════════════════════════

endlocal