---
title: 'Verify all in-progress stories across Epics 1, 2, 5'
type: 'chore'
created: '2026-10-13'
status: 'done'
review_loop_iteration: 0
baseline_revision: '75bf5b41fb6ff1e40d5840cff133b9c158a2cd8b'
followup_review_recommended: false
context:
- 'architecture/secureai-kata-opencode/epics.md'
- 'architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md'
- 'architecture/secureai-kata-opencode/sprint-status.yaml'
- 'architecture/secureai-kata-opencode/deferred-work.md'
warnings:
- 'multiple-goals'
deferred: []
---

<intent-contract>

## Intent

**Problem:** Six stories stay `in-progress` (1-7, 2-5, 5-1, 5-2, 5-3, 5-6) while all other non-removed work is `done`; the remaining backlog (1-8, 4-4, 5-4, 5-5, 5-7) was permanently removed, so these six are the entire remaining scope and live-gate proof is host-blocked on Fedora 38.

**Approach:** Verify each in-progress story statically plus unit tests on this host, attempt only what can run without a Kata guest boot, and record live gates as UNVERIFIED with host evidence rather than claiming PASS.

## Boundaries & Constraints

**Always:** Check the outermost surface each story names (CLI output, proxy log lines, file contents, test results); keep `models.opencode.ai` in the baseline; keep SDK warn-not-fail; never claim a live gate passes without a running guest; preserve all build semantics.

**Never:** Reintroduce removed stories (1-8, 4-4, 5-4, 5-5, 5-7); change build logic, allowlist membership, or addon enforcement to make checks pass; run a Kata guest boot retry loop on F38 (already exhaustively proven to fail per deferred-work.md); edit epics/spine as part of verification.

</intent-contract>

## Code Map

- `opencode/Dockerfile:14` -- base `node:22-bookworm-slim` (1-7)
- `opencode/Dockerfile:25-33` -- CA bake order guard, names `ca export` (1-7, 2-5)
- `opencode/Dockerfile:39-58` -- explicit toolchain + uv, UV_NATIVE_TLS=1 (1-7)
- `opencode/Dockerfile:64-86` -- system store extend + concatenated bundle + NODE_EXTRA_CA_CERTS absolute (2-5)
- `opencode/Dockerfile:94-99` -- glibc opencode-ai install + `--version` gate (1-7)
- `opencode/Dockerfile:101-126` -- AD-8 catalog vendored + runtime-fetch allowed, XDG explicit (1-7)
- `opencode/Dockerfile:128-145` -- SDK warn-not-fail, UID 1000 + musl refusal (1-7)
- `opencode/Dockerfile:147-153` -- WORKDIR /workspace, no baked resolv.conf (1-7)
- `docker-compose.yml:17` -- proxy `runtime: runc` pin (2-5 substrate)
- `docker-compose.yml:60-91` -- proxy fixed IP + socket/is_healthy healthcheck, retries 15 (2-5)
- `docker-compose.yml:96` -- opencode `runtime: kata` (5-6 declaration)
- `docker-compose.yml:123-141` -- placeholder-only guest env, HTTPS_PROXY by IP, NO_PROXY (1-7, 2-5)
- `docker-compose.yml:142-160` -- workspace `:z` mount, working_dir, public dns (1-7)
- `proxy/Dockerfile:13` -- mitmproxy 12.2.3 Debian base (2-5 substrate)
- `proxy/Dockerfile:33-36` -- addon world-readable install (2-5 substrate)
- `proxy/addon.py:150-151` -- one addon class (Epic 5 substrate)
- `proxy/addon.py:354-370` -- tls_clienthello observe-only (5-1 substrate)
- `proxy/addon.py:372-418` -- requestheaders 403 enforcement + scheme gate (5-3 substrate)
- `proxy/addon.py:420-441` -- responseheaders SSE streaming (Epic 3 done, regression guard)
- `proxy/addon.py:485-562` -- placeholder injection + `authz=injected ... fp=` fingerprint (5-2)
- `proxy/addon.py:64-68,334-349` -- mtime-bounded per-flow reread (live-update substrate)
- `proxy/addon.py:565-569` -- module-level `addons` list (enforcement active)
- `secureai:27-38` -- ten-host baseline array (1-7/2-5 baseline)
- `secureai:385-435` -- `cmd_ca_export` upstream-image one-shot, reuse path (1-7 order)
- `secureai:501-530` -- runtime asserts runc/kata before session (5-6 declaration guard)
- `secureai:1334-1550` -- `cmd_verify` gates a/b/c/d + 5.6/5.7/5.5 with UNVERIFIED when not live (5-1/5-2/5-3/5-6)
- `config/whitelist.yaml` -- ten-host owned baseline, quoted wildcards (1-7/2-5)
- `config/settings.yaml` -- ttl 5, plaintext denied (5-3 substrate)
- `tests/test_installer_cli.py:1101-1113` -- 10-host baseline test (read-only guard)
- `architecture/secureai-kata-opencode/deferred-work.md:1-28` -- F38 tcfilter impossibility evidence (live-blocked proof)

