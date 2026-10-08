"""H5a: which asset class each held instrument belongs to — Thomas's entries, kept on this host only.

Thomas 2026-10-08 (``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, H5a decision). The allocation
table (``allocation``) places Toss symbols through its code table and the Binance wallet by sub-class
(BTC, ETH coin; stablecoins cash). Anything else was unclassified, and while anything is, no band verdict
is given. The first complete NAV left eight wallet assets there.

**The entries name holdings, so they never reach the repository or a model.** An entry is an
instrument id (``binance:<ASSET>``, ``toss:<market>:<SYMBOL>``) and a class, and an id is a symbol —
LOCAL_ONLY under H3. So the mechanism is code, and the content is a state file
(:data:`FILENAME`, under ``.runtime_governance_state/holdings/``, never committed) that Thomas writes
from his terminal (``scripts/holdings_board.py --classify``). Running that command is the approval:
each entry records when, and the file carries a ``mapping_version`` that moves on every change. No
door reads this file; the allocation block carries only the version.

**Fail closed.** A file that exists but does not parse, or carries an unknown class, raises
:data:`CLASSIFICATION_UNREADABLE`; the allocation then treats every entry as absent and says so, so the
band verdict stays withheld rather than resting on a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .. import timeutil
from ..errors import ToolError
from ..filelock import locked

FILENAME = "holdings_classification.json"
RECORD_TYPE = "holdings_classification.v1"
CLASSIFICATION_UNREADABLE = "HOLDINGS_CLASSIFICATION_UNREADABLE"
CLASSIFICATION_REFUSED = "HOLDINGS_CLASSIFICATION_REFUSED"

# The classes an entry may name: the allocation's own, less the engine margin, which is a balance and
# never an instrument (`allocation.ENGINE_MARGIN`). Repeated by value; a test pins it to the authority.
ASSIGNABLE_CLASSES = frozenset({"global_equity", "domestic_equity", "bonds", "gold", "coin", "cash"})


@dataclass(frozen=True)
class Classification:
    mapping_version: int = 0
    entries: Mapping[str, str] = field(default_factory=dict)


def path(state_dir: Path) -> Path:
    return state_dir / FILENAME


def wallet_id(asset: str) -> str:
    return f"binance:{asset}"


def toss_id(market: str, symbol: str) -> str:
    return f"toss:{market}:{symbol}"


def _valid_id(instrument: str) -> bool:
    parts = instrument.split(":")
    return (len(parts) == 2 and parts[0] == "binance" and bool(parts[1])) or (
        len(parts) == 3 and parts[0] == "toss" and parts[1] in ("domestic", "overseas") and bool(parts[2]))


def load(state_dir: Path) -> Classification:
    """The entries in force. No file is a normal state: nothing classified, version 0."""
    target = path(state_dir)
    if not target.exists():
        return Classification()
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolError(CLASSIFICATION_UNREADABLE, f"{FILENAME} does not parse") from None
    if not isinstance(body, dict) or body.get("record_type") != RECORD_TYPE:
        raise ToolError(CLASSIFICATION_UNREADABLE, f"{FILENAME} is not a {RECORD_TYPE} record")
    entries: dict[str, str] = {}
    for instrument, row in (body.get("entries") or {}).items():
        asset_class = row.get("asset_class") if isinstance(row, dict) else None
        if not _valid_id(str(instrument)) or asset_class not in ASSIGNABLE_CLASSES:
            # Names the count, never the entry: the entry is a holding.
            raise ToolError(CLASSIFICATION_UNREADABLE, f"{FILENAME} carries an entry that is not valid")
        entries[str(instrument)] = str(asset_class)
    version = body.get("mapping_version")
    if not isinstance(version, int) or version < 0:
        raise ToolError(CLASSIFICATION_UNREADABLE, f"{FILENAME} has no mapping_version")
    return Classification(mapping_version=version, entries=entries)


def apply(state_dir: Path, *, assign: Mapping[str, str] | None = None, remove: tuple[str, ...] = (),
          now: str | None = None) -> Classification:
    """Add, change or remove entries and move the version once. Refuses an id or class it cannot place,
    before anything is written. The terminal command is the only caller."""
    assign = dict(assign or {})
    for instrument, asset_class in assign.items():
        if not _valid_id(instrument):
            raise ToolError(CLASSIFICATION_REFUSED, "an id must be binance:<ASSET> or toss:<market>:<SYMBOL>")
        if asset_class not in ASSIGNABLE_CLASSES:
            raise ToolError(CLASSIFICATION_REFUSED,
                            f"class must be one of {', '.join(sorted(ASSIGNABLE_CLASSES))}")
    stamp = now or timeutil.utc_now_iso()
    target = path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with locked(target.with_suffix(".lock"), code="HOLDINGS_CLASSIFICATION_LOCKED", label="holdings classification"):
        current = load(state_dir)
        raw: dict[str, Any] = {}
        if target.exists():
            raw = json.loads(target.read_text(encoding="utf-8")).get("entries") or {}
        for instrument in remove:
            raw.pop(instrument, None)
        for instrument, asset_class in assign.items():
            raw[instrument] = {"asset_class": asset_class, "approved_at": stamp}
        body = {"record_type": RECORD_TYPE, "mapping_version": current.mapping_version + 1,
                "updated_at": stamp, "entries": raw}
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
        tmp.replace(target)
    return load(state_dir)
