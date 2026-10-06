# System scorecard re-measured: 6 → 7, after all six v0.1 questions were handled

- **What:** `docs/proposals/SYSTEM_SCORECARD_V0.2.md`, a RECORD scored on v0.1's rubric a day later (Thomas
  asked for a re-score once Q1–Q6 were closed).
- **Moved:** ops and secrets 5 → 8 (the core backup leaves the host age-encrypted and the Mac proves the key
  daily); analysis/content lanes 7 → 8 (no failover in the 10 runs since the chain change, p95 down by half);
  development process 6 → 7 (pending proposals 13 → 5, one of them needing a Thomas answer).
- **Did not move:** safety 9, core reliability 8 (the Windows flake fix needs weeks of runs to show), crypto
  edge 4 (a new pair measurement puts 1d members below random entries on the same bars), product value 5 (no
  outcome data until the Tistory reading on 10-31).
- **A trap noted in the record:** a 24-hour digest window that straddles the chain change reports content
  p95 at 109.8 s; counting from the change gives 23.1 s.
