"""CLI entry point for the single-agent MVP pipeline.

Reads a request from CLI args (or stdin), runs it end-to-end (intake -> plan ->
worker -> validation -> audit), and writes the final response to stdout. Fail-closed:
on any BLOCK it writes the reason to stderr and exits non-zero. Uses the deterministic
MockProvider — a real hosted provider is enabled only behind the Safety-Flag Gate — so
the run performs no external write and no network I/O.

Usage:
    python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
    echo "..." | python -m runtime.mvp_runtime.cli
"""

from __future__ import annotations

import sys

from .pipeline import run_task
from .providers import select_provider

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_USAGE = 3


def _force_utf8_io() -> None:
    # Windows consoles default to a legacy code page (e.g. cp949); force UTF-8 so
    # non-ASCII requests decode/encode losslessly instead of producing surrogates.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_io()
    argv = list(sys.argv[1:] if argv is None else argv)
    text = " ".join(argv) if argv else sys.stdin.read()
    # Drop a leading BOM (some shells inject U+FEFF on an "empty" pipe) so a
    # BOM-only input is correctly treated as empty rather than a 1-char request.
    raw_request = text.lstrip("﻿").strip()

    if not raw_request:
        sys.stderr.write("BLOCKED EMPTY_REQUEST: no request text provided (arg or stdin)\n")
        return EXIT_USAGE

    # Runs the full single-agent pipeline. The provider defaults to the deterministic
    # MockProvider; a real hosted provider is used only when opted in via the environment
    # (Safety-Flag Gate opened locally). See providers.select_provider.
    result = run_task(raw_request, provider=select_provider(), channel="manual")
    if result["status"] == "COMPLETED":
        sys.stdout.write(result["final_response"] + "\n")
        return EXIT_OK

    block = result["block"] or {"reason_code": "BLOCKED", "message": "unknown"}
    sys.stderr.write(f"BLOCKED {block['reason_code']}: {block['message']}\n")
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
