# The research sandbox never starts in or just before the factory window

- **Why:** on 2026-10-21 the official OI re-score emit (`20261021T081206Z`) ran from 08:12 to 08:21 UTC, inside
  the window where the daily factory fires. It fetched nothing and was capped (CPU 50%, nice 10, ionice idle),
  and the factory kept firing. Still, the two shared the host's two cores for nine minutes, and how much the
  factory slowed was not measured. `fetch_public` already refused the window. The runner did not.
- **What:** `sandbox_run.sh` refuses to start from 30 minutes before the window until it ends (07:30–09:00 UTC).
  The runs so far took about 10 minutes. Starting at 07:29 is still allowed: the rule bounds the start, not the
  end.
- **Checked:** a behavioural test takes its bounds from `fetch_public.FACTORY_WINDOW_UTC`. It covers both edges
  and the lead, and checks that a refused run stops before anything else. Removing the `exit` fails it, and so
  does removing the lead. The test is Linux-only, so the win32 skip ceiling goes from 676 to 677.
