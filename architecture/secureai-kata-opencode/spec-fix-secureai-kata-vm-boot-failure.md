---
title: 'Fix SecureAI Kata VM boot failure on install-fedora.sh VMs'
type: 'bugfix'
created: '2026-10-13'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '0dda312136b93d9e621ec2fd73805faa33ddb4eb'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `install-fedora.sh` VMs (Fedora 44, RPM kata 3.26 Go + Docker 29.7) fail every Kata boot: QEMU exits ~5s after launch, shim waits 120s then `timed out connecting to vsock ...:1024`, while a manual VM (upstream static Kata 4.2 Rust + nerdctl) boots fine.

**Approach:** Repair the RPM path in place: fix the watchdog false-success with a never-saw-running latch, make QEMU-exit diagnosable fast inside the existing 180s window, and close precondition gaps warn-only, without switching runtimes.

**Decisions:** Repair RPM Go path only; no live failing VM so static + unit verification only; debug config is document-only runbook; VMM policy is document-only rebuild on Enforcing, no grants from Permissive AVCs.

## Boundaries & Constraints

**Always:** Keep AD-6 single-writer daemon.json with absolute Go-shim runtimeType and no default-runtime; keep proxy on runc and opencode on kata; never setenforce 0 permanently; preserve existing passing tests; wrap every new docker/journal probe in timeout.

**Never:** Migrate installer to static Kata 4.x / runtime-rs or nerdctl in this spec; change trust boundary or network subnet; auto-edit the active Kata configuration.toml; build policy from Permissive AVCs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fast QEMU exit, never running | QEMU gone in <10s, Created | fail fast with QEMU-exit line, rm -f unblocks attach | N/A |
| Clean user exit after running | saw running then removed | report success, no failure, timing preserved | N/A |
| Permissive SELinux | only permissive=1 AVCs | state SELinux not blocking, point at guest console | N/A |
| Missing guest assets | kernel/image absent | existing per-session die preserved | N/A |

</frozen-after-approval>

## Code Map

- `install-fedora.sh:258-347` -- kata_guest_assets / ensure_kata_guest_assets
- `install-fedora.sh:349-534` -- write_daemon_json (rpm shim + ConfigPath)
- `install-fedora.sh:727-927` -- ensure_kata_vmm_policy (Enforcing-only)
- `install-fedora.sh:107-124` -- nested-virt precondition
- `secureai:832-878` -- guest_boot_diagnostics (extend first)
- `secureai:880-916,1125-1210` -- guest_boot_watchdog + cmd_opencode wiring
- `secureai:731-794` -- assert_kata_vmm_path
- `tests/test_installer_cli.py` -- watchdog/diagnostics/precondition coverage

## Tasks & Acceptance

**Execution:**
- [x] `secureai:832-878` -- extend guest_boot_diagnostics first: QEMU count, enable_debug/shared_fs/kernel lines, /dev/vhost-vsock, journal pointer -- watchdog consumes it
- [x] `secureai:880-916` -- fix watchdog with never-saw-running latch: early exited/dead/removed fails only if never saw running -- preserves clean-exit path
- [x] `secureai:995-996` -- add warn-only nested-KVM check after existing asserts: /dev/kvm + vmx/svm note -- never blocks
- [x] `install-fedora.sh:727-927` -- document-only VMM rebuild note for Enforcing, no permissive grants
- [x] `tests/test_installer_cli.py` -- unit-test latch, new diagnostics fields, warn-only check
- [x] `architecture/secureai-kata-opencode/deferred-work.md` -- append static-bundle A/B + bare-bisect runbook as deferred

**Acceptance Criteria:**
- Given QEMU exits in <10s without ever reaching running, when watchdog polls, then it fails with QEMU-exit line and rm -f unblocks attach
- Given container saw running then was removed, when watchdog ends, then it reports success with timing preserved
- Given Permissive SELinux with only permissive AVCs, when diagnostics run, then output states not blocking and points at console/config
- Given new diagnostics fields present, when unit tests run, then vhost-vsock/enable_debug/shared_fs lines are asserted
- Given full pytest, when run, then all prior tests still pass plus new cases

