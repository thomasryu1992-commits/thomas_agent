# Capture door: root takes the official-page screenshots the `claude` account can no longer take

- **What:** `scripts/ops/capture_door.py` plus two systemd units (`scripts/ops/capture_door_units/`). The
  `claude` account drops a capture request into `/var/lib/thomas-capture/req/`. A path unit starts one root pass
  that validates the request and takes the screenshot with the same image and in-container script as
  `blog-capture.py`. The pass hands back only the raw screenshot and a result record in `out/`. The client
  side, in the server-only `~/.claude/scripts/blog-capture.py` (claude's local git, not this repository), uses
  the door only when `docker version` fails. It composes the PNG and its `thomas-capture` metadata exactly as
  before, so blog-preflight Y3 reads the same record. The skills do not change.
- **Why (Thomas 2026-10-20, "승인: Y3 (가)"):** C6b moved Claude to the `claude` account, outside the docker
  group by design, because that group reaches every mount. The 2026-10-20 test-draft run then showed
  `blog-capture.py` failing with a Docker socket permission error. Every new Naver draft therefore lands as `hold`
  under preflight Y3. (가) keeps the C6b boundary: `claude` still cannot reach the Docker socket (b8 N-2 unchanged).
  The other options were (나), sudo for one container, which is root-adjacent, and (다), relaxing Y3.
- **What root trusts, and nothing more:**
  - The request is read with `O_NOFOLLOW` and must be a regular file owned by `claude`, at most 4 KiB.
  - The id is 32 hex characters and equals the file stem.
  - The URL is `https`. Its host is a name, not an IP literal, and outside the blocked list (the same list as
    blog-capture: Naver, Tistory, Daum, Coupang), and every resolved address is global.
  - The text is 1–200 characters and the click text at most 60, with no control characters.
  - At most 30 captures an hour, and 5 requests per pass.
  - A rejection is a fixed code. The request's contents are never echoed into a result or the log, so a request
    that is swapped for a link cannot carry back out what root read.
  - The page's own output is untrusted too: only `ok`, `status`, `reason`, `final_url` and `title` are passed on,
    bounded. The screenshot must start with the PNG signature and be at most 10 MiB.
- **Hardening over the old tool:** `--ipc=host` is gone, replaced by `--shm-size=1g`. Added:
  `--cap-drop ALL`, `no-new-privileges`, `--pids-limit 512`, `--memory 1g`, `--cpus 1`.
  - The unit has `ProtectHome=yes` (the door never touches `/home`), `ProtectSystem=strict` and write access
    only to `/var/lib/thomas-capture` and the Docker socket.
  - The unit has **no `PrivateTmp`**, and the work directory is never under `/tmp`. dockerd resolves `-v` in the
    host's mount namespace, so a private tmp would silently mount the wrong directory.
  - The pass refuses to run at all unless `req/`, `out/` and `work/` are root-owned with the installed modes.
- **Residual risk, unchanged from before C6b:** the container reaches the network, so DNS rebinding or a host-local
  service stays reachable. Root Claude ran this same tool with the same reach until 2026-10-20. The door does not
  widen that reach.
- **Race handled:** the client writes `.<id>.tmp` and renames it to `<id>.json`. A pass skips dot names and removes
  them once they are a minute old, so it never reads a half-written request.
- **Not done here:**
  - Drafts already held for a missing capture are not re-captured. That pass edits drafts and is a separate step.
  - The door is not added to the C6b b8 "reached" rows.
  - **Claude did not execute the root or Docker half.** It is tested with the Docker runner injected (25 cases,
    `tests/test_capture_door.py`). The layout check was run against the install's modes made in a scratch
    directory, and the units pass `systemd-analyze verify`.

## Install (Thomas, as root, after merge)

```
docker image inspect blog-capture:1 --format '{{.Id}}'
git -C /root/thomas_agent fetch -q origin main
git -C /root/thomas_agent show origin/main:scripts/ops/capture_door.py > /usr/local/sbin/thomas-capture-door && chown root:root /usr/local/sbin/thomas-capture-door && chmod 0755 /usr/local/sbin/thomas-capture-door
install -d -o root -g claude -m 0750 /var/lib/thomas-capture
install -d -o root -g claude -m 1730 /var/lib/thomas-capture/req
install -d -o root -g claude -m 0750 /var/lib/thomas-capture/out
install -d -o root -g root -m 0700 /var/lib/thomas-capture/work /var/lib/thomas-capture/docker-config
for u in path service; do git -C /root/thomas_agent show origin/main:scripts/ops/capture_door_units/thomas-capture-door.$u > /etc/systemd/system/thomas-capture-door.$u; done; chmod 0644 /etc/systemd/system/thomas-capture-door.*
systemctl daemon-reload && systemctl enable --now thomas-capture-door.path
python3 -I /usr/local/sbin/thomas-capture-door; echo "exit=$?"
```

- The first command shows whether the image root used before C6b is still there. If it is missing, the first
  pass builds it (about 2–5 minutes), so the first client call may time out once.
- The last command is an empty pass. It must print nothing and `exit=0`; `exit=2` means a directory's owner or
  mode differs from the lines above.
- **End-to-end check (Claude, as `claude`):** one `blog-capture.py` call against a known-good official page, then
  `ls /var/lib/thomas-capture/out/` (a `.json` and a `.png` for that id) and the PNG's `thomas-capture` metadata.
- **Revert:** `systemctl disable --now thomas-capture-door.path`, then remove the two units, the script and
  `/var/lib/thomas-capture`. The client falls back to recording the failure, and drafts become `hold` as they do
  today.
