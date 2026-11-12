# 2026-11-12 — the spread observation: one request a slot, and the first research fetch that runs on a schedule

`docs/proposals/SPREAD_OBSERVATION_V0.1.md` (DECIDED), `scripts/research/spread_sample.py`.

**Why it is built this small.** The draft's whole argument was that a missed day cannot be bought back, so the
tool's job is to be boring enough to leave running: one keyless request for the whole venue, at most one per
invocation on every path, and nothing written when the budget, the window or the answer says no. It rides
`fetch_public.Budget` instead of restating the sandbox's rules, so a change to those rules reaches it.

**The endpoint was called once before the parser was written.** The draft said it had not been called. The first
call (804 symbols, used weight 5, prices as strings, one quote 28 minutes old) fixed three things the draft had
guessed at: the size of a line (52 KB, not the 40 KB estimate), the weight, and that a quote carries its own time.
That last one is why each row keeps the venue's time: a halted symbol answers with a stale quote that looks live.

**All symbols, not the universe.** Membership of a month's top 30 is known only after the month. The answer
already holds every symbol, so keeping all of them costs nothing extra in requests.

**Why the slot check comes before the request.** A manual run beside the cron, or a cron line fired twice, must not
spend the IP's weight for a row that is already held. The slot is the wall clock's 15-minute period, read from the
day's file first.

**What it is the first of.** Every research fetch so far was run by hand. This one runs from the `claude`
account's crontab, from a folder pinned at the merged commit. Thomas decided it sits where Deribit sits (outside
the env gate list, because the runtime does not read it); the sandbox doc now says which fetch is scheduled.

**The stall question is only half answered.** The monthly refresh prints the coverage on one line, so a dead
sampler shows within a month. An alert within a day would have to live in root's watch, which is a blog watch
and root's to install. That is asked as P6 after the first month shows how often samples are actually missed.

**The install, and two things it taught.** The code is pinned as a `git archive` export of the merged commit, not
as a worktree: merged worktrees get swept on this host, and an export cannot drift with a branch. The exact cron
command was run once under an empty environment before it was installed, which gave the first real sample; then
two consecutive cron slots were watched. The windows suite had failed on the cron-line test because the line was
built from `Path` objects, which spell themselves with backslashes there; the line is for a Linux crontab, so it
now spells its paths the POSIX way on every platform. And `crontab <file>` could not read a file under the
session's scratchpad (it reported a truncated path), so the new table was installed from `~/research/spread/`.

