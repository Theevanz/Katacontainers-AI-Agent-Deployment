# Sprint Change Proposal — Video-Ready POC Simplification

> NOTE 2026-10-13: the VIDEO RELAXATION approach in this doc was REJECTED by the
> user ("should not be 'oh i will implement next time'"). Applied instead as
> PERMANENT cuts: 1-8, 4-4, 5-4, 5-5, 5-7 removed in epics + status=backlog;
> 1-7/2-5/2-6/3-6 simplified; AD-1/4/8/10/15 + Open Questions simplified;
> cwd-mount + launch guard + 10-host baseline + SDK warn-not-fail in code.
> This doc remains as history.

- date: 2026-10-13
- trigger: POC too robust to demo; 3 days debugging substrate vs dockertest 1-day working baseline
- mode: Batch (user chose batch 2026-10-13)
- scope: Moderate — backlog reorganisation, no fundamental replan. SecureAI identity kept.
- status: proposed — awaiting approval (yes / no / revise)

> Intent in one line: keep SecureAI's sealed-egress idea (Kata VM + proxy holds keys + placeholder injection + allowlist 403), cut every **undone hardening** that blocks a video demo. This is NOT a copy of dockertest. dockertest is referenced only for simplicity patterns (fixed IP, ca-public volume, file-exists healthcheck, inode-safe allowlist edit, EXIT-trap teardown).

---

## 1. Issue Summary

**Problem:** The SecureAI build substrate grew past its own PRD quality bar ("showcase proof of concept, not production-hardened"). Eight stories are `in-progress`, all of Epic 5's live gates are blocked on a Kata guest boot, and the active spec (`spec-fix-kata-qemu-pid-denied.md`) is host SELinux/VMM policy work. Three days of debugging went to installer paths, SELinux labels, build ordering, image readability, and kata+veth+tcfilter — almost none of it the sandbox demo itself.

**When/how discovered:** During sprint execution 2026-10-08 → 2026-10-13. Evidence:

- `sprint-status.yaml` (2026-10-12): 1-7, 1-8, 2-5, 2-6, 3-6, 4-4 in-progress; 5-1, 5-2, 5-3, 5-4, 5-6, 5-7 in-progress; 5-5 backlog.
- `deferred-work.md`: F38 + kata 3.0.2 Go + Docker-managed bridge cannot program veth (`conflicts with existing route`); proven live across every bridge form. Guest E2E needs F43/44 host.
- Active spec `spec-fix-kata-qemu-pid-denied.md` (2026-10-13, F44 nested VM): proxy healthy, guest stuck `Created`, `cannot create PID file ... Permission denied` under `/run/vc/vm` + 3 AVC classes.
- `dockertest/` baseline: ~400 lines total (`ai-agent` 231 + `compose.yml` 89 + `Dockerfile` 86 + `filter.py` 54), builds and runs in 1 day on this same host's `daemon.json` (`runtimeType: /usr/bin/containerd-shim-kata-v2`), which the SecureAI repo re-derived from scratch.
- User statement 2026-10-13: "additional complexity is good, robust but not what i need for a POC. i just need it to work for a video."

**What the video needs (and only that):**

1. `secureai opencode` (or equivalent) drops into OpenCode inside a Kata VM.
2. Proxy injects real key for placeholder; agent talks to one model API.
3. Non-allowlisted host → readable `403 blocked by secureai`.
4. `uname -a` inside guest ≠ host kernel (VM proof, one shot).
5. Clean teardown, no orphans.

Everything else is post-video.

---

## 2. Impact Analysis

### Checklist run (checklist.md Sections 1–3)

- [x] 1.1 Triggering stories — 1-7, 1-8, 2-5, 2-6, 3-6, 4-4, 5-1/5-2/5-3/5-4/5-6/5-7 + `spec-fix-kata-qemu-pid-denied`. Not one story; systemic over-scope.
- [x] 1.2 Core problem — **PRD MVP Review** (checklist 4.3 family). PRD already says "MVP for showcase, not production-hardened"; epics/spine added hardening beyond it. Category: scope exceeds demo need, not technical impossibility.
- [x] 1.3 Evidence — sprint-status + deferred-work + live containerd/QEMU logs + dockertest working baseline (see §1).
- [x] 2.1 Current epics completable as planned? No — Epic 5 live gates cannot complete on F38; 1-7/2-5 carry unproven Bun + SDK pre-seed work.
- [x] 2.2–2.5 Epic changes — defer, don't delete. No epic removed; in-progress stories simplified or deferred to `post-video`. Order unchanged; Epic 5 split into concept-demo subset vs deferred decisions.
- [x] 3.1 PRD conflicts? No core-goal conflict. PRD already out-of-scopes hardening/testing/CI/perf/docs. Only needs a "video scope" note.
- [x] 3.2 Architecture conflicts — AD-4 (concatenated bundle), AD-8 (vendored catalog + SDK pre-seed), AD-10 gate (d), AD-11 (strict single-source), AD-13 (audit lines). Each needs a `POST-VIDEO` marker, not a rewrite.
- [x] 3.3 UX — [N/A]. No UX doc; CLI surface unchanged (`opencode`, `down`, `status`, `whitelist add|list`, `keys set|list`, `ca export`).
- [x] 3.4 Other artifacts — `tests/`, `install-fedora.sh` policy-module automation, automated `verify` — deferred, not deleted.

