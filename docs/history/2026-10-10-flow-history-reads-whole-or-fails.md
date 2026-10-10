# A Binance cash-flow history is read whole, or the source fails and keeps its cursor

- **What this PR changes (`holdings/binance_wallet.py`, `flow_history`):**
  - Each request names its page size at the documented maximum, through a new `FLOW_PAGES` mapping.
    `FLOW_SOURCES` is unchanged.
  - A full page is read again as two windows. They are split at the median time of the page's rows, or
    at the middle when the rows carry no numeric time. The two windows overlap on that instant and the
    next, and a row read twice is kept once, by its venue id. A full page whose `total` equals its rows
    is not split.
  - A read that cannot be shown whole raises `BINANCE_FLOW_HISTORY_INCOMPLETE`. That happens when:
    - `total` is missing or not a whole number on a page with rows, or disagrees with the rows;
    - a full page (without a matching `total`) sits in a window under 3 ms, too short to split;
    - one id comes back with different content in two windows;
    - more than `FLOW_MAX_REQUESTS` (8) requests or `FLOW_READ_SECONDS` (15) are needed.

    A failed or timed-out request fails the whole read as before, and nothing read before it is
    returned. `collect_binance` already turns a raised read into that source's `FAIL <code>` and
    advances only the cursors of sources that read cleanly. The source is read again from the same
    cursor at the next fire.
- **Why:** found in the C7 review (#1209), 2026-10-09. Each history was one request with no page
  argument, so the venue answered its default page and dropped the rest without a word. The cursor still
  moved to `now`.
  - Universal transfer defaults to 10 rows, Pay to 100 and fiat to 100.
  - Against the previous code, the new tests show it: 15 transfers in a window read as 10, 150 Pay
    rows read as 100, and the ledger-level fire reports no error and moves the cursor.
- **The venue's contract (Binance developer docs, read 2026-10-10):**

  | Source | Page parameter (default / max) | `total` | Weight |
  |---|---|---|---|
  | deposit | `limit` 1000 / 1000, `offset` | no | IP 1 |
  | withdraw | `limit` 1000 / 1000, `offset` | no | UID 18000 |
  | fiat orders | `rows` 100 / 500, `page` | yes | UID 45000 |
  | fiat payments | `rows` 100 / 500, `page` | yes | IP 1 |
  | Pay | `limit` 100 / 100, no page | no | UID 3000 |
  | universal transfer | `size` 10 / 100, `current` | yes | IP 1 |

  No page documents its sort order or whether its time bounds are inclusive. That is why the read
  splits time and does not page by number or offset: a row inserted between two pages would shift an
  offset and skip a row without trace.
- **Shaped this way because:**
  - The normal case stays one request per source, as before. Splits happen only on a full page, so the
    heavy endpoints (withdraw, fiat orders) cost no more in an ordinary hour.
  - A median split cuts a burst inside a long window in a few reads. The first draft halved at the middle,
    and the ledger-level test ran past its ceiling: 150 transfers within one hour of a 26-hour re-read.
  - The ceiling is small for the same weights.
- **Not changed:**
  - **The 7-day transfer window.** The code comment calls it the documented longest window. The current
    universal transfer page names no longest window: it supports the last 6 months, and 7 days is what it
    returns when no times are sent. Widening it would widen the C7 re-read reach, and it is left for a
    separate decision.
  - **Past reads.** This prevents new silent loss. It does not find or restore rows an earlier read may
    already have dropped; that needs the ledger and is part of the C7 work (#1209). Whether any
    production read was cut is not known.
- **Follow-up review (same day): four defects in the first version, reproduced, then fixed.**
  - **`total` was optional.** A transfer or fiat page whose `total` was missing, null, text, a boolean or
    a fraction was read with no check. Now such a page with rows is `INCOMPLETE`. Only a quiet window,
    with no rows, may come without a total.
  - **Rows that are not objects were dropped silently.** On Pay and deposit, nothing caught it. Now the
    page is `MALFORMED_RESULT`.
  - **A page exactly full was always split.** That cost 3 requests where 1 does. It also failed 100
    transfers on one instant whose `total` said 100. Where the venue gives `total`, a page whose `total`
    equals its rows is whole. Without `total` (Pay, deposit, withdraw), a full page is still split, and a
    full page on one instant still fails.
  - **The halves shared one instant.** A venue that leaves both ends of a window out would lose the row on
    the seam: 2 of 150 in the test. The halves are now `[start, middle + 1]` and `[middle, end]`, which
    cover every row under all four readings of the bounds.

  The request budget was already per source and per read. A source over its budget leaves the others
  whole, and that is now pinned. Each fix has a mutation that fails its tests.
- **Final integrity review (same day): three more paths, each reproduced first, then fixed.**
  - **The asked window's own ends.** The halves now overlap, but a row exactly on the first or last
    instant of the caller's window was lost whenever the venue leaves an end out. That happened in 12 of
    16 cases: `[)`, `(]` and `()`, one page or split, transfer and Pay. The read now asks one instant
    wider on each side. It does not widen when that would pass the source's longest window (the probe asks
    exactly that), and then those two instants stay unguaranteed. `collect_binance` always leaves 60 s of
    room, so its reads are always widened.
  - **Rows without their venue id.** They were folded by their content, so two different transactions
    alike became one. The read now folds only by id. Rows without one are all returned, for the
    normalizer to record as malformed (the existing policy, which also folds identical malformed rows by
    digest). A read of more than one piece that holds one is `INCOMPLETE`: the pieces overlap, so one row
    read twice and two rows alike cannot be told apart.
  - **Fiat and Pay failure bodies.** Their `code` and `success` were not read, so `code 100001,
    success false, data []` was an empty history and the cursor moved. Now the body must say it
    worked, with `code` `000000` or `success` true, and must not say otherwise. Anything else is the
    existing `BINANCE_WALLET_REJECTED`, carrying the venue's code only, never its message. Accepted: one
    field missing while the other says it worked. Refused: both missing, or a contradiction.
- **Tests:** `tests/test_mvp_runtime_holdings_flow_history_pages.py` covers T1–T16 on a fake venue that
  applies the asked window and page, or its default page when none is asked.
  - Five mutations each fail at least one test: no split, no fold of duplicates, no ceiling, no `total`
    check, no changed-row check.
