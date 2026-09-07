# live-stt handoff.md

Windows 백그라운드 음성 받아쓰기 데몬. **Right Alt** 1회 = 받아쓰기 시작, 2회 = 종료+자동 Paste. **Right Shift** = 지금까지 발화 내용 재작성. tkinter UI에 **위쪽(Raw)** / **아래쪽(Clean)** 두 텍스트 버퍼를 상시 표시.

마지막 갱신: 2026-09-07 (Phase 2c 통합 완료 — Windows 검증 대기)

---

## 1. 한 줄 요약

```
[마이크] → [Gemini Live STT] → UI 위쪽 버퍼 (raw)
                                     │
                            Right Shift │ Gemini 4 31b IT 재작성
                                     ↓
                            UI 아래쪽 버퍼 (clean)
                                     │
                          Right Alt 2nd│ SendInput + IMM close
                                     ↓
                          활성 윈도우 hwnd에 자동 입력
```

---

## 2. 프로젝트 메타

| 항목 | 값 |
|---|---|
| 이름 | `live-stt` |
| 바이너리 | `live-stt.exe` (Windows) |
| Python | ≥ 3.14 |
| 패키징 | `src/` layout + hatchling |
| 설치 | `uv tool install -e . --force` |
| 단독 실행 | `uv run live-stt.py` (PEP 723) |
| 의존성 | `google-genai`, `sounddevice`, `numpy` |

---

## 3. 그릴링 라운드 요약

### 라운드 1 — Windows vs WSL
- **결정**: Windows 네이티브
- **이유**: 키보드 hook, IME 조작, `SendInput` 모두 Win32 API

### 라운드 2 — 패키지 구조
- **결정**: `src/` layout (PEP 660 editable) + PEP 723 인라인 스크립트 단독 실행 옵션

### 라운드 3 — 바이너리 vs 인터프리터
- **결정**: `uv tool install -e .` 등록 → 콘솔에서 `live-stt` 호출

### 라운드 4 — 핵심 결정 트리
- **결정**: 트리거 한 단계로 단순화 (LISTENING on/off 단일 상태, 별도 RECEIVING 단계 없음)
- 초기 후보(CapsLock 더블탭, Shift-F1, L+R Shift, Right Alt) 중 **Right Alt** 채택

### 라운드 5 — Right Alt/Shift + 이중 버퍼
- 받아쓰기 도중 **Right Shift** = 지금까지 raw를 Gemini에 보내 재작성 → clean에 append
- Right Alt 종료 시 clean 전체 → `SendInput`으로 자동 입력

### 라운드 6 — 그릴링 질문 답변 (모두 추천안 수락)
- Q-A: 시작 시 위쪽 버퍼 클리어 ✅
- Q-B: Right Shift마다 아래쪽 append ✅
- Q-C: 종료 시 아래쪽 clean만 Paste ✅
- Q-D: 자동 부분 교정 제거 ✅
- Q-E: 명시적 문장 종결 키 제거 ✅
- Q-F: PanedWindow VERTICAL ✅
- Q-G: 재작성 컨텍스트 = raw + clean tail ✅
- Q-H: 실시간 청크 교정 제거 (Shift에 한 번에 처리) ✅
- Q-I: 종료는 UI 버튼 / Ctrl+C ✅

### 라운드 7 (Phase 2c) — UI 디자인 변경 요청 → 수용 보류
- 사용자가 "단축키 버튼으로 재정의" 제안했지만 이번 초판은 **고정 단축키**로 단순 구현 (재정의 UI는 후속)
- 동작 흐름은 그대로 유지, UI에 두 텍스트 뷰만 노출

---

## 4. 최종 디자인

### 4.1 UI

```
┌─────────────────────────────────────────────────┐
│ 단축키: Right Alt (시작/종료)   상태: 🔴 받아쓰기 진행 중   │
├─────────────────────────────────────────────────┤
│ 위쪽 — Raw (Gemini Live 실시간)                  │
│ ┌─────────────────────────────────────────────┐ │
│ │안녕하세요 오늘은 날씨가                                       │ │
│ │정말 좋네요                                                    │ │
│ └─────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────┤
│ 아래쪽 — Clean (Gemini 4 31b IT 재작성)         │
│ ┌─────────────────────────────────────────────┐ │
│ │안녕하세요. 오늘은 날씨가 정말 좋네요.            │ │
│ └─────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────┤
│ Right Alt: 시작/종료 | Right Shift: 재작성 | Ctrl+Z: 마지막 취소 │
│                              [ 종료 ]                       │
└─────────────────────────────────────────────────┘
```