### Epic impact

| Epic | Done (keep) | In-progress → proposal |
|------|-------------|------------------------|
| 1 Host & Guest | 1-1…1-6 | 1-7 SIMPLIFY (drop SDK pre-seed + vendored catalog mandate for video); 1-8 DEFER |
| 2 Keys & Intercept | 2-1…2-4 | 2-5 SIMPLIFY (dockertest-style CA env, defer Bun proof); 2-6 SIMPLIFY (file-exists healthcheck) |
| 3 Egress | 3-1…3-5 | 3-6 SIMPLIFY (grep print, drop sync-assertion) |
| 4 Lifecycle | 4-1…4-3 | 4-4 DEFER (timing number not needed for video) |
| 5 Proof | — | 5-1, 5-2, 5-3, 5-6 KEEP as manual demo steps; 5-4, 5-5, 5-7 DEFER |

### Artifact conflicts

- PRD: no goal change. Add video-scope note only.
- Spine: 5 AD markers to relax for video (details §4). Paradigm (sealed egress, one chokepoint) UNCHANGED — falsifiable prediction stays.
- Epics: AC simplifications in §4. No FR/NFR deletion, only `POST-VIDEO` deferral.
- Specs: `spec-fix-kata-qemu-pid-denied.md` narrowed to early-fail guard (<10s, named path + fix command); `audit2allow` module automation deferred.

---

## 3. Recommended Approach

**Selected: Option 3 PRD MVP Review + Option 1 Direct Adjustment (hybrid).**

- Option 1 Direct Adjustment — viable alone for 2-6/3-6, insufficient for 1-7/2-5/Epic 5. Effort Low, Risk Low.
- Option 2 Rollback — NOT viable. Done substrate (1-1…1-6, 2-1…2-4, 3-1…3-5, 4-1…4-3) works; reverting gains nothing. Effort Medium, Risk Medium. Rejected.
- Option 3 MVP Review — VIABLE and recommended. Effort Low (doc + small code relaxations), Risk Low (demo path gets simpler, not wider). Timeline: unblocks video on the host that already boots Kata; F38 tcfilter impossibility stays deferred, not fixed.

**Rationale:** The PRD's own quality bar settles it — demonstrability wins over hardening. Every cut below is an *undone* item; nothing done is unpicked. The trust boundary (proxy runc + guest kata + placeholder-only guest + 403 fail-closed) is untouched, so the video still shows SecureAI, not dockertest.

**Trade-offs accepted for video:**

- `models.opencode.ai` temporarily allowlisted (like dockertest) instead of vendored catalog. Post-video: re-seal per AD-8.
- `SSL_CERT_FILE`-style CA env allowed for video (dockertest pattern). Post-video: concatenated bundle per AD-4.
- Scheme-gate, SDK pre-seed, timing number, `internal:true` answer, bypass measurement: all deferred with names, not silently dropped.

---

## 4. Detailed Change Proposals (Batch)

### PRD (`prd-workspace/prd.md`)

```text
OLD: Scope section ends at "Not production-hardened. Goal is to demonstrate the concept works end-to-end..."

NEW: Append "Video-ready scope (2026-10-13 correct-course): the demo shows Kata boot + key injection + allowlist 403 + VM-kernel proof + clean teardown. Vendored model catalog, provider-SDK pre-seeding, plaintext-scheme hardening beyond host check, session timing measurement, and the `internal:true`/bypass answers are POST-VIDEO and do not gate the video."

Rationale: Names the cut so later hardening is resumption, not scope creep.
```

### Architecture (`architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md`)

