# measure_oi_lookahead builds frames once per context and alignment

- **Why:** the first container run (2026-10-20) selected 373 candidates across 15 contexts. The script rebuilt
  both feature frames for every candidate, at about 12 s per 96,000-bar 15m frame, inside the live
  `thomas-scheduler` container, which has no memory limit on a host with about 1 GB free. It was stopped before
  finishing; being read-only, it left nothing behind.
- **What changed:**
  - `holdouts(specs, snapshots, known_after_hours)` builds each context's frames once per alignment and replays
    every spec of that context over them. That is about 30 builds instead of about 1,100.
  - Snapshots are cached one timeframe at a time.
  - Progress per context goes to stderr, so `--json` stays parseable.
  - The reading, selection and output are unchanged.
- **Test:** three candidates in one context cost exactly two frame builds (L and F). Restoring per-candidate
  builds fails it.
