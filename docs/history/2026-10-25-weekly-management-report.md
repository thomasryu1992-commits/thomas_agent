# The weekly management report: four sections, one message, the control bot

- **Why:** Hermes was the only thing that sent Thomas a periodic report: a morning brief, a weekly decision
  digest and the weekly lane summary D8 relied on. It was retired the same day (#1305, #1307). The 2026-10-25
  company-design audit named the missing management report as the largest gap in the secretary's role.
  Design and decisions: `docs/proposals/WEEKLY_MANAGEMENT_REPORT_V0.1.md` (WMR-1: Q1=A, Q2 holdings
  excluded, Q3 Monday 09:00 KST, Q4 counts only).
- **What:**
  - `management_report.py` collects four sections, each read on its own:
    - lanes: `lane_digest` plus one crypto line from the dashboard's status;
    - approvals still pending and not expired: id, kind, expiry;
    - failing schedules, and failed or abandoned fires per schedule over the week;
    - `/feedback` GOOD/BAD/NOTE per lane, joined to the runs by `trace_id`.

    A section that cannot be read renders `읽지 못함: <code>`, and the others still go.
  - Kind `management_report` runs on the maintenance lane and sends once on the control bot. A send that
    fails fails the fire (`MANAGEMENT_REPORT_NOT_SENT`), so the existing failure alert reports it. This
    differs from `crypto_report`, which only notes a failed send.
  - `operator.select_control_channel` is the control bot behind the same env gate, send-only by
    construction (`ControlSendChannel` refuses `poll` and `peek`). scheduler-maint receives the control
    bot's token a second time as `MVP_CONTROL_BOT_TOKEN`, drawn from the same `.env` source it already held
    as its fallback, so no service gains a credential.
  - `LedgerStore.iter_events_with_archive` reads the scheduler and feedback ledgers across rotation. Both
    rotate at 2,000 rows, which a week can exceed. When the history is shorter than the window, the report
    says how far back it counted.
- **What it never sends:** a failure's text (codes only, the read door's projection-v1 reason), an
  approval's snapshot or fingerprint, a feedback comment, or anything from the holdings lane.
- **Not built (Q4, recorded in `REMAINING_WORK.md` §E):** reading feedback back into later runs, and sending
  scheduled analyses' results. The report shows the count of both.
- **Checked:**
  - 12 new tests.
  - Two mutations were caught: sending the raw status instead of the code, and offering expired asks.
  - Full suite and release gate: see the PR.
- **Host steps (Thomas):**
  1. Deploy.
  2. On a Monday at 09:00 KST, register the schedule:
     `docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind management_report --interval-seconds 604800 --reason "WMR-1"`.
     The first report arrives one week later.
  3. No `.env` change is needed.
