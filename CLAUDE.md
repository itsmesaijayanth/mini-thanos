# CLAUDE.md — Thanos (local voice agent)

## Goal
A local, private laptop assistant. You speak → local Whisper transcribes → a small Ollama
model picks a **deterministic tool** (Python function) → the tool does the action
(play YouTube, run shell commands, search/read the web, later Spotify, PDFs, …).
**Reliable tool calling matters more than model smarts.**

## Stack
Python 3.11 · uv · Ollama (`qwen3:8b`, `think=False`, temperature 0, `num_ctx` 16k)
· mlx-whisper (Apple GPU) · kokoro-onnx (neural TTS, `say` fallback) · sounddevice · yt-dlp · ddgs (DuckDuckGo) · httpx · trafilatura
· pydantic / pydantic-settings · rich · pytest

## Commands
```bash
brew install ollama && brew services start ollama   # one-time
ollama pull qwen3:8b                                # one-time (~5 GB)
uv sync                                             # install deps
uv run thanos                                       # voice mode (Enter = talk)
uv run thanos --text                                # type instead of talk
uv run thanos --once "play believer on youtube"     # single command, then exit
uv run thanos --mute                                # any mode, without spoken replies
uv run pytest                                       # unit tests (no Ollama needed)
uv run python evals/run_evals.py --model qwen3:8b   # tool-calling accuracy (needs Ollama)
```
Settings: `config.yaml` (or env vars `THANOS_<FIELD>`, e.g. `THANOS_MODEL=qwen2.5:7b`).

## Architecture
```
mic → voice/recorder.py → voice/stt.py → agent/loop.py ⇄ llm/ollama_client.py
                                              ↓
                                   tools/registry.py → tools/<module>.py → ToolResult
                                              ↓
                                   final reply → console + voice/tts.py (spoken)
```
- `thanos/config.py` — the only source of settings (model, host, STT model, max_steps, tts).
- `thanos/llm/` — `LLMClient` protocol + Ollama implementation. Agent depends on the protocol only.
- `thanos/agent/` — tool-call loop (call LLM → validate → execute → feed result back → repeat
  up to `max_steps`), plus the short system prompt.
- `thanos/tools/` — `registry.py` (`@tool` decorator, JSON-schema generation, validation,
  dispatch, auto-discovery) + one module per tool area. `base.py` has `ToolResult`.
- `thanos/voice/` — push-to-talk recorder with silence auto-stop, STT, TTS.
  `tts.py`: `Speaker.speak()` is non-blocking and streams sentence by sentence; `stop()` is
  called on Enter (barge-in, so Thanos never records itself). `clean_for_speech()` strips
  URLs/markdown/emoji/list numbers, speaks file names by kind ("valid_lossrun.pdf" →
  "valid lossrun PDF", "1000055692.jpg" → "an image"), and caps length (`tts_max_chars`).
  Kokoro model files are cached in `~/.cache/thanos/kokoro/` (auto-downloaded, ~350 MB).
- `evals/` — utterance → expected tool/args cases and a runner that scores a model.

