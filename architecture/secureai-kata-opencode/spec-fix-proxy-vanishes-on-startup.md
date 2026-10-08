---
title: 'Fix: proxy container dies invisibly at session start (stale CA key chmod + --rm log loss)'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `sudo secureai opencode` never starts a session. Two compounding bugs in `secureai`:
(1) Root cause — `cmd_ca_export` (secureai:418) chmods `mitmproxy-ca-cert-key.pem`, a filename mitmproxy 12.x does not create; the real private key `mitmproxy-ca.pem` (and `mitmproxy-ca.p12`) stay `0600 root:1000`, unreadable by the proxy container's unprivileged gid-1000 user, so mitmdump exits at startup. The reuse-existing-CA branch (secureai:394-396) never re-pins permissions, so affected installs are not repaired by re-running `ca export`.
(2) Observability — the proxy runs as `compose run --rm -d` (secureai:613), so its exit auto-removes the container: the wait loop's `exited` early-exit (secureai:631) can never fire, the full 60s burns, and both the failure-path `docker logs` (secureai:639) and teardown's log preservation write only "No such container" — the real mitmdump error is unrecoverable.

**Approach:** In `cmd_ca_export`, chmod 0640 the files mitmproxy 12.x actually writes (`mitmproxy-ca.pem`, `mitmproxy-ca.p12`) instead of the stale name, and hoist the permission re-pin so it runs on BOTH the generate and reuse branches (repairs existing installs without invalidating baked-in trust); fail loudly if the key file is not group-readable afterward. In `cmd_opencode`, stream `docker logs -f` to `${SECUREAI_VAR}/proxy.log` from the moment the proxy is created so the cause survives auto-removal, and treat "container no longer exists" as death: break the wait loop early and print the captured log tail in the failure path. Add structural tests in `tests/test_installer_cli.py` (matching its existing source-grep style) covering the corrected chmod targets, the reuse-branch re-pin, the missing-container early-exit, and the streamed log capture. Keep `--rm` (PRD F10 / AD-14: proxy dies with the agent) — do not switch to `compose up`.

</frozen-after-approval>

## Implementation Notes

- TDD: 7 structural tests added first (`TestProxyDeathVisibility` in `tests/test_installer_cli.py`), watched red, then implementation drove them green. Full suite 77/77.
- `secureai` `cmd_ca_export`: re-pin (chown/chmod) hoisted below the generate/reuse `fi` so it runs on both paths; chmod now targets `mitmproxy-ca.pem` + `mitmproxy-ca.p12` (0640) without `|| true` so a missing key dies loudly; stale `mitmproxy-ca-cert-key.pem` removed.
- `secureai` `cmd_opencode`: `: >proxy.log` + background `docker logs -f` started immediately after `compose run`; wait loop breaks with `vanished=1` when `docker inspect` fails (the only observable form of an auto-removed exit); failure path tails the captured log and uses a distinct die message for the vanished case.
- `secureai` `teardown_session`: log snapshot guarded by `docker inspect` so the daemon's "No such container" error can never overwrite the streamed log.
- Kept `--rm` (PRD F10 / AD-14) — the fix makes death observable instead of preventing cleanup.
- Out of scope (noted, not fixed): install-fedora.sh's SELinux label assertion compares full context (`ls -Zd` field 1) against the bare type string, so it warns spuriously. Separate cosmetic bug.

## Review Triage Log

Blind-hunter pass (diff ~9 kB, finding floor N=4; 7 findings returned):

- `chmod` of `mitmproxy-ca.p12` unguarded under `set -e` — **medium, patched**: split from the `.pem` chmod and `|| true`-guarded like its neighbors; regression test `test_ca_export_p12_chmod_is_guarded` added.
- New die's remediation was a no-op loop (reuse branch re-hits the same die) — **medium, patched**: message now directs removing `${SECUREAI_VAR}/ca` and rebuilding.
- Generate-path die names `${SECUREAI_PROXY_IMAGE}` but the container pulls the upstream tag — **low, deferred**: pre-existing, not caused by this change; recorded in `deferred-work.md`.
- `docker logs -f` PID never captured; teardown snapshot raced the live appender — **medium, patched**: `LOG_STREAM_PID` global, killed and waited before the snapshot; asserted by `test_teardown_does_not_clobber_the_streamed_log`.
- Stream's own stderr could write the daemon's "No such container" into proxy.log if the proxy died before attach — **medium, patched**: stream stderr discarded, empty-log fallback line in the failure path.
- `: >proxy.log` truncates the prior session's log — **low, rejected**: the file is documented as per-session ("this session's proxy log"); a new session legitimately owns it.
- Timeout-branch die lacked the proxy.log pointer — **patched** (both failure messages now name it); "within 0s" phrasing on the belt-and-braces exited path — **low, rejected** (near-unreachable).
- Stream test would pass with a foreground (hanging) `docker logs -f` — **medium, patched**: now asserts the backgrounded append form and `LOG_STREAM_PID=$!`.
- Misnamed reuse-branch test renamed; executable `bash -n` syntax gate added (`test_cli_is_syntactically_valid_bash`).

Final suite: 79/79 green, `bash -n secureai` clean.
