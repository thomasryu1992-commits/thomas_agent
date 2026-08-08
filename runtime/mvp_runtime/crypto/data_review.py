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
# Raised when the fire AFTER a degraded one degrades too. See `review_loop_is_stalled`.
DATA_REVIEW_STALLED = "DATA_REVIEW_PERSISTENTLY_DEGRADED"

DATA_REVIEW_TOKEN_ALLOWANCE = 4000
DATA_REVIEW_TIMEOUT_SECONDS = TRIAGE_TIMEOUT_SECONDS
MAX_SUGGESTIONS_PER_RUN = 5

# The sources the runtime collects today — stated deterministically so the model is
# told, not asked, what exists. Kept in one place; a new source joins this list in the
# same PR that wires it.
#
# **The rule used to say "a new gated feed", and that wording is what let this list go
# stale for two weeks.** Four sources shipped after 2026-07-25 and none of them read as a
# new gated feed: daily open interest rides the liquidation feed's existing grant and
# object, and `oi_store` / `positioning_store` / `candle_archive` are local accumulators.
# So the list said the runtime collects four series while it collects eight, and the model
# is asked what to collect NEXT — an inventory that under-reports is the one input that
# turns this review into a re-proposal of work already done. `evaluate_suggestion` reads
# the same list for its `already_collected` check, so a stale entry silently disarms the
# only deterministic filter the review has. The rule is now about SOURCES, not grants.
#
# Entries that feed no feature yet say so. They are still collected — the question the
# model is answering is what to collect, so omitting them would invite re-collection — but
# "collected" and "usable by a template today" are different facts and the accumulators
# exist precisely because the second is years of retention away from the first.
#
# `binance_futures_positioning` is the one to read carefully, because `positioning_store`'s
# own docstring said "it feeds nothing" and that claim went stale under it: the columns ARE
# computed onto every feature row and ARE mintable, and what is gated is whether the two
# POSITIONING_FAMILIES are OFFERED (`positioning_eligible`, a coverage measurement). Saying
# "feeds nothing" here would invite the one suggestion that is already built.
CURRENT_SOURCES = (
    {"source": "binance_futures_klines",
     "content": "OHLCV candles + the taker aggressor split (taker_buy_base -> taker_*), "
                "15m/1h/4h/1d, over the CROSS_SECTION_UNIVERSE cohort"},
    {"source": "binance_futures_funding", "content": "funding-rate history (funding_rate, funding_zscore)"},
    {"source": "coinalyze_liquidations", "content": "liquidation history (spike ratio + long/short/total)"},
    {"source": "binance_futures_mark_index",
     "content": "mark / index / premium-index price series (mark_index_basis_bps, premium_*)"},
    {"source": "coinalyze_open_interest",
     "content": "daily open-interest history (open_interest, _change_pct, _zscore) — "
                "what every oi_* family reads"},
    {"source": "coinalyze_open_interest_1h",
     "content": "hourly open interest accumulated into oi_store, because the vendor keeps "
                "~84 days and the factory replays 500. Feeds no feature yet: this is depth "
                "for the series above, not a second signal"},
    {"source": "binance_futures_positioning",
     "content": "the three /futures/data/ long-short ratio series (top_position, top_account, "
                "global_account) accumulated into positioning_store, because the vendor keeps "
                "30 days. Feeds the positioning_* columns; the two families that read them stay "
                "unminted until coverage covers the replay window"},
    {"source": "dex_candle_archive",
     "content": "15m/1h/4h/1d candles for every perp the xyz DEX lists, accumulated into "
                "candle_archive because that venue serves a rolling 5,000-candle window and "
                "nothing behind it. Feeds no feature yet"},
)

_REQUIRED_FIELDS = ("name", "data_kind", "rationale", "expected_use")