### 4.2 트리거 키

| 키 | 동작 |
|---|---|
| **Right Alt** (1st) | hwnd 캡처 → 마이크 ON → Gemini Live 시작 → 위쪽 버퍼 클리어 |
| **Right Shift** | 위쪽 raw + 아래쪽 tail(N=500자) → Gemini 4 31b IT → 아래쪽 append |
| **Right Alt** (2nd) | 마이크 OFF → Gemini Live 종료 → 아래쪽 clean → `SendInput`으로 자동 입력 |
| **Ctrl+Z** | 아래쪽 버퍼 마지막 줄 삭제 (재작성 취소) |
| **Esc / 종료 버튼** | 데몬 종료 (hook 해제 + asyncio loop stop + Ctrl+C 가능) |

### 4.3 스레딩 모델

```
메인 스레드       : tkinter mainloop + WH_KEYBOARD_LL hook 콜백
asyncio 스레드   : Gemini Live send_loop / recv_loop
sounddevice 스레드: 마이크 콜백 → transcriber 큐에 put
rewrite 스레드    : Right Shift마다 새로 spawn (결과만 반환)
```

UI 업데이트는 모두 `ui.schedule(func, *args)` (= `root.after(0, ...)`) 사용 → 어느 스레드에서든 안전.

---

## 5. 아키텍처

### 5.1 디렉토리

```
live-stt/
├── pyproject.toml             # hatchling + deps + script entry
├── pyrightconfig.json         # extraPaths: src
├── live-stt.py                # PEP 723 standalone (uv run live-stt.py)
├── handoff.md                 # 본 문서
├── README.md
└── src/live_stt/
    ├── __init__.py
    ├── config.py              # 상수 + get_api_key()
    ├── daemon.py              # 통합 진입점
    ├── ui.py                  # MainWindow (tkinter)
    ├── audio.py               # AudioStream (sounddevice)
    ├── live.py                # LiveTranscriber (Gemini Live)
    ├── rewrite.py             # Rewriter (Gemini 4 31b IT)
    ├── target.py              # TargetApp (hwnd 캡처 + focus 복원)
    ├── trigger.py             # TriggerHook (WH_KEYBOARD_LL)
    └── inject.py              # send_text + clear_ime
```

### 5.2 주요 흐름 (코드)

#### 시작 (Right Alt 1st)

```
trigger hook (메인 스레드)
   └─ Daemon._on_alt_pressed
        └─ ui.schedule(Daemon._toggle_listening)
             └─ Daemon._toggle_listening
                  ├─ TargetApp.capture()           # GetForegroundWindow + GetFocus
                  ├─ ui.clear_raw()
                  ├─ AudioStream.start()           # 16kHz 16-bit mono
                  ├─ is_listening = True
                  ├─ ui.set_status("🔴 받아쓰기 진행 중")
                  └─ asyncio.run_coroutine_threadsafe(
                       LiveTranscriber.start(), loop)
                       └─ session.send_realtime_input(audio=Blob)
                       └─ recv_loop → on_text → ui.schedule(append_raw)
```

#### 재작성 (Right Shift)

```
trigger hook → Daemon._on_shift_pressed
   └─ ui.schedule(Daemon._do_rewrite)
        ├─ ui.get_raw()  +  ui.get_clean_tail(500)
        ├─ ui.set_status("✍️ 재작성 중...")
        └─ threading.Thread(_do, daemon=True).start()
             └─ Rewriter.rewrite(raw, tail)
                  └─ response.text.strip()
             └─ ui.schedule(ui.append_clean, rewritten)
             └─ ui.schedule(ui.set_status, "🔴 받아쓰기 진행 중")
```

#### 종료 (Right Alt 2nd)

```
Daemon._stop_listening
   ├─ AudioStream.stop()                         # sounddevice stop + close
   ├─ asyncio.run_coroutine_threadsafe(
   │      LiveTranscriber.stop()).result(2.0)   # session.close + cancel tasks
   ├─ ui.get_clean()
   ├─ TargetApp.is_valid() + restore_focus()    # AllowSetForegroundWindow + SetForegroundWindow
   ├─ clear_ime(hwnd)                            # ImmNotifyIME(IMN_CLOSECANDIDATE)
   ├─ send_text(clean)                           # SendInput KEYEVENTF_UNICODE × len
   ├─ ui.set_status("✅ Paste 완료")
   └─ is_listening = False
```

