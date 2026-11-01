"""Score a model's tool-calling accuracy on evals/tool_calls.yaml.

Only the FIRST LLM turn is evaluated and tools are NOT executed (nothing opens).

    uv run python evals/run_evals.py --model qwen3:8b
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from thanos.agent.parsing import extract_text_tool_calls
from thanos.agent.prompts import build_system_prompt
from thanos.config import Settings
from thanos.llm.ollama_client import OllamaClient
from thanos.tools import registry

CASES = Path(__file__).with_name("tool_calls.yaml")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Ollama model (default: config.yaml)")
    args = ap.parse_args()

    settings = Settings(**({"model": args.model} if args.model else {}))
    registry.discover()
    llm = OllamaClient(settings)
    llm.ensure_model()
    llm.warmup()
    tools = registry.schemas()
    cases = yaml.safe_load(CASES.read_text())

    console = Console()
    table = Table(title=f"Tool-call eval · {settings.model}")
    for col in ("", "utterance", "expected", "got", "s"):
        table.add_column(col)

    passed, latencies = 0, []
    for case in cases:
        t0 = time.perf_counter()
        resp = llm.chat(
            [{"role": "system", "content": build_system_prompt(settings)}, {"role": "user", "content": case["say"]}],
            tools,
        )
        latencies.append(time.perf_counter() - t0)
        calls = resp.tool_calls or extract_text_tool_calls(resp.content, registry.names())
        got = calls[0] if calls else None
        ok = _check(case, got)
        passed += ok
        got_str = f"{got.name}({got.arguments})" if got else f"text: {resp.content[:40]!r}"
        table.add_row(
            "[green]✓[/]" if ok else "[red]✗[/]",
            case["say"],
            str(case.get("tool")),
            got_str,
            f"{latencies[-1]:.1f}",
        )

    console.print(table)
    pct = 100 * passed / len(cases)
    console.print(
        f"[bold]{passed}/{len(cases)} passed ({pct:.0f}%)[/] · "
        f"avg latency {sum(latencies) / len(latencies):.2f}s"
    )
    return 0 if pct >= 95 else 1


def _check(case: dict, got) -> bool:
    expected = case.get("tool")
    if expected is None:
        return got is None
    if got is None or got.name != expected:
        return False
    got_args = got.arguments if isinstance(got.arguments, dict) else {}
    for key, needle in (case.get("args_contain") or {}).items():
        if str(needle).lower() not in str(got_args.get(key, "")).lower():
            return False
    return True


if __name__ == "__main__":
    sys.exit(main())
