"""키보드 단축키 hook (Right Ctrl 시작/종료).

WH_KEYBOARD_LL low-level hook 사용. Windows는 hook 콜백을 GUI 스레드에서
호출하므로 mainloop와 동일 스레드에서 install해야 함.

ctypes argtypes/restype을 Win32 스펙에 맞게 설정하지 않으면 64-bit에서
``LPARAM`` (포인터)이 잘려서 ``OverflowError`` 발생 — hook 체인이 끊김.
"""
import ctypes
from ctypes import wintypes

from .config import VK_RCONTROL

user32 = ctypes.windll.user32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

_LRESULT = ctypes.c_longlong

user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
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
    """Right Ctrl 단축키 hook (시작/종료 토글)."""

    def __init__(self, on_ctrl=None) -> None:
        self.on_ctrl = on_ctrl
        self._hook_id: int = 0
        self._proc = None

    def install(self) -> int:
        """Hook 설치. 메인 GUI 스레드에서 호출해야 함."""
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
            None,
            0,
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
        if n_code == 0 and w_param == WM_KEYDOWN and l_param:
            vk = ctypes.cast(l_param, ctypes.POINTER(wintypes.DWORD))[0]
            if vk == VK_RCONTROL and self.on_ctrl:
                try:
                    self.on_ctrl()
                except BaseException:
                    pass
        try:
            return user32.CallNextHookEx(self._hook_id, n_code, w_param, l_param)
        except BaseException:
            return 0