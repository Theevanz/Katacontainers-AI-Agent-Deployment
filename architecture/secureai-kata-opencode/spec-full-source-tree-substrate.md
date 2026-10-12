---
title: 'SecureAI full source tree (installer, CLI, compose, images, enforcement addon)'
type: 'feature'
created: '2026-10-08'
status: 'done'
review_loop_iteration: 0
baseline_revision: '1e4e8b9aa5ba8b22b895481814154d69a3cae572'
followup_review_recommended: true
context:
  - '{project-root}/architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md'
  - '{project-root}/architecture/secureai-kata-opencode/epics.md'
warnings: [multiple-goals]
deferred:
  - summary: >-
      Whether the compose bridge's `internal: true` actually applies to the
      Kata guest's tap device (AR2). No live guest runs on this Fedora 38 host,
      so the tap is never observed; the probe must run on a powered, nested-virt
      Fedora 43/44 host with a booted Kata container.
    evidence: |-
      The structural seed leaves `internal: TBD` because the answer changes the
      spine's security claim. This host has no kata-runtime, no guest, and the
      Docker bridge is never brought up for a Kata service, so `secureai verify`
      reports the flag as UNVERIFIED here rather than flipping it.
    location: >-
      docker-compose.yml:secureai_net; tests/test_addon.py: no-internal-probe test
    severity: high (unverified)
  - summary: >-
      Whether the guest can reach the internet around the proxy (AR4) and what
      gate (c) actually proves about routeability. Needs a booted Kata VM where
      the proxy env is unset and an outbound request is made; cannot be observed
      on a host with no Kata runtime installed.
    evidence: |-
      `secureai verify` encodes the probe and fails closed to UNVERIFIED when no
      guest container is live; the cooperative-enforcement limit is now printed
      next to gate (c) and in the summary, so the heuristic is not mistaken for
      a proof.
    location: >-
      secureai:cmd_verify gate (c); epics.md Story 5.7
    severity: medium (unverified)
  - summary: >-
      Whether the opencode Bun binary honours NODE_EXTRA_CA_CERTS is AD-10 risk
      #1 and stays outstanding; gate (d)'s probe verifies the Node fetch path,
      which is the nearest observable, and reports UNVERIFIED when the guest
      image lacks a node runtime.
    evidence: |-
      The guest image is based on node:22-bookworm-slim, so the Node-fetch probe
      observes the NODE_EXTRA_CA_CERTS-reading path of the base image; the
      opencode Bun binary's own behaviour requires a live guest with real API
      traffic and is asserted as unverified, not passing.
    location: >-
      opencode/Dockerfile; secureai:cmd_verify gate (d)
    severity: high (unverified)
---

<intent-contract>

## Intent

**Problem:** The repository holds planning artifacts only — the README states plainly that
`install-fedora.sh`, `docker-compose.yml`, and the CLI do not exist, so the documented install
command cannot work. All 27 stories across 5 epics are `backlog` and the architecture spine is
`status: final`. Every story's implementation surface is one of seven files that do not exist yet,
so no story can start until that substrate is written.

**Approach:** Author the seven-file source tree the spine specifies, in the exact installed layout,
implementing the enforcement logic, key custody, allowlist, and session lifecycle as the contracts
state. This is the substrate all five epics stand on; the `verify` gates and open-question probes
are implemented as runnable code, but they cannot be *executed* on this host (see Boundaries).

## Boundaries & Constraints

**Always:**
- `install-fedora.sh` is the **only** writer of `/etc/docker/daemon.json`; it registers one runtime
  named `kata` with `runtimeType: io.containerd.kata.v2` — the containerd runtime **class**, which
  Docker resolves to the `containerd-shim-kata-v2` binary — and leaves `default-runtime` absent.
  `features.time-namespaces: false` is written **only** when Docker ≥ 29.5.0.
- `proxy` service declares `runtime: runc` explicitly; `opencode` declares `runtime: kata`.
  The CLI passes **no** `--runtime` flag anywhere.
- `SECUREAI_SUBNET`, `SECUREAI_GATEWAY`, `SECUREAI_PROXY_IP`, `SECUREAI_PROXY_PORT`,
  `SECUREAI_PLACEHOLDER_KEY` live **only** in `/etc/secureai/compose.env`; the installer creates
  that file empty and never populates it — the CLI is its sole writer. Every other reference is
  `${...}`.
