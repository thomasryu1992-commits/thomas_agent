# The deploy runner can be upgraded in place: `deploy_runner_c6a.py upgrade`

- **Why:** #1307 retired four containers, and the installed runner (1bad6dc1, 2026-10-19) still counts them among
  its runtime containers.
  - A `prepare_candidate` now refuses with `RUNNING_IMAGES_INCONSISTENT`, so the runner fails closed but cannot
    be used.
  - Nothing could move it: `install` refuses a host where the accounts exist (and since C6b, `claude` always
    does), and `uninstall` keeps `claude`.
  - Thomas approved an upgrade subcommand on 2026-10-26: design, implementation and PR; synthetic-path tests only;
    Thomas runs it.
- **What it replaces:** the per-install part only.
  - `/opt/thomas-deploy/<sha>`: the CR-8b interpreter, the venv and the code.
  - Both configurations: `install_sha`, the new interpreter's fingerprint, and the store_id and host_label they
    already named.
  - Both unit files.
- **What it never touches:** the accounts, the trust files, the actor's consumption store and audit chain, the
  gate's records and the trees.
  - An approval spent before the upgrade stays spent: the store is read by its store_id, and audit rows carry their
    own install sha.
- **Order:**
  - b0 copies the current four files aside.
  - b1–b3 build and stage while the old runner still serves.
  - b4 stops gate then actor, swaps the four files and runs `serve --check` as X and G against the new install.
  - b5 starts actor then gate.
  - b6 is the install's own gate probe: an inspection as claude succeeds, and the same frame as root gets
    `PEER_NOT_PERMITTED`.
  - If b4–b6 fail, the previous four files go back and the previous runner restarts. Its install stays on disk.
- **Going back:** `upgrade --sha <previous> --reuse-installed` switches to an install already on disk, with the
  same checks.
- **Shared with install:** a2/a3 (interpreter, code) and a9 (the probe) are now functions that install and upgrade
  both call. The install's own tests are unchanged and pass.
- **Checked (synthetic paths):**
  - The preflight refuses 13 cases before acting.
  - Dry runs list b0–b5 in order and change nothing.
  - A real switch under tmp_path rewrites configs and units for the new sha and keeps the store_id, the
    consumption store and the trust anchor byte for byte.
  - A failed start check (actor or gate), a failed `daemon-reload` after the swap, or a failed probe restores all
    four files byte for byte and restarts actor then gate.
  - The real-host guard now also refuses `os.replace`, `copyfile` and `copy2` onto runner paths.
  - Seven mutations were each caught: configs not swapped, restore skipped, a new store id, actor stopped first,
    an existing target overwritten, a failed check ignored, and no restore on a failed switch.
  - The real runner's files were unchanged after the mutation runs.
- **Not tested here:** systemd and Docker for real. The C4 workflow runs install/uninstall on a throwaway runner;
  adding an upgrade step there is a workflow-file change, which is Thomas's.
