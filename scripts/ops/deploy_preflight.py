"""Read-only deploy preflight: the host facts the Deploying procedure in CLAUDE.md depends on.

Run it twice: before tagging anything, and again immediately before the promote::

    python3 scripts/ops/deploy_preflight.py <PR#>                               # before any tag
    python3 scripts/ops/deploy_preflight.py <PR#> --promote --tree <worktree>   # before the promote

Each check prints ``[PASS]``, ``[WARN]`` or ``[STOP]``; any STOP exits 1. It changes nothing:
the only commands it runs are ``gh pr view``, ``git fetch``/``rev-parse``/``merge-base``/``status``,
``docker inspect``/``image inspect``/``images``, ``docker compose … config``, and ``tail``/``cat`` of
two scheduler state files. Each check is a
mistake a session on this host has made or nearly made:

- **The PR is merged and its merge commit is on origin/main.** A PR can sit OPEN with every check
  green, and a note saying "fixed in #N" does not say it merged (#890, open four days).
- **The rollback point names the running image.** When a concurrent session built without
  deploying, ``latest`` is ahead of what runs. Tagging it then names the new image as the safety
  net. The check finds the tag that does name the running image.
- **Nothing was redeployed between your tag and your promote** (``--promote``). A concurrent
  session redeploys everything, and that window is where it lands. The running image must still be
  the one ``rollback-pre-<N>`` names, and ``candidate-<N>`` must exist.
- **Nobody else is on this PR.** ``rollback-pre-<N>`` or ``candidate-<N>`` that you did not create
  means another session is deploying it (2026-08-30: two sessions built candidate-811 at once).
- **compose runs with the deployed project and env file, from a clean tree.** Without ``-p
  thomas_agent`` the directory name becomes the project and nothing is recreated. Without
  ``--env-file`` the state volume resolves relative to the compose file, and the live scheduler
  sees an empty state directory. The primary checkout is often on another session's branch, so its
  compose file can roll back what is running (2026-08-21, 41 commits behind). The check reads the
  project and env file off the running containers' own compose labels, and with ``--tree`` it
  resolves that tree's compose file and checks every bind source.
- **No scheduled fire is in flight, and no factory fire is about to start.** ``compose up`` recreates
  the scheduler lanes, and a lane restart kills whatever fire it was running: the next process
  writes ``abandoned_mid_run`` and the occurrence is not retried until its next interval
  (2026-10-05: a deploy at 08:12 UTC cut the DOGEUSDT 1h factory fire 15 s in, and that day's
  generation for it was lost). The check pairs recent ``started`` scheduler events against their
  terminal events, and reads the factory schedules' ``next_run_at``. A fire in flight stops both
  runs; a factory fire due within the margin stops the promote run and warns on the first.

``docker compose config`` interpolates ``.env``, so its output holds secret values. This script
reads only service names, container names and bind sources from it, and never prints it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Callable, Sequence

PRIMARY = "/root/thomas_agent"
ENV_FILE = f"{PRIMARY}/.env"
PROJECT = "thomas_agent"
IMAGE = "thomas-agent-runtime"
PROBE_CONTAINER = "thomas-scheduler"
STATE_SOURCE = f"{PRIMARY}/.runtime_governance_state"
SCHEDULER_EVENTS = f"{STATE_SOURCE}/runtime_ledger/scheduler_events.jsonl"
SCHEDULES = f"{STATE_SOURCE}/schedules.jsonl"
# The scheduler's own pairing (`scheduler.find_abandoned_runs`): a `started` with no terminal
# event under the same `schedule_run_id`. Restated here because this script runs on the host with
# no repo on its path.
TERMINAL_ACTIONS = frozenset({"fired", "failed", "abandoned"})
# A `started` older than this with no terminal is a dead run the next restart will close, not one a
# deploy can still kill: the factory child's own timeout (`scheduler.FACTORY_CHILD_TIMEOUT_SECONDS`).
IN_FLIGHT_SECONDS = 900
# How far ahead a factory fire counts as about to start: a promote plus `compose up` takes a minute
# or two, and a fire claimed in that gap dies with the old lane.
FACTORY_DUE_MARGIN_SECONDS = 600
EVENTS_TAIL = 400

Runner = Callable[[Sequence[str]], "tuple[int, str]"]


def run_command(argv: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout


@dataclass(frozen=True)
class Result:
    level: str  # PASS / WARN / STOP
    name: str
    message: str


def _out(run: Runner, argv: Sequence[str]) -> str | None:
    rc, out = run(argv)
    return out.strip() if rc == 0 else None


def check_pr_merged(run: Runner, pr: int) -> Result:
    raw = _out(run, ["gh", "pr", "view", str(pr), "--json", "state,mergedAt,mergeCommit"])
    if raw is None:
        return Result("STOP", "pr", f"gh pr view {pr} failed; cannot tell whether it merged")
    data = json.loads(raw)
    if data.get("state") != "MERGED":
        return Result("STOP", "pr", f"#{pr} is {data.get('state')}, not merged. Nothing to deploy yet")
    sha = (data.get("mergeCommit") or {}).get("oid")
    if not sha:
        return Result("STOP", "pr", f"#{pr} reports MERGED without a merge commit")
    rc, _ = run(["git", "-C", PRIMARY, "merge-base", "--is-ancestor", sha, "origin/main"])
    if rc != 0:
        return Result("STOP", "pr", f"#{pr}'s merge commit {sha[:10]} is not on origin/main (after fetch)")
    return Result("PASS", "pr", f"#{pr} merged as {sha[:10]}, on origin/main")


def _image_ids(run: Runner) -> dict[str, str]:
    raw = _out(run, ["docker", "images", IMAGE, "--no-trunc", "--format", "{{.Tag}} {{.ID}}"]) or ""
    ids: dict[str, str] = {}
    for line in raw.splitlines():
        tag, _, image_id = line.partition(" ")
        if tag and image_id:
            ids[tag] = image_id
    return ids


def check_rollback_point(run: Runner, pr: int) -> Result:
    running = _out(run, ["docker", "inspect", PROBE_CONTAINER, "--format", "{{.Image}}"])
    if not running:
        return Result("STOP", "rollback", f"cannot read the running image of {PROBE_CONTAINER}")
    ids = _image_ids(run)
    if ids.get("latest") == running:
        return Result("PASS", "rollback",
                      f"latest is the running image: tag rollback-pre-{pr} from latest")
    named = sorted(t for t, i in ids.items() if i == running and t.startswith("rollback-pre-"))
    if named:
        return Result("WARN", "rollback",
                      f"latest is NOT the running image (another session built without deploying). "
                      f"Tag rollback-pre-{pr} from {named[-1]}, never from latest or a raw id")
    return Result("STOP", "rollback",
                  "latest is not the running image and no rollback-pre-* tag names it; "
                  "the running image has no name to roll back to")


def check_tags_free(run: Runner, pr: int) -> Result:
    ids = _image_ids(run)
    taken = [t for t in (f"rollback-pre-{pr}", f"candidate-{pr}") if t in ids]
    if taken:
        return Result("STOP", "tags",
                      f"{', '.join(taken)} already exist. If you did not create them, another session "
                      f"is deploying #{pr}: re-read the host and verify its deploy instead of racing it")
    return Result("PASS", "tags", f"rollback-pre-{pr} and candidate-{pr} are free")


def check_promote_window(run: Runner, pr: int) -> Result:
    running = _out(run, ["docker", "inspect", PROBE_CONTAINER, "--format", "{{.Image}}"])
    ids = _image_ids(run)
    rollback, candidate = f"rollback-pre-{pr}", f"candidate-{pr}"
    if candidate not in ids:
        return Result("STOP", "promote", f"{candidate} does not exist; build it first")
    if rollback not in ids:
        return Result("STOP", "promote", f"{rollback} does not exist; tag the rollback point before building")
    if not running or ids[rollback] != running:
        return Result("STOP", "promote",
                      f"the running image is no longer the one {rollback} names: another session "
                      f"redeployed after you tagged. Re-read the host and start over")
    if ids[candidate] == running:
        return Result("STOP", "promote", f"{candidate} is already running")
    return Result("PASS", "promote", f"running image is still {rollback}; {candidate} is ready to promote")


def _label(run: Runner, key: str) -> str | None:
    return _out(run, ["docker", "inspect", PROBE_CONTAINER, "--format",
                      '{{index .Config.Labels "' + key + '"}}'])


def check_compose_labels(run: Runner) -> Result:
    project = _label(run, "com.docker.compose.project")
    env_file = _label(run, "com.docker.compose.project.environment_file")
    if project == PROJECT and env_file == ENV_FILE:
        return Result("PASS", "compose",
                      f"running stack was started with -p {PROJECT} --env-file {ENV_FILE}. Run: "
                      f"docker compose -p {PROJECT} --env-file {ENV_FILE} -f <tree>/docker-compose.yml up -d")
    return Result("WARN", "compose",
                  f"running stack labels read project={project!r} env_file={env_file!r}, not "
                  f"{PROJECT!r}/{ENV_FILE!r}. Find out how it was started before running compose")


def check_primary_checkout(run: Runner) -> Result:
    behind = _out(run, ["git", "-C", PRIMARY, "rev-list", "--count", "HEAD..origin/main"])
    branch = _out(run, ["git", "-C", PRIMARY, "branch", "--show-current"]) or "(detached)"
    return Result("PASS" if behind == "0" else "WARN", "primary",
                  f"{PRIMARY} is on {branch}, {behind} commits behind origin/main. Never build or run "
                  f"compose from it: use a clean worktree of origin/main")


def check_tree(run: Runner, tree: str) -> list[Result]:
    results: list[Result] = []
    status = _out(run, ["git", "-C", tree, "status", "--porcelain"])
    head = _out(run, ["git", "-C", tree, "rev-parse", "HEAD"])
    main = _out(run, ["git", "-C", tree, "rev-parse", "origin/main"])
    if status is None or head is None:
        return [Result("STOP", "tree", f"{tree} is not a readable git worktree")]
    if status:
        results.append(Result("STOP", "tree", f"{tree} has uncommitted changes; build only a clean tree"))
    elif main is None:
        results.append(Result("STOP", "tree", f"{tree} cannot resolve origin/main; build only a clean origin/main tree"))
    elif head != main:
        # A STOP, not a WARN: a clean tree at another commit ships something other than origin/main,
        # which is the one thing this procedure exists to rule out. A clean tree is not enough.
        results.append(Result("STOP", "tree", f"{tree} HEAD {head[:10]} is not origin/main {main[:10]}; "
                                              f"build only a clean origin/main tree"))
    else:
        results.append(Result("PASS", "tree", f"{tree} is clean at origin/main {head[:10]}"))

    raw = _out(run, ["docker", "compose", "-p", PROJECT, "--env-file", ENV_FILE,
                     "-f", f"{tree}/docker-compose.yml", "config", "--format", "json"])
    if raw is None:
        return results + [Result("STOP", "volumes", "docker compose config failed for that tree")]
    services = json.loads(raw).get("services") or {}
    bad: list[str] = []
    state_seen = False
    for name, spec in sorted(services.items()):
        for vol in spec.get("volumes") or []:
            if vol.get("type") != "bind":
                continue
            source = str(vol.get("source", ""))
            state_seen |= source == STATE_SOURCE
            if not PurePosixPath(source).is_absolute() or source.startswith(tree.rstrip("/") + "/"):
                bad.append(f"{name}:{source}")
    if bad:
        results.append(Result("STOP", "volumes",
                              f"bind sources resolve inside the tree or relative: {', '.join(bad)}. "
                              f"--env-file is not taking effect"))
    elif not state_seen:
        results.append(Result("STOP", "volumes", f"no service mounts {STATE_SOURCE}"))
    else:
        results.append(Result("PASS", "volumes", f"state mounts resolve to {STATE_SOURCE}"))

    wanted = {spec.get("container_name") or name for name, spec in services.items()}
    running = set((_out(run, ["docker", "ps", "--format", "{{.Names}}"]) or "").split())
    gone = sorted(n for n in running if n.startswith("thomas-") and n not in wanted)
    new = sorted(wanted - running)
    if gone:
        results.append(Result("WARN", "services", f"running but not in this compose file: {', '.join(gone)}"))
    if new:
        results.append(Result("WARN", "services", f"in this compose file but not running: {', '.join(new)}"))
    if not gone and not new:
        results.append(Result("PASS", "services", f"{len(wanted)} services match the running containers"))
    return results


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _jsonl(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn or partial line (tail can cut the first one) is not a fact
        if isinstance(row, dict):
            rows.append(row)
    return rows


def check_scheduled_fires(run: Runner, now: datetime, promote: bool) -> Result:
    events = _out(run, ["tail", "-n", str(EVENTS_TAIL), SCHEDULER_EVENTS])
    schedules = _out(run, ["cat", SCHEDULES])
    if events is None or schedules is None:
        return Result("WARN", "fires", "cannot read the scheduler state files; check by hand that no "
                                       "scheduled fire is running before compose up")
    started: dict[str, dict] = {}
    closed: set[str] = set()
    for event in _jsonl(events):
        run_id = event.get("schedule_run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        if event.get("action") == "started":
            started.setdefault(run_id, event)
        elif event.get("action") in TERMINAL_ACTIONS:
            closed.add(run_id)
    in_flight = []
    for run_id, event in started.items():
        at = _parse_time(event.get("created_at"))
        if run_id not in closed and at is not None and (now - at).total_seconds() <= IN_FLIGHT_SECONDS:
            in_flight.append(f"{event.get('kind')} {str(event.get('schedule_id'))[-8:]} since {event.get('created_at')}")
    if in_flight:
        return Result("STOP", "fires",
                      f"a scheduled fire is running ({'; '.join(in_flight)}). compose up would kill it and it "
                      f"is not retried until its next interval: wait for its fired/failed event, then re-run")
    latest: dict[str, dict] = {}
    for row in _jsonl(schedules):
        if row.get("schedule_id"):
            latest[str(row["schedule_id"])] = row
    horizon = now + timedelta(seconds=FACTORY_DUE_MARGIN_SECONDS)
    due = sorted(str(row.get("next_run_at")) for row in latest.values()
                 if row.get("kind") == "crypto_factory" and row.get("enabled")
                 and (at := _parse_time(row.get("next_run_at"))) is not None and at <= horizon)
    if due:
        return Result("STOP" if promote else "WARN", "fires",
                      f"{len(due)} crypto_factory fire(s) due by {horizon:%H:%M}Z (first {due[0]}). A promote "
                      f"now restarts the lane under them: promote after they have fired")
    return Result("PASS", "fires", "no scheduled fire in flight and no factory fire due within "
                                   f"{FACTORY_DUE_MARGIN_SECONDS // 60} minutes")


def preflight(run: Runner, pr: int, tree: str | None, promote: bool = False,
              now: datetime | None = None) -> list[Result]:
    run(["git", "-C", PRIMARY, "fetch", "-q", "origin", "main"])
    results = [check_pr_merged(run, pr)]
    if promote:
        results.append(check_promote_window(run, pr))
    else:
        results += [check_tags_free(run, pr), check_rollback_point(run, pr)]
    results += [check_compose_labels(run), check_primary_checkout(run),
                check_scheduled_fires(run, now or datetime.now(timezone.utc), promote)]
    if tree:
        results += check_tree(run, tree)
    return results


def main(argv: Sequence[str] | None = None, run: Runner = run_command) -> int:
    parser = argparse.ArgumentParser(prog="deploy_preflight", description=__doc__.splitlines()[0])
    parser.add_argument("pr", type=int, help="the PR number being deployed")
    parser.add_argument("--tree", help="the clean origin/main worktree you will build and compose from")
    parser.add_argument("--promote", action="store_true",
                        help="the run immediately before the promote: your tags exist, nothing redeployed since")
    args = parser.parse_args(argv)
    if args.promote and not args.tree:
        parser.error("--promote needs --tree: the promote is followed by compose from that tree")
    results = preflight(run, args.pr, args.tree, args.promote)
    for r in results:
        print(f"[{r.level}] {r.name}: {r.message}")
    stops = sum(r.level == "STOP" for r in results)
    print(f"DEPLOY_PREFLIGHT={'STOP' if stops else 'OK'}")
    return 1 if stops else 0


if __name__ == "__main__":
    sys.exit(main())
