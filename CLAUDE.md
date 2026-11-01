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

## Current tools
| Module | Tools | Notes |
|---|---|---|
| `youtube.py` | `play_youtube`, `open_youtube_search` | yt-dlp search, opens default browser; `direct` |
| `system.py` | `get_current_time` | `direct` |
| `terminal.py` | `run_shell`, `write_file` | Runs **without confirmation** (user's choice). Starts in `workspace_dir` (`~/Thanos`). `BLOCKED_PATTERNS` hard-refuses sudo, `rm -rf /`/`~`, mkfs, dd to devices, shutdown, fork bombs, `curl \| sh`… — extend that list, don't remove it. `write_file` remaps Linux-style `/home/...` paths. |
| `web.py` | `web_search`, `fetch_webpage` | DuckDuckGo (no key); trafilatura main-text extraction; output trimmed to `max_tool_output_chars` |

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
6. Anything destructive, shell-running, or file-writing → `@tool(dangerous=True)` (asks the user).
7. Add a test in `tests/` with external calls (subprocess, network) mocked, and 2–3 cases in
   `evals/tool_calls.yaml`.

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
- [ ] Spotify control · PDF read/summarize · wake word · tool routing
