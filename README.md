# live-stt

Windows background voice dictation daemon: streams microphone audio to Gemini Live for real-time transcription, then lets Gemini rewrite the accumulated text into clean Korean before auto-pasting it into the active window.

**Platform: Windows only** (uses Win32 `WH_KEYBOARD_LL` hook, `SendInput`, and IME control).

## Run

```cmd
run.bat
```

(`run.bat` invokes the installed `live-stt` binary; run `install.bat` once first to install via `uv tool install -e . --force`.)

CLI override (inline Gemini keys):

```cmd
live-stt --gemini-api-keys "key1,key2,key3"
```

## Gemini API keys

Loaded in priority order (top wins):

| # | Source | Path / format |
|---|---|---|
| 1 | CLI inline | `--gemini-api-keys "key1,key2,key3"` |
| 2 | Current directory | `./gemini-api-keys.txt` (one key per line) |
| 3 | Home config | `~/.config/google-ai/gemini-api-keys.txt` (one key per line) |

If none of the three are present, the daemon exits with an error message at startup.

### File format

```text
# 한 줄에 키 1개, '#' 시작은 주석, 빈 줄 무시
AIzaSyA...key1
AIzaSyB...key2
AIzaSyC...key3
```

### Setup examples

CLI inline (quickest):

```cmd
live-stt --gemini-api-keys "AIzaSyA...,AIzaSyB..."
```

Home config (persistent, recommended):

```cmd
mkdir "%USERPROFILE%\.config\google-ai" 2>nul
notepad "%USERPROFILE%\.config\google-ai\gemini-api-keys.txt"
:: one key per line, save and close
```

Current directory (per-project):

```cmd
notepad gemini-api-keys.txt
:: one key per line, save and close
```

## Shortcuts

| # | Action | Key |
|---|---|---|
| 1 | 받아쓰기 시작 (Start dictation) | **Right Ctrl** |
| 2 | 받아쓰기 종료 (Stop dictation) | **Right Ctrl** (2nd press) |
| 3 | 제미나이에게 문장 정리하기 (Cleanup accumulated text via Gemini) | **Right Shift** |
| 4 | 중간에 정리하기 (Cleanup current buffer mid-dictation) | **Right Shift** |
| 5 | 보내기 (Send cleaned text to active window) | **Right Ctrl** (2nd press — auto-paste) |

Additional: **Ctrl+Z** undo last clean line · **Esc / 종료 버튼** exit daemon.

## How it works

```
[Microphone] → Gemini Live STT → UI top buffer (raw)
                                       │
                              Right Shift │ Gemini rewrite
                                       ↓
                              UI bottom buffer (clean)
                                       │
                        Right Ctrl 2nd │ SendInput + IME close
                                       ↓
                              Active window auto-paste
```

Two text buffers are always visible: top = raw live transcript, bottom = cleaned text. Right Ctrl toggles dictation on/off; Right Shift rewrites on demand.