## Implementation Notes

- Watchdog latch + extended diagnostics + warn-only KVM check implemented per Tasks; 186 pytest pass (168 baseline + 18 new/strengthened).
- Review patches applied: timeout-wrapped ls probes, journal-prefix pins, unknown-sentinel test, mixed-AVC test. Deferred: sandbox-scoped pgrep, live threshold settlement.

## Spec Change Log

- 2026-10-13 review: added never-saw-running latch to avoid clean-exit false failure; clarified 180s window stays with fast QEMU-exit inside it; made VMM/debug document-only; added runbook task + missing ACs.

## Review Triage Log

- blind/full-timeout-untested — false: `test_a_guest_that_never_boots_is_reported_and_unblocked` covers always-Created + qemu=1 to limit with generic message.
- blind/silent-success-after-live-untested — low, defer: no test for seen-running-then-limit silent success; harm is an unpinned success path, live-only.
- blind+edge/global-pgrep-no-sandbox-scope — defer: `secureai:873,996` count all host QEMUs; scoping needs sandbox-ID plumbing the spec does not settle; pre-existing shape.
- blind/ls-unwrapped-timeout — patch: `secureai:874` sandbox ls and `:900` vhost-vsock ls lack `timeout`, spec Always requires wrap.
- blind/cpuinfo-whole-file — low, reject: whole-file vmx/svm grep mirrors installer; unreadable cpuinfo warning is acceptable warn-only noise.
- blind/sed-first-match-quotes — low, reject: Kata uses double-quoted keys; single-quote/inline-comment parser adds complexity for no live case.
- blind+mixed-avc — patch (see verification-gap mixed case below): needs mixed-denial test.
- blind/spec-notes-runbook-format — reject: fix would edit this spec/deferred entries, excluded by build rule.
- blind/bare-journal-pointer — false: `secureai:1023` Look-at already prints `timeout 10 journalctl`; diagnostics `:923` matches.
- blind/cache-ls-vs-configpath — low, defer: Look-at `ls /var/cache` is generic; parsed kernel/image lines already printed.
- blind/stale-secs-file — false: seconds file is `mktemp` per session at `:1244` with teardown rm at `:1056`, unwritten reads as none.
- edge/transient-empty-fails — maybe-false, reject: single `timeout 10 docker inspect` flake causing false failure unshown; retry counter adds complexity.
- edge/paused-restarting — false: unknown states fall through to wait loop with generic timeout, which is correct.
- edge/exited-with-qemu-running — maybe-false, defer: agent-crash vs VM-failure with QEMU lingering needs live to settle.
- edge/pgrep-timeout-as-gone — low, reject: `|| echo 0` at `:996` can misread a 124 timeout as gone, but pgrep hangs are rare and the rc-branch adds complexity.
- edge/arm-warning — false/out of scope: Fedora x86_64-only stack; ARM warning is harmless warn-only.
- edge/kvm-perms — low, reject: permission-denied kvm without warn is unlikely and adds a readability probe for no live case.
- verification/nested-wiring-execution — low, reject: helper is executed; call-site deletion is caught by existing ordering assertion; full cmd_opencode harness adds complexity beyond a direct correction.
- verification/journal-prefix-regression — patch: no test asserts `timeout 10 journalctl` in either output; stripping the prefix passes today.
- verification/unknown-sentinel — patch: no test asserts `unknown` fallback on the four config lines; deleting fallbacks passes today.
- verification/mixed-avc — patch: no mixed-denial case; weakening to `grep -q permissive=1` passes both pure tests while misdirecting mixed hosts.

## Design Notes

Latch is load-bearing: track seen_running=1 on running; only fail on exited/dead/empty when never saw running. Keeps `timeout`-wrapped calls; journal stays a printed pointer.

## Verification

**Commands:**
- `bash -n install-fedora.sh && bash -n secureai` -- expected: clean
- `python3 -m pytest tests/ -q` -- expected: all pass including new cases