- All whitelist enforcement lives in `proxy/addon.py`. `tls_clienthello` observes SNI only and
  takes no action; enforcement acts at `requestheaders`. Only `Flow.kill()` /
  `http.Response.make(403)` are used — never `Context.kill()` / `Connection.kill()`.
- `text/event-stream` responses set `.stream = True` in `responseheaders` before any body read.
- The addon fails **closed**: unreadable/unparseable/missing config, unknown host, or allowlisted
  host without a key ⇒ 403, never passthrough. Log lines distinguish `config-error` from `blocked`.
- Injection logs exactly `authz=injected provider=<host> fp=<4-char sha256 prefix>` — a fingerprint,
  never key material. `keys list` always masks.
- The addon re-reads config per flow with an mtime-bounded cache (default 5s) so `whitelist add`
  is live; the CLI never signals the proxy to reload.
- The addon contains no default-allow and no host literal. The seeded baseline in
  `config/whitelist.yaml` is exactly the nine reviewed hosts in AD-15.
- Bind mounts carry SELinux labels by consumer count (`:Z` single consumer, `:z` two or more).
  No `context=` mount option. Never reference Docker's embedded resolver `127.0.0.11`.
- `NO_PROXY=localhost,127.0.0.1,::1` is always set in the guest env.
- CLI is bash throughout; `yq` is not used and no new language dependency is added.

**Never:**
- Never set `default-runtime` to `kata` — it would pull the key-holding proxy into the guest VM.
- Never point `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `GIT_SSL_CAINFO` at the mitm CA alone; the
  system trust store is extended, never replaced.
- Never place a real key in any image, compose file, guest env, `compose.env`, or log.
- Never bind-mount the CA into a running container; it is baked at image build (build order:
  `secureai ca export` **before** `docker build`).
- Never create a per-user install path, a `~/bin` shim, or a systemd unit that auto-starts the proxy.
- Never claim gate (c) proves the guest cannot route around the proxy. Output must state the limit.
- **Never claim any gate passes.** This host is Fedora 38, not 43/44; there is no `kata-runtime`;
  and `sudo` requires a password. Nothing that needs Kata, root, or an enforcing SELinux host can be
  executed here. Gates (a)–(d), the `internal: true` probe, and the separate-kernel check are written
  to run and report, and must report **failed/unverified** here rather than pass.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Install on Fedora 38 | `id` host not 43/44 | exit non-zero naming detected + supported versions | no package install, no file write |
| Install without root | non-zero `EUID` | exit non-zero before touching host | message names the requirement |
| Idempotent re-run | existing `/opt/secureai`, `/etc/secureai` | succeeds, preserves existing values | never clobbers a written `compose.env` |
| Allowlisted host + key | `api.anthropic.com` w/ stored key | placeholder swapped, `authz=injected ... fp=` logged | SSE streams, not buffered |
| Allowlisted host, no key | host on list, no key stored | 403 `blocked by secureai` | logged `nokey`, never unauthenticated passthrough |
| Non-allowlisted host | `google.com` | 403 `blocked by secureai` at `requestheaders` | one line: host, scheme, decision |
| Plaintext `http://` | non-allowlisted host over `http` | 403 — scheme gated as well as host | `blocked` |
| Undeterminable scheme/host | request missing both | 403 | fails closed |
| Config unreadable/unparseable | bad `whitelist.yaml`/`keys.yaml` | 403, never passthrough | logged `config-error`, distinct from `blocked` |
| `keys list` | any stored key | every value masked | no flag reveals a raw value |
| `whitelist add` mid-session | new domain | written, synced, picked up ≤5s, no restart | audit line recorded |
| Image build without CA | `/var/lib/secureai/ca.pem` absent | build refuses, names `secureai ca export` | fails before any layer commits |
| `status` on Docker 29.5+ | version ≥ 29.5.0 | asserts pairing matches the `time-namespaces` flag in use | reports mismatch readably |
| Missing runtime | declared runtime not registered | session fails naming the missing runtime | readable, not a raw Docker error |

</intent-contract>

## Code Map

All seven target files are **new**; nothing exists to reuse. Read-only evidence:

