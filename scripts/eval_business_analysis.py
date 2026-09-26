#!/usr/bin/env python3
"""Operator tool: run the business-analysis evaluation set and score what came back.

System review B3 (2026-09-25): the analysis lane had no evaluation set and no regression
measurement, so a prompt or model change could degrade every reply with nothing to say so. This runs
each item of ``scripts/eval/business_ideas_v0.1.yaml`` through the governed pipeline and scores
the result on floors that need no second model:

* **outcome** — delivered (validation PASS) or withheld, and by which checks;
* **sections** — risks, assumptions and next actions are non-empty;
* **themes** — the share of the item's expected themes any phrase of which appears in the analysis.

The default provider is the deterministic Mock, which measures the harness and nothing else. ``--live``
selects providers exactly as the intake CLI does (the Safety-Flag Gate: the deployment's env names
the chain), so it spends the free tier's quota — 24 items is 24+ model calls — and belongs in the
container that holds the provider env::

    docker exec thomas-pipeline-worker python -m scripts.eval_business_analysis --live \\
        --output /app/.runtime_governance_state/eval/business_analysis-$(date +%F).json

Runs write no ledger records and read no working memory: an evaluation must not become the context
of the next real run. The kill switch still binds a ``--live`` run, as it binds the CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402
from runtime.mvp_runtime.pipeline import run_task  # noqa: E402
from runtime.mvp_runtime.worker import MockProvider  # noqa: E402

EVAL_SET = ROOT / "scripts" / "eval" / "business_ideas_v0.1.yaml"
_SECTIONS = ("risks", "assumptions", "next_actions")


def load_items(path: Path = EVAL_SET) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["items"])


def _analysis_text(output: Mapping[str, Any]) -> str:
    rso = output.get("role_specific_output") or {}
    parts: list[str] = [str(output.get("summary") or "")]
    for key in ("risks", "assumptions", "next_actions", "uncertainty"):
        parts += [str(v) for v in output.get(key) or []]
    parts += [str(v) for v in rso.get("key_findings") or []]
    parts += [str(p.get("basis", "")) for p in rso.get("perspectives") or [] if isinstance(p, Mapping)]
    recommendation = output.get("recommendation") or {}
    parts += [str(recommendation.get("action", "")), str(recommendation.get("reason", ""))]
    return "\n".join(parts).lower()


def score(item: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """One item's floors, from a ``run_task`` result."""
    records = result.get("records") or {}
    output = records.get("agent_output") or {}
    validation = (records.get("validation_result") or {}).get("validation") or {}
    text = _analysis_text(output)
    met = [t["name"] for t in item["themes"] if any(str(p).lower() in text for p in t["any_of"])]
    return {
        "id": item["id"],
        "status": result.get("status"),
        "validation": validation.get("result"),
        "withheld_by": [c.get("check_id") for c in validation.get("checks") or []
                        if isinstance(c, Mapping) and c.get("result") != "PASS"],
        "sections_present": [s for s in _SECTIONS if output.get(s)],
        "themes_met": met,
        "theme_coverage": round(len(met) / len(item["themes"]), 3) if item["themes"] else None,
    }


def summarize(scores: list[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(scores) or 1
    coverage = [s["theme_coverage"] for s in scores if s["theme_coverage"] is not None]
    return {
        "items": len(scores),
        "delivered": sum(1 for s in scores if s["validation"] == "PASS"),
        "all_sections": sum(1 for s in scores if len(s["sections_present"]) == len(_SECTIONS)),
        "mean_theme_coverage": round(sum(coverage) / len(coverage), 3) if coverage else None,
        "delivered_rate": round(sum(1 for s in scores if s["validation"] == "PASS") / n, 3),
    }


def _live_selection() -> dict[str, Any]:
    """The providers the intake CLI would use — through the same gated selectors."""
    from runtime.mvp_runtime.providers import (
        select_provider,
        select_tiered_provider,
        select_validator_provider,
    )
    from runtime.mvp_runtime.tools import select_search_tool

    provider = select_provider()
    return {
        "provider": provider,
        "validator_provider": select_validator_provider(),
        "search_tool": select_search_tool(),
        "tiered_provider_selector": lambda difficulty: select_tiered_provider(
            difficulty, base_provider=provider),
    }


def main(argv: list[str] | None = None, *, now: str | None = None,
         items: list[dict[str, Any]] | None = None, control_store: Any = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_business_analysis", description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true",
                        help="use the deployment's gated providers (spends quota); default is Mock")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N items")
    parser.add_argument("--output", default=None, help="also write the full report as JSON here")
    args = parser.parse_args(argv)
    stamp = now or timeutil.utc_now_iso()
    chosen = (items if items is not None else load_items())[: args.limit]

    if args.live:
        from runtime.mvp_runtime.control import ControlStore

        state = (control_store or ControlStore.default()).load()
        if not state.execution_allowed:
            print(f"BLOCKED {state.refusal_reason_code()}: runtime is {state.mode}", file=sys.stderr)
            return EXIT_BLOCKED
        try:
            selection = _live_selection()
        except MvpRuntimeError as exc:
            print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
            return EXIT_BLOCKED
    else:
        selection = {"provider": MockProvider()}

    scores = []
    for item in chosen:
        result = run_task(item["request"], now=stamp, store=None, working_memory=None, **selection)
        scores.append(score(item, result))
        s = scores[-1]
        print(f"{s['id']}  {s['validation'] or s['status']:<8} themes {len(s['themes_met'])}/"
              f"{len(item['themes'])}  sections {len(s['sections_present'])}/{len(_SECTIONS)}"
              + (f"  withheld_by={','.join(s['withheld_by'])}" if s["withheld_by"] else ""))
    summary = summarize(scores)
    print(json.dumps({"provider": "live" if args.live else "mock", **summary}, ensure_ascii=False))
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"run_at": stamp, "provider": "live" if args.live else "mock",
                                   "eval_set": EVAL_SET.name, "summary": summary, "items": scores},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
