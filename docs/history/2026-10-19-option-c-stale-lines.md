# Option C: three lines that still called settled things open

**What changed** (documents only, `HOST_PRIVILEGE_SEPARATION_V0.1.md`).

**Basis:** Thomas 2026-10-19, "별도 승인을 제외하고 남은 일을 순서대로 진행해줘". This is the cleanup named
when #1244 was opened. Merge is still a separate approval.

Lines changed:
- §4.25.3, the clock row of the table of differences from the specification: dated notes say the source
  is the kernel (CLOCK-SOURCE = A, #1240) and the values were decided (#1244).
- §4.25.4, "아직 없는 것": CR-10·11·19 removed; it points to §4.25.1 and #1244.
- §4.25.5:
  - "아직 없는 것": the same change;
  - "#1242(열림)" now reads merged (`50dcc534`).

**Order.** The CR values' decision row is added by #1244. This PR refers to it, so it merges after #1244.
The two do not conflict.

**Not changed.** No decision made or reworded. The decision rows of §4.25.1 are a dated record and stay as
written.