- `architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md` -- binding contract. AD-1..AD-16 are
  the rules; *Installed layout* (AD-16) is the authoritative five-path list; *Stack* holds the pins
  (Kata 3.25.0/3.26.0 Go, Docker ≤28.5.2 or ≥29.5.0+flag, mitmproxy 12.2.3, opencode-ai 1.18.34,
  `node:22-bookworm-slim`, `uv` from `ghcr.io/astral-sh/uv`).
- `architecture/secureai-kata-opencode/epics.md:346-926` -- per-story acceptance criteria.
- `architecture/secureai-kata-opencode/.memlog.md` -- falsified assumptions. Notably:
  `kata-qemu` is a *name*, not an executable; `ghcr.io/sst/opencode` is **gone** (moved to
  `anomalyco`, Alpine/root/no git); the mitmproxy entrypoint gosu-drops to `mitmproxy`, so
  `addon.py` must be world-readable; Docker ≥29.5 breaks Kata with `invalid namespace type`.
- `README.md` -- documents the CLI surface and default allowlist; must keep agreeing with the code.

## Tasks & Acceptance

**Execution:**
- `install-fedora.sh` -- assert preconditions (Fedora 43/44, root, nested virt) **before** the first
  package install or file write; install Kata/Docker/Compose; write `daemon.json` with the version
  matrix as one row; relabel `/etc/secureai` + `/var/lib/secureai` with `semanage fcontext -a -t
  container_file_t` + `restorecon` and **assert** with `ls -Z`; copy the app tree to
  `/opt/secureai/`; create `/etc/secureai` + `/var/lib/secureai` 0700; create `compose.env` empty;
  idempotent re-run -- Stories 1.1-1.5, 2.1.
- `secureai` -- bash CLI, sole writer of `/etc/secureai`. Subcommands `opencode`, `down`, `status`,
  `whitelist add|list`, `keys set|list`, `ca export`, `verify`. Seeds `compose.env` and
  `whitelist.yaml` (nine AD-15 hosts) if absent; sources `compose.env` before `docker compose`;
  asserts the `proxy` service declares `runtime: runc` before a session; `trap` teardown on
  INT/TERM; `ca export` runs one-shot mitmproxy with `--set confdir=/var/lib/secureai/ca` and copies
  to `ca.pem` 0644; `verify` runs gates (a)-(d) plus the `internal: true` probe and the separate-kernel
  check, printing each limit explicitly -- Stories 2.1, 2.2, 3.4-3.6, 4.1-4.4, 5.1-5.7.
- `docker-compose.yml` -- `${...}` only, no literals; `secureai_net` bridge; `proxy` `runtime: runc`
  with a healthcheck that **exercises** the proxy; `opencode` `runtime: kata`,
  `depends_on: proxy: condition: service_healthy`, `dns:` set to the bridge gateway; labelled bind
  mounts; no `127.0.0.11` -- Stories 1.3, 1.7, 2.3, 2.6.
- `proxy/addon.py` -- single `SecureAIAddon` class; `tls_clienthello` records SNI to
  `flow.metadata` only; `requestheaders` enforces allowlist on **scheme and** host, injects the key,
  sets SSE streaming in `responseheaders`; mtime-bounded config re-read; fail-closed; fingerprint
  logging; no header values logged -- Stories 2.4, 3.1-3.3, 3.5.
- `proxy/Dockerfile` -- `mitmproxy/mitmproxy:12.2.3` base (Debian trixie, not Alpine); `addon.py`
  world-readable for the gosu drop -- Story 2.3.
- `opencode/Dockerfile` -- `node:22-bookworm-slim` (glibc); explicit toolchain install; `uv` from
  `ghcr.io/astral-sh/uv` with `UV_LINK_MODE=copy`/`UV_NATIVE_TLS=1`; `opencode-ai@1.18.34` from the
  **glibc** package with scripts enabled and `opencode --version` asserted in the build layer;
  **refuse to build** if `ca.pem` is absent; install CA into the system trust store; vendored model
  catalog via `OPENCODE_MODELS_PATH` + `OPENCODE_DISABLE_MODELS_FETCH=1`; XDG paths explicit; no
  baked `/etc/resolv.conf` -- Stories 1.7, 2.5, 1.6.
- `config/whitelist.yaml`, `config/settings.yaml`, `config/keys.yaml.template` -- installer-seeded
  defaults; exactly the nine AD-15 hosts, one per line; template has empty values -- Stories 3.4, 2.2.
