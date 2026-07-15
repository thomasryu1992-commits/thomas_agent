"""Thin CLI entry point for R2.1 Task Intake.

Reads a request from CLI args (or stdin) and emits a schema-valid RECEIVED
``task.v0.3`` record as JSON on stdout. Fail-closed: on any BLOCK it writes the
reason to stderr and exits non-zero. No external effect.

Usage:
    python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
    echo "..." | python -m runtime.mvp_runtime.cli
"""

from __future__ import annotations

import json
import sys

from .errors import TaskIntakeBlocked
from .intake import build_task

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

    try:
        task = build_task(raw_request, channel="manual")
    except TaskIntakeBlocked as exc:
        sys.stderr.write(f"BLOCKED {exc}\n")
        return EXIT_BLOCKED

    json.dump(task, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
