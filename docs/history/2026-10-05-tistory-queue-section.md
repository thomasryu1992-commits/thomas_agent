# A Tistory fire takes its queue seeds from the queue's "티스토리 전용" section

- **Why.** Thomas asked for a weekly Tistory row (2026-10-05). As built in #1123,
  `platform=tistory, source=queue` read the same "<lane> — 다음 편 후보" sections as Naver. Its top
  pick that day was '개인사업자정책자금', a 실무 keyword. The vault's 2026-10-03 benchmark found
  0/8 실무 keywords with a blog slot in Google's top ten. Thomas decided the same day that the
  Tistory-only slot is for AI-tool intents.
- **What changed.**
  - `VaultKeywordQueue(root, platform="tistory")` reads the "## 티스토리 전용" section that
    `kw_pipeline` writes: tool name plus 요금제·무료 한도·해지·환불·오류·비교.
  - The section's items are `keyword(volume)`, and the volume is the score.
  - A "자리 없음" line is never a candidate.
  - Without the section, the read fails closed (`KEYWORD_QUEUE_UNAVAILABLE`).
  - Naver reads exactly what it read before.
  - `selection_evidence.seed_source.section` records which part was read. It is an optional,
    additive field on v0.3; earlier v0.3 rows stay valid.
- **The row.** `schedule_d7972fb2ea02529f73ba` (`platform=tistory, source=queue`, weekly) was added
  **disabled** on 2026-10-05. It is to be enabled once this is deployed.
- **Limit.** The section's slot values are those from the day the queue was built; the vault's own
  `kw_pipeline.py tistory-pick` re-measures live, and the runtime cannot. A measured "자리 없음" is
  still excluded.