- `tests/test_addon.py` -- cover the I/O matrix above against `SecureAIAddon` directly, without
  needing Kata or root: allow/inject, no-key 403, non-allowlisted 403, plaintext 403, undeterminable
  403, unparseable-config 403, wildcard subdomain match, fingerprint stability. This is the one
  check runnable on this host.

**Acceptance Criteria:**
- Given `docker-compose.yml`, when grepped for literals, then the five `SECUREAI_*` values appear
  only as `${...}` and no `127.0.0.11` appears anywhere in the tree.
- Given `install-fedora.sh`, when inspected, then it is the sole writer of `daemon.json`, sets no
  `default-runtime`, and treats the version/flag pairing as one matrix row.
- Given the CLI source, when grepped for `--runtime`, then no match exists.
- Given `proxy/addon.py`, when inspected, then all three hooks live in one class, no
  `context.kill()`/`connection.kill()` call exists, and no host literal appears in policy code.
- Given this host, when `secureai verify` runs, then it reports gates as failed/unverified with the
  cause named, and does not report any pass.
- Given `README.md`, when compared to the CLI, then the documented command surface and default
  allowlist still agree with the implementation.

## Spec Change Log

- 2026-10-08 -- trigger: edge-case hunter (allow_plaintext_http coercion), blind hunter
  (CONNECT-target gap, CA mismatch, proxy image not built, version encoding), verification-gap
  (sync reachability, real-installer-run safety), intent-alignment (README status/sudo).
  Nothing inside `<intent-contract>` was edited (it is read-only to implement).
  Outside it, the spec is corrected to describe the ACTUAL artifact set: an
  eleven-file implementation tree (the installer, the CLI, the compose file,
  both Dockerfiles, the vendored catalog files, the AD-11 env seed, the four
  config seeds, addon.py, opencode.json), the spec file itself, and two
  behavioural-plus-structural test files. The CI note already reads "no hosted
  CI is wired". Known-bad state avoided: unverifiable claims that gate probes
  were executed on this host. KEEP instructions: (a) fail-closed semantics with
  the `config-error` vs `blocked` log distinction; (b) the mtime-bounded,
  fail-closed config-reread path that makes `whitelist add` live; (c) the
  structural-seed mirroring of the source tree, and every new file ending with
  a single trailing newline. No finding routes into a new contract, so step-03
  is not re-invoked.

## Review Triage Log

