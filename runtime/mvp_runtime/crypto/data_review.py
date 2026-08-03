"""Data-gap reviewer — the model suggests what data to collect NEXT; a human decides.

Loop ① of the three review loops (data → templates → fusion): a periodic, budgeted
review of the pipeline's *input* side. The deterministic half inventories what the
runtime already has — the collected sources, the feature vocabulary the factory may
mint from, the features computed but unusable, each feed's live status, and the paper
book's realized performance per timeframe. The model half reads that inventory and
suggests additional data worth collecting, each with a rationale and the kind of
template family it would enable.

**A suggestion is evidence for a human decision — it grants and installs nothing**
(the M4b proposer posture, applied to data). Actually collecting a new source stays a
Thomas decision plus a code change behind its own Safety-Flag Gate, exactly like the
funding and liquidation feeds before it. The record's ``collection_effect: NONE`` is
the schema-visible statement of that.

Fail direction — degraded, never blocking (the ``TRIAGE_DEGRADED`` precedent): a
provider failure or an unparseable answer yields zero suggestions with the reason
recorded. Nothing downstream depends on a suggestion existing.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from ..budgets import TRIAGE_TIMEOUT_SECONDS
from ..errors import ProviderError, ToolBlocked
from ..worker import Provider
from . import factory, market_data

DATA_REVIEW_WORKER_ID = "mvp.crypto.data_gap_reviewer.llm"
DATA_REVIEW_WORKER_VERSION = "0.1.0"
DATA_REVIEW_PROMPT_VERSION = "mvp_crypto_data_gap_review.v1"

DATA_REVIEW_RECORD_TYPE = "crypto_data_review.v0"
DATA_REVIEW_LEDGER_KIND = "crypto_data_review"
DATA_REVIEW_DEGRADED = "DATA_REVIEW_DEGRADED"

DATA_REVIEW_TOKEN_ALLOWANCE = 4000
DATA_REVIEW_TIMEOUT_SECONDS = TRIAGE_TIMEOUT_SECONDS
MAX_SUGGESTIONS_PER_RUN = 5

# The sources the runtime collects today — stated deterministically so the model is
# told, not asked, what exists. Kept in one place; a new gated feed joins this list
# in the same PR that wires it.
CURRENT_SOURCES = (
    {"source": "binance_futures_klines", "content": "OHLCV candles, 15m/1h/4h/1d, 5 symbols"},
    {"source": "binance_futures_funding", "content": "funding-rate history (funding_rate, funding_zscore)"},
    {"source": "coinalyze_liquidations", "content": "liquidation history (spike ratio + long/short/total)"},
    {"source": "binance_futures_mark_index", "content": "mark/index price and basis (mark_index_basis_bps)"},
)

_REQUIRED_FIELDS = ("name", "data_kind", "rationale", "expected_use")


def _mintable(venue: str) -> frozenset[str]:
    """The venue's vocabulary as one flat set. Never raises: this record is a description of
    what exists, and an undeclared venue is a fact to report rather than a reason to produce
    no inventory at all — the gates that must refuse it already do."""
    try:
        numeric, categorical = factory.known_features(venue)
    except ToolBlocked:
        return frozenset()
    return numeric | frozenset(categorical)


def build_data_inventory(
    cycle_records: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    contexts: Sequence[tuple[str, str]] = (),
    venue: str = market_data.BINANCE_FUTURES,
) -> dict[str, Any]:
    """The deterministic facts the review runs on. Pure; never raises on odd rows.

    ``cycle_records`` are ledger rows (kind ``crypto_cycle``) — the most recent per-feed
    status wins, so a feed that recovered reads ``ok``, one that never configured reads
    ``absent``. ``outcomes`` is the paper book's realized history; performance is
    reported per timeframe with honest counts (a two-trade win rate is visibly thin).

    ``mintable_features`` is scoped to ``venue``. This record is read by a model asked what
    data is worth ADDING, so the global vocabulary would tell it a feature is already
    covered here when the venue cannot serve it — the one suggestion it would have been
    most useful to receive."""
    feed_status: dict[str, str] = {}
    for row in cycle_records:
        record = row.get("record") if isinstance(row.get("record"), Mapping) else row
        feeds = record.get("feeds")
        if isinstance(feeds, Mapping):
            for feed, status in feeds.items():
                if isinstance(feed, str) and isinstance(status, str):
                    feed_status[feed] = status  # later rows overwrite: latest wins

    performance: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        timeframe = str(outcome.get("timeframe") or "unknown")
        bucket = performance.setdefault(timeframe, {"closed": 0, "wins": 0, "total_R": 0.0})
        bucket["closed"] += 1
        if outcome.get("win_loss") == "WIN":
            bucket["wins"] += 1
        try:
            bucket["total_R"] += float(outcome.get("result_R") or 0.0)
        except (TypeError, ValueError):
            pass
    for bucket in performance.values():
        bucket["win_rate"] = round(bucket["wins"] / bucket["closed"], 3) if bucket["closed"] else None
        bucket["total_R"] = round(bucket["total_R"], 3)

    return {
        "current_sources": [dict(s) for s in CURRENT_SOURCES],
        # `known_features` returns (numeric, categorical); the categorical mapping's keys
        # join the vocabulary, and both halves are already narrowed to what `venue` serves.
        "venue": venue,
        "mintable_features": sorted(_mintable(venue)),
        "feed_status": feed_status,
        "performance_by_timeframe": performance,
        "traded_contexts": [f"{symbol} {timeframe}" for symbol, timeframe in contexts],
    }


def build_review_prompt(inventory: Mapping[str, Any], *, count: int = MAX_SUGGESTIONS_PER_RUN) -> str:
    """One self-contained prompt: the inventory as data, the ask as JSON-only."""
    return (
        "You review the DATA INPUTS of a governed crypto paper-trading pipeline.\n"
        "Below is the full inventory of what it already collects and how its paper\n"
        "trades have performed. Suggest ADDITIONAL data worth collecting to enable\n"
        "better strategy templates. Do not suggest sources already collected. Prefer\n"
        "data orthogonal to price-derived indicators (positioning, flow, regime).\n\n"
        f"INVENTORY:\n{json.dumps(dict(inventory), ensure_ascii=False, indent=1, sort_keys=True)}\n\n"
        f"Answer with ONLY a JSON object: {{\"suggestions\": [up to {count} items]}}.\n"
        "Each item must have exactly these string fields:\n"
        "- name: short snake_case identifier for the data series\n"
        "- data_kind: one of market_microstructure | positioning | cross_sectional | onchain | macro | other\n"
        "- rationale: why this data plausibly carries edge HERE, referencing the inventory\n"
        "- expected_use: the kind of entry condition or template family it would enable\n"
    )


class MockDataReviewProvider:
    """Deterministic reviewer: no network, no real model (the ``MockProposerProvider``
    precedent). Returns one well-formed suggestion and one missing a required field,
    so the mock path exercises acceptance AND shape rejection, never only degradation."""

    model_id = "mock.data_gap_reviewer"
    model_version = "0.1.0"
    network_egress = False
    # Holds a model_id for record-keeping but reaches no model. Declared explicitly
    # because gate_banners now announces anything carrying a model_id: silence is the
    # thing that must be opted into, so a real capability can never go unannounced.
    model_invocation = False

    _ANSWER = {
        "summary": "one usable suggestion, one malformed",
        "suggestions": [
            {
                "name": "open_interest_history",
                "data_kind": "positioning",
                "rationale": "Funding and liquidations are collected but open interest — the "
                             "third leg of the positioning picture — is not.",
                "expected_use": "oi_squeeze family: enter when OI builds while price ranges.",
            },
            {
                "name": "malformed_suggestion",
                "data_kind": "other",
                # rationale/expected_use deliberately absent: shape rejection path.
            },
        ],
    }

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int):
        from ..worker import ProviderResult
        return ProviderResult(
            analysis=dict(self._ANSWER),
            model_id=self.model_id,
            model_version=self.model_version,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )


def _extract_suggestions(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pull the suggestion list out of a provider answer. Never raises — an unusable
    answer is zero suggestions, which the caller reports as degraded."""
    payload: Any = analysis
    if isinstance(payload, Mapping) and "suggestions" not in payload:
        for key in ("summary", "raw_text"):
            text = payload.get(key)
            if isinstance(text, str):
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        candidate = json.loads(text[start:end + 1])
                    except ValueError:
                        continue
                    if isinstance(candidate, Mapping) and "suggestions" in candidate:
                        payload = candidate
                        break
    if not isinstance(payload, Mapping):
        return []
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list):
        return []
    return [s for s in suggestions if isinstance(s, Mapping)][:MAX_SUGGESTIONS_PER_RUN]


