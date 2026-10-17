"""C7 PR-C: the operator's adoption of a ledger the checkpoint refuses (C7 §7, D3; synthetic only).

Two steps: the first shows what would be adopted and writes nothing; the second takes back the 8 characters
it printed and, under both locks, adopts the ledger only if it still ends there. The adoption line erases
nothing, a second run on the same end adds no line, a stop between the line and the state save is finished
by the next write, and an adoption never raises the history check by itself. Claude never runs either step
(D3); the guard refuses the mode (``test_claude_local_only_guard.py``).
"""

from __future__ import annotations

import json
import shutil
from unittest import mock

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import CURRENT_OK, CUTOVER, Feed, iso, ledger, ms, pay, resolve, set_cutover

T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z"
P2 = pay("p2", "777.77", at="2026-10-09T04:30:00Z", currency="SECRETCOIN")
WHO = {"requested_by": "thomas", "reason": "ledger restored from the 10-08 archive"}


class VenueFeed(Feed):
    def flow_history(self, source, *, start_ms, end_ms, timeout_seconds=5):
        rows = super().flow_history(source, start_ms=start_ms, end_ms=end_ms)
        return [r for r in rows if start_ms <= int(r.get("transactionTime") or r.get("insertTime") or 0) <= end_ms]


def fire(d, now, *pays):
    return cash_flows.collect_binance(VenueFeed(pay=list(pays)), d, now=now, now_ms=ms(now))


def later(at, hours=0.0):
    return iso(ms(at) + int(hours * 3_600_000))


def files(d):
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file() and not p.name.endswith(".lock")}


def state(d):
    return cash_flows.load_state(d)


def preview(d, **kw):
    return cash_flows.adoption_preview(d, interactive=True, **{**WHO, **kw})


def adopt(d, confirm, now, **kw):
    return cash_flows.adopt_ledger(d, confirm=confirm, interactive=True, now=now, **{**WHO, **kw})


@pytest.fixture
def refused(tmp_path):
    """A lane whose ledger was put back from a backup taken before P2: every write now refuses. The cutover
    line is stamped on the synthetic day (as a real lane's first line is older than any told record)."""
    d = tmp_path / "lane"
    with mock.patch.object(cash_flows.timeutil, "utc_now_iso", return_value=CUTOVER):
        set_cutover(d)
    fire(d, T1)
    backup = tmp_path / "backup"
    shutil.copytree(d, backup)
    fire(d, T2, P2)
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(d))
    with pytest.raises(ToolError) as caught:
        fire(d, later(T2, 1), P2)
    assert caught.value.reason_code == cash_flows.LEDGER_TAMPERED
    return d


def test_step_one_shows_what_would_be_adopted_and_writes_nothing(refused):
    before = files(refused)
    shown = preview(refused)
    assert files(refused) == before
    rows = ledger(refused)
    assert shown["ledger_lines"] == len(rows) and shown["confirm"] == rows[-1]["sha256"].removeprefix("sha256:")[:8]
    assert shown["detector"] == cash_flows.CHECKPOINT_SHORTER
    assert shown["replaced_checkpoint"]["lines"] > len(rows)
    text = json.dumps(shown)
    for leak in ("SECRETCOIN", "777.77", "pay:p2", "USDT"):
        assert leak not in text, leak


def test_step_two_adopts_the_ledger_and_the_writes_resume(refused):
    shown = preview(refused)
    before = ledger(refused)
    done = adopt(refused, shown["confirm"], later(T2, 2))
    rows = ledger(refused)
    assert done["written"] is True and len(rows) == len(before) + 1 and rows[:-1] == before   # erases nothing
    line = rows[-1]
    assert line["event"] == cash_flows.EVENT_LEDGER_ADOPTED and line["requested_by"] == "thomas"
    assert line["adopted"] == {"lines": len(before), "sha256": before[-1]["sha256"]}
    assert line["replaced_checkpoint"]["lines"] == shown["replaced_checkpoint"]["lines"]
    assert line["detector"] == cash_flows.CHECKPOINT_SHORTER
    body = state(refused)
    assert cash_flows.DETECTION_MARKER_KEY not in body
    assert body[cash_flows.CHECKPOINT_KEY]["lines"] == len(rows)
    assert body[cash_flows.CHECKPOINT_KEY]["observed_at"] == line["at"] == later(T2, 2)   # observed from here on
    assert cash_flows.blocked_state(refused)["blocked"] is False
    fire(refused, later(T2, 3), P2)                                                         # writes resume
    assert "pay:p2" in {r.get("source_event_key") for r in ledger(refused)}               # inside the overlap
    ready = cash_flows.readiness(refused, now=later(T2, 3), current=CURRENT_OK)
    assert ready["checks"]["ledger_state_consistency"] == "PASS"