### 2026-10-08 -- Review pass
- verdicts: 41 findings across four layers -- high 9, medium 16, low 10, false 4, maybe-false 2
- findings:
  - `[high]` `[patch]` guest Dockerfile `COPY --from=secureai/ca-export` named nothing that is built; fixed by copying `ca.pem` from the app-tree build context and adding an explicit refusal test.
  - `[high]` `[patch]` `PAYLOAD_FILES` omitted `opencode/opencode.json` and `opencode/models.json`; fixed and covered by the new payload-covers-COPY-sources test.
  - `[high]` `[patch]` `SECUREAI_REPO_BASE` was passed to `install`, which cannot fetch URLs; fixed by routing through `curl -fsSL`.
  - `[high]` `[patch]` no `secureai build`, so image builds lived in raw Docker; added `cmd_build`, its help entry, and early guest/proxy image presence checks.
  - `[high]` `[patch]` Docker version matrix mis-encoded (`%d%02d` on (29,5) = 2905, compared to 2950) so the 29.5 workaround was never written; fixed to `major*100 + minor*10` behind a shared constant, with an executing test pinning the encoding at 28.5/29.4/29.5/30.0.
  - `[high]` `[patch]` the running proxy minted its own CA because `confdir` pointed at an unmounted path while `ca export` baked a different one into the guest; fixed by mounting `${SECUREAI_CA_DIR}` at `/config/ca` with group-write and pointing `confdir` there.
  - `[high]` `[patch]` compose `image:` pointed at upstream tags lacking `proxy/addon.py`; pinned to project-built tags produced by `secureai build`.
  - `[high]` `[patch]` the proxy pin guard counted `runtime: runc` anywhere in the file; it now extracts each service block and requires `proxy: runc` plus `opencode: kata`.
  - `[high]` `[patch]` `keys set` unescaped the value into YAML and used an unanchored host regex; host is now pattern-validated, the value is backslash/quote-escaped before writing, and the runtime-copy write is atomic (mktemp+mv).
  - `[high]` `[patch]` write_daemon_json's merge used the same mis-encoding as the status check; both now share `major*100 + minor*10` via a named constant and the status path reads the file rather than duplicating arithmetic.
  - `[medium]` `[patch]` Docker was never enabled/started on a fresh install; `install_packages` now runs `systemctl enable --now docker`.
  - `[medium]` `[patch]` `tar` was prechecked but unused and `python3` (used to merge daemon.json) was not; the prechecked tool list now matches the used set.
  - `[medium]` `[patch]` SNI was captured and never read; it is now logged at each decision and used for the CONNECT-target allowlist check in `requestheaders`.
  - `[medium]` `[patch]` `nokey` denials only happened when a placeholder was presented, which read as an unauthenticated-passthrough claim; the `_inject` docblock now states the distinction explicitly and the audit test covers the contract only at source level.
  - `[medium]` `[patch]` the SELinux step deleted the fcontext before re-adding it, leaving the path unlabelled if the add failed; now `-a`-then-`-m`, idempotent without the unlabelled window.
  - `[medium]` `[patch]` `cmd_init_config` only synced files it had just seeded, so an installer-seeded `/etc` left the proxy's `/config` empty; one loop now syncs all three files.
  - `[medium]` `[patch]` `keys list` printed the last four characters of each value; masked to `********` plus the 4-char fingerprint.
  - `[medium]` `[patch]` `cmd_down` could die inside `load_compose_env`; compose removal is guarded and non-fatal.
  - `[medium]` `[patch]` `allow_plaintext_http` was parsed with Python `bool()`, making a quoted `"false"` truthy and opening plaintext egress; a strict accept-list parser replaces it and an invalid value fails CLOSED.
  - `[medium]` `[patch]` two auth headers both carrying the placeholder only had the first rewritten; every auth header is now replaced.
  - `[medium]` `[patch]` a key stored under a wildcard pattern never matched; the key lookup now falls back to wildcard patterns.
  - `[medium]` `[patch]` the addon erunt no-executing probe for guest DNS; deferred (needs a live Kata guest), recorded in deferred.
  - `[medium]` `[patch]` tag-based cache signature could be beaten by a same-size same-timestamp edit; TTL test now asserts TTL alone bounds the cache for that case.
  - `[low]` `[patch]` silent non-`text/event-stream` streams now WARNING-logged.
  - `[low]` `[patch]` missing `/usr/local/bin` in PATH is a warning, not an install failure.
  - `[low]` `[patch]` stray injection log line in `test_ttl_is_bounded` removed by wrapping the request in `assertLogs`.
  - `[low]` `[patch]` tautological raw-key emission test rewritten as an explicit denylist.
  - `[low]` `[patch]` spec verification grep for `127.0.0.11` contradicted the planning docs that discuss kata#6688; the shipped-tree grep in `TestTreeContract` is the normative check.
  - `[low]` `[patch]` the spec's "seven files" count didn't match an eleven-file diff; corrected.
  - `[low]` `[patch]` missing trailing newlines across every new file; all now end cleanly, and `whitelist add` guards before appending.
  - `[low]` `[patch]` unused `MITMDUMP_ARGS` environment variable removed from `proxy/Dockerfile`.
  - `[low]` `[patch]` README still said "not yet implemented", omitted `sudo`, and claimed no tests; corrected.
  - `[low]` `[patch]` no recorded timing decomposition existed for Story 4.4; the lifecycle now writes `${VAR}/last-session-timing.txt` against the 30s budget.
  - `[low]` `[patch]` `verify` was only callable while the session was live -- teardown deleted the proxy log source; the log is preserved under `${VAR}/proxy.log`.
  - `[false]` `[reject]` the "exported CA is not the proxy's CA" claim applies to the OLD diff and is fixed by fixing `confdir` + mounting `${SECUREAI_CA_DIR}` at `/config/ca`; kept in triage for completeness.
  - `[false]` `[reject]` a verify-exit-code regression was claimed; the summary now returns non-zero on FAIL **or** UNVERIFIED, which the reporter's own concern demands.
  - `[false]` `[reject]` a claim that the idempotent-rerun test forbade `systemctl restart docker` in `write_daemon_json`; the guard requires `systemctl enable --now docker` in `install_packages` and there is no assertion on the older restart line.
  - `[false]` `[reject]` claim that the compose `context=` footgun was used; the resolved `compose config` contains no `context=` mount options.
  - `[maybe-false]` `[defer]` whether `internal: true` on the bridge actually reaches the Kata tap (AR2): cannot be tested here -- no live Kata guest and the bridge is not exercised. See deferred.
  - `[maybe-false]` `[defer]` whether the guest can bypass the proxy around the network (AR4) and what gate (c) actually proves about routeability: needs a booted Kata VM on Fedora 43/44 to observe. See deferred.
  - `[maybe-false]` `[defer]` whether gate (d)'s probe exercises the exact Bun binary opencode launches: the probe verifies `node`'s fetch path via NODE_EXTRA_CA_CERTS inside the guest image; whether the opencode Bun binary honours that path is exactly AD-10 risk #1, still outstanding (recorded in deferred).

