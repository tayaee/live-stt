"""타겟 앱 윈도우 관리 (hwnd 캡처/복원).

Right Alt 1번째 누를 때 활성 앱을 캡처하여, Right Alt 2번째로 Paste 시
포커스를 복원한 뒤 SendInput으로 텍스트 주입.
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32


class TargetApp:
    """받아쓰기 결과를 전송할 대상 앱."""

    def __init__(self) -> None:
        self.hwnd: int = 0
        self.focus_hwnd: int = 0
        self.pid: int = 0
        self.title: str = ""

    def capture(self) -> bool:
        """현재 활성 앱의 윈도우 정보 캡처."""
        self.hwnd = user32.GetForegroundWindow()
        if not self.hwnd:
            return False
        self.focus_hwnd = user32.GetFocus()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        self.pid = pid.value
        # 윈도우 타이틀 (선택, 디버깅용)
        length = user32.GetWindowTextW(self.hwnd, None, 0)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(self.hwnd, buf, length + 1)
            self.title = buf.value
        else:
            self.title = ""
        return True

    def is_valid(self) -> bool:
        """캡처된 윈도우가 여전히 유효한지."""
        if not self.hwnd:
            return False
        return bool(user32.IsWindow(self.hwnd))

    def restore_focus(self) -> bool:
        """포커스 복원. AllowSetForegroundWindow + SetForegroundWindow."""
        if not self.is_valid():
            return False
        user32.AllowSetForegroundWindow(self.pid)
        ok = bool(user32.SetForegroundWindow(self.hwnd))
        if ok:
            time.sleep(0.05)  # 포커스 전환 안정화
        return ok

    def __repr__(self) -> str:
        return f"TargetApp(hwnd={self.hwnd}, pid={self.pid}, title={self.title!r})"