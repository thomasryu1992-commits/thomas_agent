# The LOCAL_ONLY guard judges a Monitor command as it judges Bash

- **Why:**
  - After the guard was installed globally (2026-10-09), a live check found that the `Monitor` tool, which
    runs a shell command in the background, was matched but only half judged.
  - The guard read the command text for `Bash` alone. For every other tool it judged the input as JSON text
    for paths and file names. So a Monitor command reached the terminal-only `holdings_board` modes, and inline
    Python that imports the holdings package, both of which Bash refuses.
  - Thomas approved fixing exactly that mismatch.
- **What changes:**
  - `.claude/hooks/guard-local-only.sh`:
    - A Bash or Monitor input with a string `command` is judged as a command: paths, file names, a
      recursive read of the state root, the terminal-only modes, and the holdings import.
    - Monitor's other form, `ws`, opens a WebSocket and runs no shell, so it gets the path checks only.
    - A Bash or Monitor input with neither form is refused, because it is not a shape the guard can read.
    - A payload that does not parse is still judged raw, as a command.
    - The file tools are judged as before.
  - `.claude/settings.json`: the guard's matcher gains `Monitor`. This matches the user-level install.
  - `tests/test_claude_local_only_guard.py`: 40 new cases.
    - The ten terminal-only modes, from both Bash and Monitor.
    - The import, paths and a recursive read through Monitor.
    - An unknown or non-string Monitor input.
    - The allowed Monitor commands: the one table, `--json`, a log tail, a CI poll, a description that
      mentions a file, an ordinary socket.
    - Bash and Monitor giving the same answer.
    - The settings wrapper, with the guard and without it.

    Against the previous guard, 19 of them fail.
- **Not changed:** the guard's rules for Read, Grep and Glob, the fallback in the wrapper, and any runtime
  code.
- **Applying it:** a merge updates the repository copy. The user-level copy at
  `/root/.claude/hooks/guard-local-only.sh` is Thomas's: he reinstalls it from main and checks that its
  sha256 matches.
- **Still not covered:** tools outside the matcher (Edit, Write, MCP terminal or browser tools), `--bare`
  runs, sessions started without user settings, and everything the guard's own header lists. This is a
  tripwire, not a boundary.
