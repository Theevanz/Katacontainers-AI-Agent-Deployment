---
title: 'Story 1-7 guest image stale-comment hygiene'
type: 'chore'
created: '2026-10-13'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context:
- 'architecture/secureai-kata-opencode/epics.md'
- 'architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md'
warnings: []
deferred: []
baseline_revision: 'dea25ad9ef620f1b4730bb9e5c068a03dacf8b91'
---

<intent-contract>

## Intent

**Problem:** Story 1-7 code implements the permanent concept-demo cuts (SDK warn-not-fail, 10-host baseline with runtime catalog fetch), but three Dockerfile comments and one CLI comment still describe the superseded strict rules, misdirecting the next reader.

**Approach:** Update only the stale comments to match the implemented warn-not-fail behavior and 10-host baseline; make zero functional changes.

## Boundaries & Constraints

**Always:** Comment-only edits in `opencode/Dockerfile` and `secureai`; preserve all build semantics,Uid checks, musl refusal, CA bake, and test expectations; keep `models.opencode.ai` in the baseline.

**Never:** Change build logic, compose wiring, allowlist membership, or test assertions; do not reintroduce vendored-catalog-mandatory or SDK-fail-closed wording; do not touch Epic 5 live-gate behavior.

</intent-contract>

## Code Map

- `opencode/Dockerfile:10-12` -- stale header claiming runtime discovery is a build failure; contradicts warn path at :140-143
- `opencode/Dockerfile:102-103` -- stale AD-8 note claiming catalog vendored so models.opencode.ai leaves allowlist; superseded by runtime-fetch baseline
- `opencode/Dockerfile:127-133` -- stale SDK pre-seed note claiming empty set fails build; code now warns
- `opencode/Dockerfile:139-143` -- current warn-not-fail implementation (read-only reference, do not alter logic)
- `secureai:23` -- stale nine-host comment above 10-entry `SECUREAI_BASELINE_ALLOWLIST` at :27-38 (read-only array, fix comment only)
- `architecture/secureai-kata-opencode/epics.md` -- Story 1.7 AC source (runtime fetch allowed, SDK warning)
- `tests/test_installer_cli.py:1101-1113` -- 10-host baseline test (read-only, must keep passing)

## Tasks & Acceptance

**Execution:**
- `opencode/Dockerfile` -- rewrite lines 10-12 header to state runtime fetch via allowlist is fallback and missing SDK is warning -- removes fail-closed misdirection
- `opencode/Dockerfile` -- rewrite lines 102-103 AD-8 note to state catalog vendored but runtime fetch via allowlist permitted, models.opencode.ai in baseline -- aligns with epics 1.7
- `opencode/Dockerfile` -- rewrite lines 127-133 SDK note to state empty set warns and install failure warns -- matches code at 139-143
- `secureai` -- fix line 23 comment from nine to ten hosts -- aligns with array and tests

**Acceptance Criteria:**
- Given the built tree, when grepping Dockerfile for `build failure` in the three stale notes, then no fail-closed claim remains for SDK discovery
- Given the CLI source, when reading the baseline comment, then it states ten hosts and matches the 10-entry array
- Given the test suite, when running installer CLI and addon tests, then all tests pass unmodified

## Spec Change Log

## Review Triage Log

### 2026-10-13 — Review pass
- verdicts: 10 findings — high 0, medium 0, low 0, false 10, maybe-false 0
- findings:
  - `[false]` `[reject]` Missing reversion path for warn-not-fail weakening — permanent cuts per 2026-10-13 proposal NOTE (rejected video-relaxation, applied as permanent); no restore condition intended.
  - `[false]` `[reject]` Missing AD-8/AD-15 amendment — spine already amended in baseline dea25ad (AD-8 runtime-fetch fallback, AD-15 10-host baseline); comments now match amended ADs.
  - `[false]` `[reject]` Duplicated baseline list not synced — config/whitelist.yaml, README.md, CLI array already at 10 hosts and enforced by tests/test_installer_cli.py:1101-1113 (passing); secureai comment was the last stale piece.
  - `[false]` `[reject]` No proof array holds ten entries — array at secureai:27-38 unchanged by design; tests assert len 10 and CLI==yaml==README, 168 passed.
  - `[false]` `[reject]` Runtime-fetch precedence undefined — defined by OPENCODE_DISABLE_MODELS_FETCH=1 (vendored primary) plus allowlist fallback per epics 1.7; comment story needs no behavior spec.
  - `[false]` `[reject]` SDK warning visibility unstated — warnings emit via `>&2` in the same RUN layer (opencode/Dockerfile:140-143), visible in docker build output by construction.
  - `[false]` `[reject]` No test for warn path — comment-only diff changes no logic; existing 168 tests cover baseline and build-order gates; new warn-path test would need docker build, out of unit scope.
  - `[false]` `[reject]` Spec Code Map line anchors shift — file:line anchors are the build-auto convention for a snapshot spec; fix would edit the spec, forbidden by review routing.
  - `[false]` `[reject]` Spec typo `semantics,Uid` — spec-body typo, zero code impact; fix would edit the spec, forbidden by review routing.
  - `[false]` `[reject]` Brittle 168-count, no docker build, nonstandard diff header — count is descriptive (command asserts all-pass, not exact N); docker build N/A for comment-only; ---UNTRACKED header lives only in ephemeral /tmp review diff, never committed.

## Auto Run Result

Summary: Story 1-7 stale-comment hygiene implemented comment-only. Dockerfile fail-closed notes rewritten to warn-not-fail with runtime-fetch fallback; CLI nine-host comment corrected to ten-host baseline. Zero functional changes.

Files changed:
- `opencode/Dockerfile` -- three comment hunks (header, AD-8 note, SDK note) aligned to warn-not-fail
- `secureai` -- baseline comment nine to ten hosts
- `architecture/secureai-kata-opencode/spec-1-7-guest-image-stale-comments.md` -- new chore spec (planning artifact)

Review findings: 10 blind-hunter findings triaged, all false/reject with refutation above; edge-case hunter 0 findings; verification-gap 0 gaps; intent-alignment descriptive only (diff implements hygiene step R4; full guest-build R1 and live gates need Kata host, blocked per deferred-work.md). Patches applied: none. Deferred: none.

Follow-up review recommended: false. Patched counts: high 0, medium 0, low 0. No unverified risk names a follow-up; remaining 1-7 live-image proof and 2-5/Epic-5 gates need a Fedora 43/44 Kata host.

Verification performed:
- `python3 -m pytest tests/test_installer_cli.py tests/test_addon.py -q` -- 168 passed
- `grep -n "build failure\|fails the build\|leaves the allowlist entirely" opencode/Dockerfile` -- no output (stale claims gone)
- `grep -n "nine-host\|9-host\|ten-host\|10-host" secureai` -- line 23 ten-host, line 193 10-host only

Residual risks: none for this chore. Story 1-7 functional image build and Epic 5 live gates remain host-blocked (F38 tcfilter per deferred-work.md); 1-7 stays in-progress in sprint-status.yaml until a Kata-boot host verifies the image.

## Verification

**Commands:**
- `python3 -m pytest tests/test_installer_cli.py tests/test_addon.py -q` -- expected: all pass (168 tests)
- `grep -n "build failure\|fails the build\|leaves the allowlist entirely" opencode/Dockerfile || true` -- expected: no stale fail-closed lines in the three notes
- `grep -n "nine-host\|9-host\|ten-host\|10-host" secureai` -- expected: ten-host wording only
