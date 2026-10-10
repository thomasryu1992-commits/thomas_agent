"""PR-0 (2026-10-10): a Binance cash-flow history is either read whole or the source fails and keeps its cursor.

Before this, each history was one request with no page argument: the venue answered its default page
(universal transfer 10 rows, Pay 100) and the rest of the window was dropped without a word, while the
cursor moved to ``now``. The fake venue here applies the window it is asked for (both bounds inclusive)
and the page size it is sent, or the venue's default when none is sent, so the old code shows the loss.
Synthetic rows only; no real API is called.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import binance_wallet, cash_flows

NOW_MS = 1_760_000_000_000                      # 2025-10-09T08:53:20Z
DAY = 86_400_000
HOUR = 3_600_000
DEFAULT_PAGE = {"crypto_deposit": 1000, "crypto_withdraw": 1000, "fiat_deposit": 100, "fiat_withdraw": 100,
                "fiat_buy": 100, "fiat_sell": 100, "pay": 100, "transfer_spot_to_futures": 10,
                "transfer_futures_to_spot": 10}
ABSENT = object()                                # a body with no ``total`` field
NULL = object()                                  # ``"total": null``
TIME_FIELD = {"crypto_deposit": "insertTime", "pay": "transactionTime", "transfer_spot_to_futures": "timestamp",
              "transfer_futures_to_spot": "timestamp", "fiat_deposit": "createTime", "fiat_withdraw": "createTime",
              "fiat_buy": "createTime", "fiat_sell": "createTime"}


class _Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _source_of(path: str, query: dict[str, str]) -> str:
    for name, (src_path, fixed, _window, _days) in binance_wallet.FLOW_SOURCES.items():
        if src_path == path and all(query.get(k) == str(v) for k, v in fixed.items()):
            return name
    raise AssertionError(f"unexpected request {path} {query}")


class Venue:
    """Rows per source; answers the asked window and page. ``fail`` maps a request number (1-based, per
    source) to an exception to raise; ``total_bias`` skews the reported total; ``change`` edits rows after
    the given request number, as a venue whose rows move under a read. ``bounds`` is how the venue reads
    a window — ``"[]"`` both ends in, ``"[)"``, ``"(]"``, ``"()"`` — since no Binance page says.
    ``total`` replaces the reported total (``ABSENT`` drops it); ``junk`` is appended to every page."""

    def __init__(self, rows=None, *, fail=None, total_bias=0, change=None, bounds="[]", total=None, junk=(),
                 envelope=None):
        self.rows = {name: list(r) for name, r in (rows or {}).items()}
        self.fail, self.total_bias, self.change = dict(fail or {}), total_bias, change
        self.bounds, self.total, self.junk = bounds, total, list(junk)
        self.envelope = dict(envelope or {})            # fiat and Pay: replaces code/success (ABSENT drops)
        self.sent: list[tuple[str, dict[str, str]]] = []

    def _inside(self, t, start, end):
        low = start <= t if self.bounds[0] == "[" else start < t
        high = t <= end if self.bounds[1] == "]" else t < end
        return low and high

    def count(self, source):
        return sum(1 for name, _q in self.sent if name == source)

    def __call__(self, request, timeout=None):
        parsed = urllib.parse.urlparse(request.full_url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        source = _source_of(parsed.path, query)
        self.sent.append((source, query))
        n = self.count(source)
        if (source, n) in self.fail:
            raise self.fail[(source, n)]
        if self.change and self.change[0] == (source, n - 1):
            self.change[1](self)
        _p, _f, (start_name, end_name), _d = binance_wallet.FLOW_SOURCES[source]
        start, end = int(query[start_name]), int(query[end_name])
        field = TIME_FIELD.get(source, "insertTime")
        inside = sorted((r for r in self.rows.get(source, []) if self._inside(r[field], start, end)),
                        key=lambda r: r[field], reverse=True)               # newest first
        page_name = binance_wallet.FLOW_PAGES[source][0] if hasattr(binance_wallet, "FLOW_PAGES") else None
        size = int(query.get(page_name, DEFAULT_PAGE[source])) if page_name else DEFAULT_PAGE[source]
        page = inside[:size] + self.junk
        total = (len(inside) + self.total_bias if self.total is None
                 else None if self.total is NULL else self.total)
        if source.startswith("transfer"):
            body = ({"rows": page} if self.total is ABSENT
                    else {"total": total, "rows": page} if page else {"total": 0})
        elif source.startswith("fiat"):
            body = {"code": "000000", "message": "success", "data": page, "total": total, "success": True}
            if self.total is ABSENT:
                body.pop("total")
        elif source == "pay":
            body = {"code": "000000", "message": "success", "data": page, "success": True}
        else:
            body = page
        if isinstance(body, dict) and (source.startswith("fiat") or source == "pay"):
            for key, value in self.envelope.items():
                if value is ABSENT:
                    body.pop(key, None)
                else:
                    body[key] = value
        return _Response(body)


def transfer(i, at, *, status="CONFIRMED", source="transfer_spot_to_futures"):
    kind = "MAIN_UMFUTURE" if source == "transfer_spot_to_futures" else "UMFUTURE_MAIN"
    return {"tranId": 9_000_000 + i, "asset": "USDT", "amount": "1", "status": status, "timestamp": at,
            "type": kind}


def pay_row(i, at):
    return {"transactionId": f"P{i:05d}", "amount": "1", "currency": "USDT", "transactionTime": at}


@pytest.fixture
def wallet(monkeypatch):
    monkeypatch.setenv(binance_wallet.BINANCE_WALLET_ENV, binance_wallet.BINANCE_WALLET_ON)
    monkeypatch.setenv(binance_wallet.API_KEY_ENV, "k-DONOTLEAK")
    monkeypatch.setenv(binance_wallet.API_SECRET_ENV, "s-DONOTLEAK")
    feed = binance_wallet.select_wallet_feed()
    assert isinstance(feed, binance_wallet.BinanceWalletFeed)
    return feed


def serve(monkeypatch, venue):
    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", venue)
    return venue


def read(wallet, source, start=NOW_MS - DAY, end=NOW_MS):
    return wallet.flow_history(source, start_ms=start, end_ms=end)


def spread(n, start=NOW_MS - DAY + 1000, end=NOW_MS - 1000):
    step = (end - start) // max(n, 1)
    return [start + i * step for i in range(n)]


# --- the feed: one read is whole, or it raises ---------------------------------------------------------

# T1
def test_t1_an_empty_window_is_one_request_and_no_rows(monkeypatch, wallet):
    venue = serve(monkeypatch, Venue())
    for source in binance_wallet.FLOW_SOURCES:
        assert read(wallet, source) == []
        assert venue.count(source) == 1


# T2: the normal case costs what it cost before — one request — plus the page argument at its maximum.
def test_t2_one_page_is_one_request_with_the_page_size_at_its_maximum(monkeypatch, wallet):
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": [transfer(i, t) for i, t in enumerate(spread(5))]}))
    rows = read(wallet, "transfer_spot_to_futures")
    assert len(rows) == 5 and venue.count("transfer_spot_to_futures") == 1
    _source, query = venue.sent[0]
    assert query["size"] == "100" and query["type"] == "MAIN_UMFUTURE"
    assert (query["startTime"], query["endTime"]) == (str(NOW_MS - DAY - 1), str(NOW_MS + 1))   # one wider
    for source, (name, size, _total, _id) in binance_wallet.FLOW_PAGES.items():
        read(wallet, source)
        assert venue.sent[-1][1][name] == str(size)


# T3
def test_t3_a_full_page_is_read_again_in_halves_until_every_row_is_in(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(150))]
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    got = read(wallet, "transfer_spot_to_futures")
    assert sorted(r["tranId"] for r in got) == sorted(r["tranId"] for r in rows)
    assert 1 < venue.count("transfer_spot_to_futures") <= binance_wallet.FLOW_MAX_REQUESTS


# T4: the case that was lost — more transfers in a window than the venue's default page of 10.
def test_t4_more_transfers_than_the_default_page_all_arrive(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(15))]
    serve(monkeypatch, Venue({"transfer_futures_to_spot": [transfer(i, t, source="transfer_futures_to_spot")
                                                           for i, t in enumerate(spread(15))]}))
    assert len(read(wallet, "transfer_futures_to_spot")) == 15
    serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    assert len(read(wallet, "transfer_spot_to_futures")) == 15


# T5
@pytest.mark.parametrize("bias", [1, -1])
def test_t5_a_total_that_disagrees_with_the_rows_is_incomplete(monkeypatch, wallet, bias):
    serve(monkeypatch, Venue({"fiat_deposit": [{"orderNo": f"F{i}", "createTime": t}
                                               for i, t in enumerate(spread(3))]}, total_bias=bias))
    with pytest.raises(ToolError) as exc:
        read(wallet, "fiat_deposit")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


# T6, T7: a piece that fails fails the read; nothing read before it is returned.
@pytest.mark.parametrize("error, code", [
    (urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b'{"code": -1000}')), None),
    (socket.timeout("timed out"), "TOOL_TRANSPORT"),
])
def test_t6_t7_a_failed_or_timed_out_piece_fails_the_whole_read(monkeypatch, wallet, error, code):
    rows = [transfer(i, t) for i, t in enumerate(spread(150))]
    serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}, fail={("transfer_spot_to_futures", 2): error}))
    with pytest.raises(ToolError) as exc:
        read(wallet, "transfer_spot_to_futures")
    if code:
        assert exc.value.reason_code == code


def _seams(venue, source):
    """Where a full page was split: one piece ends at ``middle + 1`` and the other starts at ``middle``."""
    sent = [q for name, q in venue.sent if name == source]
    return {str(int(q["endTime"]) - 1) for q in sent} & {q["startTime"] for q in sent}


# T8: a full page is split at its rows' median time; the two sides share that instant and the next, so
# the row sitting on it is read twice and returned once.
def test_t8_a_row_on_the_seam_is_returned_once(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(121))]
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    got = read(wallet, "transfer_spot_to_futures")
    seams = _seams(venue, "transfer_spot_to_futures")
    assert seams and all(any(str(r["timestamp"]) == seam for r in rows) for seam in seams)   # a real row on it
    assert sorted(r["tranId"] for r in got) == sorted(r["tranId"] for r in rows)              # each once


def test_t8b_a_row_that_changes_between_two_pieces_is_incomplete(monkeypatch, wallet):
    rows = [transfer(i, t, status="PENDING") for i, t in enumerate(spread(121))]

    def settle(venue):
        # Before the third piece: the row on the seam (the second piece ends one past it) settles.
        seam = int([q for name, q in venue.sent if name == "transfer_spot_to_futures"][1]["endTime"]) - 1
        for row in venue.rows["transfer_spot_to_futures"]:
            if row["timestamp"] == seam:
                row["status"] = "CONFIRMED"

    serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}, change=(("transfer_spot_to_futures", 2), settle)))
    with pytest.raises(ToolError) as exc:
        read(wallet, "transfer_spot_to_futures")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


# T9, T11: rows piled on one instant cannot be split apart; that is a failure, never a guess.
def test_t9_rows_piled_on_one_instant_fail_closed(monkeypatch, wallet):
    at = NOW_MS - HOUR
    serve(monkeypatch, Venue({"pay": [pay_row(i, at) for i in range(101)]}))
    with pytest.raises(ToolError) as exc:
        read(wallet, "pay")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


def test_t11_a_full_page_in_a_window_of_one_millisecond_cannot_be_split(monkeypatch, wallet):
    # The asked window is widened by an instant each side; the pile on one instant still cannot be split.
    at = NOW_MS - HOUR
    venue = serve(monkeypatch, Venue({"pay": [pay_row(i, at) for i in range(100)]}))
    with pytest.raises(ToolError) as exc:
        read(wallet, "pay", at, at + 1)
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE
    assert 1 <= venue.count("pay") <= binance_wallet.FLOW_MAX_REQUESTS


# T10: Pay has no page and no total — a full page of 100 is split; 99 is whole as it is.
@pytest.mark.parametrize("n, requests", [(99, 1), (100, 3), (150, 5)])
def test_t10_pay_at_its_page_of_100(monkeypatch, wallet, n, requests):
    venue = serve(monkeypatch, Venue({"pay": [pay_row(i, t) for i, t in enumerate(spread(n))]}))
    assert len(read(wallet, "pay")) == n
    assert venue.count("pay") == requests


# T12: the request ceiling, then the time budget.
def test_t12_a_read_that_needs_more_pieces_than_allowed_fails_at_the_ceiling(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(1000, NOW_MS - 7 * DAY + DAY // 2, NOW_MS))]
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    with pytest.raises(ToolError) as exc:
        read(wallet, "transfer_spot_to_futures", NOW_MS - 7 * DAY + 60_000, NOW_MS)
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE
    assert venue.count("transfer_spot_to_futures") == binance_wallet.FLOW_MAX_REQUESTS


def test_t12b_a_read_past_its_time_budget_fails(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(150))]
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    calls = [0]

    def clock():                                   # the start and the first check read 0; then time has run out
        calls[0] += 1
        return 0.0 if calls[0] <= 2 else binance_wallet.FLOW_READ_SECONDS + 1

    monkeypatch.setattr(binance_wallet.time, "monotonic", clock)
    with pytest.raises(ToolError) as exc:
        read(wallet, "transfer_spot_to_futures")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE and venue.count("transfer_spot_to_futures") == 1


# --- the ledger: an incomplete source keeps its cursor; the next whole read brings the rows in ----------

T0, T1, T2 = NOW_MS - 2 * HOUR, NOW_MS - HOUR, NOW_MS


def _iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fire(wallet, d, at):
    return cash_flows.collect_binance(wallet, d, now=_iso(at), now_ms=at)


def cursors(d):
    return {name: (info or {}).get("cursor_ms") for name, info in cash_flows.load_state(d)["sources"].items()}


def transfer_keys(d):
    return [r["source_event_key"] for r in cash_flows.verify(d) if "transfer" in str(r.get("source"))]


def test_t13_to_t16_an_incomplete_source_keeps_its_cursor_and_the_next_fire_reads_it_whole(monkeypatch, wallet,
                                                                                          tmp_path):
    cash_flows.set_cutover(tmp_path, at=_iso(T0 - DAY), requested_by="test")
    serve(monkeypatch, Venue())
    fire(wallet, tmp_path, T0)                                               # every cursor at T0
    before = cursors(tmp_path)
    rows = [transfer(i, t) for i, t in enumerate(spread(150, T0 + 1000, T1 - 1000))]   # a full page: split
    timeout = socket.timeout("timed out")
    serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}, fail={("transfer_spot_to_futures", 2): timeout}))
    result = fire(wallet, tmp_path, T1)
    # T13: the transfer source failed, kept its cursor, and wrote nothing; the others moved on as before.
    assert set(result["errors"]) == {"transfer_spot_to_futures"}
    after = cursors(tmp_path)
    assert after["transfer_spot_to_futures"] == before["transfer_spot_to_futures"] == T0
    assert all(after[name] == T1 for name in after if name != "transfer_spot_to_futures")
    assert transfer_keys(tmp_path) == []
    # T16: the readiness of that fire is not PASS.
    ready = cash_flows.readiness(tmp_path, now=_iso(T1), current={"binance": not result["errors"], "toss": True,
                                                                  "freshness": True, "coherence": True})
    assert ready["checks"]["binance_access"] != "PASS" and ready["ready"] is False
    # T14: the next fire reads the stretch again, whole.
    serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    assert fire(wallet, tmp_path, T2)["errors"] == {}
    assert len(set(transfer_keys(tmp_path))) == 150
    # T15: and again — nothing is appended twice.
    fire(wallet, tmp_path, T2 + HOUR)
    keys = transfer_keys(tmp_path)
    assert len(keys) == len(set(keys)) == 150


# --- follow-up review (2026-10-10): total, odd rows, an exactly full page, bounds, budgets -------------

def fiat(i, at):
    return {"orderNo": f"F{i:05d}", "createTime": at}


ROWS = {"transfer_spot_to_futures": transfer, "fiat_deposit": fiat, "pay": pay_row}


# R1: where the venue documents ``total`` it is part of the answer. Rows without a usable one are not
# shown complete; a quiet window with neither rows nor total stays a normal empty read.
@pytest.mark.parametrize("source", ["transfer_spot_to_futures", "fiat_deposit"])
@pytest.mark.parametrize("total", [ABSENT, NULL, "3", True, 2.5], ids=["absent", "null", "text", "bool", "float"])
def test_r1_rows_without_a_usable_total_are_incomplete(monkeypatch, wallet, source, total):
    serve(monkeypatch, Venue({source: [ROWS[source](i, t) for i, t in enumerate(spread(3))]}, total=total))
    with pytest.raises(ToolError) as exc:
        read(wallet, source)
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


@pytest.mark.parametrize("source", ["transfer_spot_to_futures", "fiat_deposit"])
def test_r1b_a_quiet_window_without_a_total_is_still_empty(monkeypatch, wallet, source):
    serve(monkeypatch, Venue(total=ABSENT))
    assert read(wallet, source) == []


# R2: a row that is not an object is not dropped in silence — the page is malformed.
@pytest.mark.parametrize("source", ["transfer_spot_to_futures", "pay", "crypto_deposit"])
@pytest.mark.parametrize("junk", ["text", 5, None, ["a"]], ids=["text", "number", "null", "list"])
def test_r2_a_row_that_is_not_an_object_fails_the_read(monkeypatch, wallet, source, junk):
    make = ROWS.get(source, lambda i, t: {"id": f"D{i}", "insertTime": t})
    serve(monkeypatch, Venue({source: [make(i, t) for i, t in enumerate(spread(3))]}, junk=[junk]))
    with pytest.raises(ToolError) as exc:
        read(wallet, source)
    assert exc.value.reason_code == "MALFORMED_RESULT"


# R3: a page exactly at the maximum. With ``total`` equal to it the answer is whole, in one request, even
# when every row sits on one instant; without ``total`` (Pay) a full page on one instant stays unprovable.
@pytest.mark.parametrize("source, size", [("transfer_spot_to_futures", 100), ("fiat_deposit", 500)])
def test_r3_a_page_exactly_full_with_its_total_is_whole_in_one_request(monkeypatch, wallet, source, size):
    venue = serve(monkeypatch, Venue({source: [ROWS[source](i, t) for i, t in enumerate(spread(size))]}))
    assert len(read(wallet, source)) == size and venue.count(source) == 1


def test_r3b_a_full_page_with_its_total_on_one_instant_is_whole(monkeypatch, wallet):
    at = NOW_MS - HOUR
    serve(monkeypatch, Venue({"transfer_spot_to_futures": [transfer(i, at) for i in range(100)]}))
    assert len(read(wallet, "transfer_spot_to_futures", at, at + 1)) == 100


def test_r3c_a_full_page_whose_total_says_more_is_still_split(monkeypatch, wallet):
    rows = [transfer(i, t) for i, t in enumerate(spread(101))]
    venue = serve(monkeypatch, Venue({"transfer_spot_to_futures": rows}))
    assert len(read(wallet, "transfer_spot_to_futures")) == 101 and venue.count("transfer_spot_to_futures") > 1


# R4: no Binance page says whether a window's ends are in or out. Every reading must reach every row,
# the one on the seam included.
@pytest.mark.parametrize("bounds", ["[]", "[)", "(]", "()"])
@pytest.mark.parametrize("source", ["transfer_spot_to_futures", "pay"])
def test_r4_every_row_arrives_however_the_venue_bounds_a_window(monkeypatch, wallet, bounds, source):
    rows = [ROWS[source](i, t) for i, t in enumerate(spread(150))]
    serve(monkeypatch, Venue({source: rows}, bounds=bounds))
    got = read(wallet, source)
    ident = binance_wallet.FLOW_PAGES[source][3]
    assert sorted(r[ident] for r in got) == sorted(r[ident] for r in rows)


# R5: the request budget is per source and per read: one source spending its budget costs no other.
def test_r5_one_source_over_its_budget_leaves_the_others_whole(monkeypatch, wallet, tmp_path):
    window_start = NOW_MS - 7 * DAY + DAY // 2
    venue = serve(monkeypatch, Venue({
        "transfer_spot_to_futures": [transfer(i, t) for i, t in enumerate(spread(1000, window_start, NOW_MS))],
        "transfer_futures_to_spot": [transfer(i, t, source="transfer_futures_to_spot")
                                     for i, t in enumerate(spread(150))],
        "pay": [pay_row(i, t) for i, t in enumerate(spread(150))]}))
    result = fire(wallet, tmp_path, NOW_MS)
    assert set(result["errors"]) == {"transfer_spot_to_futures"}
    assert venue.count("transfer_spot_to_futures") == binance_wallet.FLOW_MAX_REQUESTS
    assert 1 < venue.count("transfer_futures_to_spot") <= binance_wallet.FLOW_MAX_REQUESTS
    assert 1 < venue.count("pay") <= binance_wallet.FLOW_MAX_REQUESTS
    assert all(venue.count(name) == 1 for name in binance_wallet.FLOW_SOURCES
               if name not in ("transfer_spot_to_futures", "transfer_futures_to_spot", "pay"))
    state = cursors(tmp_path)
    assert state["transfer_spot_to_futures"] is None and state["pay"] == state["transfer_futures_to_spot"] == NOW_MS


def test_r5b_every_read_starts_a_fresh_budget(monkeypatch, wallet):
    rows = [pay_row(i, t) for i, t in enumerate(spread(150))]
    venue = serve(monkeypatch, Venue({"pay": rows}))
    for _ in range(3):
        assert len(read(wallet, "pay")) == 150
    assert venue.count("pay") == 3 * (venue.count("pay") // 3) > 3


# --- final integrity review (2026-10-10): the outer bounds, rows without an id, a failed envelope -------

# B1–B8: a row exactly on the asked window's first or last instant arrives, however the venue bounds a
# window, in a single page and in a split read alike. The seams inside a split are R4's; these are the ends.
@pytest.mark.parametrize("bounds", ["[]", "[)", "(]", "()"])
@pytest.mark.parametrize("n", [3, 150], ids=["one page", "split"])
@pytest.mark.parametrize("source", ["transfer_spot_to_futures", "pay"])
def test_b_a_row_on_the_first_or_last_instant_arrives(monkeypatch, wallet, bounds, n, source):
    start, end = NOW_MS - DAY, NOW_MS
    make = ROWS[source]
    rows = [make(i, t) for i, t in enumerate(spread(n))] + [make(7001, start), make(7002, end)]
    serve(monkeypatch, Venue({source: rows}, bounds=bounds))
    ident = binance_wallet.FLOW_PAGES[source][3]
    got = sorted(r[ident] for r in read(wallet, source, start, end))
    assert got == sorted(r[ident] for r in rows)


def test_b9_a_window_already_at_the_venue_limit_is_asked_as_it_is(monkeypatch, wallet):
    # No room to widen: the read keeps the caller's window, so the venue's longest window is never passed.
    _p, _f, (start_name, end_name), days = binance_wallet.FLOW_SOURCES["transfer_spot_to_futures"]
    venue = serve(monkeypatch, Venue())
    read(wallet, "transfer_spot_to_futures", NOW_MS - days * DAY, NOW_MS)
    query = venue.sent[-1][1]
    assert int(query[end_name]) - int(query[start_name]) <= days * DAY


# I1–I6: the read folds only copies it can prove are one row — the same venue id. Rows without an id are
# the normalizers' to judge (a malformed-row event each); the read never merges two of them by content.
def _no_id(at, amount="1"):
    return {"amount": amount, "currency": "USDT", "transactionTime": at}


def test_i1_i3_rows_by_id(monkeypatch, wallet):
    t = spread(3)
    serve(monkeypatch, Venue({"pay": [pay_row(1, t[0]), pay_row(2, t[1])]}))
    assert len(read(wallet, "pay")) == 2                                           # I1: two ids, two rows
    serve(monkeypatch, Venue({"pay": [pay_row(1, t[0]), pay_row(1, t[0])]}))
    assert len(read(wallet, "pay")) == 1                                           # I2: one id, one content
    serve(monkeypatch, Venue({"pay": [pay_row(1, t[0]), {**pay_row(1, t[0]), "amount": "2"}]}))
    with pytest.raises(ToolError) as exc:                                          # I3: one id, two contents
        read(wallet, "pay")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


@pytest.mark.parametrize("rows, kept", [
    ([_no_id(NOW_MS - HOUR)], 1),                                                  # I4
    ([_no_id(NOW_MS - HOUR), _no_id(NOW_MS - HOUR)], 2),                           # I5: never merged by content
    ([_no_id(NOW_MS - HOUR), _no_id(NOW_MS - HOUR, "2")], 2),                      # I6
], ids=["I4 one", "I5 two alike", "I6 two different"])
def test_i4_i6_rows_without_an_id_are_all_returned_from_one_page(monkeypatch, wallet, rows, kept):
    serve(monkeypatch, Venue({"pay": rows}))
    assert len(read(wallet, "pay")) == kept


def test_i7_a_row_without_an_id_in_a_split_read_cannot_be_told_from_its_seam_copy(monkeypatch, wallet):
    rows = [pay_row(i, t) for i, t in enumerate(spread(150))] + [_no_id(NOW_MS - DAY // 2)]
    serve(monkeypatch, Venue({"pay": rows}))
    with pytest.raises(ToolError) as exc:
        read(wallet, "pay")
    assert exc.value.reason_code == binance_wallet.FLOW_INCOMPLETE


def test_i8_alike_rows_without_an_id_reach_the_ledger_as_malformed(monkeypatch, wallet, tmp_path):
    # The normalizer, not the read, owns them: a malformed-row event, the source's schema MISMATCH.
    serve(monkeypatch, Venue({"pay": [_no_id(NOW_MS - HOUR), _no_id(NOW_MS - HOUR, "2")]}))
    fire(wallet, tmp_path, NOW_MS)
    assert len([r for r in cash_flows.verify(tmp_path) if r["event"] == cash_flows.EVENT_MALFORMED]) == 2
    assert cash_flows.load_state(tmp_path)["sources"]["pay"]["schema"] == cash_flows.SCHEMA_MISMATCH


# E·A–H: fiat (and Pay) wrap their rows in ``code`` and ``success``. A body that says the call failed — or
# says nothing either way, or contradicts itself — is a refused read, never an empty history.
ROW = object()


@pytest.mark.parametrize("case, envelope, rows, ok", [
    ("A", {"code": "000000", "success": True}, [], True),
    ("B", {"code": "000000", "success": True}, ROW, True),
    ("C", {"code": "100001", "success": False}, [], False),
    ("D", {"code": "100001", "success": False}, ROW, False),
    ("E", {"success": False, "code": ABSENT}, [], False),
    ("F code missing", {"code": ABSENT, "success": True}, ROW, True),
    ("G success missing", {"code": "000000", "success": ABSENT}, ROW, True),
    ("both missing", {"code": ABSENT, "success": ABSENT}, ROW, False),
    ("H ok code, false", {"code": "000000", "success": False}, ROW, False),
    ("H bad code, true", {"code": "100001", "success": True}, [], False),
])
@pytest.mark.parametrize("source", ["fiat_deposit", "fiat_buy", "pay"])
def test_e_a_failed_envelope_is_never_an_empty_history(monkeypatch, wallet, source, case, envelope, rows, ok):
    data = [ROWS.get(source, fiat)(i, t) for i, t in enumerate(spread(2))] if rows is ROW else []
    venue = Venue({source: data}, envelope=envelope)
    if case == "E":
        venue.total = 0
    serve(monkeypatch, venue)
    if ok:
        assert len(read(wallet, source)) == len(data)
        return
    with pytest.raises(ToolError) as exc:
        read(wallet, source)
    assert exc.value.reason_code == "BINANCE_WALLET_REJECTED"
    assert "message" not in str(exc.value) and "success" not in str(exc.value).lower()


def test_e_a_failed_fiat_envelope_keeps_the_cursor(monkeypatch, wallet, tmp_path):
    serve(monkeypatch, Venue())
    fire(wallet, tmp_path, T0)
    serve(monkeypatch, Venue(envelope={"code": "100001", "success": False, "message": "sensitive detail"}))
    result = fire(wallet, tmp_path, T1)
    assert {"fiat_deposit", "fiat_withdraw", "fiat_buy", "fiat_sell", "pay"} <= set(result["errors"])
    after = cursors(tmp_path)
    assert all(after[name] == T0 for name in ("fiat_deposit", "fiat_withdraw", "fiat_buy", "fiat_sell", "pay"))
    assert after["crypto_deposit"] == T1
    assert "sensitive" not in json.dumps(cash_flows.load_state(tmp_path))