## Current tools (50; all deterministic, no API keys)
| Module | Tools | Notes |
|---|---|---|
| `youtube.py` | `play_youtube`, `open_youtube_search` | yt-dlp search; default for "play X" |
| `system.py` | `get_current_time` | |
| `terminal.py` | `run_shell`, `write_file` | Runs **without confirmation** (user's choice) in `workspace_dir` (`~/Thanos`). `BLOCKED_PATTERNS` hard-refuses sudo, `rm -rf /`/`~`, mkfs, dd to devices, shutdown, fork bombs, `curl \| sh`… — extend, don't remove. |
| `web.py` | `web_search`, `fetch_webpage` | DuckDuckGo + trafilatura |
| `macos.py` | volume (`set_volume`, `change_volume`, `mute`), `change_brightness`, `open_app`, `quit_app`, `lock_screen`, `sleep_display`, `set_dark_mode`, `battery_status`, `take_screenshot` | osascript / pmset / screencapture. Brightness & lock need Accessibility permission |
| `timers.py` | `set_timer`, `set_alarm`, `list_timers`, `cancel_timer`, `add_reminder` | Timers/alarms are in-process (`threading.Timer` → `services.notify`: chime + notification + speech) and die when Thanos exits; `add_reminder` goes to the Reminders app (persists) |
| `info.py` | `get_weather`, `get_news`, `wikipedia_summary`, `define_word`, `convert_currency`, `calculate`, `convert_units` | Open-Meteo (+ ip-api for home city, or `home_city`), Google News RSS, Wikipedia REST (needs descriptive User-Agent), dictionaryapi.dev, Frankfurter, safe AST calculator (never `eval`), pint |
| `media.py` | `play_music`, `music_control`, `now_playing`, `youtube_control` | Music app = **your library only** (Apple Music streaming isn't scriptable); status/pause never launch Music. `youtube_control` runs JS in a Brave/Safari YouTube tab — needs "Allow JavaScript from Apple Events" once |
| `apple_apps.py` | `create_note`, `find_notes`, `read_note`, `calendar_events`, `add_calendar_event`, `read_clipboard`, `copy_to_clipboard` | AppleScript; dates built from parts via `_osa.applescript_date` (locale-proof). Calendar query doesn't expand recurring events |
| `files.py` | `find_files`, `open_path`, `recent_downloads`, `read_pdf` | Spotlight (`mdfind`) in ~, skips code/dev dirs, documents first; pypdf |
| `websites.py` | `open_website`, `google_search`, `maps_directions` | Known-site table, else DuckDuckGo `!ducky` |
| `messaging.py` | `send_imessage`, `send_whatsapp` | Contact lookup in Contacts; **always** `services.confirm(...)` showing exact person/number/text; WhatsApp only pre-fills (user presses Send) |

Shared helpers (underscore = not auto-discovered): `_osa.py` (AppleScript runner, friendly
permission errors, quoting, dates), `_timeparse.py` ("in 20 minutes", "7:30 pm", "tomorrow at 9" —
times are parsed in Python, never by the LLM). `thanos/services.py`: `notify()` and `confirm()`
that tools call; the CLI installs real handlers (tests use fakes; no handler = confirm says no).

**macOS permissions** (one-time "Allow" pop-ups for the app running Thanos): Automation for
Notes, Calendar, Reminders, Music, Messages, Contacts, System Events; Accessibility for
brightness/lock keystrokes.

## Tool router (`thanos/agent/router.py`)
Embeds each tool's `routing_text()` (name + docstring + `examples=[...]`) with Ollama
`nomic-embed-text` (cached in `~/.cache/thanos/tool_embeddings.json`, re-embedded only when the
text changes) and offers the model the top `router_top_k` tools + `always_tools` + tools used last
turn, in a stable order. Falls back to all tools if embedding fails. Tune with `examples=` on
`@tool`, `router_top_k`, `always_tools`; check with `evals/run_evals.py` (reports router recall).
`--no-router` in the evals offers all tools.

**Off by default** (`router_enabled: false`). Measured 2026-11-05 on M4/16 GB, qwen3:8b, 87 evals:
router 98% correct but ~8 s/request; all 50 tools 95% at ~2.7 s/request. Ollama caches the prompt
prefix (system prompt + tool list): a fixed tool list is read once (~33 s for ~4.5k tokens at ~156
tok/s), after which requests only pay for new tokens (generation ~18 tok/s). A per-request tool list
breaks that cache. The CLI calls `Agent.warm_cache()` at startup so the first command is fast.
qwen3:4b was tried and was *slower* (~9-26 s/request: it kept reasoning).

## Agent safeguards (thanos/agent/loop.py)
- Up to `max_steps` (10) LLM rounds; on exhaustion it reports what was completed.
- Identical repeated calls (same tool + args) in one request are not re-run; the model is told to move on.
- Every tool message is trimmed to `max_tool_output_chars` (start + end kept).
- Text-form tool calls (JSON in content) are recovered by `agent/parsing.py`.
- Small models are weak at long open-ended chains (search → read → extract → save ×3). For
  frequent workflows, prefer a dedicated deterministic tool
  over hoping the model chains generic tools.

## Adding a tool (the main rule)
1. Create/extend a module in `thanos/tools/` — it is auto-discovered, no registration edits.
2. Decorate with `@tool(category="...")`. Fully type-hint every parameter; use `Literal[...]`
   for fixed choices and defaults for optional params.
3. Write a clear docstring: first line = what it does + when to use it; then `Args:` lines.
   **The model reads this** — be concrete, include an example value.
4. Return `ToolResult.success(...)` / `ToolResult.fail(...)`. Don't raise for expected failures
   (the registry catches exceptions anyway, but explicit messages help the model).
5. Keep tools deterministic — no LLM calls inside tools.
6. Anything destructive, shell-running, or file-writing → `@tool(dangerous=True)` (asks the user);
   if the prompt must show resolved details (e.g. the contact's number), call `services.confirm()`
   inside the tool instead.
7. Add a test in `tests/` with external calls (subprocess, network) mocked, and 2–3 cases in
   `evals/tool_calls.yaml`.
8. Add `examples=[...]` (3-5 natural phrasings) so the router can find the tool.

## Conventions
- No hardcoded model names/paths/hosts outside `config.py`.
- Every tool call and result is logged (rich console) — keep it that way for debugging.
- Keep the system prompt short; small models follow short rules better.
- After changing the prompt, model, or a tool's signature/docstring → rerun the evals.
- When tool count grows past ~15, use the registry's category filtering to send only
  relevant tools to the model.

## Roadmap
- [x] Iteration 1: voice→text, Ollama setup, tool-calling core, YouTube playback
- [x] Iteration 2: terminal (`run_shell`, `write_file`) + web (`web_search`, `fetch_webpage`)
- [x] Iteration 3: spoken replies (Kokoro neural voice, barge-in, speech-friendly text)
- [x] Iteration 5: 43 daily tools (Mac, timers, info, media, notes/calendar, files/PDF, web, messaging) + embedding router
- [ ] Spotify control · wake word
