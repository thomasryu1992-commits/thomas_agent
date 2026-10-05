"""The deploy preflight (`scripts/ops/deploy_preflight.py`).

Every case runs against a fake host: a table from command to output, so nothing here calls gh,
git or docker. The fake starts as a host where PR 42 merged and nothing is mid-deploy; each test
changes the one fact it is about.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.ops import deploy_preflight as pf

RUNNING = "sha256:run"
TREE = "/root/deploy-42"


def _host(**over):
    facts = {
        "pr": {"state": "MERGED", "mergedAt": "2026-09-28T00:00:00Z", "mergeCommit": {"oid": "m" * 40}},
        "on_main": True,
        "running": RUNNING,
        "images": {"latest": RUNNING, "rollback-pre-41": "sha256:old"},
        "labels": {"com.docker.compose.project": "thomas_agent",
                   "com.docker.compose.project.environment_file": "/root/thomas_agent/.env"},
        "behind": "0",
        "tree_status": "",
        "tree_head": "h" * 40,
        "main": "h" * 40,
        "state_source": pf.STATE_SOURCE,
        "running_names": ["thomas-scheduler", "hermes"],
        "extra_binds": [],
        "events": "",
        "schedules": "",
    }
    facts.update(over)
    calls = []

    def run(argv):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(facts["pr"])
        if "merge-base" in argv:
            return (0 if facts["on_main"] else 1), ""
        if argv[:2] == ["docker", "inspect"]:
            fmt = argv[-1]
            if fmt == "{{.Image}}":
                return (0, facts["running"]) if facts["running"] else (1, "")
            key = fmt.split('"')[1]
            return 0, facts["labels"].get(key, "")
        if argv[:2] == ["docker", "images"]:
            return 0, "\n".join(f"{t} {i}" for t, i in facts["images"].items())
        if "rev-list" in argv:
            return 0, facts["behind"]
        if "--show-current" in argv:
            return 0, "main"
        if "status" in argv:
            return 0, facts["tree_status"]
        if argv[-1] == "HEAD":
            return 0, facts["tree_head"]
        if argv[-1] == "origin/main":
            return (0, facts["main"]) if facts["main"] else (1, "")
        if argv[:2] == ["docker", "compose"]:
            config = {"services": {
                "scheduler": {"container_name": "thomas-scheduler", "volumes": [
                    {"type": "bind", "source": facts["state_source"], "target": "/app/.runtime_governance_state"},
                    *({"type": "bind", "source": s, "target": "/x"} for s in facts["extra_binds"])]},
                "hermes": {"container_name": "hermes", "volumes": [{"type": "volume", "source": "hermes-data"}]},
            }}
            return 0, json.dumps(config)
        if argv[:2] == ["docker", "ps"]:
            return 0, "\n".join(facts["running_names"])
        if "fetch" in argv:
            return 0, ""
        if argv[0] == "tail" and argv[-1] == pf.SCHEDULER_EVENTS:
            return (0, facts["events"]) if facts["events"] is not None else (1, "")
        if argv[0] == "cat" and argv[-1] == pf.SCHEDULES:
            return (0, facts["schedules"]) if facts["schedules"] is not None else (1, "")
        raise AssertionError(f"unexpected command {argv}")

    run.calls = calls
    return run


def _levels(results):
    return {r.name: r.level for r in results}


def test_a_merged_pr_on_a_quiet_host_passes_and_names_the_compose_command(capsys):
    assert pf.main(["42"], run=_host()) == 0
    out = capsys.readouterr().out
    assert "DEPLOY_PREFLIGHT=OK" in out
    assert "-p thomas_agent --env-file /root/thomas_agent/.env -f <tree>/docker-compose.yml up -d" in out


@pytest.mark.parametrize("over", [
    {"pr": {"state": "OPEN", "mergedAt": None, "mergeCommit": None}},
    {"on_main": False},
])
def test_an_open_pr_or_a_merge_commit_off_main_stops(over):
    assert _levels(pf.preflight(_host(**over), 42, None))["pr"] == "STOP"


def test_latest_ahead_of_the_running_image_points_at_the_tag_that_names_it():
    run = _host(images={"latest": "sha256:new", "rollback-pre-40": RUNNING, "rollback-pre-41": RUNNING})
    (result,) = [r for r in pf.preflight(run, 42, None) if r.name == "rollback"]
    assert result.level == "WARN" and "from rollback-pre-41" in result.message


def test_a_running_image_no_tag_names_stops():
    run = _host(images={"latest": "sha256:new"})
    assert _levels(pf.preflight(run, 42, None))["rollback"] == "STOP"


@pytest.mark.parametrize("tag", ["rollback-pre-42", "candidate-42"])
def test_a_tag_for_this_pr_already_present_stops(tag):
    run = _host(images={"latest": RUNNING, tag: "sha256:x"})
    assert _levels(pf.preflight(run, 42, None))["tags"] == "STOP"


def test_a_stack_started_another_way_warns():
    run = _host(labels={"com.docker.compose.project": "deploy-42"})
    assert _levels(pf.preflight(run, 42, None))["compose"] == "WARN"


def test_the_promote_window_passes_only_while_the_rollback_tag_still_names_the_running_image():
    ready = {"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:cand"}
    assert _levels(pf.preflight(_host(images=ready), 42, TREE, promote=True))["promote"] == "PASS"

    redeployed = dict(ready, **{"rollback-pre-42": "sha256:was"})
    assert _levels(pf.preflight(_host(images=redeployed), 42, TREE, promote=True))["promote"] == "STOP"

    unbuilt = {"latest": RUNNING, "rollback-pre-42": RUNNING}
    assert _levels(pf.preflight(_host(images=unbuilt), 42, TREE, promote=True))["promote"] == "STOP"


def test_promote_does_not_stop_on_the_tags_you_created():
    ready = {"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:cand"}
    levels = _levels(pf.preflight(_host(images=ready), 42, TREE, promote=True))
    assert "tags" not in levels and "STOP" not in levels.values()


def test_a_dirty_tree_stops():
    assert _levels(pf.preflight(_host(tree_status=" M x.py"), 42, TREE))["tree"] == "STOP"


def test_a_clean_tree_off_origin_main_stops_and_the_run_fails(capsys):
    # A stale, detached or branch tree can be perfectly clean and still ship something other than
    # origin/main — the procedure's one rule. Clean is not enough.
    results = pf.preflight(_host(tree_head="o" * 40), 42, TREE)
    assert _levels(results)["tree"] == "STOP"
    # The exit code is the tree's doing: the same host with the tree at origin/main exits 0.
    assert pf.main(["42", "--tree", TREE], run=_host()) == 0
    assert pf.main(["42", "--tree", TREE], run=_host(tree_head="o" * 40)) == 1


def test_a_tree_that_cannot_resolve_origin_main_stops():
    assert _levels(pf.preflight(_host(main=None), 42, TREE))["tree"] == "STOP"


def test_a_clean_tree_exactly_at_origin_main_passes():
    assert _levels(pf.preflight(_host(), 42, TREE))["tree"] == "PASS"


@pytest.mark.parametrize("source", ["./.runtime_governance_state", f"{TREE}/.runtime_governance_state"])
def test_a_state_mount_that_did_not_resolve_to_the_live_directory_stops(source):
    assert _levels(pf.preflight(_host(state_source=source), 42, TREE))["volumes"] == "STOP"


def test_any_bind_that_did_not_resolve_stops_even_when_the_state_mount_did():
    run = _host(extra_binds=["./THOMAS_CORE/approvals"])
    (result,) = [r for r in pf.preflight(run, 42, TREE) if r.name == "volumes"]
    assert result.level == "STOP" and "./THOMAS_CORE/approvals" in result.message


def test_a_running_thomas_container_missing_from_the_compose_file_warns():
    run = _host(running_names=["thomas-scheduler", "hermes", "thomas-extra"])
    assert _levels(pf.preflight(run, 42, TREE))["services"] == "WARN"


def test_the_compose_config_is_never_printed(capsys):
    # `docker compose config` interpolates .env; only the verdict lines may reach stdout.
    pf.main(["42", "--tree", TREE], run=_host())
    out = capsys.readouterr().out
    assert "container_name" not in out and "target" not in out


def test_promote_without_a_tree_is_refused():
    with pytest.raises(SystemExit):
        pf.main(["42", "--promote"], run=_host())


def test_it_runs_only_read_only_commands():
    promote = _host(images={"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:c"})
    start = _host()
    pf.preflight(promote, 42, TREE, promote=True)
    pf.preflight(start, 42, TREE)
    assert promote.calls and start.calls
    for argv in promote.calls + start.calls:
        joined = " ".join(argv)
        assert not any(word in argv for word in ("tag", "build", "up", "rm", "push", "commit")), joined


NOW = datetime(2026, 10, 5, 8, 12, 50, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(action, run_id, at, kind="crypto_factory", schedule="schedule_be5a7e5abf95e15f3982"):
    return json.dumps({"action": action, "schedule_run_id": run_id, "created_at": _iso(at),
                       "kind": kind, "schedule_id": schedule})


def _schedule(next_run, kind="crypto_factory", enabled=True, sid="schedule_x"):
    return json.dumps({"schedule_id": sid, "kind": kind, "enabled": enabled, "next_run_at": _iso(next_run)})


def _fires(promote=False, **over):
    run = _host(**over)
    tree = TREE if promote else None
    (result,) = [r for r in pf.preflight(run, 42, tree, promote=promote, now=NOW) if r.name == "fires"]
    return result


@pytest.mark.parametrize("promote", [False, True])
def test_a_fire_in_flight_stops_both_runs(promote):
    # The 2026-10-05 shape: DOGEUSDT 1h factory started 08:12:44, the deploy landed before it fired.
    events = _event("started", "srun_a", NOW - timedelta(seconds=6))
    over = {"events": events}
    if promote:
        over["images"] = {"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:c"}
    result = _fires(promote=promote, **over)
    assert result.level == "STOP" and "crypto_factory" in result.message


@pytest.mark.parametrize("terminal", ["fired", "failed", "abandoned"])
def test_a_started_fire_that_has_ended_passes(terminal):
    events = "\n".join([_event("started", "srun_a", NOW - timedelta(seconds=90)),
                        _event(terminal, "srun_a", NOW - timedelta(seconds=20))])
    assert _fires(events=events).level == "PASS"


def test_an_old_unpaired_start_is_a_dead_run_not_one_in_flight():
    # Older than the factory child's own timeout: the next restart closes it; a deploy cannot kill it.
    events = _event("started", "srun_old", NOW - timedelta(seconds=pf.IN_FLIGHT_SECONDS + 60))
    assert _fires(events=events).level == "PASS"


def test_any_kind_in_flight_stops_not_only_the_factory():
    events = _event("started", "srun_r", NOW - timedelta(seconds=10), kind="crypto_pipeline")
    assert _fires(events=events).level == "STOP"


def test_a_factory_fire_due_soon_warns_first_and_stops_the_promote():
    schedules = _schedule(NOW + timedelta(minutes=4))
    assert _fires(schedules=schedules).level == "WARN"
    ready = {"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:c"}
    assert _fires(promote=True, schedules=schedules, images=ready).level == "STOP"


def test_an_overdue_factory_fire_waiting_its_turn_counts_as_due():
    # Deferred behind the running child, `next_run_at` stays in the past until it is claimed.
    assert _fires(schedules=_schedule(NOW - timedelta(minutes=2))).level == "WARN"


@pytest.mark.parametrize("schedule", [
    {"next_run": NOW + timedelta(seconds=pf.FACTORY_DUE_MARGIN_SECONDS + 60)},
    {"next_run": NOW + timedelta(minutes=2), "enabled": False},
    {"next_run": NOW + timedelta(minutes=2), "kind": "crypto_pipeline"},
])
def test_only_an_enabled_factory_schedule_inside_the_margin_counts(schedule):
    assert _fires(schedules=_schedule(**schedule)).level == "PASS"


def test_the_latest_schedule_record_wins():
    rows = "\n".join([_schedule(NOW + timedelta(minutes=3)), _schedule(NOW + timedelta(days=1))])
    assert _fires(schedules=rows).level == "PASS"


def test_unreadable_scheduler_state_warns_rather_than_passing():
    assert _fires(events=None).level == "WARN"
    assert _fires(schedules=None).level == "WARN"


def test_a_torn_line_is_skipped_not_fatal():
    events = '{"action": "start' + "\n" + _event("started", "srun_a", NOW - timedelta(seconds=5))
    assert _fires(events=events).level == "STOP"