```text
1. AD-8 — OLD: vendored catalog + SDK pre-seed mandatory; models.opencode.ai leaves allowlist.
   NEW: append "VIDEO RELAXATION 2026-10-13: models.opencode.ai MAY appear in the video allowlist; SDK pre-seed failure is a warning, not a build failure. Post-video: restore strict rule."
2. AD-4 — OLD: SSL_CERT_FILE never points at mitm CA alone.
   NEW: append "VIDEO RELAXATION: dockertest-style CA env (NODE_EXTRA_CA_CERTS/SSL_CERT_FILE/REQUESTS_CA_BUNDLE → /certs/mitmproxy-ca-cert.pem via ca-public volume) permitted for video. Post-video: concatenated bundle."
3. AD-10 — OLD: four gates must all pass.
   NEW: "Video requires gates (a)(b)(c) + 5.6 VM-kernel check only. Gate (d) Bun-runtime proof deferred post-video as known risk."
4. AD-11 — OLD: no literal copy of any of the five constants anywhere.
   NEW: "VIDEO RELAXATION: literals allowed in compose for demo portability; compose.env remains canonical. Post-video: restore grep-verifiable single source."
5. AD-13 — OLD: whitelist add writes audit line.
   NEW: "VIDEO RELAXATION: audit line optional. Post-video: restore."
6. Open Questions — append "5-5 internal:true and 5-7 bypass path deferred POST-VIDEO (AR2/AR5). AD-1 cooperative-enforcement risk statement stands for video."
```

### Epics (`architecture/secureai-kata-opencode/epics.md`)

```text
Story 1-7 — OLD ACs: vendored catalog mandatory; SDK pre-seed or build fails; no 127.0.0.11; UID 1000 assert; musl refusal; compose.env sole source.
NEW (video): base node:22-bookworm-slim + explicit toolchain + opencode-ai pinned + CA baked + HTTPS_PROXY by fixed IP + NO_PROXY set. SDK set + catalog vendoring become warnings. DNS via public resolvers kept. UID/musl/compose.env-strictness deferred.
Rationale: Unblocks image build; video still shows toolchain + fixed-IP contract.

Story 1-8 — OLD: greppable naming convention enforced.
NEW: DEFER POST-VIDEO. No code impact.
Rationale: Doc-only robustness; zero demo value.

Story 2-5 — OLD: concatenated bundle, NODE_EXTRA_CA_CERTS absolute at process start, public-HTTPS proof, pinning record, Bun gate.
NEW (video): CA trusted via shared public cert (ca-public pattern); curl + opencode smoke suffices. Bun investigation deferred.
Rationale: Highest-risk item (bun#17325 family) must not gate a video.

Story 2-6 — OLD: socket + addon.is_healthy() dual healthcheck, CLI poll loop, bounded retries.
NEW (video): healthcheck `test -f /public-ca/mitmproxy-ca-cert.pem` (interval 2s, retries 15) + depends_on service_healthy — dockertest pattern.
Rationale: 6-line gate that already works; is_healthy returns post-video.

Story 3-6 — OLD: listing reflects synced config proxy reads; empty/missing reported clearly.
NEW (video): `grep -v '^#' allowlist | grep -v '^$'` print (dockertest list_whitelist). Sync-assertion deferred.
Rationale: Trivial command; strictness adds no demo value.

Story 4-4 — OLD: measure cold+warm, decompose proxy/VM/agent, record vs 30s budget.
NEW: DEFER POST-VIDEO in full.
Rationale: Produces a number, not a demo. PRD tension stays named.

Story 5-1/5-2/5-3/5-6 — OLD: automated `secureai verify` gates.
NEW (video): manual demo steps, no automation mandate. (a) proxy log shows API host; (b) `authz=injected ... fp=...` line; (c) curl to non-allowlisted host → 403 `blocked by secureai` with on-screen honesty caption; (5.6) `docker inspect runtime==kata` + guest `uname -r` ≠ host.
Rationale: Video shows proof without building the verifier.

Story 5-4/5-5/5-7 — OLD: Bun-runtime gate, internal:true decision, bypass measurement.
NEW: DEFER POST-VIDEO as a named trio. No code.
Rationale: Decision work, not demo work. Blocking question stays open honestly.

Spec spec-fix-kata-qemu-pid-denied.md — OLD: installer builds audit2allow module + CLI asserts /run/vc/vm + status reports AVC count.
NEW (video): CLI early-fail only — if /run/vc/vm unwritable, fail <10s naming path + label + fix command. Policy-module automation deferred; manual host fix allowed for video. Never permanent setenforce 0.
Rationale: Video needs a readable failure, not an SELinux compiler.
```

### Tests / installer / verify

```text
- tests/test_addon.py, test_installer_cli.py: freeze as-is for video; new strict ACs need no new tests until post-video restore.
- install-fedora.sh: no new audit2allow automation for video. Keep existing daemon.json/labels/ca-export behaviour.
- `secureai verify`: not built for video. Demo uses the four manual commands above.
```

---

## 5. Implementation Handoff

