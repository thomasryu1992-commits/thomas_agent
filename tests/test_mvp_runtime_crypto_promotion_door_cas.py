"""The promotion door does not write over a pool that changed while it worked (crypto refactor plan PR-S3;
``docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`` finding S-3, decision D-3).

The door reads the active pool without the lock, runs its checks, and replaces the whole file. A write
that landed in between was lost: the cycle disarming a LIVE entry whose allowance was spent, or a
lifecycle move. The read now returns the digest of the file it read, the install is handed that digest,
and under the lock every pool writer takes it refuses when the file is no longer that one.

What is pinned here:

- the store: an install with the digest of the pool on disk goes through; one built from an earlier
  read is refused with ``STRATEGY_POOL_CHANGED`` and writes nothing, whichever writer came between;
  an install that names no digest replaces the file as it always did;
- the door: both of its modes (replace, and keep-active) are refused when a disarm lands between the
  door's read and its install, nothing is ledgered, and the same command run again goes through
  against the pool as it then is;
- the callers: every call of the install in ``runtime/`` and ``scripts/`` either hands it a digest or is
  the one named wholesale replace, so a new door cannot leave the digest out unnoticed.

Nothing here waits on a thread. The write in between is made at a fixed point inside the door, so the
order is the test's and not the scheduler's.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import pool as pool_store
from runtime.mvp_runtime.crypto import pool_state
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL
from tests._helpers import stamped_pool_entry
from tests.test_mvp_runtime_crypto_evidence_depth import NOW, _TODAY_1D, _current_cost_summary, _spec_dict


def _entry(strategy_id="S1", *, tier=pool_store.LIVE_TIER_LIVE, **spec):
    built = StrategySpec.from_dict(_spec_dict(strategy_id=strategy_id, **spec)).to_dict()
    return stamped_pool_entry({"strategy_id": strategy_id, "status": "PAPER_ACTIVE", "strategy_spec": built,
                               "strategy_rule_hash": built["strategy_rule_hash"],
                               pool_store.LIVE_TIER_FIELD: tier})


def _pool(*entries):
    return {"pool_version": "active_strategy_pool.v1", "stage": "paper", "active_strategies": list(entries),
            "updated_by": "op", "updated_at": NOW}


def _tiers(root):
    return {e["strategy_id"]: pool_store.entry_live_tier(e)
            for e in pool_store.load_active_pool(root)["active_strategies"]}


# --- the store ------------------------------------------------------------------------------------------

def test_an_install_built_from_the_pool_on_disk_goes_through(tmp_path):
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)
    read, digest = pool_state.load_active_pool_with_digest(tmp_path)
    assert digest.startswith("sha256:") and read == pool_store.load_active_pool(tmp_path)

    installed = pool_store.install_active_pool(
        _pool(*read["active_strategies"], _entry("S2", direction="short")), root=tmp_path, expected_digest=digest)

    assert installed == 2
    assert pool_state.load_active_pool_with_digest(tmp_path)[1] != digest   # a new file, a new digest


def test_the_first_install_on_a_machine_with_no_pool_goes_through(tmp_path):
    read, digest = pool_state.load_active_pool_with_digest(tmp_path)
    assert (read, digest) == ({"active_strategies": []}, pool_state.POOL_ABSENT)

    assert pool_store.install_active_pool(_pool(_entry()), root=tmp_path, expected_digest=digest) == 1


def _disarm(root):
    assert pool_store.disarm_live_tier(["S1"], root=root, now=NOW, reasons=["allowance"]) == 1


def _another_install(root):
    pool_store.install_active_pool(_pool(_entry(), _entry("S9", direction="short")), root=root)


def _removal(root):
    pool_store.pool_path(root).unlink()


@pytest.mark.parametrize("write_in_between", [_disarm, _another_install, _removal])
def test_an_install_built_from_an_earlier_read_is_refused_and_writes_nothing(tmp_path, write_in_between):
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)
    read, digest = pool_state.load_active_pool_with_digest(tmp_path)

    write_in_between(tmp_path)
    path = pool_store.pool_path(tmp_path)
    left_by_the_writer = path.read_bytes() if path.is_file() else None

    with pytest.raises(ToolError) as refused:
        pool_store.install_active_pool(_pool(*read["active_strategies"]), root=tmp_path, expected_digest=digest)

    assert refused.value.reason_code == pool_state.STRATEGY_POOL_CHANGED
    assert (path.read_bytes() if path.is_file() else None) == left_by_the_writer
    assert not path.with_suffix(".tmp").exists()


def test_a_pool_that_appeared_after_an_empty_read_is_not_written_over(tmp_path):
    _read, digest = pool_state.load_active_pool_with_digest(tmp_path)
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)

    with pytest.raises(ToolError) as refused:
        pool_store.install_active_pool(_pool(_entry("S2", direction="short")), root=tmp_path, expected_digest=digest)

    assert refused.value.reason_code == pool_state.STRATEGY_POOL_CHANGED
    assert list(_tiers(tmp_path)) == ["S1"]


def test_the_digest_names_the_content_and_not_the_write(tmp_path):
    """The same pool written again is the same pool: a writer that changed nothing does not refuse the door."""
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)
    read, digest = pool_state.load_active_pool_with_digest(tmp_path)
    path = pool_store.pool_path(tmp_path)
    path.write_bytes(path.read_bytes())

    assert pool_store.install_active_pool(_pool(*read["active_strategies"]), root=tmp_path, expected_digest=digest) == 1


def test_a_pool_that_cannot_be_read_at_the_install_is_refused_as_unreadable(tmp_path):
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)
    read, digest = pool_state.load_active_pool_with_digest(tmp_path)
    pool_store.pool_path(tmp_path).write_bytes(b"\xff\xfe not text")

    with pytest.raises(ToolError) as refused:
        pool_store.install_active_pool(_pool(*read["active_strategies"]), root=tmp_path, expected_digest=digest)

    assert refused.value.reason_code == "STRATEGY_POOL_UNREADABLE"
    assert pool_store.pool_path(tmp_path).read_bytes() == b"\xff\xfe not text"


def test_an_install_that_names_no_digest_replaces_the_pool_as_before(tmp_path):
    """The history import's mode: it installs the pool another file carries and builds nothing from the one
    on disk, so it has no read to name."""
    pool_store.install_active_pool(_pool(_entry()), root=tmp_path)
    _disarm(tmp_path)

    assert pool_store.install_active_pool(_pool(_entry("S2", direction="short")), root=tmp_path) == 1
    assert list(_tiers(tmp_path)) == ["S2"]


# --- the door -------------------------------------------------------------------------------------------

def _seed(root, *specs):
    rows = []
    for spec_dict in specs:
        spec = StrategySpec.from_dict(spec_dict)
        rows.append({
            "strategy_id": spec.strategy_id, "strategy_rule_hash": spec.strategy_rule_hash,
            "generation_id": "GEN-001", "status": "BACKTESTED", "champion_score": 0.5,
            "strategy_spec": spec.to_dict(),
            "backtest_evidence": {"closed_count": 20, "expectancy": 0.5, "bars_replayed": _TODAY_1D,
                                  "robustness": {"holdout_status": "CONFIRMED"},
                                  "cost_summary": _current_cost_summary()},
            "evidence_input_sha256": "sha256:test", "provenance": "mvp_factory",
        })
    pool_store.append_candidates(rows, root=root)


@pytest.fixture
def door(tmp_path, monkeypatch):
    """The promotion door with its approval and its quality gates passed: they have their own tests, and
    this file is about what the door writes after them. One candidate is armed LIVE already."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.promotion_mod, "verify_promotion_approval", lambda *a, **k: {
        "approval_id": "appr_live",
        "approved_action_snapshot": {"content_sha256": prom.promotion_mod.content_sha256_of(
            pool_store.resolve_candidates(k["selectors"], k["root"]), keep_active=k["keep_active"],
            live_tier=k["live_tier"], root=k["root"])},
    })
    monkeypatch.setattr(prom.promotion_mod, "run_promotion_gates", lambda *a, **k: None)
    _seed(tmp_path, _spec_dict(), _spec_dict(strategy_id="S2", direction="short"))

    def promote(selector, *, keep_active, live_tier, **kw):
        return prom.run_promotion(selectors=[selector], promoted_by="Thomas", reason="r", keep_active=keep_active,
                                  live_tier=live_tier, root=tmp_path, now=NOW, **kw)

    promote("S1", keep_active=False, live_tier="LIVE", approval_id="appr_live")
    assert _tiers(tmp_path) == {"S1": "LIVE"}
    return prom, promote


