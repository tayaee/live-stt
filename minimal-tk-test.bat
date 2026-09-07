@echo off
setlocal

REM ─────────────────────────────────────────────────────────────
REM 5개의 단계별 최소 테스트 — 가장 작은 환경에서부터 점검
REM ─────────────────────────────────────────────────────────────

set VENV_DIR=.venv-min
if exist %VENV_DIR% (
    echo [setup] reusing %VENV_DIR%
) else (
    echo [setup] creating %VENV_DIR% from system Python
    uv venv %VENV_DIR% --python 3.13
)

set PY=%VENV_DIR%\Scripts\python.exe

echo.
echo ══════════════════════════════════════════════════════════
echo   M0: tkinter only (no other deps)
echo ══════════════════════════════════════════════════════════
%PY% -c "import tkinter as tk; r=tk.Tk(); r.after(500, r.destroy); r.mainloop(); print('[M0 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   M1: tkinter + mainloop 3sec
echo ══════════════════════════════════════════════════════════
%PY% -c "import tkinter as tk; r=tk.Tk(); r.title('M1'); r.after(3000, r.destroy); r.mainloop(); print('[M1 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   M2: tkinter + tk.StringVar (UI 패턴)
echo ══════════════════════════════════════════════════════════
%PY% -c "import tkinter as tk; from tkinter import ttk; r=tk.Tk(); v=tk.StringVar(value='hello'); ttk.Label(r, textvariable=v).pack(); r.after(1500, lambda: (v.set('world'), r.after(1500, r.destroy))); r.mainloop(); print('[M2 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   M3: tkinter + PanedWindow + ScrolledText (UI 패턴)
echo ══════════════════════════════════════════════════════════
%PY% -c "import tkinter as tk; from tkinter import ttk, scrolledtext; r=tk.Tk(); pw=ttk.PanedWindow(r, orient=tk.VERTICAL); pw.pack(fill=tk.BOTH, expand=True); t=scrolledtext.ScrolledText(pw, height=5); pw.add(t); r.after(1500, r.destroy); r.mainloop(); print('[M3 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   M4: tkinter + the live_stt imports (audio etc.) only
echo ══════════════════════════════════════════════════════════
%VENV_DIR%\Scripts\pip.exe install --quiet --disable-pip-version-check pyaudio 2>nul
%PY% -c "from live_stt.config import SAMPLE_RATE; import pyaudio; import tkinter as tk; r=tk.Tk(); r.title('M4'); r.after(1500, r.destroy); r.mainloop(); print('[M4 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   M5: tkinter + ctypes (있으면 list에 뜨는지)
echo ══════════════════════════════════════════════════════════
%PY% -c "import ctypes; import tkinter as tk; r=tk.Tk(); r.title('M5'); r.after(1500, r.destroy); r.mainloop(); print('[M5 OK]')" 2>&1

echo.
echo ══════════════════════════════════════════════════════════
echo   ALL MINIMAL TESTS DONE.
echo   어떤 단계에서 [OK]가 안 뜨고 Fatal이 뜨는지 알려주세요.
echo ══════════════════════════════════════════════════════════

endlocal