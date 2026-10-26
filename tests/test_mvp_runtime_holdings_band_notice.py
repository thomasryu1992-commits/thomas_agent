"""RB5 (Thomas 2026-10-26): the monthly band check — a count on Telegram, once a month, after delivery."""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.errors import OperatorBlocked
from runtime.mvp_runtime.holdings import allocation as al
from runtime.mvp_runtime.holdings import rebalance as rb
from runtime.mvp_runtime.holdings import store

NAV = 100_000_000.0


def _body(complete=True, **over):
    krw = {c: NAV * al.TARGET_PCT[c] / 100 for c in rb.PLANNED}
    krw.update(over)
    return {"allocation": {"complete": complete, "classes": {c: {"krw": v} for c, v in krw.items()}}}


# 2026-11-01 is a Sunday: November's first weekday is Monday 11-02 (KST).
SUNDAY = "2026-11-01T03:00:00Z"
MONDAY = "2026-11-02T00:30:00Z"          # 09:30 KST
LATER = "2026-11-17T03:00:00Z"


def test_nothing_before_the_months_first_weekday(tmp_path):
    assert store.band_notice(_body(), now=SUNDAY, root=tmp_path) is None


def test_the_first_weekday_counts_the_bands_outside_and_names_neither_class_nor_amount(tmp_path):
    # Equity 46 (outside ±5) and bonds 31 (outside ±5): two. Engine margin is not a band here (RB5).
    month, text = store.band_notice(_body(global_equity=36_000_000.0, bonds=31_000_000.0, engine_margin=9e6),
                                    now=MONDAY, root=tmp_path)
    assert month == "2026-11" and "밴드 이탈 2개" in text and "--rebalance" in text
    assert not any(word in text for word in ("equity", "bonds", "주식", "채권", "원", "36"))


def test_inside_every_band_says_there_is_nothing_to_do(tmp_path):
    _, text = store.band_notice(_body(), now=MONDAY, root=tmp_path)
    assert "밴드 이탈 0개" in text and "리밸런싱할 것이 없습니다" in text


def test_an_incomplete_nav_is_said_not_judged(tmp_path):
    _, text = store.band_notice(_body(complete=False), now=MONDAY, root=tmp_path)
    assert "판정할 수 없습니다" in text and "밴드 이탈" not in text


def test_once_told_the_month_is_quiet_and_the_next_month_asks_again(tmp_path):
    store.mark_band_told("2026-11", now=MONDAY, root=tmp_path)
    assert store.band_notice(_body(), now=LATER, root=tmp_path) is None
    assert store.band_notice(_body(), now="2026-12-01T03:00:00Z", root=tmp_path)[0] == "2026-12"


def test_the_scheduler_marks_the_month_only_after_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "refresh", lambda **_kw: {"status": "holdings snapshot: refreshed", "alert": None,
                                                        "band_alert": store.band_notice(_body(), now=MONDAY,
                                                                                         root=tmp_path)})
    monkeypatch.setattr(store, "blocked_check", lambda **_kw: {})
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")

    def down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    def fire():
        schedule = scheduler.Schedule(schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS,
                                      request="", interval_seconds=3600, enabled=True, created_by="test",
                                      created_at=MONDAY, next_run_at=MONDAY)
        return scheduler._execute(schedule, now=MONDAY, ledger=None, working_memory=None, programization=None,
                                  repo_root=tmp_path, executor=lambda **_: {})

    monkeypatch.setattr(operator_mod, "notify_operator", down)
    assert "band notice not sent:CHANNEL_DOWN" in fire()
    assert not store.band_notice_path(tmp_path).exists()
    sent = []
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: sent.append(text))
    assert "band notice told" in fire() and len(sent) == 1
    assert store.band_notice(_body(), now=MONDAY, root=tmp_path) is None