---

## 6. 구현 단계

| Phase | 상태 | 내용 |
|---|---|---|
| 1.1 | ✅ | 디렉토리 이름 변경 (`capslock_live_stt` → `live_stt`) |
| 1.2 | ✅ | `pyproject.toml` 갱신 |
| 1.3 | ✅ | 패키지 디렉토리 이름 변경 |
| 1.4 | ✅ | PEP 723 인라인 스크립트 보존 |
| 1.5 | ✅ | `uv tool install -e . --force` 검증 |
| 2a | ✅ | 인프라 (config/target/trigger/inject) |
| 2b | ✅ | AI (audio/live/rewrite) |
| 2c | ✅ | **UI + 통합 (daemon.py)** |
| 5 | 🔵 | **Windows 검증 + 개선점 도출** |
| 4 | ⏳ | (deferred) 자동 시작 레지스트리 등록 |
| 후속 | ⏳ | 단축키 재정의 UI, 트레이 최소화, 그릴링 후속 |

---

## 7. 핵심 코드 스니펫

### 7.1 WH_KEYBOARD_LL hook (메인 스레드 필수)

```python
# src/live_stt/trigger.py
import ctypes
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
VK_RMENU = 0xA5
VK_RSHIFT = 0xA1

LPFN_LOWLEVELKEYBOARDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

class TriggerHook:
    def __init__(self, on_alt, on_shift):
        self.on_alt = on_alt
        self.on_shift = on_shift
        self._hook_id = None

    def install(self):
        user32 = ctypes.windll.user32
        CMP_FLAGS = 0
        self._proc = LPFN_LOWLEVELKEYBOARDPROC(self._callback)
        self._hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, user32.GetModuleHandleW(None), 0
        )
        if not self._hook_id:
            raise RuntimeError("SetWindowsHookExW failed")

    def _callback(self, nCode, wParam, lParam):
        if nCode == 0 and wParam == WM_KEYDOWN:
            vk = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong))[0]
            try:
                if vk == VK_RMENU:  self.on_alt()
                elif vk == VK_RSHIFT: self.on_shift()
            except Exception:
                pass
        user32 = ctypes.windll.user32
        return user32.CallNextHookEx(self._hook_id, nCode, wParam, lParam)
```

### 7.2 SendInput + IME clear

```python
# src/live_stt/inject.py
import ctypes
from ctypes import wintypes

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
IMN_CLOSECANDIDATE = 0x11

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 64)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

def _send_char(ch: str):
    user32 = ctypes.windll.user32
    code = ord(ch)
    down = INPUT(type=1, u=_INPUT_UNION(ki=KEYBDINPUT(
        wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None)))
    up   = INPUT(type=1, u=_INPUT_UNION(ki=KEYBDINPUT(
        wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
    user32.SendInput(2, (INPUT * 2)(down, up), ctypes.sizeof(INPUT))

def send_text(text: str, pacing_ms: float = 2.0):
    for ch in text:
        _send_char(ch)
        if pacing_ms > 0:
            ctypes.windll.kernel32.Sleep(int(pacing_ms))

def clear_ime(hwnd: int):
    imm = ctypes.windll.imm32
    hIMC = imm.ImmGetContext(hwnd)
    if hIMC:
        imm.ImmNotifyIME(hIMC, IMN_CLOSECANDIDATE, 0x02, 0)
        imm.ImmReleaseContext(hwnd, hIMC)
```

### 7.3 Focus stolen 복원

```python
# src/live_stt/target.py
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class TargetApp:
    def capture(self) -> bool:
        self.hwnd = user32.GetForegroundWindow()
        self.pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(self.pid))
        return self.is_valid()

    def is_valid(self) -> bool:
        return bool(self.hwnd) and user32.IsWindow(self.hwnd)

    def restore_focus(self):
        if not self.is_valid():
            return
        user32.AllowSetForegroundWindow(self.pid.value)
        user32.SetForegroundWindow(self.hwnd)
```

### 7.4 Gemini Live

```python
# src/live_stt/live.py
from google import genai
from google.genai import types

class LiveTranscriber:
    def __init__(self, on_text):
        self.client = genai.Client(api_key=get_api_key())
        self.on_text = on_text
        self._audio_queue: asyncio.Queue[bytes] | None = None
        self._config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def start(self):
        self._audio_queue = asyncio.Queue()
        self.session = await self.client.aio.live.connect(
            model=GEMINI_LIVE_MODEL, config=self._config)
        asyncio.create_task(self._send_loop())
        asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        async for response in self.session.receive():
            sc = response.server_content
            if sc and sc.input_transcription:
                text = sc.input_transcription.text
                if text:
                    self.on_text(text)
```

