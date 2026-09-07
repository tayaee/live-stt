"""SendInput으로 텍스트 주입 (KEYEVENTF_UNICODE + IME 클리어).

한글 포함 모든 Unicode 한 글자씩 주입. pacing으로 일부 앱의 빠른 입력
처리 한계 회피.
"""
import time
import ctypes
from ctypes import wintypes

from .config import PASTE_PACING_MS

user32 = ctypes.windll.user32
imm32 = ctypes.windll.imm32

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

# ── Win32 시그니처 고정 (64-bit 호환) ────────────────────────────────
user32.SendInput.argtypes = (
    wintypes.UINT,    # nInputs
    ctypes.c_void_p,  # pInput (LPINPUT)
    ctypes.c_int,     # cbSize
)
user32.SendInput.restype = wintypes.UINT

imm32.ImmGetContext.argtypes = (wintypes.HWND,)
imm32.ImmGetContext.restype = wintypes.HANDLE  # HIMC = c_void_p

imm32.ImmNotifyIME.argtypes = (
    wintypes.HANDLE,  # HIMC
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
)
imm32.ImmNotifyIME.restype = wintypes.BOOL

imm32.ImmReleaseContext.argtypes = (wintypes.HWND, wintypes.HANDLE)  # HIMC
imm32.ImmReleaseContext.restype = wintypes.BOOL


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("padding", ctypes.c_byte * 32),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def _send_unicode_char(ch: str) -> None:
    """한 글자 Unicode 주입 (keydown + keyup)."""
    code = ord(ch)
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.u.ki.wVk = 0
    inp_down.u.ki.wScan = code
    inp_down.u.ki.dwFlags = KEYEVENTF_UNICODE
    inp_down.u.ki.time = 0
    inp_down.u.ki.dwExtraInfo = None

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.u.ki.wVk = 0
    inp_up.u.ki.wScan = code
    inp_up.u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    inp_up.u.ki.time = 0
    inp_up.u.ki.dwExtraInfo = None

    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))


def clear_ime(hwnd: int) -> None:
    """진행 중 IME composition 클리어."""
    if not hwnd:
        return
    hIMC = imm32.ImmGetContext(hwnd)
    if hIMC:
        imm32.ImmNotifyIME(hIMC, 0x11, 0x02, 0)  # IMN_CLOSECANDIDATE
        imm32.ImmReleaseContext(hwnd, hIMC)


def send_text(text: str, pacing_ms: float | None = None) -> None:
    """텍스트를 한 글자씩 SendInput으로 주입."""
    pacing = (pacing_ms if pacing_ms is not None else PASTE_PACING_MS) / 1000.0
    for ch in text:
        _send_unicode_char(ch)
        if pacing > 0:
            time.sleep(pacing)