"""Step 2 of the archive backfill (Thomas 2026-10-03, R1): archive OI into ``oi_store``, as the service
user, never over a vendor row.

Pinned: a row is checked before it can be written (allowed symbol, an hour label for a closed hour, a
finite positive value); a held hour keeps the vendor's row; the dry run writes nothing; ``--confirm``
records the import beside the store; and what the host exports is exactly what the importer reads,
relabelled to the hour's start.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta

from runtime.mvp_runtime.crypto import oi_store
from scripts import binance_archive as ba
from scripts import import_archive_oi as imp

NOW = "2026-10-03T12:30:00Z"
ALLOWED = frozenset({"BTCUSDT", "ETHUSDT"})


def _line(**row):
    return json.dumps(row) + "\n"


def test_each_check_refuses_with_its_own_reason():
    good = {"symbol": "BTCUSDT", "timestamp": "2026-10-03T10:00:00Z", "open_interest": 84000.5}
    assert imp.check_row(good, allowed=ALLOWED, now=NOW) is None
    assert imp.check_row({**good, "symbol": "XRPUSDT"}, allowed=ALLOWED, now=NOW) == imp.REFUSED_SYMBOL
    assert imp.check_row({**good, "timestamp": "2026-10-03T10:05:00Z"}, allowed=ALLOWED, now=NOW) == imp.REFUSED_LABEL
    # 11:00 starts the hour that closes at 12:00 (closed); 12:00 starts one still open at 12:30.
    assert imp.check_row({**good, "timestamp": "2026-10-03T11:00:00Z"}, allowed=ALLOWED, now=NOW) is None
    assert imp.check_row({**good, "timestamp": "2026-10-03T12:00:00Z"}, allowed=ALLOWED, now=NOW) == imp.REFUSED_OPEN_HOUR
    for bad in (0, -1.0, float("nan"), float("inf"), True, "84000"):
        assert imp.check_row({**good, "open_interest": bad}, allowed=ALLOWED, now=NOW) == imp.REFUSED_VALUE
    assert imp.check_row(["not", "a", "row"], allowed=ALLOWED, now=NOW) == imp.REFUSED_SHAPE


def test_the_plan_counts_and_fingerprints_what_it_was_given():
    lines = [_line(symbol="BTCUSDT", timestamp="2026-10-01T00:00:00Z", open_interest=1.0),
             "not json\n", "\n",
             _line(symbol="DOGEUSDT", timestamp="2026-10-01T00:00:00Z", open_interest=1.0)]
    planned = imp.plan(lines, allowed=ALLOWED, now=NOW)
    assert planned["offered"] == 3
    assert planned["refused"] == {imp.REFUSED_SHAPE: 1, imp.REFUSED_SYMBOL: 1}
    assert list(planned["rows"]) == ["BTCUSDT"] and planned["sha256"].startswith("sha256:")


def test_a_held_hour_keeps_the_vendor_row_and_the_import_is_recorded(tmp_path):
    oi_store.append_rows([{"timestamp": "2026-10-01T01:00:00Z", "open_interest": 999.0}],
                         symbol="BTCUSDT", root=tmp_path)
    lines = [_line(symbol="BTCUSDT", timestamp=f"2026-10-01T0{h}:00:00Z", open_interest=100.0 + h)
             for h in range(3)]
    written = imp.apply(imp.plan(lines, allowed=ALLOWED, now=NOW), root=tmp_path, now=NOW)
    assert written == {"BTCUSDT": 2}
    by_hour = {r["timestamp"]: r["open_interest"] for r in oi_store.read_rows(tmp_path, symbol="BTCUSDT")}
    assert by_hour == {"2026-10-01T00:00:00Z": 100.0, "2026-10-01T01:00:00Z": 999.0,
                       "2026-10-01T02:00:00Z": 102.0}
    [record] = [json.loads(l) for l in imp.imports_path(tmp_path).read_text().splitlines()]
    assert record["per_symbol"] == {"BTCUSDT": {"checked": 3, "written": 2}}
    assert record["offered"] == 3 and record["input_sha256"].startswith("sha256:")
    # Rows carry the store's own shape and seal, nothing archive-specific.
    raw = [json.loads(l) for l in oi_store.oi_1h_path(tmp_path).read_text().splitlines()]
    assert all(set(r) == {"record_type", "symbol", "timestamp", "open_interest", "record_sha256"} for r in raw)


def test_the_dry_run_writes_nothing(tmp_path, capsys):
    lines = io.StringIO(_line(symbol="BTCUSDT", timestamp="2026-10-01T00:00:00Z", open_interest=1.0))
    assert imp.main([], root=tmp_path, stdin=lines) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not oi_store.oi_1h_path(tmp_path).exists() and not imp.imports_path(tmp_path).exists()


def test_apply_writes_through_the_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(imp, "assert_not_foreign_root_run", lambda root=None: None)
    lines = io.StringIO(_line(symbol="ETHUSDT", timestamp="2026-10-01T00:00:00Z", open_interest=2.0))
    assert imp.main(["--confirm"], root=tmp_path, stdin=lines) == 0
    assert "ETHUSDT: wrote 1" in capsys.readouterr().out
    assert [r["open_interest"] for r in oi_store.read_rows(tmp_path, symbol="ETHUSDT")] == [2.0]


def test_what_the_host_exports_is_what_the_importer_accepts(tmp_path):
    """The two halves agree on the row shape and on the label: the archive's 01:00 reading becomes the
    store's 00:00 hour."""
    day = date(2026, 9, 20)
    name = ba.daily_name("BTCUSDT", ba.METRICS, day)
    header = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
              "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio")
    start = datetime(2026, 9, 20)
    body = [header] + [f"{start + timedelta(minutes=5 * i):%Y-%m-%d %H:%M:%S},BTCUSDT,{1000 + i},1,1,1,1,1"
                       for i in range(288)]
    target = tmp_path / ba.METRICS / "BTCUSDT" / name
    target.parent.mkdir(parents=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name.replace(".zip", ".csv"), "\n".join(body) + "\n")
    target.write_bytes(buffer.getvalue())
    exported = list(ba.export_oi_rows(tmp_path, symbols=["BTCUSDT"]))
    assert exported[0] == {"symbol": "BTCUSDT", "timestamp": "2026-09-19T23:00:00Z", "open_interest": 1000.0}
    assert exported[1] == {"symbol": "BTCUSDT", "timestamp": "2026-09-20T00:00:00Z", "open_interest": 1012.0}
    planned = imp.plan([json.dumps(r) + "\n" for r in exported], allowed=ALLOWED, now=NOW)
    assert planned["refused"] == {} and len(planned["rows"]["BTCUSDT"]) == 24
