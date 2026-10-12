---
title: 'Continue sprint from status: close implementable gaps for 1-7, 2-6, 3-6, 4-4'
type: 'feature'
created: '2026-10-12'
status: 'done'
review_loop_iteration: 0
baseline_revision: '2334d9fb78973f6f55d38cf369b5ea8ffadf5a63'
followup_review_recommended: true
context:
  - 'architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md'
  - 'architecture/secureai-kata-opencode/epics.md'
warnings:
  - multiple-goals
  - oversized
deferred: []
---

<intent-contract>

## Intent

**Problem:** Sprint status shows Epic 1–5 in-progress with done substrate, but investigation finds four implementable gaps on this host: 1-7 provider SDKs are a comment-only mkdir, 2-6 health never loads keys in-process, 3-6 list has zero tests, and 4-4 timing ignores cold/warm and judges proxy-only against the 30s budget.

**Approach:** Close those four gaps in dependency order (image, then health, then CLI surface, then timing) with build-failing guards and structural tests, leaving Epic 5 live-guest probes as runnable-but-UNVERIFIED code that must not claim pass here.

## Boundaries & Constraints

**Always:**
- `install-fedora.sh` stays sole writer of `daemon.json` (one `kata` runtime, no `default-runtime`); CLI stays sole writer of `/etc/secureai`; proxy mounts `/var/lib/secureai` `:ro`; compose refs stay `${...}` only.
- `proxy: runc` and `opencode: kata` pins preserved; CLI passes no `--runtime`; `dns:` stays on opencode; no baked `/etc/resolv.conf`; no `127.0.0.11` in shipped tree; `NO_PROXY` exact.
- Addon stays one `SecureAIAddon` class; `tls_clienthello` observes only; enforcement at `requestheaders` via 403 `blocked by secureai`; fail-closed with `blocked` vs `config-error` distinct; injection logs `authz=injected provider=<host> fp=<4-hex>` only, never header values.
- CA order preserved: `ca export` before `docker build`; CA baked, never bind-mounted at runtime; trust extends store, never replaces; `NODE_EXTRA_CA_CERTS` absolute concatenated bundle.
- CLI stays bash, no `yq`; `whitelist add` audit+sync preserved; `keys list` fully masked.

**Never:**
- Never claim any verify gate passes on this host; gates report FAIL/UNVERIFIED with cause; never claim gate (c) proves no bypass; never claim `internal: true` guarantees anything; never add a network backstop.
- Never treat Bun `NODE_EXTRA_CA_CERTS` as proven; gate (d) failure reopens 2-5; never ship a concatenated-bundle fallback silently.
- Never fix a 30s miss with tuning; record as PRD amendment (NFR14); never report timing as a guarantee.
- Never introduce per-user install, `~/bin` shim, systemd proxy unit, `context=` mounts, or second Zone-A-to-internet path.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Build without pre-seeded SDK | `~/.cache/opencode/bin` empty of `@ai-sdk/*` binaries | build fails naming missing SDK set | fail before commit, not at runtime |
| Build as wrong UID | `node` UID != 1000 | build fails naming UID contract | no image produced |
| Musl package attempt | Dockerfile references `-musl` | build/test refuses | never installs musl variant |
| Whitelist list empty | `whitelist.yaml` zero hosts | prints empty warning, every host blocked | exit 0, no silent empty |
| Whitelist list missing | no file in `/etc` nor `/var/lib` | dies naming `whitelist add` | non-zero, readable |
| Whitelist list stale etc | `/etc` newer than `/var/lib` copy | prints runtime copy source path | shows what proxy reads |
| Health before keys loaded | proxy listening, policy not loaded | health stays unhealthy | no healthy before load |
| Timing cold vs warm | first vs second session run | two records preserved, labeled cold/warm | never overwrite blindly |
| Timing budget verdict | total invocation-to-ready + proxy-ready subtime | total judged against 30s MET/NOT MET, proxy subverdict preserved separately | miss changes no code |
| Live gates here | `secureai verify` on Fedora 38, no live guest | FAIL/UNVERIFIED with cause, exit non-zero | never prints PASS unrun |

</intent-contract>

## Code Map

