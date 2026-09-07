"""키보드 단축키 hook (Right Alt 시작/종료, Right Shift 재작성).

WH_KEYBOARD_LL low-level hook 사용. Windows는 hook 콜백을 GUI 스레드에서
호출하므로 tkinter mainloop와 동일 스레드에서 install해야 함.
"""
import ctypes
from ctypes import wintypes

from .config import VK_RMENU, VK_RSHIFT

user32 = ctypes.windll.user32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100


class TriggerHook:
    """Right Alt / Right Shift 단축키 hook."""

    def __init__(self, on_alt=None, on_shift=None) -> None:
        self.on_alt = on_alt  # callable: Right Alt 누를 때 호출
        self.on_shift = on_shift  # callable: Right Shift 누를 때 호출
        self._hook_id: int = 0
        self._proc = None  # 콜백 참조 유지 (GC 방지)

    def install(self) -> int:
        """Hook 설치. 메인 GUI 스레드에서 호출해야 함."""
        CMPFUNC = ctypes.WINFUNCTYPE(
            wintypes.INT, wintypes.INT, wintypes.WPARAM, wintypes.LPARAM
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
        # l_param은 KBDLLHOOKSTRUCT 포인터. vkCode가 첫 DWORD.
        if n_code == 0 and w_param == WM_KEYDOWN and l_param:
            vk = ctypes.cast(l_param, ctypes.POINTER(wintypes.DWORD))[0]
            if vk == VK_RMENU and self.on_alt:
                try:
                    self.on_alt()
                except Exception:
                    pass  # 콜백 예외는 hook 체인 끊김 방지
            elif vk == VK_RSHIFT and self.on_shift:
                try:
                    self.on_shift()
                except Exception:
                    pass
        return user32.CallNextHookEx(self._hook_id, n_code, w_param, l_param)