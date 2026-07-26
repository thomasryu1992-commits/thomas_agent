"""The suite's own guard against running against live state — and against blaming a test for it.

``conftest`` compares the repo's state directory before and after every test and fails the
test that changed it. That inference is only sound while the suite is the sole writer: on
the deployed tree the operator and scheduler loops stamp a heartbeat every pass, and the
guard named whichever test happened to be running when one landed. Observed 2026-07-26 —
``test_why_naming_them_matters``, a pure in-memory strategy test that touches no state at
all, was reported as having written ``heartbeats/operator.json``.

So the classifier below decides, before any test runs, whether a live runtime owns this
tree. These pin what it may claim: FRESH means *currently writing*, and nothing else does.
A stale or broken heartbeat is a stopped service, which must not block the suite forever.
"""

from __future__ import annotations

from runtime.mvp_runtime import heartbeat, timeutil

from tests import conftest


def _stamp(root, service: str, *, age_seconds: int, interval_seconds: float = 30.0) -> None:
    heartbeat.write_heartbeat(
        service,
        interval_seconds=interval_seconds,
        now=timeutil.plus_seconds(timeutil.utc_now_iso(), -age_seconds),
        root=root,
    )


def test_a_tree_with_no_state_directory_has_no_live_writer(tmp_path):
    """The CI case: a fresh checkout has no state at all, so the suite must simply run."""
    assert conftest.live_state_writers(tmp_path) == []


def test_a_fresh_heartbeat_is_a_live_writer(tmp_path):
    _stamp(tmp_path, "operator", age_seconds=5)
    live = conftest.live_state_writers(tmp_path)
    assert [row["service"] for row in live] == ["operator"]
    assert live[0]["status"] == heartbeat.FRESH
    # The detail is what the refusal prints, so it must carry the age rather than a bare name.
    assert "ago" in str(live[0]["detail"])


def test_a_stale_heartbeat_is_not_a_live_writer(tmp_path):
    """A stopped service left its last heartbeat behind. Treating that as a live writer
    would make the suite unrunnable in this tree until someone deleted a file by hand."""
    _stamp(tmp_path, "scheduler", age_seconds=3600)
    assert conftest.live_state_writers(tmp_path) == []


def test_an_unreadable_heartbeat_is_not_a_live_writer(tmp_path):
    """A corrupt record proves nothing about a loop turning. It is a finding for the
    ``audit``/``recovery`` console, not a reason to refuse to run tests."""
    path = heartbeat.heartbeat_path("operator", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert conftest.live_state_writers(tmp_path) == []


def test_every_live_service_is_named_not_just_the_first(tmp_path):
    """The refusal tells the operator which loops to stop; naming one of two would send
    them back for a second round."""
    _stamp(tmp_path, "operator", age_seconds=5)
    _stamp(tmp_path, "scheduler", age_seconds=5)
    _stamp(tmp_path, "frontdesk", age_seconds=7200)
    assert [row["service"] for row in conftest.live_state_writers(tmp_path)] == [
        "operator",
        "scheduler",
    ]