- `opencode/Dockerfile:14` -- base `node:22-bookworm-slim`; `:41-50` explicit 8 apt pkgs; `:56-58,:124-125` uv + `UV_LINK_MODE=copy`/`UV_NATIVE_TLS=1`; `:94-99` `opencode-ai@1.18.34` glibc install + `opencode --version` assert; `:25-33` CA-missing refuse; `:64-71` CA bake + `update-ca-certificates`; `:80-86` concatenated bundle + `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE` unset; `:108-123` XDG + vendored `OPENCODE_MODELS_PATH`/`OPENCODE_DISABLE_MODELS_FETCH=1`; `:127-134` SDK GAP (mkdir only); `:139-141` no baked resolv.
- `docker-compose.yml:12` project `secureai`; `:16-17` proxy `runc`; `:60-79` CONNECT healthcheck (raw socket, never touches addon policy); `:82-83` opencode `kata`; `:94-98` `depends_on service_healthy`; `:107-111` proxy env + `NO_PROXY`; `:114-117` XDG; `:130-132` `dns:`; `:134-145` `internal` unset (TBD).
- `proxy/addon.py:150` `SecureAIAddon`; `:64-67` mtime+TTL 5s cache; `:190-265` lazy `_load_policy`; `:267-282` per-flow `_policy`; `:393-416` `_deny` 403 + single structured line; `:458-495` `_inject` all auth headers; `:481` `authz=` log; `:115-119` `fp=sha256[:4]`.
- `proxy/Dockerfile:21` `pip install pyyaml`; `:33-36` addon baked to `/opt/secureai/addon.py` via layer (not context COPY).
- `secureai:271-296` `cmd_whitelist_list` (runtime copy preferred, missing/empty handled, no tests); `:868,928,945-994` timing (proxy_s only judged, cold/warm missing, agent-ready = container running); `:502-565` runtime pin guards; `:1160-1362` verify gates (a)-(d) + 5-5/5-6/5-7 probes, UNVERIFIED-safe.
- `config/compose.env:14-33` five constants sole source; `config/whitelist.yaml:16-24` nine AD-15 hosts.
- `tests/test_addon.py:509-588` `TestTreeContract` (resolver, constants `10.211.0.` only, runtime flags); `:475-507` one-class hooks, no literals; `:215-279` injection/fingerprint/403.
- `tests/test_installer_cli.py:912-927` CA contract; `:844-861` health/timeout/`--no-deps`; `:805-826` whitelist_add only; `:548-682` watchdog (not timing file).

## Tasks & Acceptance

**Execution:**
- `opencode/Dockerfile` -- pre-seed `@ai-sdk/*` provider binaries from `opencode/models.json` npm fields into `/home/node/.cache/opencode/bin` at build, assert `id -u node == 1000`, refuse `-musl` and refuse build when SDK set is empty -- closes 1-7 runtime-discovery hole.
- `proxy/addon.py` -- eager-load policy at startup (`configure`/`start`) and fail-closed until loaded, so health cannot pass before keys are in-process -- closes 2-6 keys-before-healthy gap; keep per-flow mtime reread for live updates.
- `docker-compose.yml` -- document that CLI poll loop (not `depends_on`) is the enforcing gate when using `--no-deps`, keep `service_healthy` as declarative backup -- prevents future removal of CLI wait.
- `tests/test_installer_cli.py` -- add `whitelist list` cases (missing dies, empty warns, prefers `/var/lib` copy and prints source) -- closes 3-6 zero-coverage gap.
- `secureai` -- fix session timing to label cold vs warm (flag + separate records, never overwrite), judge total invocation-to-ready against 30s while preserving the proxy-ready subverdict separately, record Kata/proxy/total decomposition with one-host-one-day note -- closes 4-4 verdict-scope gap.
- `tests/test_addon.py` -- extend shipped-tree literals to cover `8080` and `sk-secureai-placeholder` alongside `10.211.0.`, and assert no `musl` in opencode install layer -- hardens 1-7 contract.

**Acceptance Criteria:**
- Given a clean build context, when building the guest image with SDKs unseeded, then the build fails naming the missing SDK set rather than producing a runtime-discovering image.
- Given the built image, when inspecting `id -u node` and the install layer, then UID is 1000 and no `-musl` string appears in the install step.
- Given a starting proxy with unreadable keys, when the healthcheck runs, then health stays unhealthy until policy loads successfully.
- Given `whitelist.yaml` missing, when running `secureai whitelist list`, then it dies naming `whitelist add`; given empty, when listing, then it warns that every host is blocked.
- Given two consecutive sessions, when timing is recorded, then cold and warm records are preserved separately with proxy/Kata/total decomposition.
- Given total session time over 30s, when timing verdict is computed, then it reports NOT MET on the total while preserving the proxy-ready subverdict separately, and changes no code.
- Given this host, when running `secureai verify`, then gates report FAIL/UNVERIFIED with causes named and exit non-zero, never PASS unrun.

