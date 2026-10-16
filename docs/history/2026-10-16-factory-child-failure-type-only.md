# A factory child's failure status keeps the exception type only; the message goes to a private diagnostics file

**What changed** (`runtime/mvp_runtime/scheduler.py`).

The factory child reports a failure as `"<type>: <message>"`, and the status used to be
`failed:FACTORY_CHILD:<type>: <first 160 characters of the message>`. That message is whatever the
computation raised (a path, a value, a symbol) and it reached every reader of the scheduler's
records: the ledger event, `last_status`, the operator alert, the service log and any door that
reads them. Now:

- **Status is the type only (D-11 e2, review E-03).** The status is `failed:FACTORY_CHILD:<Type>`.
  - The type is taken only when the text before the child's `": "` separator is a plain ASCII class
    name: `^[A-Za-z][A-Za-z0-9_]{0,63}\Z`.
  - Everything else is `failed:FACTORY_CHILD:UNKNOWN`: no separator, empty, not a string, newline,
    control character, non-ASCII, a lone surrogate, more than 64 characters, path-like.
  - The one exception is the parent's own `CORRUPT_RESULT`, which is kept.
  - The `failed:` prefix every reader keys on is unchanged.
- **The message goes to one private diagnostics file** (up to 2,000 characters, as ASCII-escaped
  JSON): `.runtime_governance_state/diagnostics/factory_child_errors.jsonl`. No door read serves it.
- **A failed diagnostics write cannot cost the occurrence its terminal event (review E-02).**
  - The broad `except` lives only in `_record_factory_child_error`.
  - The occurrence stays `failed` and keeps its terminal ledger event.
  - The service log gets the fixed code `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE` with the
    `schedule_id` and `schedule_run_id`, never the message.
  - A short write is cut back to where it began.
- **The diagnostics file and directory are private (review E-04).**
  - The directory is created 0700 only when absent; an existing one is never chmod-ed.
  - The directory is opened `O_DIRECTORY|O_NOFOLLOW`. The file is opened relative to that directory
    descriptor (`dir_fd`) with `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, mode 0600.
  - Each descriptor is checked after opening: right type, owned by the euid, no group or other bits,
    and for the file one link. Anything else is refused and left exactly as it was.
  - A platform without `O_NOFOLLOW`, `O_DIRECTORY` or `dir_fd` (Windows) writes no row at all. The
    status never depended on the row.
- **Leftover spool files (e-4).** A spool result, or its `.tmp`, left without its meta is deleted
  unread.

**Why.** Review of Option C (host privilege separation) found the failure message flowing into
records that a less-trusted reader can reach. The design record is
`docs/proposals/HOST_PRIVILEGE_SEPARATION_V0.1.md`, which lands with its own PR. The read-side
projection already merged in #1223 normalises both the old and the new status forms to
`failed:FACTORY_CHILD`.

**How the operator reads a message (E-01, approved as E1-a: procedure only).**
- Thomas reads the diagnostics file on the host, through his own approved access path.
- The file is treated as possibly holding secrets.
- Claude does not read it, gets no Docker or root access to read it, and Thomas's terminal is not used
  as Claude's runner.
- Neither Hermes, any read door nor a Telegram alert ever carries the original text.
- Retention and rotation are not decided yet.

**Decision state.** Thomas approved all of the following on 2026-10-16 for the repository only:
- D-11 e2: EXPLICITLY_APPROVED (repository implementation only).
- The crypto research pause exemption: APPROVED, security bug fixes only.
  - This changes only `scheduler.py` and its tests; nothing under `crypto/`.
  - No strategy, signal, SL/TP, risk-gate, stage or order path changes.
  - Crypto_AI_System stays review-only / signed-testnet-preparation.
- E-01 E1-a (documentation only), and E-02 to E-04 (repository security fixes only).
- **Not covered:** no operational approval. Deploying this would change the format the running
  scheduler records, and that needs its own approval.

**Not done.**
- Past ledger rows are not rewritten, so after a deploy the ledger holds both formats.
- There is no operator CLI for the diagnostics file (E1-b, not decided).
- Retention and rotation of the diagnostics file are undecided.

**Numbers.**
- **Tests:** `tests/test_mvp_runtime_scheduler_factory_child.py` grows from 14 to 45 cases, all
  synthetic `tmp_path` state.
- **Fault injection:** in a scratch run outside the suite, each of 16 injected faults made a test fail.
- **Local runs:** Python 3.14; CI runs 3.12.
- **Reason codes:** `docs/DIAGNOSTIC_CODE_INDEX.md` is regenerated. Its count of raises that carry a
  message instead of a code goes up by 3: the three internal `OSError` raises that
  `_record_factory_child_error` catches.
- **Windows skips:** +12. These are the POSIX diagnostics-file tests; on Windows the row is refused
  instead, and that refusal is tested there. The win32 ceiling moves 294 → 306, predicted from main's
  measured 294 plus a marker count and a simulated run that agree; this PR's own Windows run is the
  measurement.