### 7.5 audio → transcriber 라우팅 (콜백 모드)

```python
# Daemon은 on_chunk만 등록하면 됨 — 큐 폴링 불필요
self.audio = AudioStream(on_chunk=self._on_audio_chunk)

def _on_audio_chunk(self, chunk: bytes):
    if self.transcriber:
        self.transcriber.enqueue_audio(chunk)
```

### 7.6 스레드 안전 UI 업데이트

```python
# src/live_stt/ui.py
def schedule(self, func, *args):
    self.root.after(0, func, *args)  # 메인 스레드에서 실행 예약

# 사용 예 (어느 스레드에서든):
self.ui.schedule(self.ui.append_raw, "안녕")
```

---

## 8. 알려진 한계 / 주의사항

### 8.1 Linux 개발 환경
- `ctypes.windll.*` 호출 시 `AttributeError` → Linux에선 import조차 안 됨
- `sounddevice` 모듈 + PortAudio는 Windows에서 사용 가능 (Linux엔 `.venv`에 별도 설치)
- `tkinter`는 Linux/Win 둘 다 동작하지만 display 필요

### 8.2 pyright 진단 (Linux venv)
- `Import "sounddevice" could not be resolved` — Linux venv에 미설치 (정상)
- Pyright는 `reportMissingImports: none` + `reportUnused*: none`으로 진단 정리 (`pyrightconfig.json`)
- Windows에서 `uv sync` 후 정밀 진단 가능

### 8.3 Windows 측 주의
1. **마이크 권한**: 첫 실행 시 OS에서 마이크 접근 허용 다이얼로그 → 허용 필수
2. **Gemini API 키**: `GEMINI_API_KEY` 환경변수 (또는 `.env` 로더 확장 가능)
3. **보안 프로그램**: 키보드 hook이 일부 키로거 차단 SW에서 차단될 수 있음
4. **IME**: 한글 입력 중인 타겟에 Paste 시 `clear_ime`로 composition 종료 후 전송 (한국어 syllable 한 글자 = 1 SendInput 호출이라 사전완성형 보장)
5. **다른 데스크톱/관리자 권한 앱**: focus steal이 안 될 수 있음

### 8.4 Gemini Live 모델
- `models/gemini-3.5-transcribe-live` — Preview 모델, 변경 가능성 있음
- 응답 지연/가격 등은 Google 문서 참조

---

## 9. 확장 항목 (deferred)

다음 라운드에서 우선 협의할 후보:

| # | 기능 | 효과 | 복잡도 |
|---|---|---|---|
| E1 | **자동 시작** (`HKCU\...\Run` 등록) | 부팅 후 자동 실행 | M |
| E2 | **단축키 재정의 UI** | 사용자가 키 변경 | M |
| E3 | **문장 종결 키** (Enter / `.` / `?`) | 실시간 부분 교정 | M |
| E4 | **3초 침묵 자동 교정** | 빈 묵음마다 재작성 | M |
| E5 | **부분 누적 모드** (Shift 안 눌러도 계속 누적) | 발화 단위 자동 처리 | L |
| E6 | **클립보드 모드** (Paste 대신 클립보드 복사) | 멀티앱 활용 | S |
| E7 | **tray minimization** | UI 숨김/축소 | S |
| E8 | **.env 로더 + 다중 API 키** | 키 순환 | S |
| E9 | **STT 모델 선택** (Live vs Batch) | 비용/품질 트레이드오프 | L |
| E10 | **시각 표시기** (overlay icon) | 현재 상태 외부 표시 | M |

---

## 10. 다음 라운드 작업 순서 (사용자 작업)

1. Windows PowerShell에서:
   ```powershell
   $env:GEMINI_API_KEY = "your-key"
   git clone <repo> && cd live-stt
   uv tool install -e . --force
   live-stt
   ```
2. 5가지 시나리오 실측:
   - 발화 → 위쪽 실시간 표시 확인
   - Right Shift → 아래쪽 정확히 재작성되는지
   - Right Alt 2nd → 타겟 앱에 깨짐 없이 붙여넣기 (한글 포함)
   - Ctrl+Z → 마지막 라인 취소
   - 종료 버튼 후 재실행 시 hook 정상 동작
3. 문제점 보고 → 다음 라운드 그릴링
