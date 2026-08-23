"""Post-stop-loss cooldown marks — block re-entry for N bars after a stop-out.

After a position is settled by stop-loss, the cooldown prevents the same
``(venue, symbol, timeframe)`` context from opening a new position for
:data:`paper.COOLDOWN_BARS_AFTER_STOPLOSS` bars.  This guards against whipsaw
re-entries where a stop-out is immediately followed by a signal into the same
direction on the next bar — a pattern that in aggregate costs more than it earns
because the volatility regime that triggered the stop persists.

The counterfactual tracker shadows cooldown-blocked signals (block reason
``STOP_LOSS_COOLDOWN``) so the cost of the gate is measurable and tunable via
the same ``r_values_by_reason`` expectancy the other gates use.

State is a small JSON map at
``.runtime_governance_state/crypto/cooldown_marks.json``.  Fail-closed direction
is toward LIFTING the cooldown (no mark = no restriction): an unreadable file
reads as "all marks expired", which at worst allows one premature re-entry —
no worse than the system before cooldown existed, and the next stop-out re-arms.

This module imports :func:`paper.state_dir` one-directionally.
``paper.run_paper_update`` receives a store instance duck-typed, so there is no
import cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..filelock import locked
from .paper import state_dir

COOLDOWN_FILENAME = "cooldown_marks.json"


class CooldownMarkStore:
    """Per-context JSON map: ``context_key -> cooldown_expiry_candle_time``.

    ``cooldown_expiry_candle_time`` is the earliest candle ``close_time`` at which
    re-entry is allowed.  A candle whose ``close_time < expiry`` is still inside
    the cooldown window and must not open a position.
    """

    def __init__(self, root: Path | None = None):
        self._root = root

    @property
    def path(self) -> Path:
        return state_dir(self._root) / COOLDOWN_FILENAME

    def _load(self) -> dict[str, str]:
        path = self.path
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def is_cooling_down(self, context_key: str, candle_time: str | None) -> bool:
        """True when re-entry is BLOCKED (cooldown has not yet elapsed)."""
        expiry = self._load().get(context_key)
        if expiry is None:
            return False
        if candle_time is None:
            return True
        return candle_time < expiry

    def expiry(self, context_key: str) -> str | None:
        """The cooldown expiry for this context, or None if no cooldown is active."""
        return self._load().get(context_key)

    def record_stoploss(self, context_key: str, expiry_time: str) -> None:
        """Record a cooldown expiry.  Never regresses: a later stop-out extends
        cooldown, a stale one cannot shorten it."""
        state_dir(self._root).mkdir(parents=True, exist_ok=True)
        path = self.path
        with locked(
            path.with_suffix(".lock"),
            code="COOLDOWN_MARKS_LOCKED",
            label="crypto cooldown marks",
        ):
            marks = self._load()
            current = marks.get(context_key)
            if current is not None and expiry_time <= current:
                return
            marks[context_key] = expiry_time
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            tmp.replace(path)
