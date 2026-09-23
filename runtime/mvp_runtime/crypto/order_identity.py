"""The identity of a live order intent: its idempotency key, its client order id and its intent id
(crypto PR7d-1).

Derived from the intent itself, with no I/O, and read by every layer that names an order: the
pre-order gate (risk) records it, the order path (execution) sends it, and the book keys positions on
it. It is the order's `candidate_identity`: an id computed from the thing, one leaf, below every
reader. It lived in `live_order`, the sender, which put the gate's import of it upward. `live_order`
re-exports all three functions as the same objects. A test that
changes how an id is derived patches this module: `enrich_order_identity` reads its helpers here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from runtime.read_only_kernel import integrity


def make_idempotency_key(payload: Mapping[str, Any]) -> str:
    """Stable key over the order's identity. Two attempts at the same trade produce the
    same key, so a retry after an ambiguous submit reuses the client order id instead of
    opening a second position."""
    blob = json.dumps(dict(payload), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def make_client_order_id(symbol: str, direction: str, idempotency_key: str) -> str:
    """Venue-safe client order id (Binance caps these at 36 characters)."""
    return f"TAI_{symbol}_{direction}_{idempotency_key[:18]}"[:36]


def enrich_order_identity(intent: dict[str, Any]) -> dict[str, Any]:
    """Attach the idempotency key and client order id derived from the intent itself."""
    payload = {
        "symbol": intent.get("symbol"),
        "direction": intent.get("direction"),
        "strategy_id": intent.get("strategy_id"),
        "candle_time": intent.get("candle_time") or intent.get("created_at"),
        "position_id": intent.get("position_id"),
    }
    # A bar time names a bar only together with its timeframe (PR2a review): a 4h bar and a 1d bar
    # open at the same instant every day, and a display strategy id can be reused across
    # generations, so without it two contexts mint the same client order id a day apart. Added
    # only when present, so the probe's and the testnet cycle's ids — no timeframe — are unchanged.
    if intent.get("timeframe"):
        payload["timeframe"] = intent.get("timeframe")
    key = make_idempotency_key(payload)
    intent["idempotency_key"] = key
    intent["client_order_id"] = make_client_order_id(
        str(intent.get("symbol") or "UNKNOWN"), str(intent.get("direction") or "NONE"), key
    )
    intent["order_intent_id"] = integrity.short_id("live_intent", {"key": key})
    return intent