def _mintable(venue: str) -> frozenset[str] | None:
    """The venue's vocabulary as one flat set, or ``None`` when nobody has declared the venue.

    Still never raises — this record describes what exists and the module's contract is to
    degrade rather than block. What changed is that it no longer answers both questions the
    same way. It used to return an empty set for an undeclared venue, which `market_data`
    names as the thing not to do, about this exact table:

        "this venue provides nothing" and "nobody has said what this venue provides"
        must not produce the same silence.

    An empty list is a claim. ``None`` is the absence of one, and `review_data_gaps` reads it
    as a reason to degrade rather than as an inventory to reason from.
    """
    try:
        numeric, categorical = factory.known_features(venue)
    except ToolBlocked:
        return None
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
    most useful to receive. It is ``None``, not ``[]``, when nobody has declared the venue:
    an empty list is the claim that nothing is mintable, and `review_data_gaps` reads the
    absence of an answer as a reason to degrade rather than as an inventory to reason from."""
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

    mintable = _mintable(venue)
    return {
        "current_sources": [dict(s) for s in CURRENT_SOURCES],
        # `known_features` returns (numeric, categorical); the categorical mapping's keys
        # join the vocabulary, and both halves are already narrowed to what `venue` serves.
        # `null` rather than `[]` when the venue is undeclared — an empty list is the claim
        # that nothing is mintable, which is a different statement from having no answer.
        "venue": venue,
        "mintable_features": None if mintable is None else sorted(mintable),
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
    so the mock path exercises acceptance AND shape rejection, never only degradation.

    The usable one must name something genuinely NOT in :data:`CURRENT_SOURCES`, or it
    exercises ``already_collected`` instead of acceptance — which is a different test's
    job. It used to suggest open-interest history and assert in its own rationale that OI
    "is not" collected; that became false when the daily series shipped, and nothing
    caught it because ``already_collected`` matches the source NAME and the two spellings
    differ. So the fixture went on stating a fact about the inventory that the inventory
    contradicted. Resting liquidity is the replacement because no collected series
    describes it: every one is a trade or a position, none is an order that is still
    waiting."""

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
                "name": "orderbook_depth_imbalance",
                "data_kind": "market_microstructure",
                "rationale": "Every collected series is a trade that happened or a position "
                             "outstanding; none is resting liquidity, so nothing separates a "
                             "move into a thin book from the same move into a deep one.",
                "expected_use": "liquidity_vacuum family: take breakouts only when the book "
                                "ahead of price is thin.",
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
    nothing. A provider failure degrades to zero suggestions with the reason recorded.

    An inventory with no known vocabulary degrades the same way, and BEFORE the call rather
    than after it. The review's question is "what data is worth adding here", which cannot
    be judged against a venue nobody has described: every suggestion would read as new
    because nothing is known to be covered. Degrading is also the cheaper half — the
    alternative is paying a provider for an answer that could not be used."""
    degraded: str | None = None
    invocation: dict[str, Any] | None = None
    raw: list[dict[str, Any]] = []
    if inventory.get("mintable_features") is None:
        # Not an early return: the record is built once at the bottom, so a degraded review
        # has exactly the shape of every other one and no second construction to drift.
        degraded = (
            f"venue {inventory.get('venue')!r} has no declared feeds; "
            "the mintable vocabulary is unknown"
        )
    else:
        prompt = build_review_prompt(inventory)
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


def review_loop_is_stalled(record: Mapping[str, Any], previous_status: str | None) -> bool:
    """Whether THIS degraded review follows one that also degraded — a stalled loop.

    Degrade-never-block is the right posture for one bad fire and the wrong one for every
    fire. This review runs weekly; its only run on the live host degraded on a provider shape
    error (``MALFORMED_RESPONSE``), and a recurring version of that produces nothing,
    indefinitely, while every fire reports COMPLETED. `scheduler`'s candle-archive branch
    already settled the principle for exactly this shape — *"off on purpose is quiet; on and
    not working RAISES"* — because a COMPLETED summary string reaches nobody, and the alert
    surfaces are FAILED fires and the operator sheet.

    So: the first degrade stays quiet (it is delivered on the sheet, with its reason), and the
    second in a row raises onto the existing failure alert. Two, matching the lifecycle's own
    "one bad window is noise" bar, and at a weekly cadence two is already a fortnight of a
    review loop producing nothing.

    **The signal is the schedule's own ``last_status``, not the ledger.** A weekly record has
    almost certainly rotated out of the active file by the next fire, so counting ledger rows
    would answer "not degraded" for the very gap this exists to catch. ``last_status`` is
    per-schedule and never rotates.

    Reading it is safe because ``0/0`` is uniquely the degraded signature — `review_data_gaps`
    only ever produces ``suggested_count == 0`` with ``degraded`` set (provider failure,
    unknown vocabulary, unparseable answer), so no healthy zero-suggestion run exists to
    confuse it with. The raised status is recognised too: without that, raising would clear
    the very marker the next fire reads and the alert would fire every OTHER week.
    """
    if not record.get("degraded"):
        return False
    previous = str(previous_status or "")
    return previous.startswith("data_review=0/0") or DATA_REVIEW_STALLED in previous


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
