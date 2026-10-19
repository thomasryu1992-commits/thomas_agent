# Option C C6a: the script's tests can no longer touch the host they run on

**What changed:** `tests/test_deploy_runner_c6a.py` gains a module-wide autouse fixture. Under it, a test that
reaches any of these fails with an `AssertionError` instead of acting:
- the network (`urllib.request.urlopen`);
- creating, moving or removing anything under `/opt/thomas-deploy`, `/etc/thomas-deploy`,
  `/var/lib/thomas-deploy` or `/etc/systemd/system` (`Path.mkdir`, `Path.unlink`, `shutil.rmtree`, `shutil.move`).

No case was added or removed, and the script is unchanged.

**Why.** The tests drive `install` and `uninstall` with a fake host, and every command goes through it. A few steps
act on the filesystem directly, though: a2 downloads and `mkdir`s, a4 `mkdir`s, u1 unlinks the units, u2 moves the
records, and u4 `rmtree`s. Correct code never reaches those steps in a test. Mutated code can:
- On 2026-10-19 the scratch fault-injection mutation "dry run acts" ran a2 from the dry-run test. It downloaded
  the real interpreter and left an empty `/opt/thomas-deploy/<"a"×40>` on the production host. The real preflight
  then refuses that directory ("already exists").
- Found the same day before any C6a run. A later injection without `-x` let the uninstall test's u4 remove it.
  It was the only thing there, and it was empty. Nothing else was touched: u1, u3 and u5 act through the fake host,
  and u2 found no records.

**Checked.** All 21 cases pass. Four mutations were re-run under the fixture: dry run acts, existing install
ignored, interpreter not checked, and taken uid ignored. Each was caught by a failing test, three of them at the
fixture, and the host's runner paths, unit files and `/etc/gitconfig` were the same before and after.