@pytest.mark.parametrize("confirm", ["00000000", "zzzzzzzz", "1234", "", None])
def test_a_wrong_confirmation_writes_nothing(refused, confirm):
    before = files(refused)
    with pytest.raises(ToolError) as caught:
        adopt(refused, confirm, later(T2, 2))
    assert caught.value.reason_code == cash_flows.ADOPT_REFUSED and files(refused) == before


def test_a_ledger_that_changed_after_step_one_is_refused(refused, tmp_path):
    shown = preview(refused)
    other = tmp_path / "other"
    set_cutover(other)
    fire(other, T1, pay("p9", at="2026-10-09T03:45:00Z"))
    shutil.copyfile(cash_flows.ledger_path(other), cash_flows.ledger_path(refused))   # put back again
    before = files(refused)
    with pytest.raises(ToolError) as caught:
        adopt(refused, shown["confirm"], later(T2, 2))
    assert caught.value.reason_code == cash_flows.ADOPT_REFUSED and "no longer ends" in str(caught.value)
    assert files(refused) == before


def test_a_second_run_on_the_same_end_adds_no_line(refused):
    shown = preview(refused)
    adopt(refused, shown["confirm"], later(T2, 2))
    rows, kept = ledger(refused), state(refused)[cash_flows.CHECKPOINT_KEY]
    again = adopt(refused, shown["confirm"], later(T2, 3))
    assert again["written"] is False and ledger(refused) == rows
    assert state(refused)[cash_flows.CHECKPOINT_KEY]["observed_at"] == kept["observed_at"]


def test_a_stop_between_the_line_and_the_save_is_finished_by_the_next_write(refused, monkeypatch):
    shown = preview(refused)
    real = cash_flows._save_state

    def lost(_d, _body):
        raise OSError("the process stopped")

    monkeypatch.setattr(cash_flows, "_save_state", lost)
    with pytest.raises(OSError):
        adopt(refused, shown["confirm"], later(T2, 2))
    monkeypatch.setattr(cash_flows, "_save_state", real)
    line = ledger(refused)[-1]
    assert line["event"] == cash_flows.EVENT_LEDGER_ADOPTED                                # the line landed
    assert cash_flows.blocked_state(refused)["blocked"] is False                           # §7.6: not BLOCKED
    fire(refused, later(T2, 3), P2)                                                        # the write finishes it
    body = state(refused)
    assert body[cash_flows.CHECKPOINT_KEY]["observed_at"] == line["at"]
    assert body[cash_flows.CHECKPOINT_KEY]["lines"] == len(ledger(refused))


def test_a_stop_between_the_line_and_the_save_is_also_finished_by_running_step_two_again(refused, monkeypatch):
    shown = preview(refused)
    real = cash_flows._save_state
    monkeypatch.setattr(cash_flows, "_save_state", lambda _d, _b: (_ for _ in ()).throw(OSError("stopped")))
    with pytest.raises(OSError):
        adopt(refused, shown["confirm"], later(T2, 2))
    monkeypatch.setattr(cash_flows, "_save_state", real)
    rows = ledger(refused)
    assert adopt(refused, shown["confirm"], later(T2, 3))["written"] is False and ledger(refused) == rows
    assert state(refused)[cash_flows.CHECKPOINT_KEY]["lines"] == len(rows)


