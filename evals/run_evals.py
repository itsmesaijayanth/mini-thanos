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
from thanos.agent.router import create_router
from thanos.config import Settings
from thanos.llm.ollama_client import OllamaClient
from thanos.tools import registry

CASES = Path(__file__).with_name("tool_calls.yaml")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Ollama model (default: config.yaml)")
    ap.add_argument("--no-router", action="store_true", help="offer every tool (old behaviour)")
    ap.add_argument("--all-rows", action="store_true", help="show passing cases too")
    args = ap.parse_args()

    settings = Settings(**({"model": args.model} if args.model else {}))
    registry.discover()
    llm = OllamaClient(settings)
    llm.ensure_model()
    llm.warmup()
    router = None if args.no_router else create_router(registry, settings)
    if router:
        router.warmup()
    cases = yaml.safe_load(CASES.read_text())
    system = build_system_prompt(settings)

    console = Console()
    mode = f"router top {settings.router_top_k}" if router else "all tools"
    table = Table(title=f"Tool-call eval · {settings.model} · {mode}")
    for col in ("", "utterance", "expected", "got", "routed?", "s"):
        table.add_column(col)

    passed = routed_ok = routed_total = 0
    latencies = []
    for case in cases:
        expected = _expected(case)
        names = router.select(case["say"]) if router else registry.names()
        tools = registry.schemas_for(names)
        in_route = None
        if expected:
            routed_total += 1
            in_route = any(e in names for e in expected)
            routed_ok += in_route
        t0 = time.perf_counter()
        resp = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": case["say"]}], tools)
        latencies.append(time.perf_counter() - t0)
        calls = resp.tool_calls or extract_text_tool_calls(resp.content, registry.names())
        got = calls[0] if calls else None
        ok = _check(case, got)
        passed += ok
        if ok and not args.all_rows:
            continue
        got_str = f"{got.name}({got.arguments})" if got else f"text: {resp.content[:40]!r}"
        table.add_row(
            "[green]✓[/]" if ok else "[red]✗[/]",
            case["say"],
            "/".join(expected) or "None",
            got_str,
            "-" if in_route is None else ("yes" if in_route else "[red]no[/]"),
            f"{latencies[-1]:.1f}",
        )

    if table.row_count:
        console.print(table)
    pct = 100 * passed / len(cases)
    recall = f" · router recall {routed_ok}/{routed_total}" if router else ""
    console.print(
        f"[bold]{passed}/{len(cases)} passed ({pct:.0f}%)[/]{recall} · "
        f"avg latency {sum(latencies) / len(latencies):.2f}s"
    )
    return 0 if pct >= 95 else 1


def _expected(case: dict) -> list[str]:
    tool = case.get("tool")
    if tool is None:
        return []
    return list(tool) if isinstance(tool, list) else [tool]


def _check(case: dict, got) -> bool:
    expected = _expected(case)
    if not expected:
        return got is None
    if got is None or got.name not in expected:
        return False
    got_args = got.arguments if isinstance(got.arguments, dict) else {}
    for key, needle in (case.get("args_contain") or {}).items():
        if str(needle).lower() not in str(got_args.get(key, "")).lower():
            return False
    return True


if __name__ == "__main__":
    sys.exit(main())