## Tasks & Acceptance

**Execution:**
- `opencode/Dockerfile` -- grep-verify base, toolchain, CA bake, bundle env, glibc install, warn-not-fail comments, XDG, no resolv.conf -- 1-7 static surface
- `docker-compose.yml` -- grep-verify runc/kata pins, fixed-IP proxy env, NO_PROXY, dns, workspace :z, healthcheck + depends_on -- 1-7/2-5 wiring surface
- `proxy/addon.py` -- grep-verify single class, observe-only tls hook, 403 body, SSE stream flag, fingerprint format, no host literal -- 5-1/5-2/5-3 substrate
- `secureai` + `config/whitelist.yaml` -- diff-verify CLI 10-host array equals yaml equals README baseline; verify `cmd_verify` emits UNVERIFIED (never PASS) without live stack -- gates honesty surface
- `tests/test_installer_cli.py + tests/test_addon.py` -- run full unit suite unmodified -- regression surface
- `secureai verify` -- run without live stack, capture gate output -- record 5-1/5-2/5-3/5-6 as UNVERIFIED with F38 host evidence (F38/kata-3.0.2/Docker-26.1.4 + deferred-work tcfilter)

**Acceptance Criteria:**
- Given this F38 host, when the Dockerfile/compose/addon greps run, then every 1-7/2-5 static AC matches without edits
- Given the unit suite runs, when pytest completes, then all tests pass unmodified
- Given no live guest exists, when `secureai verify` runs, then gates report UNVERIFIED/FAIL with named reasons and never PASS, and the F38 block is recorded with version evidence
- Given the six stories, when verification completes, then no removed story is touched and no functional code is changed unless a static mismatch is found (fix + re-verify in same run)

## Spec Change Log

## Review Triage Log

### 2026-10-13 — Review pass
- verdicts: 20 findings — high 0, medium 0, low 0, false 20, maybe-false 0
- findings:
  - `[false]` `[reject]` Missing per-story acceptance mapping for six stories — grouped greps/UNVERIFIED leave no per-story mark; uniform outcome (all static pass, all live host-blocked) left nothing to disambiguate, so no mis-verification occurred.
  - `[false]` `[reject]` Triple-equality README check missing from Verification diff command — README check was performed live (all 10 hosts present in README.md) and no drift existed; command gap caused no missed defect, and the fix would edit the spec.
  - `[false]` `[reject]` Code Map surfaces without matching Task/Verification grep — extra statics were all checked by the implementer (toolchain, UID, bundle, XDG, healthcheck, :z, dns, ttl, addons list) with zero mismatches; no surface went unverified.
  - `[false]` `[reject]` Spec Change Log empty with no creation entry — empty is correct pre-loopback per the template (populated only on bad_spec loopback); nothing to record.
  - `[false]` `[reject]` No evidence location/format for verify output — evidence lands in `## Auto Run Result` per Finalize, which this pass writes; no loss occurred.
  - `[false]` `[reject]` No partial-stack (proxy-up/guest-down) handling in AC — partial state never arose (no live stack at all) and `secureai verify` already branches per gate; no ambiguous recording occurred.
  - `[false]` `[reject]` `multiple-goals` warning never mitigated — workflow directs carry-forward without splitting, which was done; no mitigation is required.
  - `[false]` `[reject]` Hardcoded 168-test expectation breaks on suite change — command asserts pytest all-pass exit, not an exact count (descriptive only, per prior triage precedent); suite passed unmodified.
  - `[false]` `[reject]` Empty `deferred: []` despite host-blocked gates — host-blocks link via `deferred-work.md` context plus residual risks in Auto Run Result; `deferred` is for review findings, of which there are none routable here.
  - `[false]` `[reject]` Grep-zero-match mistaken for present verification — all spec greps matched live (base, bundle, pins, class, stream flag, fingerprint, addons list); absence never arose.
  - `[false]` `[reject]` PASS hidden above `tail -20` wrongly recorded UNVERIFIED — output summary is FAIL with named FAIL/UNVERIFIED gates and no PASS anywhere; nothing hidden.
  - `[false]` `[reject]` Baseline diff aborts under POSIX sh process substitution — command is explicitly `bash -c` and ran successfully (NO_DIFF); trigger condition false.
  - `[false]` `[reject]` Valid suite fails on 168-count change — same refutation as 168 finding above; pytest exit decides, count is descriptive.
  - `[false]` `[reject]` Line-number shift makes Code Map anchors check wrong lines — zero tracked edits since baseline and anchors verified by direct reads; snapshot convention holds.
  - `[false]` `[reject]` Non-F38 host invalidates version evidence — host is F38 (os-release + kernel confirmed) and ACs scope to F38; out-of-scope trigger never arose.
  - `[false]` `[reject]` Partial proxy-up/guest-down PASS misread — no live stack existed so partial never arose; per-gate branching already handles it.
  - `[false]` `[reject]` Real FAIL hidden as expected UNVERIFIED — gates (a)/(b) FAIL were recorded as FAIL with causes and summary FAIL; nothing hidden.
  - `[false]` `[reject]` Static mismatch triggers unbounded fix loops — no mismatch found and zero code changes made; single pass, no loop.
  - `[false]` `[reject]` YAML comment quoted strings cause false baseline diff — baseline diff returned NO_DIFF, proving comments contribute no extra quoted strings.
  - `[false]` `[reject]` Removed backlog stories still listed as backlog get skipped — backlog+REMOVED is the agreed representation (epics REMOVED notes + proposal NOTE) with no remaining ACs; nothing skipped.