- Scope: **Moderate** (backlog reorganisation + small relaxations; no PM/Architect replan — paradigm unchanged).
- Route to: **Developer agent** for direct implementation after approval.
- Developer tasks:
  1. Apply PRD video-scope note (§4).
  2. Append 6 spine VIDEO RELAXATION markers (§4).
  3. Rewrite 1-7/2-5/2-6/3-6 ACs to video form; mark 1-8/4-4/5-4/5-5/5-7 `POST-VIDEO deferred` in `epics.md`.
  4. Narrow `spec-fix-kata-qemu-pid-denied.md` to early-fail guard; move policy-module tasks to deferred-work.md.
  5. Update `sprint-status.yaml`: set 1-8/4-4/5-4/5-5/5-7 `backlog` with `post-video` tag; keep 1-7/2-5/2-6/3-6/5-1/5-2/5-3/5-6 `in-progress` under relaxed ACs.
  6. Code relaxations (small, reversible): allow `models.opencode.ai` in seed allowlist; downgrade SDK pre-seed fail to warn; simplify proxy healthcheck to file-exists; permit CA-env pattern — each behind a `# VIDEO-RELAXATION 2026-10-13` comment naming its AD restore.
- Success criteria: `secureai opencode` reaches TUI on the booting host; model call injects (fp logged); non-allowlisted curl 403s; guest kernel ≠ host; exit tears down with no orphans. Nothing else gates the video.
- Post-video restore: remove each `# VIDEO-RELAXATION` marker to re-harden; deferred stories resume from `backlog`.

**Approval required:** Do you approve this Sprint Change Proposal for implementation? (yes / no / revise)
- **Continue** — finalise + hand to Developer agent.
- **Edit** — revise proposal first (say what to change).

---

## Addendum 2026-10-13 — Video demo: microVM + mount isolation (user requirement)

**Requirement (verbatim intent):** video must show the guest is a microVM (empty/new VM,
`uname -r` differs) AND that the only host content visible inside is the directory the
session was launched from — e.g. launch from `Katacontainers-AI-Agent-Deployment/` and
`ls` shows its content; `dockertest/` next to it is invisible inside.

**Gap:** dockertest already does this; SecureAI currently does not.

- dockertest (`compose.yml:37`): `${WORKSPACE}:/workspace:z` with `WORKSPACE="$(pwd -P)"`
  exported by `ai-agent` (`ai-agent:104-112`), plus `check_launch_directory` refusing
  `$HOME`/`/` (`ai-agent:61-72`). Per-directory project `sandbox-$(basename)` isolates sessions.
- SecureAI (`docker-compose.yml:140`): `${SECUREAI_WORKSPACE}:/home/node/.local/share/opencode:Z`
  where `SECUREAI_WORKSPACE=/var/lib/secureai/workspace` (fixed system path, `secureai:20`).
  The guest never sees the user's cwd — the demo the user wants is impossible. It also
  conflates workspace with opencode state dir.

**Change (video form, keeps AD-1/AD-12):**

```text
OLD (docker-compose.yml:137-140):
  volumes:
    - ${SECUREAI_WORKSPACE}:/home/node/.local/share/opencode:Z

NEW (video):
  volumes:
    - ${SECUREAI_WORKSPACE}:/workspace:z   # cwd, exported by CLI from `pwd -P` (dockertest pattern)
    - opencode-config:/home/node/.config/opencode
    - opencode-data:/home/node/.local/share/opencode
  working_dir: /workspace
```

```text
OLD (secureai cmd_opencode): uses fixed /var/lib/secureai/workspace; no cwd export; no launch-dir guard.
NEW (video): at top of cmd_opencode, after root/docker asserts —
  1. refuse `pwd -P == $HOME` or `/` (copy dockertest check_launch_directory wording);
  2. `export SECUREAI_WORKSPACE="$(pwd -P)"` so compose mounts the launch dir;
  3. keep fixed-path behaviour behind `# VIDEO-RELAXATION` comment for post-video restore
     discussion (fixed workspace vs cwd mount is a real product decision, not just demo).
```

- SELinux: `:z` (shared, host+guest) is correct here — dockertest already uses it; `:Z`
  would relabel the user's project dir private to the guest.
- Video runbook addition (30s): host `ls` → guest `ls /workspace` (same) → guest
  `ls /` + `ls /home` (empty/new VM) → `uname -r` host vs guest (differ) → `ls ../`
  or `ls /workspace/../` shows no `dockertest` sibling. That sequence is the isolation proof.
- Post-video: decide fixed-workspace vs cwd-mount as product (cwd-mount is dockertest-usable
  and demo-friendly; fixed-workspace is cleaner for multi-user). No decision needed for video.

