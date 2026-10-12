"""The deploy preflight (`scripts/ops/deploy_preflight.py`).

Every case runs against a fake host: a table from command to output, so nothing here calls gh,
git or docker. The fake starts as a host where PR 42 merged and nothing is mid-deploy; each test
changes the one fact it is about.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from scripts.ops import deploy_preflight as pf

RUNNING = "sha256:run"
TREE = "/root/deploy-42"
# What `docker compose config` interpolates from .env: a synthetic value that must never be printed.
SECRET = "synthetic-secret-value-7f3a"


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
        "compose_raw": None,   # None: a well-formed config; a string: compose printed exactly that
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
            return (0, facts["labels"].get(key, "")) if facts["labels"] is not None else (1, "")
        if argv[:2] == ["docker", "images"]:
            if facts["images"] is None:
                return 1, ""
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
            if facts["compose_raw"] is not None:
                return 0, facts["compose_raw"]
            config = {"services": {
                "scheduler": {"container_name": "thomas-scheduler", "environment": {"TELEGRAM_BOT_TOKEN": SECRET},
                              "volumes": [
                    {"type": "bind", "source": facts["state_source"], "target": "/app/.runtime_governance_state"},
                    *({"type": "bind", "source": s, "target": "/x"} for s in facts["extra_binds"])]},
                "hermes": {"container_name": "hermes", "volumes": [{"type": "volume", "source": "hermes-data"}]},
            }}
            return 0, json.dumps(config)
        if argv[:2] == ["docker", "ps"]:
            if facts["running_names"] is None:
                return 1, ""
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



# --- a fact that cannot be read is never a PASS (2026-10-12) -------------------------------------------------

def _promote_host(**over):
    return _host(images={"latest": RUNNING, "rollback-pre-42": RUNNING, "candidate-42": "sha256:c"}, **over)


def test_an_unreadable_image_list_never_reports_the_tags_free():
    levels = _levels(pf.preflight(_host(images=None), 42, None))
    assert levels["tags"] == "STOP" and levels["rollback"] == "STOP"


def test_an_unreadable_image_list_says_unverified_not_untagged():
    (result,) = [r for r in pf.preflight(_host(images=None), 42, None) if r.name == "rollback"]
    assert "unverified" in result.message and "no name to roll back to" not in result.message


def test_an_unreadable_image_list_stops_the_promote():
    assert _levels(pf.preflight(_host(images=None), 42, TREE, promote=True))["promote"] == "STOP"


def test_unreadable_scheduler_state_stops_the_promote():
    assert _fires(promote=True, events=None).level == "STOP"
    assert _fires(promote=True, schedules=None).level == "STOP"


def test_an_unpaired_start_of_unreadable_time_stops_the_promote_and_not_the_first_run():
    events = json.dumps({"action": "started", "schedule_run_id": "srun_x", "created_at": "garbled",
                         "kind": "crypto_factory", "schedule_id": "schedule_x"})
    assert _fires(promote=True, events=events).level == "STOP"
    assert _fires(events=events).level == "PASS"


def test_unreadable_compose_labels_warn_first_and_stop_the_promote():
    assert _levels(pf.preflight(_host(labels=None), 42, None))["compose"] == "WARN"
    assert _levels(pf.preflight(_promote_host(labels=None), 42, TREE, promote=True))["compose"] == "STOP"


_LABELS = {"com.docker.compose.project": "thomas_agent",
           "com.docker.compose.project.environment_file": "/root/thomas_agent/.env"}


@pytest.mark.parametrize("labels", [
    {**_LABELS, "com.docker.compose.project": "deploy-42"},
    {**_LABELS, "com.docker.compose.project.environment_file": "/root/deploy-42/.env"},
    {**_LABELS, "com.docker.compose.project.environment_file": ""},
])
def test_a_label_mismatch_warns_first_and_stops_the_promote(capsys, labels):
    """A known mismatch is a STOP at the promote (Thomas 2026-10-12); the first run keeps its WARN."""
    assert _levels(pf.preflight(_host(labels=labels), 42, None))["compose"] == "WARN"
    assert _levels(pf.preflight(_promote_host(labels=labels), 42, TREE, promote=True))["compose"] == "STOP"
    assert pf.main(["42", "--promote", "--tree", TREE], run=_promote_host(labels=labels)) == 1
    captured = capsys.readouterr()
    assert "DEPLOY_PREFLIGHT=STOP" in captured.out and SECRET not in captured.out + captured.err


def test_matching_labels_pass_both_runs():
    assert _levels(pf.preflight(_host(), 42, None))["compose"] == "PASS"
    assert _levels(pf.preflight(_promote_host(), 42, TREE, promote=True))["compose"] == "PASS"


@pytest.mark.parametrize("raw", ["not json", "[]", '{"services": []}',
                                 '{"services": {"s": {"volumes": ["./x:/y"]}}}',
                                 '{"services": {"s": "x"}}'])
def test_a_compose_config_that_does_not_parse_stops_without_echoing_it(capsys, raw):
    noisy = raw.replace("x", SECRET)
    assert pf.main(["42", "--tree", TREE], run=_host(compose_raw=noisy)) == 1
    captured = capsys.readouterr()
    assert "[STOP] volumes:" in captured.out and "DEPLOY_PREFLIGHT=STOP" in captured.out
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("raw", ["not json", "[]", '"MERGED"'])
def test_a_pr_answer_that_does_not_parse_stops(raw):
    def run(argv):
        return (0, raw) if list(argv)[:3] == ["gh", "pr", "view"] else _host()(argv)
    assert _levels(pf.preflight(run, 42, None))["pr"] == "STOP"


@pytest.mark.parametrize("over", [{}, {"extra_binds": ["relative/x"]}, {"state_source": "/elsewhere"}])
def test_the_interpolated_env_is_never_printed_on_any_path(capsys, over):
    """PASS, a bad bind and a missing state mount: the env value that compose resolved never reaches
    stdout or stderr."""
    pf.main(["42", "--tree", TREE], run=_host(**over))
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err and "TELEGRAM_BOT_TOKEN" not in captured.out


@pytest.mark.parametrize("over", [{"images": None}, {"events": None}, {"labels": None},
                                  {"compose_raw": "not json"}])
def test_every_unknown_at_promote_fails_the_run(capsys, over):
    run = _host(**over) if "images" in over else _promote_host(**over)
    assert pf.main(["42", "--promote", "--tree", TREE], run=run) == 1
    assert "DEPLOY_PREFLIGHT=STOP" in capsys.readouterr().out


def test_the_required_checks_all_still_run():
    """Every check the procedure relies on is still in both runs: none was dropped for the unknowns."""
    first = {r.name for r in pf.preflight(_host(), 42, TREE)}
    promote = {r.name for r in pf.preflight(_promote_host(), 42, TREE, promote=True)}
    assert {"pr", "tags", "rollback", "compose", "primary", "fires", "tree", "volumes"} <= first
    assert {"pr", "promote", "compose", "primary", "fires", "tree", "volumes"} <= promote


# --- the services compose up would touch (Thomas 2026-10-12) -------------------------------------------------

EIGHT = ("thomas-operator", "thomas-scheduler", "thomas-scheduler-maint", "thomas-read-bridge",
         "thomas-dispatch-bridge", "thomas-pipeline-worker", "thomas-knowledge-bridge", "thomas-switch-bridge")


def _stack(**over):
    """A host whose tree compose names the eight runtime services and hermes, as the real file does."""
    services = {name: {"container_name": name, "environment": {"TELEGRAM_BOT_TOKEN": SECRET}, "volumes": [
        {"type": "bind", "source": pf.STATE_SOURCE, "target": "/app/.runtime_governance_state"}]} for name in EIGHT}
    services["hermes"] = {"container_name": "hermes", "volumes": []}
    over.setdefault("running_names", [*EIGHT, "hermes"])
    return _promote_host(compose_raw=json.dumps({"services": services}), **over)


def _services(run, promote):
    return [r for r in pf.preflight(run, 42, TREE, promote=promote, now=NOW) if r.name == "services"]


@pytest.mark.parametrize("promote", [False, True])
def test_the_whole_stack_running_passes_both_runs(promote):
    assert [r.level for r in _services(_stack(), promote)] == ["PASS"]


@pytest.mark.parametrize("promote", [False, True])
def test_unrelated_containers_of_other_projects_are_not_compared(promote):
    assert [r.level for r in _services(_stack(running_names=[*EIGHT, "hermes", "postgres", "blog-cron"]),
                                       promote)] == ["PASS"]


@pytest.mark.parametrize("running", [
    None,                                   # docker ps failed
    [],                                     # docker ps answered: nothing runs
    [*EIGHT[1:], "hermes"],                 # one service down
    [*EIGHT[:3], "hermes"],                 # several down
    [*EIGHT, "hermes", "thomas-legacy"],    # a thomas- container this file no longer names
], ids=["ps-failed", "empty", "one-missing", "several-missing", "unexpected-thomas"])
def test_any_service_difference_warns_first_and_stops_the_promote(capsys, running):
    assert {r.level for r in _services(_stack(running_names=running), False)} == {"WARN"}
    assert {r.level for r in _services(_stack(running_names=running), True)} == {"STOP"}
    assert pf.main(["42", "--promote", "--tree", TREE], run=_stack(running_names=running)) == 1
    captured = capsys.readouterr()
    assert "DEPLOY_PREFLIGHT=STOP" in captured.out and SECRET not in captured.out + captured.err


def test_a_failed_container_list_is_not_read_as_an_empty_one():
    (failed,) = _services(_stack(running_names=None), True)
    (empty,) = [r for r in _services(_stack(running_names=[]), True) if "not running" in r.message]
    assert "cannot list" in failed.message and "cannot list" not in empty.message


# --- the compose bind contract, statically (no .env value is read) -------------------------------------------

def _raw_compose() -> dict:
    import yaml  # the raw file: ${...} left as written, nothing interpolated
    return yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml")
                          .read_text(encoding="utf-8"))


def _bind_sources() -> dict[str, list[str]]:
    """Each service's bind sources as written: everything before the ``:/target`` part. A source that
    is not ``${VAR:-default}[/sub]`` is kept whole, so the test below names it."""
    out = {}
    for name, spec in _raw_compose()["services"].items():
        sources = []
        for volume in spec.get("volumes") or []:
            match = re.match(r"(\$\{[A-Z_]+:-[^}]+\}[^:]*):/", str(volume))
            sources.append(match.group(1) if match else str(volume))
        out[name] = sources
    return out


# The variables the bind sources may be built from: paths only, never a credential. What each resolves
# to lives in .env, which only the volume check at deploy time sees.
PATH_VARIABLES = {"THOMAS_STATE_DIR", "THOMAS_CORE_ACTIVATIONS_DIR", "THOMAS_CORE_APPROVALS_DIR",
                  "THOMAS_WORKSPACE_DIR", "THOMAS_BLOG_VAULT_DIR", "HERMES_DATA_DIR"}


def test_every_bind_source_is_a_path_variable_with_a_default():
    """Each bind source is ``${PATH_VAR:-default}[/sub]``: no literal host path, no other variable."""
    sources_by_service = _bind_sources()
    assert sum(map(len, sources_by_service.values())) >= 25   # the file has 26; an empty parse is not a pass
    for service, sources in sources_by_service.items():
        for source in sources:
            match = re.fullmatch(r"\$\{([A-Z_]+):-[^}]+\}(/[\w./-]+)?", source)
            assert match, (service, source)
            assert match.group(1) in PATH_VARIABLES, (service, source)


def test_every_runtime_service_mounts_the_state_root_from_the_one_variable():
    compose = _raw_compose()
    runtime = {n: s for n, s in compose["services"].items() if s.get("image") == "thomas-agent-runtime"}
    assert {s.get("container_name") for s in runtime.values()} == set(EIGHT)
    for name, spec in runtime.items():
        assert "${THOMAS_STATE_DIR:-./.runtime_governance_state}:/app/.runtime_governance_state" in spec["volumes"], name


def test_an_unset_state_variable_is_caught_by_the_volume_check():
    """The static contract cannot say what .env sets; it says what happens when it sets nothing: the
    default is relative, compose resolves it inside the tree, and the deploy-time check stops."""
    assert "${THOMAS_STATE_DIR:-./.runtime_governance_state}".split(":-")[1].startswith("./")
    unresolved = f"{TREE}/.runtime_governance_state"
    assert _levels(pf.preflight(_host(state_source=unresolved), 42, TREE))["volumes"] == "STOP"


# --- an enabled factory's next run must be a readable, zoned instant (Thomas 2026-10-12) ---------------------

FAR = NOW + timedelta(hours=3)


def _row(sid="schedule_bad00001", kind="crypto_factory", enabled=True, **next_run):
    row = {"schedule_id": sid, "kind": kind, "enabled": enabled}
    row.update(next_run)                      # no key at all when the caller passes nothing
    return json.dumps(row)


BAD_NEXT_RUN = {
    "missing": {},
    "null": {"next_run_at": None},
    "empty": {"next_run_at": ""},
    "impossible-date": {"next_run_at": "2026-13-40T08:00:00Z"},
    "garbage": {"next_run_at": "garbage-ts-91c4"},
    "no-zone": {"next_run_at": "2026-10-05T08:15:00"},
    "a-number": {"next_run_at": 1791187200},
}


@pytest.mark.parametrize("next_run", BAD_NEXT_RUN.values(), ids=BAD_NEXT_RUN.keys())
def test_an_enabled_factory_with_an_unreadable_next_run_warns_first_and_stops_the_promote(next_run):
    schedules = _row(**next_run)
    first, promote = _fires(schedules=schedules), _fires(promote=True, schedules=schedules)
    assert (first.level, promote.level) == ("WARN", "STOP")
    assert "bad00001" in promote.message and "unverified" in promote.message


@pytest.mark.parametrize("value", [_iso(FAR), FAR.astimezone(timezone(timedelta(hours=7))).isoformat(),
                                   FAR.isoformat()], ids=["Z", "plus-07", "plus-00"])
def test_a_zoned_next_run_far_off_passes_both_runs(value):
    schedules = _row(next_run_at=value)
    assert (_fires(schedules=schedules).level, _fires(promote=True, schedules=schedules).level) == ("PASS", "PASS")


def test_an_offset_next_run_inside_the_margin_is_due():
    soon = (NOW + timedelta(minutes=5)).astimezone(timezone(timedelta(hours=-5))).isoformat()
    schedules = _row(next_run_at=soon)
    assert (_fires(schedules=schedules).level, _fires(promote=True, schedules=schedules).level) == ("WARN", "STOP")


@pytest.mark.parametrize("schedules", [
    _row(enabled=False, next_run_at="garbage"),
    _row(kind="health_watch", next_run_at="garbage"),
    _row(kind="holdings_refresh"),
], ids=["disabled-factory", "other-kind", "other-kind-missing"])
def test_only_an_enabled_factory_is_held_to_it(schedules):
    assert _fires(promote=True, schedules=schedules).level == "PASS"


def test_the_latest_record_of_a_schedule_decides():
    fixed = "\n".join([_row(next_run_at="garbage"), _row(next_run_at=_iso(FAR))])
    broken = "\n".join([_row(next_run_at=_iso(FAR)), _row(next_run_at="garbage")])
    assert _fires(promote=True, schedules=fixed).level == "PASS"
    assert _fires(promote=True, schedules=broken).level == "STOP"


def test_one_unreadable_factory_among_good_ones_stops_the_promote():
    schedules = "\n".join([_row(sid="schedule_good0001", next_run_at=_iso(FAR)),
                           _row(sid="schedule_good0002", next_run_at=_iso(FAR + timedelta(hours=1))),
                           _row(sid="schedule_bad00001", next_run_at="garbage")])
    result = _fires(promote=True, schedules=schedules)
    assert result.level == "STOP" and "bad00001" in result.message and "good" not in result.message


def test_a_due_fire_and_an_unreadable_one_are_both_named():
    schedules = "\n".join([_schedule(NOW + timedelta(minutes=4)), _row(next_run_at="garbage")])
    result = _fires(promote=True, schedules=schedules)
    assert result.level == "STOP" and "1 more with an unreadable next_run_at" in result.message


def test_an_unreadable_next_run_fails_the_promote_and_prints_no_value(capsys):
    run = _promote_host(schedules=_row(next_run_at="garbage-ts-91c4"))
    assert pf.main(["42", "--promote", "--tree", TREE], run=run) == 1
    captured = capsys.readouterr()
    assert "DEPLOY_PREFLIGHT=STOP" in captured.out
    assert "garbage-ts-91c4" not in captured.out + captured.err and SECRET not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_a_zoneless_start_time_is_unreadable_not_a_crash():
    """``(now - at)`` with a naive ``at`` raised TypeError; it is now the unreadable-start case."""
    events = json.dumps({"action": "started", "schedule_run_id": "srun_n", "created_at": "2026-10-05T08:12:40",
                         "kind": "crypto_factory", "schedule_id": "schedule_x"})
    assert _fires(events=events).level == "PASS"
    assert _fires(promote=True, events=events).level == "STOP"
