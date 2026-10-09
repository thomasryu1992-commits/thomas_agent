"""Shared synthetic fixtures for the H6d-min tests. No real account, order, amount or file is read."""

from __future__ import annotations

import datetime
import sys
from types import SimpleNamespace

import pytest

from runtime.mvp_runtime.holdings import binance_wallet, cash_flows

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fork and flock: the services run on Linux")

CUTOVER = "2026-10-09T03:00:00Z"
CURRENT_OK = {"binance": True, "toss": True, "freshness": True, "coherence": True}


def ms(at: str) -> int:
    return int(datetime.datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() * 1000)


def iso(at_ms: int) -> str:
    return datetime.datetime.fromtimestamp(at_ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Feed:
    """A fake Binance feed: ``answers[source]`` is what each read returns (or raises)."""

    def __init__(self, **answers):
        self.answers = {name: [] for name in binance_wallet.FLOW_SOURCES}
        self.answers.update(answers)

    def flow_history(self, source, *, start_ms, end_ms, timeout_seconds=5):
        answer = self.answers[source]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


class Toss:
    def __init__(self, orders=(), truncated=False):
        self.orders, self.truncated = list(orders), truncated

    def closed_orders(self, **_kw):
        return list(self.orders), self.truncated


def snap(krw: float, usd: float = 0.0, rate: float = 1400.0) -> SimpleNamespace:
    return SimpleNamespace(usd_krw_rate=rate, domestic=SimpleNamespace(cash_krw=krw),
                           overseas=SimpleNamespace(cash_krw=usd * rate))


def collect(state_dir, feed=None, *, now: str):
    return cash_flows.collect_binance(feed or Feed(), state_dir, now=now, now_ms=ms(now))


def set_cutover(state_dir, at: str = CUTOVER):
    cash_flows.set_cutover(state_dir, at=at, requested_by="test")


def pay(tid: str, amount: str = "5", *, at: str = "2026-10-09T03:30:00Z", currency: str = "USDT") -> dict:
    return {"transactionId": tid, "amount": amount, "currency": currency, "transactionTime": ms(at)}


def order(oid, side: str, amount: str, filled_at: str, *, currency: str = "KRW", settlement: str | None = None,
          symbol: str | None = None, ordered_at: str | None = None) -> dict:
    row = {"side": side, "currency": currency,
           "execution": {"filledAmount": amount, "commission": "0", "tax": "0", "filledAt": filled_at}}
    if oid is not None:
        row["orderId"] = oid
    if symbol is not None:
        row["symbol"] = symbol
    if ordered_at is not None:
        row["orderedAt"] = ordered_at
    if settlement is not None:
        row["execution"]["settlementDate"] = settlement
    return row


def toss_fire(state_dir, krw: float, now: str, orders=(), *, truncated: bool = False, usd: float = 0.0):
    return cash_flows.collect_toss(Toss(orders, truncated), snap(krw, usd), state_dir, now=now)


def ledger(state_dir) -> list[dict]:
    return cash_flows.verify(state_dir)


def events(state_dir, name: str) -> list[dict]:
    return [r for r in ledger(state_dir) if r["event"] == name]


def ready_fire(state_dir, now: str, feed=None, current=CURRENT_OK) -> dict:
    """A fire at ``now`` (every history read), then the readiness with that fire's health."""
    collect(state_dir, feed, now=now)
    return cash_flows.readiness(state_dir, now=now, current=current)


def start_epoch(state_dir, *, ref: str = "#1203", now: str = "2026-10-09T03:10:00Z") -> dict:
    return cash_flows.start_semantics_epoch(state_dir, requested_by="thomas", reason="reconciliation change",
                                            change_ref=ref, confirm=cash_flows.reconciliation_digest()[:8],
                                            interactive=True, now=now)


def resolve(state_dir, target: str, kind: str, **kw) -> dict:
    return cash_flows.resolve(state_dir, target=target, kind=kind, reason=kw.pop("reason", "checked the statement"),
                              requested_by=kw.pop("requested_by", "thomas"), interactive=kw.pop("interactive", True),
                              **kw)


def hourly(start: str, count: int) -> list[str]:
    return [iso(ms(start) + i * 3_600_000) for i in range(count)]
