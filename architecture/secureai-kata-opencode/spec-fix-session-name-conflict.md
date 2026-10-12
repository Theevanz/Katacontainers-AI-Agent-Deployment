---
title: 'Fix: guest start collided with the proxy container name (compose dependency auto-start)'
type: 'bugfix'
created: '2026-10-12'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With the proxy finally healthy, every session died at the guest start: `The container name "/secureai-proxy" is already in use`. `docker compose run` starts the target service's dependencies unless told otherwise, and the proxy service declares `container_name: secureai-proxy` — the same name the CLI had already used to create that container by hand. Compose therefore tried to create the proxy a second time and the daemon refused. A second, latent variant of the same failure: a session killed mid-flight leaves containers holding those names, so the next session cannot claim them either.

**Approach:** Pass `--no-deps` on the guest run — the CLI has already started and health-gated the proxy, which is a stronger enforcement of the same contract than compose's `depends_on` — and clear any leftover container holding either session name before the first `compose run`, reporting the removal and dying with a readable message if it cannot be removed.

</frozen-after-approval>

## Implementation Notes

- Root cause reproduced against real compose (v2.27.1) with a toy project mirroring `docker-compose.yml`: `container_name` + dependency auto-start is what produces the Conflict; without `container_name` there is no conflict, which is why a first attempt at the repro appeared to pass. `--no-deps` skips dependency startup, still creates the network, and leaves the hand-started proxy running.
- The flag is `--no-deps` (plural). `--no-dep` is rejected as an unknown flag by compose.
- `secureai` `cmd_opencode`: pre-flight `docker inspect` + `docker rm -f` for both session names, before the traps are armed (so a failure there cannot orphan a container), with an `ok` line when something was actually removed and a `die` naming the manual fix when removal fails.
- Red/green: two tests (`test_guest_run_does_not_restart_the_health_gated_proxy`, `test_session_start_removes_leftover_containers_first`); the second initially anchored on the first `compose run`, which matched a *comment* earlier in the function — fixed to anchor on `compose run --rm`. Suite 85/85, `bash -n secureai` clean.
- Not changed: the health contract. `--no-deps` only removes compose's redundant dependency start; the CLI's own bounded wait is what guarantees the proxy can serve before the guest starts.

## Review Triage Log

- No review layer run: every claim here was executed against real compose and the live proxy image rather than reasoned about, and the change is two edits plus tests.