## Spec Change Log

## Review Triage Log

### 2026-10-12 — Review pass
- verdicts: 26 findings — high 3, medium 3, low 11, false 9, maybe-false 0
- findings:
  - `[low]` `[reject]` fresh-process healthcheck has no shared readiness signal with the serving mitmproxy process — bounded 5s cache race fails closed (first request 403, not passthrough); adding a shared signal is more than a direct correction for an everyday-unlikely window.
  - `[false]` `[reject]` `import addon` fails in the healthcheck interpreter without mitmproxy — disproved: the healthcheck runs inside the proxy image built `FROM mitmproxy/mitmproxy:12.2.3` (proxy/Dockerfile:13), which ships mitmproxy.
  - `[low]` `[patch]` `_ready` set by `_eager_load()` but never read by `is_healthy()` — dead flag future callers will mistake for the verdict — fixed: `is_healthy()` now records the `_policy()` outcome into `_ready` and returns it (proxy/addon.py).
  - `[false]` `[reject]` Dockerfile uses jq without asserting it and hides missing-file vs empty-set — disproved: jq is apt-installed in the same stage (opencode/Dockerfile:48); a missing catalog aborts via jq under `set -e` while an empty set hits the named FATAL.
  - `[low]` `[reject]` total-ready derived from container-running misnamed as invocation-to-ready with 1s `$SECONDS` granularity — documented in spec Design Notes as a one-host measurement with not-a-guarantee notes printed in the file and console; a TUI probe risks TUI corruption the watchdog deliberately avoids.
  - `[low]` `[reject]` every run appends `session-timing-history.log` with no rotation — a few hundred bytes per session; rotation adds complexity for no everyday harm.
  - `[false]` `[reject]` `TestWhitelistList` stubs hide ETC/VAR overrides and transitive helpers — disproved: `_run` sets `SECUREAI_ETC/VAR` in env and stubs all of `die/ok/warn/log/cmd_init_config`; `yaml_list` needs no other helper; the tests execute and pass.
  - `[low]` `[patch]` musl test mixes `or`/`and` without parentheses with duplicated branches — fixed: parenthesized `is_guard`/`is_install` with one clear condition, no behavior change on the current tree (tests/test_addon.py).
  - `[false]` `[reject]` spec keeps `multiple-goals/oversized` with empty `deferred` and no live-host acceptance — fix would edit this build's spec (forbidden); warnings are correct (4 goals, ~2400 tokens) and `deferred` populates at review, not planning.
  - `[high]` `[patch]` `${SDK_SET// /}` is bash-only but RUN executes under dash on bookworm-slim, aborting every guest build with Bad substitution — fixed: POSIX `[ -z "$(printf '%s' "$SDK_SET" | tr -d '[:space:]')" ]` (opencode/Dockerfile).
  - `[low]` `[patch]` unknown first flag to `opencode` silently defaults to cold, mislabeling timing on a typo — fixed: die with usage unless the flag is empty/`--cold`/`--warm` (secureai).
  - `[false]` `[reject]` second same-kind run truncates the prior kind file, losing the measurement — disproved: the per-kind file is latest-by-design while every run appends to `session-timing-history.log`, which the executed test asserts contains both.
  - `[low]` `[reject]` history log never rotates (same defect as above, edge layer) — same evidence; rotation complexity unwarranted.
  - `[false]` `[reject]` musl via apt or URL bypasses the npm-line test — the contract (AD-9) forbids the `opencode-ai` musl npm variant on glibc, not an apt `musl-tools` C library; flagging apt musl would be a false positive.
  - `[low]` `[reject]` tab-indented or foreign-file `EXPOSE 8080` evades or trips the port test — contrived: all Dockerfiles use column-0 `EXPOSE` and the AD-11 exception is image metadata, not a config copy.
  - `[low]` `[reject]` split placeholder literal evades the contiguous search — contrived obfuscation with no everyday incidence; normalization adds complexity.
  - `[medium]` `[patch]` timing tokens asserted by substring, so comment-only mentions pass without behavior (same test-strategy defect as the timing gap below) — fixed with the executed timing harness (see timing gap row).
  - `[low]` `[patch]` `--no-deps` asserted on concatenated compose+CLI text, so CLI-only docs pass — fixed: asserted separately in each file (tests/test_installer_cli.py).
  - `[low]` `[reject]` indented or `if`-led `&&`-list evades the bare-`&&` lint — contrived; current code is clean and generic detection adds complexity.
  - `[high]` `[patch]` dash Bad-substitution claim duplicates the bashism above (claim-kind, high confidence) — same evidence and same POSIX fix; grouped.
  - `[high]` `[patch]` Dockerfile SDK/UID/musl gates have no executing verification (pre-verified) — source-text checks let the dash breakage ship green — fixed: `TestGuestBuildGuardsExecuted` runs the real `jq ..|.npm?` pipeline (five `@ai-sdk/*` names) plus the POSIX/UID/musl fragments under bash with stubs (tests/test_addon.py).
  - `[medium]` `[patch]` timing cold/warm/total verdict has no executing verification (pre-verified) — inverted arms, session-end total, `>` truncating history, or dropped `cp` all keep substrings green — fixed: `TestSessionTimingExecuted` runs the actual block from `cmd_opencode` with fixture clocks for cold/warm/unknown, asserting files, `cp` latest, `>>` history, and MET/NOT MET (tests/test_installer_cli.py).
  - `[medium]` `[patch]` composed healthcheck never executes (pre-verified) — deleted socket guard, wrong `sys.path`, or uninterpolated port keep substrings green — fixed: `TestProxyHealthcheckExecuted` runs the exact interpolated `-c` body against a loopback CONNECT stub and fixture configs, asserting exit 0/1 (tests/test_installer_cli.py).
  - `[false]` `[reject]` stories 1-8/2-5 not covered by the diff — 1-8 is PRESENT with no gap per investigation; 2-5 Bun proof needs a live guest with real API traffic, excluded here by the substrate Never contract and AD-10 risk; scoping to the host-implementable subset is correct.
  - `[false]` `[reject]` Epic 5 live probes not covered — gates and 5-5/5-6/5-7 need a live Kata guest on Fedora 43/44; runnable-but-UNVERIFIED code already reports FAIL/UNVERIFIED here by contract; no honest advance is possible on this host.
  - `[false]` `[reject]` no `sprint-status.yaml`/`stories/*.md` mutation — status advancement is sprint-planning's job; build-auto records continuation as this spec file by design.

