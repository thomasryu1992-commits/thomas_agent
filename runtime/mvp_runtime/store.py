"""Append-only runtime ledger — durable, tamper-evident evidence.

The MVP produces governance records (task, permission decision, agent output, validation
result) and a hash-chained audit trail for every run. Before this store they lived only
in memory and were discarded when the process exited, so "append-only, hash-chained,
auditable" described nothing durable. :class:`LedgerStore` writes them to append-only
JSONL files under a local, per-machine, gitignored directory (``.runtime_governance_state/
runtime_ledger/`` by default, mirroring the Core pointer and safety-flag activation).

Three files, each append-only (one JSON object per line):

- ``audit_events.jsonl`` — the hash-chained ``audit_event.v0.1`` records. ``last_audit_hash``
  returns the tip so a new run chains onto the previous run's last event, making the
  ledger tamper-evident *across* runs, not just within one.
- ``records.jsonl`` — the non-audit records produced by a run, each tagged with its kind
  and the run's trace id. Includes ``budget_usage``: what the run actually spent against
  its allocation (the task/assignment budgets are pre-execution allocations, so their
  usage blocks are zero by construction and cannot carry this).
- ``blocks.jsonl`` — lightweight block entries for runs that fail *before* a Core binding
  exists. Such a failure cannot be expressed as an ``audit_event.v0.1`` (that schema
  requires a bound task with a ``core_context_binding_id``), so a minimal, still-durable
  block entry is recorded instead.
- ``control_events.jsonl`` — operator emergency-console events (pause/kill/resume/stop).
  These are runtime control actions, not task outcomes, so like blocks they are durable
  standalone entries rather than task-bound ``audit_event.v0.1`` records.
- ``memory_events.jsonl`` — memory maintenance events (working-memory retention/deletion),
  likewise standalone rather than task-bound.
- ``scheduler_events.jsonl`` — scheduler events (a schedule fired, or was skipped by the kill
  switch), likewise standalone.
- ``programization_events.jsonl`` — operator programization-review events (a pattern moved
  UNDER_REVIEW/CLOSED, a program candidate drafted), likewise standalone.
- ``feedback_events.jsonl`` — operator feedback on delivered runs (E1: Thomas's verdict
  on an answer, the ground truth later evaluation reads), likewise standalone.

Fail-closed: any write or read failure raises :class:`PersistenceError`. Secrets are never
written — the records are already metadata-only and secret-scanned upstream.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import jsonl
from .errors import PersistenceError
from .filelock import locked
from .paths import repo_root as _repo_root

LEDGER_REL = ".runtime_governance_state/runtime_ledger"
AUDIT_FILE = "audit_events.jsonl"
RECORDS_FILE = "records.jsonl"
BLOCKS_FILE = "blocks.jsonl"
CONTROL_FILE = "control_events.jsonl"
MEMORY_FILE = "memory_events.jsonl"
SCHEDULER_FILE = "scheduler_events.jsonl"
PROGRAMIZATION_FILE = "programization_events.jsonl"
FEEDBACK_FILE = "feedback_events.jsonl"
# The assistant doors' at-most-once bookkeeping: one row per `request_id` transition, folded
# latest-wins by `bridge_idempotency`. It lives here rather than in a store of its own because
# the doors already hold a `LedgerStore` and this file needs exactly what the other eight need —
# the same root, the same per-file lock, the same append-only discipline — and nothing else.
# There is no `append_bridge_request` sibling on purpose: the check and the record must sit in
# ONE critical section (a method that took the lock itself could only offer two, with the door's
# effect between them), so `bridge_idempotency` takes `file_lock` and drives `jsonl` directly,
# the way `retention` does.
BRIDGE_REQUESTS_FILE = "bridge_requests.jsonl"

# Where rotation puts the rows it moves out of the active files, and the shape of the stamp it
# names them with (`utc_now_iso` with the colons removed, so the name sorts chronologically).
# Here rather than in `retention` because a reader has to find those files too — `retention`
# writes them, `LedgerStore.iter_records_with_archive` reads them, and one owner for the layout
# is the difference between a reader that follows rotation and one that quietly stops at it.
# `retention` imports both from here; the directory name is unchanged.
ARCHIVE_DIR = "archive"
_ARCHIVE_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{6}Z")

# Non-audit records persisted per run, in pipeline order.
_RECORD_KINDS = (
    "received_task", "task", "binding", "permission_decision",
    "search_permission_decision", "role_assignment",
    "validator_permission_decision", "validator_assignment",
    "triage_permission_decision", "triage_result", "triage_invocation",
    "model_tier_selection",
    "write_permission_decision", "tool_use",
    # The Naver keyword brief (#666). Missed when the wiring merged, and found by the first
    # real CLI run rather than by the suite: every wiring test called run_task WITHOUT a
    # store, so this closed list was never exercised — the store refused the run
    # (LEDGER_UNKNOWN_RECORD_KIND) exactly as designed. Sits beside tool_use because it is
    # the same shape of thing: one evidence-collection record per run, pipeline-ordered.
    "keyword_research", "keyword_permission_decision",
    "agent_output", "invocation", "validation_result",
    "independent_validation_result", "validator_invocation", "write_use",
    "revision",
    "budget_usage",
    "trial_report",
    "crypto_cycle",
    "crypto_factory",
    "crypto_strategy_proposal",
    "crypto_data_review",
    "crypto_null_control",
    # The blog content lane's package (Phase 2). A ledger row rather than a file: the workspace
    # writer is behind `filesystem_write`, which is unset on this deployment, so the record IS
    # the artefact until that opens.
    "blog_content_package",
    "programization_observation", "programization_pattern",
)

# The same roster, public, for the archive index: rotation records which of these a closed
# archive can contain so a kind-filtered read can skip the file without opening it.
RECORD_KINDS = _RECORD_KINDS

# Sidecar naming. An archive is immutable once written, so an index written beside it at
# rotation stays true forever; the suffix deliberately does not end in `.jsonl` so the
# archive glob cannot pick it up as a ledger.
ARCHIVE_INDEX_SUFFIX = ".kinds"


def archive_index_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ARCHIVE_INDEX_SUFFIX)


def read_archive_index(archive: Path) -> frozenset[str] | None:
    """The kinds an archive may contain, or ``None`` when that is not known.

    ``None`` is the fail-closed answer and it is what every unindexed archive returns — the
    caller must then read the file. Only a positively-read index may cause a file to be
    skipped, and the index is a SUPERSET of what the archive holds (rotation screens by
    substring without parsing, so it can over-report and never under-report). Both halves
    matter: over-reporting costs one needless file read, under-reporting would silently drop
    rows from a filtered answer.
    """
    index = archive_index_path(archive)
    try:
        raw = index.read_text(encoding="utf-8")
    except OSError:
        return None
    kinds = frozenset(line.strip() for line in raw.splitlines() if line.strip())
    # An index that names nothing is indistinguishable from a truncated write; refuse to read
    # it as "this archive is empty of every kind", which would skip a file on damaged evidence.
    return kinds or None


# Keys the pipeline carries in its records mapping that are deliberately NOT persisted as
# record rows: the audit trail and block entry have their own files, and retrieved memory
# is read-only context (already durable in the working-memory store, not a run product).
_NON_RECORD_KEYS = frozenset({"audit_trail", "block_record", "memory_retrieved",
                              "validated_memory_retrieved"})


def _kind_prescreen(kinds: Iterable[str] | None) -> tuple[str, ...] | None:
    """The quoted tokens a kind-filtered ledger read may skip parsing on.

    A row of kind K is written with ``"kind": "K"``, so ``'"K"'`` appears verbatim in its line
    and the screen cannot miss it; it can only over-include, which costs one parse the caller
    then discards. See ``jsonl.iter_numbered`` for why that asymmetry is the whole safety
    argument, and for what a filtered read gives up on corruption it did not ask about.

    Why this lives here rather than in each caller: the lock is held for the WHOLE iteration,
    so a reader after a rare kind does not merely waste its own time — it holds the appender's
    lock while it wastes it. Measured 2026-08-31 on the live host: the active ledger is 23 MB /
    5,458 rows of which 97.6% are ``crypto_cycle``, and the archives add 177 MB / 31,590 rows.
    An unfiltered ``--list`` over both paid 1.29 s under that lock, all of it to decode rows it
    dropped, and the cost grows with every archive rotation.
    """
    if kinds is None:
        return None
    tokens = tuple(sorted({f'"{kind}"' for kind in kinds}))
    # An empty selection is a caller bug, not "match nothing": screening on () would make
    # `any(...)` false for every line and silently return an empty ledger.
    if not tokens:
        raise PersistenceError("LEDGER_EMPTY_KIND_FILTER",
                               "kinds= was given an empty selection; omit it to read every kind")
    return tokens


class LedgerStore:
    """Append-only JSONL ledger rooted at a directory (created on first write)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    @classmethod
    def default(cls, root: Path | None = None) -> "LedgerStore":
        """The repo-local ledger under ``.runtime_governance_state/`` (gitignored)."""
        return cls((root if root is not None else _repo_root()) / LEDGER_REL)

    @property
    def root(self) -> Path:
        return self._root

    def append_audit_events(self, events: list[Mapping[str, Any]]) -> None:
        """Append a chain segment, re-anchored onto the CURRENT tip under the ledger lock.

        Builders chain from the tip their caller read at run start; in the multi-process
        deployment another process may have appended since, which would fork the chain and
        make an honest ledger verify as tampered. Re-reading the tip and rechaining inside
        one cross-process exclusion makes the ledger a single unforked chain by
        construction. The events are mutated in place (hashes/links updated), so callers
        holding them see exactly what was persisted."""
        from .audit import rechain_events

        events = list(events)
        with self._audit_lock():
            rechain_events(events, self._tip())  # type: ignore[arg-type]
            jsonl.append_lines(self._root / AUDIT_FILE, events, write_code="LEDGER_WRITE_FAILED", label="the audit ledger")

    def append_records(self, trace_id: str | None, records: Mapping[str, Any]) -> None:
        # Fail-closed on an unrecognized kind: silently dropping a record the pipeline
        # produced would persist an audit trail whose fingerprints reference evidence that
        # no longer exists anywhere (this exact hole once swallowed the R8 write records).
        unknown = set(records) - set(_RECORD_KINDS) - _NON_RECORD_KEYS
        if unknown:
            raise PersistenceError(
                "LEDGER_UNKNOWN_RECORD_KIND",
                f"refusing to silently drop unrecognized record kinds: {sorted(unknown)}",
            )
        rows = [
            {"kind": kind, "trace_id": trace_id, "record": records[kind]}
            for kind in _RECORD_KINDS
            if kind in records
        ]
        self._append_locked(RECORDS_FILE, rows, "the record ledger")

    def append_block(self, entry: Mapping[str, Any]) -> None:
        self._append_locked(BLOCKS_FILE, [dict(entry)], "the block ledger")

    def append_control(self, entry: Mapping[str, Any]) -> None:
        """Durably record one operator emergency-console event (pause/kill/resume/stop)."""
        self._append_locked(CONTROL_FILE, [dict(entry)], "the control ledger")

    def append_memory_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one memory maintenance event (e.g. working-memory retention/deletion)."""
        self._append_locked(MEMORY_FILE, [dict(entry)], "the memory ledger")

    def append_scheduler_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one scheduler event (a schedule fired, or was skipped by the kill switch)."""
        self._append_locked(SCHEDULER_FILE, [dict(entry)], "the scheduler ledger")

    def append_programization_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one operator programization-review event (transition / candidate draft)."""
        self._append_locked(PROGRAMIZATION_FILE, [dict(entry)], "the programization ledger")

    def append_feedback_event(self, entry: Mapping[str, Any]) -> None:
        """Durably record one operator feedback event (Thomas's verdict on a delivered run)."""
        self._append_locked(FEEDBACK_FILE, [dict(entry)], "the feedback ledger")

    def _append_locked(self, filename: str, rows: list[Mapping[str, Any]], label: str) -> None:
        """Append under this file's own cross-process lock.

        The deployment is multi-process (the operator loop plus ``docker exec`` CLIs and
        scheduler ticks on one volume), and a record row can be tens of KB — far past the
        stdio buffer — so one logical line reaches the file in several write syscalls.
        Only the audit file was locked; interleaved partial lines from two unlocked
        writers would corrupt these stores, every later read would fail closed, and there
        is no sanctioned repair path. Per-file sidecar locks, so writers to different
        files never serialize against each other."""
        jsonl_path = self._root / filename
        with self.file_lock(filename, label=label):
            jsonl.append_lines(jsonl_path, rows, write_code="LEDGER_WRITE_FAILED", label=label)

    def file_lock(self, filename: str, *, label: str | None = None):
        """This ledger file's cross-process lock — the same one appends take.

        Public because retention rotates these files and MUST hold the writer's lock while
        it does: a rotation that interleaved with an append would tear the file the append
        was careful not to tear. One owner for the sidecar-path convention, so a second
        caller cannot invent a slightly different lock and think it is holding this one."""
        return locked(self._root / (filename + ".lock"),
                      code="LEDGER_WRITE_FAILED", label=label or f"the {filename} ledger")

    def last_audit_hash(self) -> str | None:
        """Return the last persisted event's ``event_sha256`` (the chain tip), or None.

        Advisory for builders: the tip that MATTERS is re-read under the ledger lock at
        append time (see ``append_audit_events``), so a stale value here can only cause a
        rechain, never a fork. Reading a corrupt or unparseable ledger fails closed rather
        than silently starting a fresh chain over a damaged one."""
        with self._audit_lock():
            return self._tip()

    def _audit_lock(self):
        return locked(self._root / (AUDIT_FILE + ".lock"),
                      code="LEDGER_WRITE_FAILED", label="the audit ledger")

    def _tip(self) -> str | None:
        """The current chain tip, caller-locked. Fail-closed on a corrupt ledger.

        Streams for one row rather than materializing the chain to take its last element.
        This is the one ledger file that is **never rotated** — ``retention`` refuses it on
        purpose, because front truncation makes an honest ledger verify as tampered and tail
        truncation is the chain's documented blind spot — so it grows for the life of the
        machine, and every run that appends an audit event reads it to find this one hash.
        Holding it whole to look at the end was the cost that would grow without a ceiling.

        **Every line is still parsed**, which is the point of not seeking to the end instead:
        a corrupt line anywhere fails closed rather than letting a new chain start over a
        damaged one, and that property is this method's, not an accident of how it read.
        """
        tip: dict[str, Any] | None = None
        for event in jsonl.iter_objects(
            self._root / AUDIT_FILE, read_code="LEDGER_UNREADABLE", label="the audit ledger tip"
        ):
            tip = event
        if tip is None:
            return None
        try:
            return tip["integrity"]["event_sha256"]
        except (KeyError, TypeError) as exc:
            raise PersistenceError("LEDGER_UNREADABLE", f"could not read the audit ledger tip: {exc}") from exc

    def read_blocks(self) -> list[dict[str, Any]]:
        """Every persisted block entry, in append order (pre-binding blocks, recorded audit
        gaps, operator probe notes). Fails closed on a corrupt file; `recovery` treats an
        unreadable store as its own finding rather than propagating this."""
        with locked(self._root / (BLOCKS_FILE + ".lock"),
                    code="LEDGER_WRITE_FAILED", label="the block ledger"):
            return jsonl.read_objects(self._root / BLOCKS_FILE,
                                      read_code="LEDGER_UNREADABLE", label="the block ledger")

    def read_records(self) -> list[dict[str, Any]]:
        """Every persisted record row (``{"kind", "trace_id", "record"}``), in append order.

        Read under the appender's own lock, like the block/scheduler readers: a run may be
        appending its records while a reader scans the stream. Fails closed on a corrupt file.

        Holds the whole ledger. Every caller that scans it to keep a handful of rows should
        use :meth:`iter_records` instead — on the live host this file is ~23 MB and costs
        81 MB of process memory to materialize, and it is rotated daily rather than bounded."""
        return list(self.iter_records())

    def iter_records(
        self, *, kinds: Iterable[str] | None = None, trace_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Record rows one at a time, in append order, under the appender's lock.

        ``trace_id`` is the prescreen for the reader after ONE run's rows — ``/result`` and
        the dispatch door's idempotent replay re-render a delivered response from them. A
        row of that run carries ``"trace_id": "<id>"`` verbatim, so a line without the
        quoted id is skipped unparsed (see ``_kind_prescreen`` for why that is safe and
        what it gives up). Measured 2026-09-04 on the live 21 MB file: 160–220 ms decoding
        every row to keep four, all of it under the appender's lock. When both ``kinds`` and
        ``trace_id`` are given the screen is the trace id alone (the narrower token; the
        prescreen is OR-shaped and cannot express both), and the caller still checks kinds.

        For the readers that scan the ledger to retain almost none of it: the last N rows of
        one kind, a count, the rows carrying one trace id. `crypto/dashboard.py` already had
        to be rewritten this way after materializing the same file OOM-killed the board; the
        three remaining callers were doing the same thing to the same file for the same
        reason, so the shape now lives here rather than being solved a fourth time locally.

        **Reads the ACTIVE file only**, so what it can see ends at
        ``retention.DEFAULT_KEEP_ROWS`` — a row count, not a span. That is the right horizon
        for the readers above (all of them want the recent tail) and the wrong one for
        anything measuring a window of TIME; use :meth:`iter_records_with_archive` there.

        The lock is held for the whole iteration, exactly as ``read_records`` held it for the
        whole read — so **consume or close it**. A caller that abandons the generator keeps
        the record ledger locked until it is collected, which would stall the run trying to
        append to it. All present callers run the stream to exhaustion.
        """
        prescreen = (f'"{trace_id}"',) if trace_id else _kind_prescreen(kinds)
        with self.file_lock(RECORDS_FILE, label="the record ledger"):
            yield from jsonl.iter_objects(self._root / RECORDS_FILE,
                                          read_code="LEDGER_UNREADABLE", label="the record ledger",
                                          must_contain=prescreen)

    def iter_records_with_archive(
        self, *, appended_since: str | None = None, kinds: Iterable[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Record rows one at a time, ARCHIVES INCLUDED, oldest first, under the same lock.

        For the readers that measure a WINDOW OF TIME. :meth:`iter_records` answers those
        wrongly and silently: rotation moves rows the window still covers out of the active
        file, so the count shrinks with no error and no signal. Measured 2026-08-08 on the
        live host — the proposer's documented 30-day backlog window could actually see nine
        days, because four in-window proposal records had already rotated into the archive,
        and the backlog it throttles on had dropped 15 -> 11 for that reason alone.

        ``appended_since`` is an ISO timestamp that bounds which FILES are opened, never
        which rows are yielded — the caller still filters, because only the caller knows
        which timestamp on a record it means. An archive's name carries the rotation moment
        and every row in it was appended before that, so an archive stamped earlier than
        ``appended_since`` is skipped WITHOUT BEING OPENED. Without this the cost would be
        the whole history on a reader that runs daily, against an archive that only grows.
        The skip is safe exactly while a record's own timestamp is never NEWER than its
        append, which holds for everything this runtime writes: ``created_at`` is stamped as
        the record is built and the append is the same call.

        A name that does not parse as a stamp is read rather than skipped. This method exists
        because a count under-reported; the uncertain case must therefore add rows, never
        drop them.

        Order is archives by rotation stamp, active file last. Two rotations inside one
        second sort by filename instead, which puts the collision-suffixed one first — no
        consumer can observe it, since both hold rows older than the same second.

        The lock is the appender's, held for the whole iteration exactly as
        :meth:`iter_records` holds it, so **consume or close it**. Rotation takes that same
        lock (see :meth:`file_lock`), so no archive can appear mid-scan and no row can be
        yielded twice or missed.
        """
        screen = _kind_prescreen(kinds)
        with self.file_lock(RECORDS_FILE, label="the record ledger"):
            for path in self._archived_record_files(appended_since, kinds):
                yield from jsonl.iter_objects(
                    path, read_code="LEDGER_UNREADABLE",
                    label=f"the archived record ledger {path.name}", must_contain=screen)
            yield from jsonl.iter_objects(self._root / RECORDS_FILE,
                                          read_code="LEDGER_UNREADABLE", label="the record ledger",
                                          must_contain=screen)

    def _archived_record_files(
        self, appended_since: str | None, kinds: Iterable[str] | None = None
    ) -> list[Path]:
        """Archived record ledgers oldest first, minus those this read cannot need.

        Two independent skips, and the difference between them is worth stating. The
        ``appended_since`` bound drops archives wholly older than a time window. The ``kinds``
        bound drops archives whose INDEX says they carry none of the wanted kinds — and only
        an index that was positively read may do that: an unindexed or unreadable archive is
        always opened, because "cannot see" must never render as "nothing there".

        This is the only skip that does not scale with the store. Archives are kept forever by
        design (`retention`: *nothing is ever destroyed*), so a full-history reader was on a
        line that rises ~5 MB a day — 201 MB across 36 files on 2026-08-31, and a filtered walk
        of it cost 0.89 s under the appender's lock. The kinds a weekly package lands in are a
        handful of files; the rest are now a stat and a 100-byte read.
        """
        directory = self._root / ARCHIVE_DIR
        if not directory.is_dir():
            return []
        stem = RECORDS_FILE.removesuffix(".jsonl")
        cutoff = appended_since.replace(":", "") if appended_since else None
        wanted = frozenset(kinds) if kinds is not None else None
        kept: list[Path] = []
        for path in sorted(directory.glob(f"{stem}.*.jsonl")):
            stamp = path.name[len(stem) + 1:].removesuffix(".jsonl").split(".", 1)[0]
            if not (cutoff is None or not _ARCHIVE_STAMP.fullmatch(stamp) or stamp >= cutoff):
                continue
            if wanted is not None:
                indexed = read_archive_index(path)
                if indexed is not None and indexed.isdisjoint(wanted):
                    continue
            kept.append(path)
        return kept

    def read_scheduler_events(self) -> list[dict[str, Any]]:
        """Every persisted scheduler event, in append order. Fails closed on a corrupt file.

        Read under the appender's own lock, like the block/audit readers: the tick loop may
        be appending a fire's outcome while a startup scan reads the stream."""
        with locked(self._root / (SCHEDULER_FILE + ".lock"),
                    code="LEDGER_WRITE_FAILED", label="the scheduler ledger"):
            return jsonl.read_objects(self._root / SCHEDULER_FILE,
                                      read_code="LEDGER_UNREADABLE", label="the scheduler ledger")

    def read_scheduler_events_tail(self, limit: int) -> list[dict[str, Any]]:
        """The newest ``limit`` scheduler events, read from the end of the active file and
        WITHOUT the appender's lock — the read door's shape of this question.

        :meth:`read_scheduler_events` parses the whole active file under the lock: right
        after rotation that is ~2,000 rows, by the end of the day ~20,000, and every call
        held the tick loop's append lock for the whole parse (18–22 ms measured 2026-09-04 on
        a 2.4 MB file). A poller wanting the last twenty was paying for all of them and
        making the scheduler wait while it did. See ``jsonl.tail_objects`` for why the
        lock-free read is honest for an append-only file: a torn final line is dropped,
        a corrupt complete line in the tail still raises, and the rest of the file is not
        this reader's business."""
        return jsonl.tail_objects(self._root / SCHEDULER_FILE, limit,
                                  read_code="LEDGER_UNREADABLE", label="the scheduler ledger")

    def count_scheduler_events(self) -> int:
        """Rows in the active scheduler file — a newline count, lock-free, ~1 ms."""
        return jsonl.count_lines(self._root / SCHEDULER_FILE,
                                 read_code="LEDGER_UNREADABLE", label="the scheduler ledger")

    def read_audit_events(self) -> list[dict[str, Any]]:
        """Every persisted audit event, in append order. Fails closed on a corrupt ledger.

        Reads under the same lock the appender holds: a multi-event segment is flushed in
        several buffered writes, so an unlocked read racing an append could see a torn
        final line and report LEDGER_UNREADABLE — alarming the operator about tampering
        that never happened — when waiting out the append reads a whole chain."""
        with self._audit_lock():
            return jsonl.read_objects(self._root / AUDIT_FILE, read_code="LEDGER_UNREADABLE", label="the audit ledger")

    def health(self) -> list[dict[str, Any]]:
        """Report each ledger file's readability without failing on a bad one.

        Deliberately does NOT fail closed: this is the diagnostic that runs precisely when
        something is already broken, so it must survive a corrupt file and name it rather
        than raise and leave the operator with the same blank stare that sent them here.
        """
        report: list[dict[str, Any]] = []
        for kind, filename in (
            ("audit_events", AUDIT_FILE), ("records", RECORDS_FILE), ("blocks", BLOCKS_FILE),
            ("control_events", CONTROL_FILE), ("memory_events", MEMORY_FILE),
            ("scheduler_events", SCHEDULER_FILE), ("feedback_events", FEEDBACK_FILE),
        ):
            path = self._root / filename
            entry: dict[str, Any] = {"kind": kind, "path": filename, "present": path.is_file()}
            if not entry["present"]:
                # Absent is normal: a store is created on first write, not at startup.
                entry.update({"status": "ABSENT", "count": 0, "detail": "not written yet (normal before first use)"})
            else:
                try:
                    entry.update({
                        "status": "OK",
                        "count": len(jsonl.read_objects(path, read_code="LEDGER_UNREADABLE", label=kind)),
                        "detail": None,
                    })
                except PersistenceError as exc:
                    entry.update({"status": "CORRUPT", "count": None, "detail": exc.reason})
            report.append(entry)
        return report
