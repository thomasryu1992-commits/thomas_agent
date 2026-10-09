"""An append-only JSONL log whose every line carries the previous line's hash and its own.

Shared by the holdings lane's two explanatory logs (H5b ``baseline_log``, H6b ``cash_flows``): one
chain rule, so a tamper reads the same in both. Each line is ``record_type`` plus its fields, then
``prev_sha256`` (the previous line's ``sha256``, ``None`` first) and ``sha256`` (``integrity.sha256_value``
over the line without it). :func:`verify` re-derives the chain and raises the caller's tamper code on any
break; :func:`append` verifies under the lock before it writes, so a broken log is never extended.
Values must survive the kernel's canonical hashing: no floats (amounts go in as strings or integers).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked


def _hash(row: Mapping[str, Any]) -> str:
    return integrity.sha256_value({key: value for key, value in row.items() if key != "sha256"})


def verify(path: Path, *, record_type: str, tamper_code: str) -> list[dict[str, Any]]:
    """Every line, after re-deriving the chain. A missing file is an empty log."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    previous = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            raise ToolError(tamper_code, f"{path.name} line {number} does not parse") from None
        if (not isinstance(row, dict) or row.get("record_type") != record_type
                or row.get("prev_sha256") != previous or row.get("sha256") != _hash(row)):
            raise ToolError(tamper_code, f"{path.name} line {number} breaks the chain")
        previous = row["sha256"]
        rows.append(row)
    return rows


def append(path: Path, *, record_type: str, tamper_code: str, lock_code: str, label: str,
           build: Callable[[str | None], Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Append the line(s) ``build(previous_sha)`` returns, under the lock; return what was written.

    ``build`` may return one row or ``{"rows": [...]}``: several lines chained in one locked step, so a
    fire that records many events either writes them all after a verified chain or none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=lock_code, label=label):
        existing = verify(path, record_type=record_type, tamper_code=tamper_code)
        previous = existing[-1]["sha256"] if existing else None
        built = build(previous)
        return _write(path, record_type, previous, list(built["rows"]) if "rows" in built else [built])


def append_unique(path: Path, *, record_type: str, tamper_code: str, lock_code: str, label: str,
                  build: Callable[[list[dict[str, Any]]], list[Mapping[str, Any]]],
                  key: str = "source_event_key") -> list[dict[str, Any]]:
    """Append the lines ``build(existing)`` returns whose ``key`` the log does not hold yet; return them.

    One locked step (H6d-min, Thomas 2026-10-09): the chain is re-verified, ``build`` sees every verified
    line and may raise to refuse, and a line whose key is already in the log — or earlier in the same
    batch — is dropped. So a writer that crashed after its append and runs again writes nothing twice:
    an append-only log alone is not exactly-once, the key check under the lock is. A line without the
    key is refused whole (``tamper_code``): it could never be deduplicated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code=lock_code, label=label):
        existing = verify(path, record_type=record_type, tamper_code=tamper_code)
        seen = {row.get(key) for row in existing}
        fresh: list[Mapping[str, Any]] = []
        for fields in build(existing):
            value = fields.get(key)
            if not value:
                raise ToolError(tamper_code, f"a {path.name} line without {key} cannot be appended")
            if value in seen:
                continue
            seen.add(value)
            fresh.append(fields)
        return _write(path, record_type, existing[-1]["sha256"] if existing else None, fresh)


def _write(path: Path, record_type: str, previous: str | None,
           pending: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    written: list[dict[str, Any]] = []
    lines: list[str] = []
    for fields in pending:
        row: dict[str, Any] = {"record_type": record_type, **dict(fields), "prev_sha256": previous}
        row["sha256"] = _hash(row)
        previous = row["sha256"]
        written.append(row)
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    if lines:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return written