## Design Notes

Eager policy load does not replace per-flow reread: startup load closes the healthy-before-keys race; mtime-bounded reread keeps `whitelist add` live without restart. Timing keeps the watchdog silent (TUI protection) and derives agent-ready from container-running + total elapsed, explicitly labeled as measurement on one host, not a readiness probe into the TUI.

## Verification

**Commands:**
- `python3 -m unittest discover -s tests -v` -- expected: all pass including new SDK/UID/musl, whitelist-list, timing, health-loaded cases.
- `bash -n install-fedora.sh secureai` -- expected: clean.
- `python3 -m py_compile proxy/addon.py` -- expected: clean.
- `grep -rn "127.0.0.11" install-fedora.sh secureai docker-compose.yml opencode proxy config` -- expected: zero hits in shipped tree.
- `grep -rn -- "--runtime" secureai` -- expected: hits only in comment documenting the ban.

**Manual checks (cannot execute fully here, must be run on Fedora 43/44 + Kata live host):**
- `secureai verify` gates (a)-(d) plus 5-5/5-6/5-7 probes -- expected: real numbers per gate on provisioned host, with limits printed next to gate (c).

## Auto Run Result

**Status: done**

**Summary of implemented change.** Closed the four host-implementable sprint gaps in dependency order: guest SDK pre-seed with UID/musl build guards (1-7), eager policy load with `is_healthy()` gating (2-6), tested `whitelist list` (3-6), and cold/warm total-judged session timing (4-4). Review then fixed a build-breaking dash bashism plus dead-flag/test-precedence/flag-parsing/`--no-deps` assertion issues, and added three executed harnesses (Dockerfile pipeline, timing block, composed healthcheck) so the gaps that let the bashism ship green cannot recur. Epic 5 live-guest probes remain runnable-but-UNVERIFIED by contract.

