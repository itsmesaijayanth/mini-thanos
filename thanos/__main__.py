"""CLI entry point.

    uv run thanos                 # voice mode: Enter = talk (or type a command)
    uv run thanos --text          # text-only mode
    uv run thanos --once "..."    # run one command and exit
    add --mute to any of them to turn spoken replies off
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

from thanos import services
from thanos.agent import Agent, ToolEvent
from thanos.agent.router import create_router
from thanos.config import Settings
from thanos.llm.ollama_client import OllamaClient
from thanos.tools import registry
from thanos.voice.tts import NullSpeaker, Speaker, create_speaker

console = Console()
SpeakerT = Speaker | NullSpeaker
EXIT_WORDS = {"q", "quit", "exit", "bye"}
PREVIEW_CHARS = 140  # tool output shown in the console (the model still gets it all)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    overrides: dict[str, object] = {"model": args.model} if args.model else {}
    if args.mute:
        overrides["tts"] = False
    settings = Settings(**overrides)

    registry.discover()
    llm = OllamaClient(settings)
    try:
        llm.ensure_model()
    except RuntimeError as e:
        console.print(f"[bold red]{e}[/]")
        return 1

    router = create_router(registry, settings)
    agent = Agent(
        llm, registry, settings, confirm=_confirm, on_tool_event=_print_tool_event, router=router
    )
    with console.status("[cyan]loading voice and tool index (first run downloads/builds them)…"):
        speaker = create_speaker(settings)
        if router:
            try:
                router.warmup()
            except Exception as e:
                console.print(f"[yellow]Tool router off ({e}); run: ollama pull {settings.embed_model}[/]")
    services.set_confirmer(_confirm)
    services.set_notifier(lambda text: _notify(text, speaker))
    routing = f"router top {settings.router_top_k}" if router else "router off"
    console.print(
        f"[dim]model={settings.model} · voice={speaker.name} · "
        f"{len(registry)} tools ({routing})[/]"
    )

    try:
        if args.once:
            _handle(agent, args.once, speaker)
            speaker.wait()
            _wait_for_timers()
            return 0
        if args.text:
            with console.status("[cyan]warming up the model…"):
                agent.warm_cache()
            return _text_loop(agent, speaker)
        return _voice_loop(agent, settings, llm, speaker)
    finally:
        speaker.stop()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="thanos", description="Local voice agent")
    p.add_argument("--text", action="store_true", help="type commands instead of speaking")
    p.add_argument("--once", metavar="COMMAND", help="run a single command and exit")
    p.add_argument("--model", help="override the Ollama model from config.yaml")
    p.add_argument("--mute", action="store_true", help="don't speak replies out loud")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


def _handle(agent: Agent, text: str, speaker: SpeakerT) -> None:
    t0 = time.perf_counter()
    with console.status("[cyan]thinking…"):
        reply = agent.run(text)
    console.print(f"[bold green]Thanos:[/] {reply.text}  [dim]({time.perf_counter() - t0:.1f}s)[/]")
    speaker.speak(reply.text)


def _text_loop(agent: Agent, speaker: SpeakerT) -> int:
    console.print("[dim]Type a command (q to quit, /reset to clear memory).[/]")
    while True:
        try:
            text = console.input("[bold cyan]You:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        speaker.stop()
        if not _dispatch_input(agent, text, speaker):
            return 0


def _voice_loop(agent: Agent, settings: Settings, llm: OllamaClient, speaker: SpeakerT) -> int:
    from thanos.voice.recorder import record_utterance
    from thanos.voice.stt import create_stt

    stt = create_stt(settings)
    with console.status("[cyan]loading speech + language models (first run downloads them)…"):
        stt.warmup()
        llm.warmup()
        agent.warm_cache()
        speaker.warmup()

    console.print("[dim]Press Enter and speak · or type a command · q to quit.[/]")
    while True:
        try:
            typed = console.input("[bold cyan]⏎ talk:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        speaker.stop()  # barge-in: never record Thanos's own voice
        if typed:
            if not _dispatch_input(agent, typed, speaker):
                return 0
            continue

        with console.status("[red]● listening… (stops when you pause)"):
            try:
                audio = record_utterance(settings)
            except Exception as e:
                console.print(f"[red]Microphone error: {e}[/]")
                console.print("[dim]Allow microphone access for your terminal app in System Settings → Privacy & Security → Microphone.[/]")
                continue
        if audio.size == 0:
            console.print("[yellow]Didn't hear anything.[/]")
            continue
        with console.status("[cyan]transcribing…"):
            text = stt.transcribe(audio)
        if not text:
            console.print("[yellow]Couldn't understand that.[/]")
            continue
        console.print(f"[bold cyan]You said:[/] {text}")
        if not _dispatch_input(agent, text, speaker):
            return 0


def _dispatch_input(agent: Agent, text: str, speaker: SpeakerT) -> bool:
    """Handle one line of input. Returns False when the user wants to quit."""
    if not text:
        return True
    lowered = text.lower().strip(" .!?")
    if lowered in EXIT_WORDS:
        return False
    if lowered == "/reset":
        agent.reset()
        console.print("[dim]memory cleared[/]")
        return True
    try:
        _handle(agent, text, speaker)
    except Exception as e:
        logging.getLogger(__name__).debug("agent error", exc_info=True)
        console.print(f"[red]Error: {e}[/]")
    return True


def _notify(text: str, speaker: SpeakerT) -> None:
    """Timer/alarm alert: chime + macOS notification + spoken text."""
    console.print(f"\n[bold yellow]⏰ {escape(text)}[/]")
    subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
    msg = text.replace("\\", "").replace('"', "'")
    subprocess.Popen(["osascript", "-e", f'display notification "{msg}" with title "Thanos" sound name "Glass"'])
    speaker.speak(text)


def _wait_for_timers() -> None:
    """In --once mode, keep running until pending timers/alarms have fired (Ctrl+C cancels)."""
    from thanos.tools.timers import timers

    if not timers.active():
        return
    console.print("[dim]Waiting for your timer… (Ctrl+C to cancel)[/]")
    try:
        while timers.active():
            time.sleep(0.5)
        time.sleep(4)  # let the alert speak
    except KeyboardInterrupt:
        timers.cancel("all")


def _print_tool_event(ev: ToolEvent) -> None:
    args = ev.arguments if isinstance(ev.arguments, str) else json.dumps(ev.arguments)
    status = "[green]✓[/]" if ev.result.ok else "[red]✗[/]"
    preview = " ".join(ev.result.message.split())
    if len(preview) > PREVIEW_CHARS:
        preview = preview[:PREVIEW_CHARS] + "…"
    console.print(
        f"  [magenta]→ {ev.name}[/]({_short(args)}) {status} [dim]{escape(preview)}[/]"
    )


def _short(text: str, limit: int = 160) -> str:
    return escape(text if len(text) <= limit else text[:limit] + "…")


def _confirm(prompt: str) -> bool:
    try:
        return console.input(f"[yellow]{prompt} [y/N][/] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


if __name__ == "__main__":
    sys.exit(main())