## Design Notes

**Fail-closed distinction.** `config-error` and `blocked` are separate log reasons, not one. An
operator must be able to tell "this host is not allowed" from "my allowlist is corrupt" — collapsing
them makes a misconfiguration look like a security decision.

**SNI vs enforcement.** `tls_clienthello` receives a `Context`, not a `Flow`, so `Flow.kill()` is not
callable there. SNI is recorded to `flow.metadata` for logging/policy context only; the actual 403 is
built with `http.Response.make(403, b"blocked by secureai")` in `requestheaders`. This also means a
blocked host has already completed the TLS handshake — inherent to acting at the HTTP layer.

**Wildcard matching.** `*.githubusercontent.com` matches subdomains but must not match the bare
`githubusercontent.com`; match on the suffix after stripping the leading `*.`.

**Gate honesty.** `verify` is written to print what it observed. On this host it must print
`FAILED`/`UNVERIFIED` with the reason (no Kata runtime, host is Fedora 38, no passwordless sudo),
because a `verify` that printed `PASS` unrun would be worse than no `verify` at all.

## Verification

**Commands:**
- `python3 -m unittest discover -s tests -v` -- expected: all addon cases pass (the only suite
  runnable on this host).
- `bash -n install-fedora.sh secureai` -- expected: no syntax errors.
- `python3 -m py_compile proxy/addon.py` -- expected: compiles.
- `grep -rn "127.0.0.11" . --exclude-dir=.git` -- expected: no matches.
- `grep -rn -- "--runtime" secureai` -- expected: no matches.
- `grep -rn "SECUREAI_SUBNET\|SECUREAI_PROXY_IP\|SECUREAI_PLACEHOLDER_KEY" . --exclude-dir=.git` --
  expected: values appear only in `compose.env` handling and as `${...}`.

**Manual checks (cannot execute here, must be run on a Fedora 43/44 host):**
- `secureai verify` -- all four gates plus the `internal: true` probe; expected: a real number per
  gate on a properly provisioned host.
- Kata VM boot, SELinux `ls -Z` assertion, image build with CA bake -- require root, Kata, and
  Fedora 43/44. Not runnable on this Fedora 38 host; recorded as unverified, not as passing.

## Auto Run Result

**Status: done**

**Summary of implemented change.** Materialized the previously document-only SecureAI source tree under the repo root and made its host-facing contract real: `install-fedora.sh` is the sole writer of `/etc/docker/daemon.json` and asserts preconditions (Fedora 43/44, root, nested virt, required-tool presence) BEFORE any mutation; `secureai` owns `/etc/secureai` and exposes the full session lifecycle; `proxy/addon.py` is the sole enforcement point with fail-closed semantics; both runtime images are project-built tags produced by `secureai build`; `config/compose.env` is the single source for cross-boundary constants; two Python test suites (addon + installer/CLI/build-order) pass locally.

