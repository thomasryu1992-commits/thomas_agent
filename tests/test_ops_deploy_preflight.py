"""The deploy preflight (`scripts/ops/deploy_preflight.py`).

Every case runs against a fake host: a table from command to output, so nothing here calls gh,
git or docker. The fake starts as a host where PR 42 merged and nothing is mid-deploy; each test
changes the one fact it is about.
"""
from __future__ import annotations

import json

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
