# The Claude read door: one named profile, projected replies, fixed refusals, an audit of every answer

**What it is.** `read_bridge.open_door(profile="claude", request_log=…)` (CLI `--profile claude
--request-log PATH`) opens a read door for a narrower reader than the assistant.
- **The profile:** one frozen `DoorProfile` in `read_bridge.PROFILES`, accepted by `apply_read` by
  identity only and checked for coherence on every use.
- **What it serves:** two reads, `schedules` and `scheduler_events`. Each goes through the closed reply
  contract v1 in `store_reads`: rows are rebuilt from closed lists, then checked against a closed JSON
  Schema. A defect refuses the whole reply (`REPLY_SCHEMA_INVALID`).
- **When it refuses to listen:** without a non-empty `MVP_BRIDGE_CLIENT_UID` (re-checked after the
  bind) and a request log it can write. There is no option that turns one of those off.

**Without a profile nothing changes.** The door is the assistant's door, and the hermes path is
unchanged:
- **Pinned by tests:** the replies, the transport's `"<code>: <text>"` refusals, `check_heartbeat`'s
  output and the `/status` text.
- **Not pinned by a test:** the startup banner. Its code is identical; no test checks it.

**Audit (review R-02, R-04).** Every answer the profiled door sends — a reply, a typed refusal, a peer
refused by uid, credentials the kernel would not report, a frame that would not decode, a busy refusal —
gets one row `{at, peer_uid, read, outcome}` before it goes out.
- **`peer_uid`:** what `SO_PEERCRED` reported, or null — never guessed.
- **`read`:** a name from the read table, `<not_a_read>`, or null.
- **Failure:** a row that cannot be written replaces the answer with `REQUEST_LOG_UNAVAILABLE`.
- **Gap:** connections that end with no answer (read timeout, oversize frame, early hang-up) are not
  logged. This is kept on purpose; closing it would mean changing the shared `socket_door`.

**The log file (review R-07).**
- Opened with `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, and only the last path component is protected.
- The open descriptor must be a regular file, with one link, owned by the euid, not group- or
  other-writable.
- Locked with `flock` on its own descriptor; there is no sidecar lock file, and the shared `filelock`
  module is untouched. A short write is cut back.
- The directory is never created.
- A platform without `O_NOFOLLOW` or `fcntl` refuses.

**Refusals carry a code and a fixed sentence (review R3-5).**
- The table is `PROFILE_ERROR_TEXT`, 11 codes. Any other code becomes `READ_UNAVAILABLE`.
- Never sent to the peer: exception text, a refusal's `data`, a path, the pid inside `error_ref`, the
  peer's own uid.
- The request log keeps the code that was really raised.
- The transport keeps writing tracebacks to the service log only.

**Two contracts written but not served (review R3-1, R3-2).**
- **heartbeat:** `heartbeat.inspect_heartbeat` returns a failure code beside the unchanged report, so
  "which part was unreadable" never comes from `detail` (whose text holds an exception message).
- **runtime_status:** read off `ControlState` (`ControlStore.load()`, which writes nothing), never the
  `/status` text. `updated_by`, `reason` and task ids are not carried.
- **Wiring:** `apply_read` routes a projecting door to both.
- **Not in any profile.** They are held, with crypto_readiness and holdings_status, in
  `_PROFILE_HELD_READS`. Each of the four answers raw today: an exception message, an operator id and
  free text, stored free text, or snapshot keys passed through.

**Why this shape.** The design is `docs/proposals/HOST_PRIVILEGE_SEPARATION_V0.1.md`, which lands with
its own PR.
- **R-01 — one profile, not four flags:** an uncommitted first cut used four independent options, and
  review found that leaving `--projection` out would have served the raw `last_status` text, which can
  hold a factory child's exception message.
- **R-02 — refused peers are logged:** in that first cut they never reached the request log.
- **R-04 — no free text in the log:** an unknown read name is logged as `<not_a_read>`.
- **R-05 — a narrowed door names only its reads:** it does not list the whole table back.
- **R-06 — patterns end in `\Z`:** `$` also matches before a trailing newline.
- **R-07 — no link is followed:** covered above.
- **R3-5 — fixed refusal text:** a synthetic ledger read error showed that `str(exc)` carries an
  absolute path.
- **R3-1, R3-2:** the two contracts above.

**Decision state.**
- **Confirmed:** Thomas confirmed D-11 a1 (the code part: the uid allowlist is mandatory), c1 (reply
  projection and closed schema), d2 (request audit, refusal when it cannot be written) and f2 (the
  profile's read subset) on 2026-10-16, **for the repository implementation only**.
- **Not decided by this:** D-11 b3, e2, D-1, D-3.
- **No operational authority:** no compose service, host account, uid/gid, socket path or log
  directory. Claude's access to this door stays BLOCKED, and merging this PR does not change that.

**Numbers.**
- **New tests:** `tests/test_mvp_runtime_read_bridge_projection.py`, 71 cases, and
  `tests/test_mvp_runtime_store_reads_held_contracts.py`, 14 cases. All synthetic `tmp_path` state.
- **Local runs:** on Python 3.14. A full local run of the combined Option C worktree passed with an
  ephemeral Core. CI runs 3.12.
- **Fault injection:** in a scratch run outside the suite, each of 30 injected faults in the door code
  and 10 in the two contracts made a test fail. This is not part of CI.
- **Reason codes:** 9 new ones in `docs/DIAGNOSTIC_CODE_INDEX.md`.
- **Windows skips:** +36. 22 need an AF_UNIX listener, which the door cannot have on Windows. 14 need
  POSIX log semantics; on Windows the log refuses at the platform guard, so those tests would pass only
  vacuously. The win32 ceiling moves 258 → 294, predicted from main's measured 258 plus a marker count
  and a simulated run that agree; this PR's own Windows run is the measurement.