def evaluate_suggestion(suggestion: Mapping[str, Any], *, index: int,
                        known_sources: frozenset[str]) -> dict[str, Any]:
    """Deterministic shape judgment. The human judges substance; this only refuses
    what a review sheet cannot use — missing fields, or a source already collected."""
    problems: list[str] = []
    for field in _REQUIRED_FIELDS:
        value = suggestion.get(field)
        if not (isinstance(value, str) and value.strip()):
            problems.append(f"missing_{field}")
    name = str(suggestion.get("name") or "").strip()
    if name and name in known_sources:
        problems.append("already_collected")
    return {
        "index": index,
        "name": name or f"unnamed_{index}",
        "data_kind": str(suggestion.get("data_kind") or ""),
        "rationale": str(suggestion.get("rationale") or ""),
        "expected_use": str(suggestion.get("expected_use") or ""),
        "accepted": not problems,
        "problems": problems,
    }


def review_data_gaps(
    inventory: Mapping[str, Any],
    *,
    provider: Provider,
    now: str,
) -> dict[str, Any]:
    """Ask the model for data suggestions, judge each one's shape, return the record.

    The record is evidence for a human decision — it grants nothing and collects
    nothing. A provider failure degrades to zero suggestions with the reason recorded."""
    prompt = build_review_prompt(inventory)

    degraded: str | None = None
    invocation: dict[str, Any] | None = None
    raw: list[dict[str, Any]] = []
    try:
        result = provider.generate(
            prompt,
            max_output_tokens=DATA_REVIEW_TOKEN_ALLOWANCE,
            timeout_seconds=DATA_REVIEW_TIMEOUT_SECONDS,
        )
    except (ProviderError, TimeoutError) as exc:
        degraded = f"data-review provider failed: {exc}"
    else:
        raw = _extract_suggestions(result.analysis if isinstance(result.analysis, Mapping) else {})
        if not raw:
            degraded = "data reviewer returned no parseable suggestions"
        invocation = {
            "worker_id": DATA_REVIEW_WORKER_ID,
            "worker_version": DATA_REVIEW_WORKER_VERSION,
            "model_id": result.model_id,
            "model_version": result.model_version,
            "prompt_version": DATA_REVIEW_PROMPT_VERSION,
            "input_tokens": int(result.input_tokens),
            "output_tokens": int(result.output_tokens),
            "tokens_used": int(result.input_tokens) + int(result.output_tokens),
            "latency_ms": int(result.latency_ms),
            "finish_reason": result.finish_reason,
            "network_egress": bool(getattr(provider, "network_egress", False)),
        }

    known_sources = frozenset(str(s.get("source")) for s in inventory.get("current_sources") or ())
    verdicts = [
        evaluate_suggestion(s, index=i, known_sources=known_sources)
        for i, s in enumerate(raw, start=1)
    ]
    accepted = [v for v in verdicts if v["accepted"]]

    record = {
        "record_type": DATA_REVIEW_RECORD_TYPE,
        "prompt_version": DATA_REVIEW_PROMPT_VERSION,
        "inventory": dict(inventory),
        "suggested_count": len(verdicts),
        "accepted_count": len(accepted),
        "suggestions": verdicts,
        "invocation": invocation,
        # Collecting a new source would be a gated code change; this record never is one.
        "collection_effect": "NONE",
        "created_at": now,
    }
    if degraded:
        record["degraded"] = DATA_REVIEW_DEGRADED
        record["degraded_reason"] = degraded
    record["review_id"] = integrity.short_id("data_review", {"at": now})
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def format_review_report(record: Mapping[str, Any]) -> str:
    """The short operator sheet for one review — what was suggested and why."""
    lines = [
        f"[데이터 검토] {record.get('review_id')}",
        f"제안 {record.get('accepted_count')}/{record.get('suggested_count')}건 "
        f"(수집 효력 없음 — 채택은 Thomas 결정 + 코드 변경)",
    ]
    if record.get("degraded"):
        lines.append(f"DEGRADED: {record.get('degraded_reason')}")
    for s in record.get("suggestions") or []:
        if not isinstance(s, Mapping):
            continue
        mark = "+" if s.get("accepted") else f"- ({','.join(s.get('problems') or [])})"
        lines.append(f"{mark} {s.get('name')} [{s.get('data_kind')}]")
        if s.get("accepted"):
            lines.append(f"    이유: {s.get('rationale')}")
            lines.append(f"    용도: {s.get('expected_use')}")
    return "\n".join(lines)


__all__ = [
    "CURRENT_SOURCES",
    "DATA_REVIEW_LEDGER_KIND",
    "DATA_REVIEW_RECORD_TYPE",
    "MAX_SUGGESTIONS_PER_RUN",
    "MockDataReviewProvider",
    "build_data_inventory",
    "build_review_prompt",
    "evaluate_suggestion",
    "format_review_report",
    "review_data_gaps",
]