**Files changed:**
- `opencode/Dockerfile` -- SDK pre-seed from catalog npm fields with POSIX emptiness check (dash-safe), UID 1000 assert, `-musl` refuse, empty-set FATAL.
- `proxy/addon.py` -- `configure`/`load`/`start` eager load plus `is_healthy()` recording `_ready` from the `_policy()` outcome; per-flow mtime reread untouched.
- `docker-compose.yml` -- healthcheck proves socket answers AND policy loads; CLI poll loop documented as the enforcing gate under `--no-deps`.
- `secureai` -- `opencode [--cold|--warm]` with unknown-flag die; total invocation-to-ready judged against 30s with proxy subverdict preserved; per-kind records plus appended history.
- `tests/test_addon.py` -- `TestAddonEagerLoad`, extended `TestTreeContract` (8080/placeholder literals, SDK/UID/musl), new executed `TestGuestBuildGuardsExecuted`.
- `tests/test_installer_cli.py` -- `TestWhitelistList` (executed), `TestSessionTiming` (structural) + `TestSessionTimingExecuted` (executed), `TestProxyHealthPolicyGate` (separate `--no-deps` asserts) + `TestProxyHealthcheckExecuted` (executed).
- `architecture/secureai-kata-opencode/spec-continue-sprint-from-status.md` -- this record.

**Review findings breakdown:** 26 findings triaged — high 3, medium 3, low 11, false 9. Routes: **patch 10** (dash bashism x2 grouped, `_ready` wiring, musl precedence, unknown-flag die, separate `--no-deps` asserts, Dockerfile executed harness, timing executed harness + comment-only token gap grouped, healthcheck executed harness — all applied in the diff), **reject 16** (fresh-process health race, mitmproxy-import, jq/missing-file, total-ready naming, history rotation x2 grouped, whitelist stubs, spec-edit request, same-kind overwrite, apt-musl, EXPOSE variants, split placeholder, `&&`-lint evasion, and the three intent-alignment scope observations — each with recorded refutation above), **defer 0**.

**Follow-up review recommendation:** true. Patched entries include highs (dash build breakage, Dockerfile source-text-only gap); although both are fixed and covered by new executed tests, the guest `docker build` itself (SDK `npm install` of the five `@ai-sdk/*` packages) has never executed anywhere — it must be confirmed via `sudo secureai build` on a networked host — and the healthcheck `import addon` path inside the real proxy image (mitmproxy present) is asserted here only via stub. Counts: high patched 3 (2 grouped), medium patched 3 (2 grouped), low patched 5.

**Verification performed:**
- `python3 -m unittest discover -s tests` -- 146 passed, 0 failed (was 114 pre-change, 136 post-implementation, +10 review harnesses).
- Targeted: `TestGuestBuildGuardsExecuted` 3/3, `TestSessionTimingExecuted` 4/4, `TestProxyHealthcheckExecuted` 3/3, plus `TestAddonEagerLoad`/`TestTreeContract`/`TestWhitelistList`/`TestSessionTiming`/`TestProxyHealthPolicyGate` all green.
- `bash -n install-fedora.sh secureai` -- clean; `python3 -m py_compile proxy/addon.py` -- clean.
- `grep -rn 127.0.0.11 install-fedora.sh secureai docker-compose.yml opencode proxy config` -- zero hits.
- `grep -rn -- --runtime secureai` -- zero hits (guard assembles the flag so even comments cannot match).
- Matrix audit: all 10 I/O rows covered by at least one executed test that ran and passed (SDK/UID/musl via pipeline+fragments; list missing/empty/stale via fixture subprocess; health-not-loaded via eager-load + missing-policy exit 1; cold/warm + verdict via fixture timing runs; live gates via existing FAIL/UNVERIFIED tests).

**Residual risks:** `docker build` of the guest image not run here (needs network + Docker on a provisioned host); healthcheck `import addon` inside the real mitmproxy image unobserved (stub stands in here); cold/warm is flag-driven with no auto-detection; Epic 5 gates and 5-5/5-6/5-7 stay UNVERIFIED until a Fedora 43/44 + Kata live host with real API traffic runs them.
