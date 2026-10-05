---
name: deploy
description: Deploy a merged PR to the running stack on this host — preflight, rollback tag, candidate build, in-image assertion, second preflight, promote, compose up, post-deploy check. Use when asked to deploy (배포) a PR or ship origin/main to the running containers.
---

# Deploy a merged PR

The rules are CLAUDE.md's **Deploying** section. This is the order to run them in. The two
preflight runs re-read the host at the points where concurrent sessions have caught us out
before. The preflight is read-only (`scripts/ops/deploy_preflight.py`, docstring says why each
check exists). A `STOP` means stop and report. Never work around one.

1. **Clean tree first:** `git -C /root/thomas_agent fetch -q origin main && git -C /root/thomas_agent worktree add /root/deploy-<N> origin/main --detach`.
   Every later step runs the preflight from this tree. The primary checkout is often far behind
   origin/main and may not have the script at all (2026-09-28: 42 commits behind, no script).
2. **Preflight:** `python3 /root/deploy-<N>/scripts/ops/deploy_preflight.py <N>`. The `rollback`
   line names what to tag from: `latest`, or an older `rollback-pre-*` when another session built
   without deploying. On a STOP, remove the tree and report.
3. **Rollback point:** `docker tag thomas-agent-runtime:<from> thomas-agent-runtime:rollback-pre-<N>`.
4. **Build:** `docker build -t thomas-agent-runtime:candidate-<N> /root/deploy-<N>`. Never `compose up --build`.
5. **Assert in the image:** call the changed behaviour, don't grep for it:
   `docker run --rm --entrypoint python thomas-agent-runtime:candidate-<N> -c "…"`. A docs-only PR
   needs no deploy. Say so and stop.
6. **Preflight again:** `python3 /root/deploy-<N>/scripts/ops/deploy_preflight.py <N> --promote --tree /root/deploy-<N>`.
   A `promote` STOP means another session redeployed after your tag. Re-read the host. If its
   image already contains your merge, verify that deploy instead of racing it.
   A `fires` STOP means a scheduled fire is running and `up -d` would kill it (it is not retried
   until its next interval). Wait for its `fired`/`failed` event and run this step again. Factory
   fires run daily around 08:09–08:52 UTC; at `--promote` a factory fire due within 10 minutes
   also stops.
7. **Promote and up**, in one step:
   `docker tag thomas-agent-runtime:candidate-<N> thomas-agent-runtime:latest && docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f /root/deploy-<N>/docker-compose.yml up -d`
8. **Verify:**
   - `docker inspect thomas-scheduler --format '{{.Image}}'` equals candidate-<N>'s id.
   - Every container in `docker ps` is healthy.
   - For a scheduled lane, wait for its next fire by an event condition: the last event is
     `fired` and its `created_at` is after the deploy time. Never wait on a minute string: fires
     jitter around :14/:29/:44/:59.
   - The first crypto fire after a restart may lack an account snapshot (the 900 s rule). That is
     expected.
9. **Clean up:** `git -C /root/thomas_agent worktree remove /root/deploy-<N>`. Remove any orphan
   candidate you built but did not promote.
10. **Report:** the PR, the candidate and rollback tags, the running image id, and what step 5
    asserted.

**Roll back:**
`docker tag thomas-agent-runtime:rollback-pre-<N> thomas-agent-runtime:latest`, then the same
compose command from a clean origin/main tree. If the rollback tag is gone, rebuild the previous
commit to a candidate tag and promote that.
