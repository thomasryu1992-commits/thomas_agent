"""The one state writer that had no door to refuse at.

Every other state-writing script under `scripts/` calls
`state_guard.assert_not_foreign_root_run`, which refuses a host-side root run against a state
directory owned by the uid the deployed services use. `ci_activate_core_for_tests.py` did not,
and it writes `.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`. On the deployment host
that directory belongs to uid 10001, so a root run left a root-owned pointer — and
`state_guard.assert_state_writable` then refuses to START either service. One run bricks both
containers at their next restart, with nothing pointing back at the command.

It could not simply call the guard: that guard's remedy is "re-run it under `docker exec`",
and here that is impossible twice over — `THOMAS_CORE/{approvals,activations}` are mounted
read-only into the containers, and this script makes a git commit against a checkout they do
not have. A correct refusal carrying an impossible instruction is worse than the silence it
replaces, because it stops a legitimate setup with nowhere to go.

So: refuse by default with the remedy that actually works here, an explicit escape for the
deliberate case, and — because `state_guard` is emphatic that these paths never self-heal — an
escaped run that leaves a root-owned pointer reports INCOMPLETE rather than success.
"""

from __future__ import annotations

import pytest

from scripts import ci_activate_core_for_tests as activate


@pytest.fixture(autouse=True)
def _never_touch_the_real_tree(monkeypatch):
    """Nothing in this file may reach `_run`, which git-commits and activates a real Core."""
    def _explode(*args, **kwargs):
        raise AssertionError("a refused run must not reach the activation subprocesses")

    monkeypatch.setattr(activate, "_run", _explode)


# --- the refusal ------------------------------------------------------------------

def test_a_root_run_against_foreign_owned_state_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(activate, "foreign_state_owner", lambda root: 10001)

    assert activate.main([]) == activate.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "STATE_FOREIGN_ROOT_RUN" in err
    assert "10001" in err


def test_the_refusal_lands_before_anything_reaches_disk(monkeypatch, tmp_path, capsys):
    """A refused run must leave the tree exactly as it found it — no evidence file, no commit.

    The autouse fixture already fails the test if `_run` is reached; this pins the other half,
    that the evidence file is not written either. The check therefore has to sit above
    `GOV_STATE.mkdir` and `evidence.write_text`, not merely above the subprocesses.
    """
    monkeypatch.setattr(activate, "foreign_state_owner", lambda root: 10001)
    monkeypatch.setattr(activate, "GOV_STATE", tmp_path / "state")
    monkeypatch.setattr(activate, "POINTER", tmp_path / "state" / "CURRENT_CORE_RELEASE.yaml")
    monkeypatch.setattr(activate, "ROOT", tmp_path)

    assert activate.main([]) == activate.EXIT_BLOCKED
    assert not (tmp_path / "state").exists(), "a refused run created the state directory"
    assert list(tmp_path.iterdir()) == [], "a refused run wrote to the tree"


def test_the_refusal_does_not_send_the_operator_to_docker_exec(monkeypatch, capsys):
    """The whole reason this refusal is local instead of `assert_not_foreign_root_run`.

    The shared guard's message says to re-run under `docker exec thomas-scheduler`, which is
    right for the other seven scripts and a dead end for this one. If someone later swaps this
    for the shared call, the instruction becomes impossible and this test says so.
    """
    monkeypatch.setattr(activate, "foreign_state_owner", lambda root: 10001)
    activate.main([])

    err = capsys.readouterr().err
    assert "read-only" in err, "the message must say WHY the container cannot run it"
    assert "git commit" in err, "…and the second, independent reason"
    assert "chown -R 10001:10001" in err, "a refusal without a remedy relocates the problem"
    assert "--allow-foreign-root-run" in err, "a refusal must name its escape"


# --- the cases that must NOT be refused -------------------------------------------

def test_a_normal_non_root_run_is_untouched(monkeypatch):
    """CI's case: both workflows run this on the runner, not as root, before pytest.

    `foreign_state_owner` already answers None when the process is not root, when the state
    directory does not exist yet, or on a platform without uids — so the guard is inert on
    every machine that was working before.
    """
    monkeypatch.setattr(activate, "foreign_state_owner", lambda root: None)
    monkeypatch.setattr(activate, "POINTER", _AlreadyActive())

    assert activate.main([]) == activate.EXIT_OK


def test_the_escape_lets_a_deliberate_root_run_through(monkeypatch):
    monkeypatch.setattr(activate, "foreign_state_owner", lambda root: 10001)
    monkeypatch.setattr(activate, "POINTER", _AlreadyActive())

    assert activate.main(["--allow-foreign-root-run"]) == activate.EXIT_OK


class _AlreadyActive:
    """A pointer that exists, so `main` short-circuits before the activation subprocesses."""

    def is_file(self) -> bool:
        return True


# --- finishing the handover -------------------------------------------------------

def test_an_escaped_run_that_leaves_a_root_owned_pointer_reports_incomplete(monkeypatch, capsys):
    """`state_guard` forbids self-healing outright — "a script that quietly chowned governed
    state would be granting itself the very thing this repo makes explicit everywhere else".

    So the script does not fix it; it refuses to call itself done. The activation genuinely
    succeeded and the exit code says the work is not finished, which is much harder to walk
    past than a note printed under a success line.
    """
    monkeypatch.setattr(activate, "_pointer_is_foreign", lambda owner_uid: True)

    text = activate._handover_reminder(10001)
    assert "INCOMPLETE" in text
    assert "chown -R 10001:10001" in text


def test_the_ownership_check_is_read_off_the_file_that_landed(monkeypatch, tmp_path):
    """Re-read rather than inferred from the check at the top: what matters is who owns the
    pointer that now exists."""
    written = tmp_path / "CURRENT_CORE_RELEASE.yaml"
    written.write_text("x", encoding="utf-8")
    monkeypatch.setattr(activate, "POINTER", written)

    assert activate._pointer_is_foreign(written.stat().st_uid) is False
    assert activate._pointer_is_foreign(written.stat().st_uid + 1) is True


def test_a_missing_pointer_asserts_nothing(monkeypatch, tmp_path):
    """No uids on this platform, or the file vanished: there is nothing to claim."""
    monkeypatch.setattr(activate, "POINTER", tmp_path / "gone.yaml")
    assert activate._pointer_is_foreign(10001) is False
