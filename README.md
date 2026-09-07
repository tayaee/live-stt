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
| 2 | 받아쓰기 종료 + Gemma 재작성 + 자동 입력 (Stop + Rewrite + Paste) | **Right Ctrl** (2nd press) |

Additional: **Ctrl+Z** undo last clean line · **Esc / 종료 버튼** exit daemon.

## How it works

```
[Microphone] → Gemini Live STT → UI top buffer (raw)
                                       │
                        Right Ctrl 2nd │ Transcribed Text to Gemma
                                       ↓
                               Gemma 4 31b IT rewrite
                                       ↓
                               UI bottom buffer (clean)
                                       ↓
                               SendInput + IME close
                                       ↓
                               Active window auto-paste
```

Two text buffers are always visible: top = raw live transcript, bottom = cleaned text. Right Ctrl toggles dictation on/off; stopping dictation automatically sends the raw transcribed text to Gemma for rewriting and pastes it into the active window.