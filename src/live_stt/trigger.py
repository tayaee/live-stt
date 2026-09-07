"""키보드 단축키 hook (Right Ctrl 시작/종료, Right Shift 재작성).

WH_KEYBOARD_LL low-level hook 사용. Windows는 hook 콜백을 GUI 스레드에서
호출하므로 tkinter mainloop와 동일 스레드에서 install해야 함.

ctypes argtypes/restype을 Win32 스펙에 맞게 설정하지 않으면 64-bit에서
``LPARAM`` (포인터)이 잘려서 ``OverflowError`` 발생 — hook 체인이 끊김.
"""
import ctypes
from ctypes import wintypes

from .config import VK_RCONTROL, VK_RSHIFT

user32 = ctypes.windll.user32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

# ── Win32 함수 시그니처 고정 (64-bit 호환) ───────────────────────────
# HHOOK, LRESULT, LPARAM 등은 64-bit에서 8바이트. argtypes 미설정 시
# ctypes 기본값 (c_int, 4바이트) 로 변환 시도 → OverflowError.
# (wintypes에 LRESULT는 없으므로 c_longlong로 직접 지정.)
_LRESULT = ctypes.c_longlong  # LRESULT = LONG_PTR = 64-bit on x64

user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,        # idHook
    ctypes.c_void_p,     # lpfn  (HOOKPROC)
    wintypes.HINSTANCE,  # hMod
    wintypes.DWORD,      # dwThreadId
)
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
user32.CallNextHookEx.restype = _LRESULT


class TriggerHook:
    """Right Ctrl / Right Shift 단축키 hook."""

    def __init__(self, on_ctrl=None, on_shift=None) -> None:
        self.on_ctrl = on_ctrl  # callable: Right Ctrl 누를 때 호출
        self.on_shift = on_shift  # callable: Right Shift 누를 때 호출
        self._hook_id: int = 0
        self._proc = None  # 콜백 참조 유지 (GC 방지)

    def install(self) -> int:
        """Hook 설치. 메인 GUI 스레드에서 호출해야 함."""
        # lParam은 64-bit 포인터이므로 LPARAM (c_void_p) 으로 받음
        CMPFUNC = ctypes.WINFUNCTYPE(
            _LRESULT,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._proc = CMPFUNC(self._callback)
        self._hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._proc,
            None,  # hMod (low-level hook은 0)
            0,  # dwThreadId (0 = 모든 스레드)
        )
        if not self._hook_id:
            err = ctypes.get_last_error() or 0
            raise RuntimeError(f"키보드 hook 설치 실패 (Win32 error={err})")
        return self._hook_id

    def uninstall(self) -> None:
        """Hook 제거."""
        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = 0
            self._proc = None

    def _callback(self, n_code, w_param, l_param):
        # lParam은 KBDLLHOOKSTRUCT 포인터. vkCode가 첫 DWORD.
        if n_code == 0 and w_param == WM_KEYDOWN and l_param:
            vk = ctypes.cast(l_param, ctypes.POINTER(wintypes.DWORD))[0]
            if vk == VK_RCONTROL and self.on_ctrl:
                try:
                    self.on_ctrl()
                except BaseException:
                    pass  # 콜백 예외는 hook 체인 끊김 방지
            elif vk == VK_RSHIFT and self.on_shift:
                try:
                    self.on_shift()
                except BaseException:
                    pass
        try:
            return user32.CallNextHookEx(self._hook_id, n_code, w_param, l_param)
        except BaseException:
            return 0