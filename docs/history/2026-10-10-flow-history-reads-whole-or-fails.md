# A Binance cash-flow history is read whole, or the source fails and keeps its cursor

- **What this PR changes (`holdings/binance_wallet.py`, `flow_history`):**
  - Each request names its page size at the documented maximum, through a new `FLOW_PAGES` mapping.
    `FLOW_SOURCES` is unchanged.
  - A full page is read again as two windows. They are split at the median time of the page's rows, or
    at the middle when the rows carry no numeric time. The two windows share that instant, and a row
    read twice is kept once, by its venue id.
  - A read that cannot be shown whole raises `BINANCE_FLOW_HISTORY_INCOMPLETE`. That happens when:
    - `total` disagrees with the rows;
    - a full page sits inside one millisecond;
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
- **Tests:** `tests/test_mvp_runtime_holdings_flow_history_pages.py` covers T1–T16 on a fake venue that
  applies the asked window and page, or its default page when none is asked.
  - Five mutations each fail at least one test: no split, no fold of duplicates, no ceiling, no `total`
    check, no changed-row check.
