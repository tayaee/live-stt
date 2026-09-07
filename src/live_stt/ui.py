"""tkinter 메인 윈도우 + 위/아래 2버퍼 (PanedWindow).

프로그램 시작 시 상시 표시. 메인 스레드에서 실행.
다른 스레드에서 UI를 업데이트하려면 `schedule()` 사용.
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Callable, Optional


class MainWindow:
    """메인 윈도우 + 위쪽(raw) / 아래쪽(clean) 2 Text 위젯."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("live-stt")
        self.root.geometry("800x600")
        self.root.minsize(400, 300)

        self._on_quit_callback: Optional[Callable[[], None]] = None

        # 상단 컨트롤
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(control_frame, text="단축키:").pack(side=tk.LEFT)
        self.shortcut_var = tk.StringVar(value="Right Alt (시작/종료)")
        ttk.Label(control_frame, textvariable=self.shortcut_var).pack(side=tk.LEFT, padx=5)

        ttk.Label(control_frame, text="  상태:").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(control_frame, textvariable=self.status_var).pack(side=tk.LEFT, padx=5)

        # PanedWindow (위/아래 2버퍼)
        self.paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 위쪽 (raw)
        raw_frame = ttk.Frame(self.paned)
        ttk.Label(raw_frame, text="위쪽 — Raw (Gemini Live 실시간)").pack(anchor=tk.W)
        self.raw_text = scrolledtext.ScrolledText(
            raw_frame, height=10, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.raw_text.pack(fill=tk.BOTH, expand=True)
        self.raw_text.config(state=tk.DISABLED)
        self.paned.add(raw_frame, weight=1)

        # 아래쪽 (clean)
        clean_frame = ttk.Frame(self.paned)
        ttk.Label(clean_frame, text="아래쪽 — Clean (Gemini 4 31b IT 재작성)").pack(anchor=tk.W)
        self.clean_text = scrolledtext.ScrolledText(
            clean_frame, height=10, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.clean_text.pack(fill=tk.BOTH, expand=True)
        self.clean_text.config(state=tk.DISABLED)
        self.paned.add(clean_frame, weight=1)

        # 사용법
        usage = (
            "Right Alt: 받아쓰기 시작/종료 (자동 Paste) | "
            "Right Shift: 재작성 | "
            "Ctrl+Z: 마지막 재작성 취소"
        )
        ttk.Label(self.root, text=usage, foreground="gray").pack(anchor=tk.W, padx=5, pady=2)

        # 종료 버튼
        ttk.Button(self.root, text="종료", command=self._on_quit).pack(pady=5)

        # 키 바인딩
        self.root.bind("<Control-z>", lambda e: self._undo_last_clean())
        self.root.bind("<Escape>", lambda e: self._on_quit())
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)

    # ── 콜백 등록 ─
    def set_quit_callback(self, callback: Callable[[], None]) -> None:
        """종료 시 호출될 콜백 (daemon 정리용)."""
        self._on_quit_callback = callback

    # ── UI 업데이트 (메인 스레드에서 호출되어야 함) ─
    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def clear_raw(self) -> None:
        self.raw_text.config(state=tk.NORMAL)
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.config(state=tk.DISABLED)

    def append_raw(self, text: str) -> None:
        self.raw_text.config(state=tk.NORMAL)
        self.raw_text.insert(tk.END, text)
        self.raw_text.see(tk.END)
        self.raw_text.config(state=tk.DISABLED)

    def get_raw(self) -> str:
        return self.raw_text.get("1.0", tk.END).rstrip("\n")

    def append_clean(self, text: str) -> None:
        self.clean_text.config(state=tk.NORMAL)
        self.clean_text.insert(tk.END, text.rstrip() + "\n")
        self.clean_text.see(tk.END)
        self.clean_text.config(state=tk.DISABLED)

    def get_clean(self) -> str:
        return self.clean_text.get("1.0", tk.END).rstrip("\n")

    def get_clean_tail(self, n_chars: int) -> str:
        text = self.get_clean()
        return text[-n_chars:] if len(text) > n_chars else text

    def undo_last_clean(self) -> None:
        """마지막 재작성 텍스트 취소 (Ctrl+Z)."""
        self.clean_text.config(state=tk.NORMAL)
        # 마지막 줄 제거
        end_index = self.clean_text.index(tk.END)
        line_str = end_index.split(".")[0]
        line_num = int(line_str)
        if line_num > 1:  # 빈 위젯("2.0")이 아닌 경우
            del_start = f"{line_num - 1}.0"
            self.clean_text.delete(del_start, end_index)
        self.clean_text.config(state=tk.DISABLED)

    # ── 스레드 안전 ─
    def schedule(self, func: Callable, *args) -> None:
        """메인 스레드에서 함수 실행 예약 (다른 스레드에서 호출 가능)."""
        self.root.after(0, func, *args)

    # ── 메인루프 ─
    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        self.root.quit()

    # ── 내부 ─
    def _on_quit(self) -> None:
        if self._on_quit_callback:
            self._on_quit_callback()
        self.root.quit()

    def _undo_last_clean(self) -> None:
        self.undo_last_clean()