def test_a_marker_pr_a_left_is_cleared_only_by_an_adoption(tmp_path):
    """A detection PR-A's code recorded before PR-C still refuses, though the ledger reads consistent now;
    the adoption is what clears it."""
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    body = state(tmp_path)
    kept = body[cash_flows.CHECKPOINT_KEY]
    body[cash_flows.DETECTION_MARKER_KEY] = {"reason_code": cash_flows.LEDGER_MISMATCH_DETECTED,
                                             "reason": cash_flows.CHECKPOINT_REPLACED, "since": T1,
                                             "checkpoint": {"lines": kept["lines"], "sha256": kept["sha256"]}}
    cash_flows._save_state(tmp_path, body)
    with pytest.raises(ToolError):
        fire(tmp_path, T2, P2)
    shown = preview(tmp_path)
    assert shown["detector"] == cash_flows.CHECKPOINT_REPLACED
    adopt(tmp_path, shown["confirm"], later(T2, 1))
    assert cash_flows.DETECTION_MARKER_KEY not in state(tmp_path)
    fire(tmp_path, later(T2, 2), P2)                                                       # writes resume
    assert "pay:p2" in {r.get("source_event_key") for r in ledger(tmp_path)}


def test_told_keys_the_ledger_lost_are_dropped_and_counted(tmp_path):
    d = tmp_path / "lane"
    set_cutover(d)
    fire(d, T1)
    backup = tmp_path / "backup"
    shutil.copytree(d, backup)
    fire(d, T2, P2)
    ready = cash_flows.update_readiness(d, now=T2, current=CURRENT_OK)
    cash_flows.mark_exceptions_told(d, token=ready["exceptions_token"], now=T2)
    assert state(d)["exceptions"]["told_keys"]
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(d))
    shown = preview(d)
    assert shown["told_keys_dropped"] == 1
    done = adopt(d, shown["confirm"], later(T2, 2))
    assert done["told_keys_dropped"] == 1 and ledger(d)[-1]["told_keys_dropped"] == 1
    assert state(d)["exceptions"]["told_keys"] == []
    assert cash_flows.ledger_problem(ledger(d), state(d)) is None                     # the second detector is quiet


def test_an_adoption_does_not_raise_the_history_until_the_cutover_moves_past_it(refused):
    shown = preview(refused)
    with pytest.raises(ToolError):                                                     # refused before the adoption
        cash_flows.set_cutover(refused, at=later(T2, 1.5), requested_by="thomas", reason="C", migrate=True,
                               now=later(T2, 1.5))
    adopt(refused, shown["confirm"], later(T2, 2))
    fire(refused, later(T2, 3))
    _verdict, detail = cash_flows.history_completeness(ledger(refused), state(refused), now=later(T2, 3))
    assert detail["reason"] == "checkpoint_after_cutover"                              # D3: adoption alone
    cash_flows.set_cutover(refused, at=later(T2, 2.5), requested_by="thomas", reason="C7 option C, after adoption",
                           migrate=True, now=later(T2, 3.5))
    fire(refused, later(T2, 4))
    _verdict, detail = cash_flows.history_completeness(ledger(refused), state(refused), now=later(T2, 4))
    assert detail["reason"] != "checkpoint_after_cutover"


def test_the_refusal_is_told_once_and_its_clearing_once_after_the_adoption(refused):
    first = cash_flows.blocked_alert(refused, now=later(T2, 1))["alert"]
    assert first is not None and "거부" in first[1] and "채택" in first[1]
    assert cash_flows.mark_blocked_told(refused, token=first[0], now=later(T2, 1))
    assert cash_flows.blocked_alert(refused, now=later(T2, 1.5))["alert"] is None
    adopt(refused, preview(refused)["confirm"], later(T2, 2))
    cleared = cash_flows.blocked_alert(refused, now=later(T2, 2.5))["alert"]
    assert cleared is not None and "해소" in cleared[1]
    assert cash_flows.mark_blocked_told(refused, token=cleared[0], now=later(T2, 2.5))
    assert cash_flows.blocked_alert(refused, now=later(T2, 3))["alert"] is None


@pytest.mark.parametrize("change, phrase", [
    (dict(interactive=False), "interactive terminal"),
    (dict(requested_by=" "), "--by and --reason"),
    (dict(reason=""), "--by and --reason"),
])
def test_the_operator_conditions_refuse_before_anything_is_read(refused, monkeypatch, change, phrase):
    def opened(_d):
        raise AssertionError("the ledger was read")
    monkeypatch.setattr(cash_flows, "verify", opened)
    for step in (lambda: cash_flows.adoption_preview(refused, **{**WHO, "interactive": True, **change}),
                 lambda: cash_flows.adopt_ledger(refused, confirm="00000000", now=T2,
                                                 **{**WHO, "interactive": True, **change})):
        with pytest.raises(ToolError) as caught:
            step()
        assert caught.value.reason_code == cash_flows.ADOPT_REFUSED and phrase in str(caught.value)