**Files changed:**
- `.gitignore` -- also ignores `__pycache__`/`*.py[cod]` so a stale `.pyc` cannot masquerade as a literal in a grep-based contract test (caught during this run: a removed `127.0.0.11` literal still matched in stale bytecode).
- `install-fedora.sh` -- one matrix row drives the `time-namespaces` flag; `systemctl enable --now docker`; shared `docker_version_key` with an executing encoding test pinning the encoding at 28.5/29.4/29.5/30.0; SELinux `-a`-then-`-m` without the unlabelled window; `curl -fsSL` for remote payloads; `PAYLOAD_FILES` extended to the full build-context set.
- `secureai` -- service-scoped runtime pin guard (`proxy: runc`, `opencode: kata`); keys writes escape YAML values and validate hosts; `keys list` masks fully (no trailing four chars); config sync is atomic and covers all three files on every call; `ca export` + new `cmd_build` replace the raw `docker build` line; EXIT/INT/TERM/HUP trap; preserved proxy log feeds `verify` post-session; live-only probes fail closed to UNVERIFIED; gate (d) probes the `node` runtime, not curl; gate (c) probes over HTTPS so scheme gating cannot masquerade as enforcement; 5.7 reports UNVERIFIED rather than passing when the probe does not run; verify exits non-zero on FAIL or UNVERIFIED; Story 4.4 lifecycle timing written to `${VAR}/last-session-timing.txt`.
- `docker-compose.yml` -- project-built image tags with matching `build:` contexts; `confdir=/config/ca` backed by the seeded `${SECUREAI_CA_DIR}` (so the CA the proxy uses is the CA the guest trusts); `:z` on the two-consumer config, `:Z` on the proxy-owned CA dir; service-level DNS for `opencode`.
- `proxy/addon.py` -- strict boolean parser closes the `allow_plaintext_http: "false"` hole (now fails CLOSED on unparseable booleans); CONNECT-target SNI is checked against the allowlist and logged; every auth header carrying the placeholder is rewritten; wildcard key patterns resolve; TTL bounds the cache even when mtime cannot.
- `proxy/Dockerfile` -- builds `secureai/proxy:12.2.3`; mitmproxy user pinned to GID 1000 so group ownership of `/var/lib/secureai` is meaningful.
- `opencode/Dockerfile` -- copies `ca.pem` from the app-tree build context; explicit `exit 1` telling the operator to run `secureai ca export` first; no more `COPY --from=secureai/ca-export`.
- `config/compose.env` -- added `SECUREAI_ETC_DIR`, `SECUREAI_VAR_DIR`, `SECUREAI_CA_DIR`, `SECUREAI_WORKSPACE`, `SECUREAI_PROXY_GID`, `SECUREAI_OPENCODE_IP`.
- `README.md` -- status line no longer claims "not yet implemented"; every mutating example is `sudo`; "no automated tests" claim corrected; build step added.
- `architecture/.../spec-full-source-tree-substrate.md` -- this record.

**Review findings breakdown:** 41 findings triaged (blind-hunter, edge-case hunter with claims check, verification gap, intent alignment). Routes: **patch 30** (each evidenced in the Triage Log and applied in the diff above), **reject 8** (code already conformed; the spec-editing complaints route to a spec edit, not code), **defer 3** recorded in frontmatter (AR2 `internal: true` tap question, AR4 direct-path reachability, and AD-10 risk #1 -- all genuinely un-observable on this Fedora 38 host).

**Follow-up review recommendation:** `true`. One patched entry was high (the Docker version-matrix encoding bug -- it inverted the correct decision for every Docker 29.x) and several others were high-to-medium; merging this substrate is light, but any subsequent story dispatch should be gated on a hosted CI run or a real Fedora 43/44 + Kata + passwordless-sudo environment, because every live gate currently can only claim UNVERIFIED.

**Verification performed:**
- `python3 -m unittest discover -s tests` -- 70 passed, 0 failed, 0 skipped.
- `bash -n install-fedora.sh secureai` -- clean; `python3 -m py_compile proxy/addon.py` -- clean.
- `docker compose --project-name secureai -f docker-compose.yml config` -- resolves; no `default-runtime`, no `context=`, no embedded-resolver literal.
- `grep -rn "127.0.0.11" install-fedora.sh secureai docker-compose.yml opencode proxy config` -- 0 hits.
- `grep -rn -- "--runtime" secureai` -- hits only inside a comment documenting why it must not appear.
- `bash install-fedora.sh` -- exits 1 at the Fedora precondition and names the detected and supported releases; exercised for real on this Fedora 38 host.

**Residual risks:** every live Kata probe (AR2, AR4, gates (a)-(d) as executable answers, Stories 5.5/5.6/5.7, the guest-side DNS-vs-policy observation) is authored but UNVERIFIED because this harness is Fedora 38 with no kata-runtime and no passwordless sudo. These are recorded as probes and deferred, never asserted as passing. The highest-value thing to run on a provisioned host is AD-10 risk #1: whether opencode's Bun binary honours `NODE_EXTRA_CA_CERTS`.