## Verification

**Commands:**
- `python3 -m pytest tests/test_installer_cli.py tests/test_addon.py -q` -- expected: all pass (168 tests)
- `grep -n "FROM node:22-bookworm-slim\|opencode-ai@\|NODE_EXTRA_CA_CERTS\|no @ai-sdk" opencode/Dockerfile` -- expected: base + bundle + warn-not-fail present
- `grep -n "runtime: runc\|runtime: kata\|HTTPS_PROXY: http://\${SECUREAI_PROXY_IP}\|NO_PROXY\|dns:" docker-compose.yml` -- expected: pins + fixed-IP + loopback guard + dns present
- `grep -n "class SecureAIAddon\|tls_clienthello\|http.Response.make(403\|response.stream = True\|authz=injected\|^addons = " proxy/addon.py` -- expected: all enforcement surfaces present, no host literal
- `bash -c 'diff <(sed -n "/BASELINE_ALLOWLIST=(/,/)/p" secureai | grep -o "\"[^\"]*\"" | sort) <(grep -o "\"[^\"]*\"" config/whitelist.yaml | sort)'` -- expected: no diff (CLI == yaml)
- `secureai verify 2>&1 | tail -20` -- expected: UNVERIFIED gates with reasons, never PASS without live stack

## Auto Run Result

Summary: Verification sweep over all six in-progress stories (1-7, 2-5, 5-1, 5-2, 5-3, 5-6). Zero code changes required — every static surface matched without edits, the full unit suite passes unmodified, and `secureai verify` without a live stack honestly reports FAIL/UNVERIFIED (never PASS). Live legs stay host-blocked on this Fedora 38 host per deferred-work.md tcfilter evidence.

Files changed:
- `architecture/secureai-kata-opencode/spec-verify-all-inprogress-epics.md` -- new verification chore spec (planning artifact; the only change in this run)

Review findings: 9 blind-hunter findings triaged, all false/reject; 11 edge-case findings triaged, all false/reject; verification-gap 0 findings; intent-alignment descriptive only (diff implements the verify-and-record reading with explicit no-live-retry constraints at spec/static/honesty surfaces, not a live-gate-pass or complete-to-done surface — live completion is unachievable on this host regardless of reading, so no intent_gap was filed). Patches applied: none. Deferred: none.

Follow-up review recommended: false. Patched counts: high 0, medium 0, low 0. No patched finding names an unverified risk; the remaining live-gate block is a residual risk below, not a review patch.

Verification performed:
- `python3 -m pytest tests/test_installer_cli.py tests/test_addon.py -q` -- 168 passed
- Dockerfile/compose/addon presence greps -- all surfaces matched (base, bundle, runc/kata pins, fixed-IP proxy env, NO_PROXY, dns, single addon class, observe-only tls hook, 403 constants, SSE stream flag, fingerprint log line, addons list)
- Baseline diff CLI vs whitelist.yaml -- NO_DIFF; README baseline also holds all 10 hosts
- `./secureai verify` (no live stack) -- Summary FAIL (gates a/b FAIL without keys/traffic, expected); gates c/d/5.6/5.7 UNVERIFIED with named causes; 5.5 `not set` with no guarantee claimed

Residual risks: live legs of 1-7 (guest boot), 2-5 (guest git-clone/model call), and 5-1/5-2/5-3/5-6 remain UNVERIFIED without a Kata guest; closing them needs a Fedora 43/44 host (kata 3.25/3.26) per deferred-work.md. Removed stories (1-8, 4-4, 5-4, 5-5, 5-7) untouched.
