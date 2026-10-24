# A kill that lands mid-run stops the run's next external effect

- **Why:** an outside review (§3.2, 2026-10-23) read the governance as "KILLED blocks external execution" and the
  workflow as "in-flight attempts run to their end". The code confirmed it: `workflow_manager` claimed nothing
  while halted, but an attempt already running went on making model calls, search requests and the
  keyword brief. The worker had checked the switch only when the frame arrived. Nothing it did was trading,
  but it was new external effect after a kill.
- **What:**
  - `pipeline.run_task(halt_check=...)` calls the hook before every stage that reaches outside the process:
    search, analysis, automatic validation, independent validation, revision and the controlled write. It
    also calls it before the Naver keyword brief. Its `ControlBlocked` becomes the run's BLOCK through the
    existing outer `except MvpRuntimeError`.
  - Unlike `on_progress`, the hook's exception is not swallowed.
  - `pipeline_worker._assert_running` now holds the worker's two arrival checks. The dispatch path and the
    content-ideation job path both pass it as `halt_check`, so it reads the switch at call time.
  - `blog_content.run_content_ideation` forwards the hook into each of its runs.
- **Checked:**
  - A worker test: the hook handed to the run passes while running and raises once a kill is applied mid-run.
  - A unit test: `_enter_stage` stops before reporting the stage, and still swallows a broken notifier.
  - Two Core-gated tests run in CI. In the first, a kill after the search BLOCKs with no model call. In the
    second, an unhalted run checks before every effect stage.
  - Mutations fail tests: the stage skips the check, or the worker passes no hook.