def test_a_ledger_that_continues_its_checkpoint_has_nothing_to_adopt(tmp_path):
    set_cutover(tmp_path)
    fire(tmp_path, T1)
    for step in (lambda: preview(tmp_path), lambda: adopt(tmp_path, "00000000", T2)):
        with pytest.raises(ToolError) as caught:
            step()
        assert caught.value.reason_code == cash_flows.ADOPT_REFUSED and "nothing to adopt" in str(caught.value)


def test_an_empty_ledger_and_a_broken_chain_are_not_adoptable(refused, tmp_path):
    lines = cash_flows.ledger_path(refused).read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["edited"] = True
    lines[0] = json.dumps(row)
    cash_flows.ledger_path(refused).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as caught:
        preview(refused)
    assert caught.value.reason_code == cash_flows.LEDGER_TAMPERED                       # a chain is not adopted
    cash_flows.ledger_path(refused).unlink()
    with pytest.raises(ToolError) as caught:
        preview(refused)
    assert caught.value.reason_code == cash_flows.ADOPT_REFUSED and "empty" in str(caught.value)


def test_an_adoption_line_outside_its_form_is_never_written():
    good = {"event": cash_flows.EVENT_LEDGER_ADOPTED, "source_event_key": "ledger_adopted:x",
            "adopted": {"lines": 1, "sha256": "sha256:" + "a" * 64}, "replaced_checkpoint": {"lines": 2, "sha256": "b"},
            "detector": cash_flows.CHECKPOINT_SHORTER, "told_keys_dropped": 0, "requested_by": "t", "reason": "r",
            "timestamp": T1}
    cash_flows._validate(good)
    for bad in ({**good, "detector": "anything"}, {k: v for k, v in good.items() if k != "replaced_checkpoint"}):
        with pytest.raises(ToolError) as caught:
            cash_flows._validate(bad)
        assert caught.value.reason_code == cash_flows.EVENT_INVALID


def test_the_adoption_line_changes_no_reader_of_the_ledger(refused):
    """D1 condition 4: a reader that predates the line sees the same flows, exceptions and Toss windows."""
    rows = ledger(refused)
    readers = (lambda r: cash_flows.detected_exceptions(r), lambda r: cash_flows.lifecycle(r),
               lambda r: cash_flows.toss_continuity(r, now=later(T2, 3)), lambda r: cash_flows.shadow_record(r),
               lambda r: cash_flows.cutover_at(r))
    before = [reader(rows) for reader in readers]
    adopt(refused, preview(refused)["confirm"], later(T2, 2))
    after_rows = ledger(refused)
    assert [reader(after_rows) for reader in readers] == before


# --- the terminal command -------------------------------------------------------------------------------

def _board(monkeypatch, state_dir, *, interactive=True):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: state_dir)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda: None)
    monkeypatch.setattr(holdings_board, "_interactive", lambda: interactive)
    return holdings_board


def test_the_command_takes_two_runs_and_prints_no_amount(refused, monkeypatch, capsys):
    board = _board(monkeypatch, refused)
    assert board.main(["--adopt-ledger", "--by", "thomas", "--reason", "restored"]) == 0
    first = capsys.readouterr().out
    confirm = first.split("--confirm ")[1].split()[0]
    assert "nothing written" in first and len(confirm) == 8
    assert board.main(["--adopt-ledger", "--by", "thomas", "--reason", "restored", "--confirm", confirm]) == 0
    second = capsys.readouterr().out
    assert "adopted" in second and "UNCHECKED" in second
    for leak in ("SECRETCOIN", "777.77", "pay:p2"):
        assert leak not in first + second, leak


def test_the_command_is_refused_without_a_terminal(refused, monkeypatch, capsys):
    board = _board(monkeypatch, refused, interactive=False)
    before = files(refused)
    assert board.main(["--adopt-ledger", "--by", "thomas", "--reason", "r"]) == board.EXIT_BLOCKED
    assert cash_flows.ADOPT_REFUSED in capsys.readouterr().out and files(refused) == before


def test_resolving_still_refuses_while_the_ledger_is_refused(refused):
    with pytest.raises(ToolError) as caught:
        resolve(refused, "pay:p2", "deposit", now=later(T2, 2))
    assert caught.value.reason_code == cash_flows.LEDGER_TAMPERED
