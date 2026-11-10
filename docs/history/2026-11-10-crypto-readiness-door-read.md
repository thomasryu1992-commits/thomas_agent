# 2026-11-10 — The Claude read door answers `crypto_readiness` through a closed contract

**What.** The Claude door (`claude` profile) gains a fourth read. `crypto_readiness` was in the bridge's table all
along and was held out of every profile because it answered raw (review D-11 f2). It now has reply contract v1,
`store_reads.project_crypto_readiness`, and leaves the held set.

Changed:
- `runtime/mvp_runtime/store_reads.py`: the contract, its closed schema, and `read_crypto_readiness`.
- `runtime/mvp_runtime/read_bridge.py`: the profile and its admissible set; `apply_read` projects under a profile.
- `scripts/claude_read.py`: the client's read list.
- `tests/test_mvp_runtime_read_bridge_crypto_readiness.py`, and the two tests that pin the profile's reads.
- `tests/skip_ceiling.json`: win32 +1 for the one POSIX-only test.

**Why.** Every design review of the crypto lane written by a Claude session since C6b quotes the execution stage,
the gate and the armed count from a document, because the account has no docker and no crypto state
(`CRYPTO_DESIGN_REVIEW_V0.1.md` §2, item 3). Thomas, 2026-11-10: work through the review's options in order; this is
part of the first.

**What the reply carries.** `live_entry_possible` (yes, no or unknown), each readiness component's state and reason
code, which block and which are unknown, the execution stage with its policy version and whether it binds, the armed
and occupying counts, the trading process's recorded gate (open, when, stale), and four venue-contract booleans.

**What it never carries.** The board's rendered text, a check row's detail, an error message, a stage id, a hash, an
approval id, a symbol, the day's order count, an amount, an env value. A value outside the closed vocabulary is
dropped and the reply says `v1-partial`. The two lists are rebuilt from the projected components, because the
board's own entries are `name:REASON` strings.

**Why the door's own environment cannot mislead it.** The door's container carries no `MVP_LIVE_*`. The readiness
model (v2, PR5a) already answers for that case: the gate component reads the trading process's last record, and what
the process cannot see is `unknown`, never "closed" read off an empty environment. A test sets and unsets the opt-in
around the read and pins it.

**Why it goes through the domain console.** `domain_console` is the core's dispatch site into a lane. The
projection takes the board's structured data and imports no crypto module; its two closed lists are literal and a
test holds them equal to the lane's constants.

**What this is not.** Not a new capability, not a policy change (the read never consulted the policy), and no key:
the door mounts the state root read-only. It takes effect only when the image is deployed; until then the door
refuses the name.