def _disarm_inside_the_door(prom, monkeypatch, root):
    """The cycle's disarm, made at a fixed point between the door's read and its install."""
    real = prom.pool_store.silent_reactivations

    def disarm_then(entries, **kw):
        _disarm(root)
        return real(entries, **kw)

    monkeypatch.setattr(prom.pool_store, "silent_reactivations", disarm_then)
    return lambda: monkeypatch.setattr(prom.pool_store, "silent_reactivations", real)


def _promotions_ledgered(root):
    from scripts.promote_strategy_candidates import PROMOTION_EVENT_TYPE

    path = root / LEDGER_REL / CONTROL_FILE
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip() and json.loads(line).get("record_type") == PROMOTION_EVENT_TYPE)


MODES = [
    # a restate of the armed candidate, which would write LIVE back over the disarm
    ("S1", {"keep_active": False, "live_tier": "LIVE", "approval_id": "appr_live"}),
    # a second candidate added beside it, which would carry the armed entry along as it was read
    ("S2", {"keep_active": True, "live_tier": "OBSERVATION", "without_approval": True}),
]


@pytest.mark.parametrize("selector,mode", MODES)
def test_the_door_is_refused_when_a_disarm_lands_between_its_read_and_its_install(
        tmp_path, monkeypatch, door, selector, mode):
    prom, promote = door
    ledgered = _promotions_ledgered(tmp_path)
    assert ledgered == 1, "the helper above does not see the door's ledger event: it would prove nothing below"
    _disarm_inside_the_door(prom, monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as refused:
        promote(selector, **mode)

    assert str(refused.value).startswith(f"BLOCKED {pool_state.STRATEGY_POOL_CHANGED}: ")
    assert _tiers(tmp_path) == {"S1": "OBSERVATION"}          # the disarm stands, and nothing else was written
    assert _promotions_ledgered(tmp_path) == ledgered         # and nothing was ledgered as installed


@pytest.mark.parametrize("selector,mode", MODES)
def test_the_same_command_run_again_goes_through_against_the_pool_as_it_is_now(
        tmp_path, monkeypatch, door, selector, mode):
    prom, promote = door
    undo = _disarm_inside_the_door(prom, monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        promote(selector, **mode)
    undo()

    promote(selector, **mode)

    # The restate arms S1 again, on the operator's approval and on the pool as it now is. The second
    # candidate joins a pool whose first entry stays disarmed.
    assert _tiers(tmp_path) == ({"S1": "LIVE"} if selector == "S1" else {"S1": "OBSERVATION", "S2": "OBSERVATION"})


@pytest.mark.parametrize("selector,mode", MODES)
def test_the_door_goes_through_when_nothing_writes_in_between(tmp_path, door, selector, mode):
    _prom, promote = door

    promote(selector, **mode)

    assert _tiers(tmp_path) == ({"S1": "LIVE"} if selector == "S1" else {"S1": "LIVE", "S2": "OBSERVATION"})


# --- the callers ----------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]

# Every call of the install outside the store itself, and whether it names the read it was built from.
INSTALL_CALLERS = {
    "scripts/promote_strategy_candidates.py": True,    # builds the new pool from the one on disk
    "scripts/import_crypto_history.py": False,         # installs the pool another file carries, whole
}


def _install_calls(repo: Path) -> dict[str, list[bool]]:
    found: dict[str, list[bool]] = {}
    for top in ("runtime", "scripts"):
        for path in sorted((repo / top).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and "install_active_pool" in (
                        getattr(node.func, "attr", None), getattr(node.func, "id", None)):
                    found.setdefault(path.relative_to(repo).as_posix(), []).append(
                        any(k.arg == "expected_digest" for k in node.keywords))
    return found


def test_every_door_that_installs_names_its_read_or_is_the_named_replace():
    calls = _install_calls(REPO)
    assert calls, "no call of the install found: the scan broke"
    assert calls == {path: [names_its_read] for path, names_its_read in INSTALL_CALLERS.items()}, (
        "a call of install_active_pool was added, removed or changed. A door that builds its pool from the "
        "one on disk must read it with load_active_pool_with_digest and hand the digest to the install")


def test_the_caller_scan_sees_a_call_with_and_without_the_digest(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "runtime").mkdir()
    (scripts / "a.py").write_text("pool.install_active_pool(p, root=r, expected_digest=d)\n")
    (scripts / "b.py").write_text("from x import install_active_pool\ninstall_active_pool(p)\n")
    assert _install_calls(tmp_path) == {"scripts/a.py": [True], "scripts/b.py": [False]}

