---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
inputDocuments:
  - 'prd-workspace/prd.md'
  - 'architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md'
  - 'architecture/secureai-kata-opencode/.memlog.md'
  - 'README.md'
---

# SecureAI — Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for SecureAI (Kata-Containerized OpenCode with a Secure Forward Proxy), decomposing the requirements from the PRD and the Architecture spine into implementable stories.

**Quality bar carried from the PRD and spine: showcase proof of concept, not production-hardened.** Where hardening and demonstrability conflict, demonstrability wins — and the conflict is named rather than silently absorbed.

**Design paradigm carried from the spine: sealed egress.** Two zones split by a trust boundary; the only path across it is a single mediating process that holds what neither zone may hold. Any change that adds a *second* path from the untrusted Kata guest to the internet violates the paradigm and requires rewriting the spine rather than amending it.

## Requirements Inventory

### Functional Requirements

```text
FR1:  A one-shot installer invoked as root on Fedora installs Kata Containers, Docker Engine, Docker Compose v2, and the SecureAI CLI to /usr/local/bin/secureai; installs the application tree machine-wide at /opt/secureai; and creates /etc/secureai (0700) and /var/lib/secureai (0700). The install is root-owned and machine-wide — no per-user variant, no checkout dependency (AD-16).
FR1b: Every absolute path used at runtime by a compose bind mount or by the CLI is one of the five installed paths (/usr/local/bin/secureai, /opt/secureai, /etc/secureai, /var/lib/secureai, and the derived workspace under /var/lib/secureai). The repository checkout is never referenced at runtime (AD-16).
FR2:  The installer asserts rather than assumes its prerequisites: root/sudo, Fedora 43 or 44, nested virtualization enabled, and a Docker/Kata version pair compatible with the Kata runtime. It fails with a readable message rather than proceeding on a bad host.
FR3:  install-fedora.sh is the ONLY writer of /etc/docker/daemon.json. It registers exactly one runtime named `kata` with `runtimeType: io.containerd.kata.v2` (the containerd runtime CLASS, which Docker resolves to the containerd-shim-kata-v2 binary) and ConfigPath under the runtime's options, and leaves `default-runtime` untouched.
FR4:  The installer writes `features.time-namespaces: false` into daemon.json ONLY when Docker >= 29.5.0 (below 29.5 the flag is not written). Docker 29.5+ without the flag breaks Kata with `invalid namespace type` (kata#13080).
FR4b: The installer treats Kata's GUEST kernel and initrd as a precondition: it reads the `kernel`/`image` paths from the Kata config it just wired, builds them via kata-osbuilder-generate.service if absent, and asserts they exist. The CLI re-asserts them before every session, because they symlink into /lib/modules/<running-kernel> and dangle after a kernel update. Absence must be reported by name, not surface as `vmlinuz.container does not exist` from the shim.
FR5:  The installer relabels /etc/secureai and /var/lib/secureai using `semanage fcontext -a -t container_file_t` followed by `restorecon`, asserts the result with `ls -Z`, and warns-but-continues when SELinux is permissive or disabled.
FR6:  OpenCode runs inside a Kata Containers VM using the `kata` runtime declared on the compose `opencode` service. Exactly one artifact declares the opencode runtime — docker-compose.yml — and the CLI passes no `--runtime` flag.
FR7:  The forward proxy runs in a container explicitly pinned to `runtime: runc`, so the key-holding process never enters the Kata VM. The installer asserts this pin.
FR8:  The proxy container holds the real API keys, mounted read-only from /var/lib/secureai. Real keys never appear in the opencode image, docker-compose.yml, guest environment, compose.env, or any log.
FR9:  At runtime the proxy replaces the placeholder key with the real key for the matching upstream host, rewriting outbound request headers only.
FR10: The placeholder key `sk-secureai-placeholder` is the only API key the guest ever sees. Keys flow one way — guest request to proxy to upstream. Keys never flow back into the guest.
FR11: Injection is logged as `authz=injected provider=<host> fp=<4-char sha256 prefix>` — a fingerprint, never key material. `keys list` always masks values.
FR12: The proxy enforces an egress whitelist by validating SNI/Host against the configured domain list. A non-allowlisted host receives HTTP 403 with body `blocked by secureai`.
FR13: Enforcement gates on transport scheme as well as SNI/Host, so plaintext `http://` egress is also gated. Until this is designed, scheme `http` is an unguarded path.
FR14: The proxy fails closed: unknown host, unreadable config, unparseable config, or missing key for an allowlisted host yields 403, never passthrough.
FR15: `tls_clienthello` reads SNI for policy and logging and takes NO action. All enforcement acts at `requestheaders` via `http.Response.make(403)`. One addon class holds all three hooks; addon instance attributes are process-global and never carry per-flow request state (per-flow state lives in `flow.metadata`).
FR16: Responses whose content-type is `text/event-stream` are streamed — `.stream = True` is set in `responseheaders` BEFORE any body is read — so LLM token streams emit instead of buffering until opencode's timeout. A response whose body must be rewritten is never streamed.
FR17: The mitmproxy CA is installed into the guest SYSTEM trust store (/usr/local/share/ca-certificates/ + update-ca-certificates) at image build. SSL_CERT_FILE / REQUESTS_CA_BUNDLE / GIT_SSL_CAINFO are either unset or point at a CONCATENATED bundle, never at the mitm CA alone. Only the public cert reaches the guest; the private key never leaves the proxy.
FR18: The guest reaches the proxy at a fixed literal IP, not by DNS: the guest env carries `HTTPS_PROXY=http://${SECUREAI_PROXY_IP}:${SECUREAI_PROXY_PORT}`. Docker's embedded resolver (nameserver 127.0.0.11) does not exist inside the Kata VM and must not be relied on.
FR19: docker-compose.yml sets `dns:` on the opencode service to the bridge gateway, and opencode/Dockerfile does NOT bake /etc/resolv.conf. Upstream name resolution is guest-local and carries no enforcement meaning — SNI is the enforcement signal.
FR20: `NO_PROXY=localhost,127.0.0.1,::1` is always set in the guest env. Without it the TUI's loopback server call routes back through the proxy and deadlocks.
FR21: `secureai whitelist add <domain>` updates /etc/secureai/whitelist.yaml, syncs it to /var/lib/secureai, and writes an audit line.
FR22: Whitelist changes take effect in a RUNNING proxy with no restart: the addon re-reads config per flow with an mtime-bounded cache (default 5s).
FR23: `secureai whitelist list` prints the current allowlist.
FR24: `secureai keys set <host> <key>` stores a key in /etc/secureai/keys.yaml and syncs to /var/lib/secureai. Config files are 0640 root:root.
FR25: `secureai keys list` prints hosts with values masked.
FR26: `secureai opencode` starts the proxy first, waits for its healthcheck, then attaches the user INTERACTIVELY inside the opencode container via `docker compose run --rm -it` — not `up`, not `docker exec`.
FR27: Both services use `--rm` semantics, and a shell `trap` in the CLI tears the session down on INT/TERM. No orphaned containers remain after a session. No systemd unit auto-starts the proxy.
FR28: The proxy service carries a real healthcheck, and the opencode service waits on `depends_on: proxy: condition: service_healthy`.
FR29: `secureai down` stops and removes the session containers.
FR30: `secureai status` reports whether the stack is running.
FR31: `secureai ca export` runs a one-shot mitmproxy container with `--set confdir=/var/lib/secureai/ca` and copies `mitmproxy-ca-cert.pem` to /var/lib/secureai/ca.pem (0644).
FR32: Build order is enforced: `secureai ca export` runs BEFORE `docker build` of the opencode image, because the Dockerfile COPYs the CA. The CA is never bind-mounted into a running container.
FR33: The opencode image is built from `node:22-bookworm-slim` (glibc) and EXPLICITLY installs git, python3, python3-pip, default-jdk-headless, ripgrep, jq, curl, openssh-client, plus `uv` from ghcr.io/astral-sh/uv — under a non-root `node` user (UID 1000), with UV_LINK_MODE=copy and UV_NATIVE_TLS=1.
FR34: `opencode-ai@1.18.34` is installed from the glibc package (never `-musl`), with scripts enabled; `opencode --version` must succeed in the build layer or postinstall throws. `--ignore-scripts` yields a useless stub and is forbidden.
FR35: Guest config disables outbound self-widening: `autoupdate: false`, `share: "disabled"`, `shell: "/bin/sh"`. The model catalog is VENDORED — `OPENCODE_MODELS_PATH` points at a pre-seeded JSON and `OPENCODE_DISABLE_MODELS_FETCH=1` — so models.opencode.ai leaves the allowlist entirely.
FR36: XDG_CONFIG_HOME=/home/node/.config, XDG_DATA_HOME=/home/node/.local/share, XDG_CACHE_HOME=/home/node/.cache, XDG_STATE_HOME=/home/node/.local/state are set explicitly, and those paths are the persisted volumes.
FR37: Provider SDK binaries opencode would otherwise install at runtime into ~/.cache/opencode/bin are PRE-SEEDED at build. A runtime-discovered dependency is a build failure, not an allowlist entry.
FR38: /etc/secureai/compose.env (0640, root) holds SECUREAI_SUBNET, SECUREAI_GATEWAY, SECUREAI_PROXY_IP, SECUREAI_PROXY_PORT, SECUREAI_PLACEHOLDER_KEY — and is the SOLE source for these constants. docker-compose.yml references them as `${...}` only; the CLI sources the same file before invoking docker compose. install-fedora.sh may create the file but not populate it; the CLI is its sole writer.
FR39: The allowlist is seeded by the CLI with the exact reviewed AD-15 baseline: api.anthropic.com, github.com, *.githubusercontent.com, registry.npmjs.org, pypi.org, files.pythonhosted.org, models.dev, models.opencode.ai, opencode.ai, astral.sh. The addon contains NO default-allow and NO host literal.
FR40: `secureai verify` shows all four gates: (a) the proxy log shows the API host; (b) injection occurred, logged with a fingerprint; (c) a non-allowlisted host fails THROUGH THE PROXY; (d) an HTTPS call from the Bun/Node runtime INSIDE THE GUEST through the proxy succeeds. curl succeeding proves only curl.
FR41: Bind mount labels follow CONSUMER COUNT, where consumer means a compose service: `:Z` for a single-consumer mount, `:z` for two or more. No unlabelled bind read is permitted on an enforcing host. No `context=` mount option is ever passed.
FR42: The proxy addon file is world-readable in the proxy image, because the mitmproxy entrypoint gosu-drops to the `mitmproxy` user with HOME=/home/mitmproxy.
FR43: The CLI is bash throughout — no new language dependency. `yq` is not used.
FR44: config/ ships whitelist.yaml, settings.yaml, and keys.yaml.template (empty values for onboarding), installer-seeded.
```

### NonFunctional Requirements

```text
NFR1:  Security — Real API keys are never exposed to the OpenCode container, never present in the opencode image, compose file, guest env, or compose.env, and never logged.
NFR2:  Security — Keys flow in one direction only: guest request carries a name, never a secret; the proxy holds what neither zone may hold. Keys never flow from the trusted zone back into the guest.
NFR3:  Isolation — OpenCode runs in a Kata VM (VM-level isolation, not merely a container).
NFR4:  Isolation — The guest is not trusted to comply. All enforcement lives in the single proxy addon; the guest receives no policy and filters nothing. HTTPS_PROXY is set because the runtime honors it, NOT because it is a control.
NFR5:  Isolation — Exactly one mediating chokepoint. Any change adding a second path from the Kata guest to the internet invalidates the paradigm and requires rewriting the spine.
NFR6:  Startup — The SecureAI stack is ready within 30s of `secureai opencode`. This budget is a NAMED TENSION against the PRD's acceptance of slow Kata boot; if 30s cannot be met that is a PRD conversation, not a code fix.
NFR7:  UX — Zero Docker knowledge required. The CLI abstracts all infrastructure; the user never types `docker`.
NFR8:  UX — `secureai opencode` takes the user directly into OpenCode (interactive attach, not `docker exec`).
NFR9:  SELinux — All bind mounts carry labels on an enforcing Fedora host; no permission-denied errors. The PRD's "all bind mounts use :Z" is AMENDED to :Z/:z by consumer count.
NFR10: Maintainability — Minimal custom code: bash CLI, standard Docker Compose, one addon file.
NFR11: Portability — Fedora 43 or 44 only. Multi-platform support is a PRD non-goal; the installer targets the platform rather than degrading on others.
NFR12: Compatibility — Version matrix is one row, not a disjunction: Docker <= 28.5.2, or >= 29.5.0 with `features.time-namespaces: false`. Kata 3.25.0 (F43) / 3.26.0 (F44), Go runtime. `kata-qemu` is a NAME, not an executable; Docker is given the runtime class `io.containerd.kata.v2`, which it resolves to containerd-shim-kata-v2.
NFR13: Observability — mitmproxy runs with `--set flow_detail=0` and `-q`. One line per blocked flow carrying host, scheme, and decision. The addon never logs header values. Injection is logged as a fingerprint only.
NFR14: Quality bar — Showcase proof of concept, not production-hardened. Automated tests/CI, upgrade/migration paths, multi-user key management, performance tuning, and comprehensive documentation are explicitly out of MVP scope.
NFR15: Config integrity — One writer, one read path: the CLI is the only writer of /etc/secureai; the proxy mounts config read-only and never writes it. The addon re-reads config per flow and fails closed.
```

### Additional Requirements

Extracted from `architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md`.

**Starter template**

```text
AR1: No starter/greenfield template is specified. All files are authored from scratch per the spine's structural seed. No scaffolding step is required or permitted to substitute for the specified layouts.
```

**Unresolved architecture questions — these become decision stories, not build stories**

```text
AR2:  BLOCKING — Determine whether `internal: true` on the bridge network applies to the Kata VM's tap device. If it does, the guest has no route to the internet and AD-1's accepted risk is VOID — the stack is stronger than the spine claims. If it does not, the setting is false comfort. AD-1's risk statement stays provisional until this is tested. Nothing else in the spine depends on it.
AR3:  Design and implement plaintext `http://` egress gating. The addon must gate on transport scheme as well as SNI/Host, or scheme http remains an unguarded path covered only by AD-1's accepted risk.
AR4:  Resolve whether the pinned Bun build honors NODE_EXTRA_CA_CERTS. Bun 1.3.6 reportedly ignored it (bun#17325); opencode carries the same failure family (#3130, #4883, #3566, #923, #1694, #7073). The spine names this the FIRST thing to smoke-test, before any other work. If Bun ignores it, AD-4's trust model does not reach the actual runtime and the concatenated-bundle fallback must be designed.
AR5:  DNS-level enforcement beyond SNI is inherited unanswered. SNI is the enforcement signal; upstream resolution is guest-local per AD-5. Record the decision either way.
```

**Infrastructure, deployment, and integration**

```text
AR6:  Deployment target is a developer Fedora workstation with nested virtualization. One environment; no staging, no multi-host.
AR7:  Bootstrap is one-shot and pipeable: `curl -fsSL <url>/install-fedora.sh | sudo bash`. It must therefore assert its own preconditions rather than assume them.
AR8:  SUPERSEDED by AD-16 (spine amended 2026-10-08). The installation is machine-wide and root-owned at conventional system paths: `/usr/local/bin/secureai` (sole PATH entry), `/opt/secureai/` (app tree: docker-compose.yml, opencode/Dockerfile, proxy/, config/), `/etc/secureai/` (0700), `/var/lib/secureai/` (0700). Any absolute path a bind mount or the CLI uses is one of these five; the repository checkout is never referenced at runtime. The spine's structural seed is the SOURCE tree only.
AR8b: Because there is no git-clone prerequisite and no upgrade path (out of MVP scope), `install-fedora.sh` must work with only the `curl | sudo bash` URL present — the install artifact is self-contained or the script fetches compose, Dockerfiles, addon, and config/ recursively from the same host.
AR9:  Exactly one artifact declares the opencode runtime (docker-compose.yml); the CLI passes no --runtime flag, because a flag silently overrides the compose field with no error. The proxy service declares `runtime: runc` explicitly.
AR10: mitmproxy 12.2.3 on the `mitmproxy/mitmproxy:12.2.3` image — Debian-based (trixie), NOT Alpine.
AR11: opencode-ai@1.18.34 is a Bun-compiled NATIVE binary (~185 MB ELF, Bun + JavaScriptCore, no V8), not a Node runtime. The `node` base image supplies nothing the runtime needs. `ghcr.io/sst/opencode` no longer exists; the upstream reference moved to `ghcr.io/anomalyco/opencode` (Alpine, root, no git) and is not used.
AR12: /etc/secureai (0700) is the sole source of truth and is synced to /var/lib/secureai (0700), which the proxy mounts read-only. This sync model is what makes F7's "no restart needed" true.
AR13: The opencode image is built AFTER `secureai ca export`; the CA has one producer and a defined build order, preventing a circular dependency and preventing the CA having three legal homes.
```

**Security implementation requirements**

```text
AR14: Real keys never appear in the opencode image, compose file, guest env, compose.env, or logs. Logs carry a 4-char fingerprint only. `keys list` masks.
AR15: The proxy fails closed: unknown host, unreadable config, missing key => 403, never passthrough.
AR16: opencode performs no TLS pinning (verified: zero rejectUnauthorized / ProxyAgent / undici in source), so MITM substitution is safe — this resolves the PRD's open question on certificate pinning.
AR17: The system trust store satisfies curl and git ONLY. Node and Bun are NOT assumed to read it; they ship bundled Mozilla sets.
```

**Logging and observability requirements**

```text
AR18: mitmproxy runs with `--set flow_detail=0` and `-q`. Scrubbing happens at source.
AR19: One line per blocked flow: host, scheme, decision.
AR20: The addon never logs header values.
```

**Data formats and conventions**

```text
AR21: YAML config: whitelist.yaml, settings.yaml, keys.yaml (+ keys.yaml.template with empty values). Whitelist is one host per line; `*.example.com` matches subdomains. Keys are `host: key`, values quoted. Config files 0640 root:root.
AR22: Error shape is HTTP 403 with body `blocked by secureai`.
AR23: Naming: `secureai` CLI; `secureai_net` bridge; compose services are singular nouns (`opencode`, `proxy`); addon classes are behavior-named (`SecureAIAddon`). Anything crossing the compose boundary lives in compose.env and is referenced as ${...} — the placeholder is one constant, never a duplicated literal.
AR24: CLI surface is exactly: `opencode`, `down`, `status`, `whitelist add|list`, `keys set|list`, `ca export`, `verify`.
```

**Diagnostics**

```text
AR25: Whether Kata + Docker needs a policy module for the VMM path is unverified. If host denials appear, `ausearch -m avc -ts recent` is the diagnostic.
AR26: The blocked-host success criterion is scoped honestly: `curl https://google.com` fails BECAUSE curl honors the proxy env vars. That test proves the proxy enforces; it does NOT prove the guest cannot route around it. No story may claim otherwise.
```

### UX Design Requirements

None. No UX design document exists for this project, and none is required: the PRD explicitly rules out a GUI or web interface as a non-goal, and the sole user-facing surface is the bash CLI (`secureai opencode`, `down`, `status`, `whitelist add|list`, `keys set|list`, `ca export`, `verify`). UX obligations are captured as NFR7 (zero Docker knowledge), NFR8 (direct interactive attach), and the CLI-surface and error-shape conventions in AR24 and AR22.

### FR Coverage Map

```text
FR1:  Epic 1 — one-shot machine-wide install to the five AD-16 paths
FR1b: Epic 1 — runtime path set closed; checkout never referenced
FR2:  Epic 1 — installer asserts preconditions (root, F43/44, nested virt, version matrix)
FR3:  Epic 1 — daemon.json: sole writer, one `kata` runtime, default-runtime untouched
FR4:  Epic 1 — time-namespaces false only when Docker >= 29.5
FR5:  Epic 1 — SELinux relabel via semanage + restorecon + ls -Z assertion
FR6:  Epic 1 — opencode service on the `kata` runtime; CLI passes no --runtime flag
FR7:  Epic 2 — proxy service pinned to `runtime: runc`
FR8:  Epic 2 — real keys in the proxy only, mounted :ro, absent from image/compose/env
FR9:  Epic 2 — placeholder swapped for the real key on outbound request headers
FR10: Epic 2 — guest sees only the placeholder; keys never flow B -> A
FR11: Epic 2 — injection logged as a 4-char fingerprint; keys list masks
FR12: Epic 3 — SNI/Host validated against the allowlist; 403 `blocked by secureai`
FR13: Epic 3 — enforcement gates on transport scheme as well as SNI/Host
FR14: Epic 3 — fail closed on unknown host, unreadable config, missing key
FR15: Epic 3 — tls_clienthello observes only; action at requestheaders; one addon class
FR16: Epic 3 — text/event-stream responses streamed in responseheaders before body read
FR17: Epic 2 — mitm CA into the guest system trust store; never SSL_CERT_FILE alone
FR18: Epic 1 — guest HTTPS_PROXY by literal IP, never Docker service-name DNS
FR19: Epic 1 — dns: bridge gateway on the service; no baked /etc/resolv.conf
FR20: Epic 2 — NO_PROXY=localhost,127.0.0.1,::1 always set
FR21: Epic 3 — whitelist add writes, syncs, audit line
FR22: Epic 3 — addon re-reads config per flow (mtime cache, 5s); no restart needed
FR23: Epic 3 — whitelist list prints current allowlist
FR24: Epic 2 — keys set stores 0640 root:root and syncs to /var/lib/secureai
FR25: Epic 2 — keys list always masks values
FR26: Epic 4 — opencode launches proxy first, waits for health, attaches interactively
FR27: Epic 4 — --rm semantics plus INT/TERM trap; no orphans; no systemd unit
FR28: Epic 2 — proxy healthcheck plus depends_on service_healthy
FR29: Epic 4 — down stops and removes session containers
FR30: Epic 4 — status reports whether the stack is running
FR31: Epic 1 — ca export runs one-shot mitmproxy, writes /var/lib/secureai/ca.pem 0644
FR32: Epic 1 — build order: ca export before docker build; CA never bind-mounted
FR33: Epic 1 — node:22-bookworm-slim base plus explicit toolchain install, non-root node
FR34: Epic 1 — opencode-ai@1.18.34 glibc, scripts enabled, --version verified in build
FR35: Epic 1 — autoupdate false, share disabled, shell /bin/sh, vendored model catalog
FR36: Epic 1 — XDG_* set explicitly to /home/node/... and are the persisted volumes
FR37: Epic 1 — provider SDK binaries pre-seeded at build; runtime discovery is a build failure
FR38: Epic 1 — compose.env is the sole source for the five cross-boundary constants
FR39: Epic 3 — CLI seeds the exact AD-15 baseline; addon has no host literal
FR40: Epic 5 — verify shows all four gates
FR41: Epic 1 — :Z single-consumer, :z multi-consumer; no unlabelled read; no context=
FR42: Epic 2 — addon.py world-readable in the proxy image for the gosu drop
FR43: Epic 1 — bash throughout, no yq, no new language dependency
FR44: Epic 1 — config/ ships whitelist, settings, keys.yaml.template, installer-seeded
```

### NFR Coverage Map

```text
NFR1:  Epic 2 — keys absent from image, compose, guest env, compose.env, and logs
NFR2:  Epic 2 — one-directional key flow; the proxy holds what neither zone may hold
NFR3:  Epic 1 — OpenCode in a Kata VM, not merely a container
NFR4:  Epic 3 — the guest is not trusted to comply; enforcement is proxy-side only
NFR5:  Epic 5 — the single-chokepoint prediction is itself verified
NFR6:  Epic 4 — Story 4.4 measures the real time to a ready session and records it against the 30s budget
NFR7:  Epic 4 — zero Docker knowledge; the user never types docker
NFR8:  Epic 4 — direct interactive attach, not docker exec
NFR9:  Epic 1 — labels on every bind mount; PRD's :Z-only wording amended to :Z/:z
NFR10: Epics 1–5 — minimal custom code throughout
NFR11: Epic 1 — Fedora 43/44 asserted by the installer, not degraded onto
NFR12: Epic 1 — version matrix as one row, time-namespaces conditional
NFR13: Epics 2, 3 — flow_detail=0 and -q; fingerprint-only injection logging
NFR14: Epics 1–5 — showcase PoC bar; hardening deferred by PRD
NFR15: Epic 3 — one writer, one read path; proxy never writes config
```

## Epic List

### Epic 1: Host Foundation & Isolated Guest Environment

A developer runs one install command on a supported Fedora workstation and, with no Docker
knowledge, launches an interactive OpenCode session inside a Kata Containers VM that has a
complete toolchain, a vendored model catalog, and a network contract addressed by fixed IP.
The installation is machine-wide at conventional system paths, the Docker runtime wiring is
owned by exactly one writer, and every host path is SELinux-labelled — so the guest runs, and
every later epic has a substrate to build on.

**FRs covered:** FR1, FR1b, FR2, FR3, FR4, FR5, FR6, FR18, FR19, FR31, FR32, FR33, FR34,
FR35, FR36, FR37, FR38, FR41, FR43, FR44
**NFRs covered:** NFR3, NFR9, NFR10, NFR11, NFR12
**Stories:** 1.1–1.8 (8)
**Architecture governed:** AD-5 (fixed IP, no baked resolv.conf), AD-6 (sole daemon.json
writer, version matrix), AD-7 (SELinux labelling), AD-8 (no self-widening egress), AD-9
(toolchain is an explicit install), AD-11 (compose.env), AD-12 (CA producer + build order),
AD-16 (machine-wide installed layout), AR23 (naming conventions)
**Deliberately absent:** key custody (Epic 2), whitelist enforcement (Epic 3), session
lifecycle commands (Epic 4). The session launches and the agent runs; it cannot yet reach an
API. `daemon.json` leaves `default-runtime` untouched here precisely so the key-holding proxy
cannot be drawn into the Kata VM when Epic 2 introduces it.

### Epic 2: Key Custody & Transparent Interception

A developer's OpenCode session reaches a real model API successfully while the agent — and
anyone reading its environment or filesystem — holds only a placeholder. The proxy holds the
real keys in a trusted runc container, trusts the guest's TLS via the system trust store,
injects the correct key per host, and logs only a fingerprint. Operators store and list keys
without ever seeing a value.

**FRs covered:** FR7, FR8, FR9, FR10, FR11, FR17, FR20, FR24, FR25, FR28
**NFRs covered:** NFR1, NFR2, NFR13
**Architecture governed:** AD-4 (CA extends the trust store, never replaces it), AD-5
(`NO_PROXY` corollary), AD-6 (proxy pinned to runc), AD-12 (CA build order), AD-13 (one writer,
one read path)
**Carries the highest risk in the project:** opencode runs a Bun-compiled native binary whose
handling of `NODE_EXTRA_CA_CERTS` is unconfirmed. The system's CA story satisfies curl and git;
whether it satisfies the actual agent runtime is the first thing to prove.

### Epic 3: Sealing Egress to an Approved List

A developer confines the agent's egress to a reviewed set of hosts. Allowlisted services work,
anything else receives a readable HTTP 403, plaintext `http://` is gated on scheme as well as
host, and unreadable or missing configuration fails closed rather than passing traffic. The
operator can add a domain and see it take effect in a running session without restarting
anything — and the list is a single owned baseline with no host literals buried in code.

**FRs covered:** FR12, FR13, FR14, FR15, FR16, FR21, FR22, FR23, FR39
**NFRs covered:** NFR4, NFR13, NFR15
**Architecture governed:** AD-1 (proxy is the sole enforcement point), AD-2 (HTTP-layer
enforcement), AD-3 (stream SSE before reading the body), AD-13 (per-flow config re-read),
AD-15 (owned allowlist baseline)
**Honesty constraint:** the blocked-host criterion proves *the proxy enforces*, because the
client honors the proxy variables. It does not prove the guest cannot route around the proxy.
No story in this epic may claim otherwise.

### Epic 4: Session Lifecycle & Operator Control

A developer's session starts as a self-contained pair, ends the moment they leave it, and
leaves nothing behind. `secureai opencode` brings the proxy up first, waits for it to be
healthy, and drops the user straight into the agent; leaving tears both containers down.
`secureai status` and `secureai down` report and control the stack without the user typing
`docker` once.

**FRs covered:** FR26, FR27, FR29, FR30
**NFRs covered:** NFR6 (measured and recorded, never optimized — Story 4.4), NFR7, NFR8
**Architecture governed:** AD-14 (session lifecycle; the proxy dies with the agent), AD-16
(single PATH entry)
**Named tension:** the PRD's 30s readiness budget is in tension with accepted Kata boot time.
This epic is where the measurement surfaces — Story 4.4 times a real session and records the
result against that budget. A miss is a PRD conversation, not a code fix.

### Epic 5: Proving the Security Claim

A reviewer can see, rather than assume, that the isolation holds: `secureai verify` shows the
API host in the proxy log, shows injection with a fingerprint, shows a non-allowlisted host
failing through the proxy, and shows an HTTPS call succeeding from the runtime inside the guest
— not merely from curl. The same pass resolves the three open architecture questions, including
the blocking one about whether the bridge's `internal: true` applies to the Kata tap device,
so the spine's accepted risk is either retired or stated accurately.

**FRs covered:** FR40
**NFRs covered:** NFR3 (isolation observed, not asserted), NFR5, NFR13
**Architecture governed:** AD-10 (four gates, fingerprint logging), AD-1 (accepted risk made
provisional pending the `internal: true` answer), AD-2 (what gate (c) may and may not claim)
**Carries AR2 (BLOCKING), AR3, AR4, AR5** — the decision work, not just the command. A clean
session launch is explicitly not evidence, and neither is a passing curl.

### Dependencies

```text
Epic 1 -> Epic 2 -> Epic 3
        -> Epic 4 (uses Epic 1's substrate and Epic 2's proxy health)
Epic 5 depends on all four; it is the last thing that can honestly be claimed.
```

Each epic stands alone: Epic 1's agent runs but cannot reach an API; Epic 2's calls work
against the seeded baseline; Epic 3 seals what Epic 2 enabled; Epic 4 governs the session all
three run inside; Epic 5 proves the result. No epic requires a later epic to function.

## Epic 1: Host Foundation & Isolated Guest Environment

A developer runs one install command on a supported Fedora workstation and launches an
interactive OpenCode session inside a Kata Containers VM that has a complete toolchain, a
vendored model catalog, and a network contract addressed by fixed IP. The installation is
machine-wide at conventional system paths, Docker runtime wiring is owned by exactly one
writer, and every host path is SELinux-labelled — so the guest runs, and every later epic has
a substrate to build on.

### Story 1.1: Machine-wide installation to conventional system paths

As a developer on a Fedora workstation,
I want one command to install SecureAI system-wide at conventional paths,
So that the tool is available to everyone on this host with no checkout to keep alive.

**Acceptance Criteria:**

**Given** a Fedora 43/44 host with sudo access
**When** I run `curl -fsSL <url>/install-fedora.sh | sudo bash`
**Then** Kata Containers, Docker Engine, and Docker Compose v2 are installed and `/usr/local/bin/secureai` is on PATH
**And** `/opt/secureai/` holds `docker-compose.yml`, `opencode/Dockerfile`, `proxy/{Dockerfile,addon.py}`, and `config/{whitelist.yaml,settings.yaml,keys.yaml.template}`, root-owned (AD-16)
**And** `/etc/secureai` (0700) and `/var/lib/secureai` (0700) exist
**And** the install completes with no `git clone` present — the artifact is self-contained or fetched recursively from the same host
**And** no per-user install path and no `~/bin` shim is created; every runtime path resolves to one of the five installed paths
**And** re-running the installer is idempotent — it does not fail on existing paths

### Story 1.2: Installer asserts preconditions instead of assuming them

As a developer,
I want the installer to refuse a host it cannot support,
So that a failed session is never blamed on SecureAI when the host was never capable.

**Acceptance Criteria:**

**Given** a host that is not Fedora 43 or 44
**When** the installer runs
**Then** it exits non-zero, naming the detected distribution and the supported versions
**And** given no root/sudo
**When** the installer runs
**Then** it exits non-zero before modifying anything on the host
**And** given nested virtualization disabled
**When** the installer runs
**Then** it exits non-zero, naming the requirement and how to enable it
**And** every precondition check completes before the first package install or file write
**And** the failure messages are readable by a developer who does not know Kata or Docker

### Story 1.3: Docker runtime wired to Kata by a single writer

As a developer,
I want the Kata runtime registered correctly and by exactly one writer,
So that the agent runs in a VM and the key-holding proxy is never drawn into it.

**Acceptance Criteria:**

**Given** a clean install
**When** I inspect `/etc/docker/daemon.json`
**Then** it declares exactly one runtime named `kata`, with `runtimeType: io.containerd.kata.v2` (the containerd runtime class, which Docker resolves to the containerd-shim-kata-v2 binary — the bare shim binary name is rejected as an invalid runtime name) and `ConfigPath` under that runtime's options
**And** `default-runtime` is **absent** — left at Docker's own default, never set to `kata` (AD-6)
**And** the CLI passes no `--runtime` flag anywhere, and the opencode runtime is declared only in `docker-compose.yml`
**And** the CLI asserts that the `proxy` service declares `runtime: runc` explicitly before starting a session
**And** no second artifact — the CLI, a compose override, or a script — writes `daemon.json`
**And** `kata-qemu` is treated as a runtime **name**, never invoked as an executable path
**And** the `kernel`/`image` paths in that Kata config are asserted to exist after the install, with `kata-osbuilder-generate.service` run to produce them if absent

### Story 1.4: Docker 29.5+ time-namespaces conditional

As a developer on a current Fedora release,
I want the Kata compatibility workaround applied exactly when it is needed,
So that Kata starts instead of failing with `invalid namespace type`.

**Acceptance Criteria:**

**Given** Docker ≥ 29.5.0
**When** the installer writes `daemon.json`
**Then** `features.time-namespaces: false` is present
**And** given Docker ≤ 28.5.2
**When** the installer writes `daemon.json`
**Then** the flag is **not** written
**And** the version/flag pairing is expressed as one matrix row, not as independent OR-ed conditions
**And** `secureai status` asserts the host's actual Docker version against the pairing in use
**And** the supported combinations are Fedora 43 (Kata 3.25.0) or Fedora 44 (Kata 3.26.0), Go runtime

### Story 1.5: SELinux labels on host state

As a developer on an enforcing Fedora host,
I want the state directories labelled correctly at install time,
So that no mount is ever refused with permission denied.

**Acceptance Criteria:**

**Given** SELinux enforcing
**When** the installer completes
**Then** `/etc/secureai` and `/var/lib/secureai` carry `container_file_t`, applied with `semanage fcontext -a -t container_file_t` followed by `restorecon` (AD-7)
**And** the resulting label is **asserted** with `ls -Z`, not assumed from the command's exit code
**And** given SELinux permissive or disabled
**When** the installer runs
**Then** it warns and continues rather than failing
**And** `restorecon` is idempotent and safe to re-run
**And** `ausearch -m avc -ts recent` is documented as the diagnostic if host denials appear on the Kata VMM path

### Story 1.6: CA export and image build order

As a developer,
I want the proxy's CA produced before the guest image is built,
So that the guest image can bake system trust instead of taking a runtime bind mount.

**Acceptance Criteria:**

**Given** no CA exists
**When** I run `secureai ca export`
**Then** a one-shot mitmproxy container runs with `--set confdir=/var/lib/secureai/ca` and `mitmproxy-ca-cert.pem` is copied to `/var/lib/secureai/ca.pem` (0644) (AD-12)
**And** re-running export does not invalidate trust already baked into the guest image
**And** when the opencode image is built and `/var/lib/secureai/ca.pem` is absent
**Then** the build refuses to proceed and names the command to run
**And** the CA is never bind-mounted into a running container
**And** only the public certificate ever reaches the guest; the private key never leaves the proxy container

### Story 1.7: Guest image with toolchain, baked CA, and fixed-IP network contract

As a developer,
I want an agent image that already contains everything it needs and reaches the proxy by fixed IP,
So that the agent works for the concept demo.

**Acceptance Criteria:**

**Given** a clean host
**When** I build the opencode image
**Then** the base is `node:22-bookworm-slim` (glibc) and `git`, `python3`, `python3-pip`, `default-jdk-headless`, `ripgrep`, `jq`, `curl`, `openssh-client`, and `uv` are installed explicitly under a non-root `node` user (UID 1000), with `UV_LINK_MODE=copy` and `UV_NATIVE_TLS=1` (AD-9)
**And** `opencode-ai@1.18.34` is installed from the **glibc** package — never `-musl` — with scripts enabled, and `opencode --version` succeeds in the build layer or the build fails
**And** the mitm CA is installed to `/usr/local/share/ca-certificates/` with `update-ca-certificates`, and the guest trusts it via the standard CA env (AD-4)
**And** guest config sets `autoupdate: false`, `share: "disabled"`, `shell: "/bin/sh"`
**And** the model catalog may be fetched at runtime via the allowlist (`models.opencode.ai` is part of the baseline); a missing provider SDK at build is a warning, not a failure
**And** `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME` are set explicitly under `/home/node`
**And** the guest env carries `HTTPS_PROXY=http://${SECUREAI_PROXY_IP}:${SECUREAI_PROXY_PORT}` sourced from `compose.env`, never a compose service name (AD-5)
**And** `NO_PROXY=localhost,127.0.0.1,::1` is set in the guest env
**And** `docker-compose.yml` sets `dns:` on the opencode service to public resolvers, and `opencode/Dockerfile` does not bake `/etc/resolv.conf`
**And** the CLI is bash throughout — `yq` is not used and no new language dependency is introduced (FR43)

### Story 1.8: One name for each thing, greppable — REMOVED (out of scope for concept demo)

Naming hygiene with zero demo value. Dropped permanently 2026-10-13; no code impact.

## Epic 2: Key Custody & Transparent Interception

A developer's OpenCode session reaches a real model API successfully while the agent — and
anyone reading its environment or filesystem — holds only a placeholder. The proxy holds the
real keys in a trusted runc container, extends the guest's trust store without breaking public
HTTPS, injects the correct key per host, and logs only a fingerprint. Operators store and list
keys without ever seeing a value.

### Story 2.1: CLI writes the single source of cross-boundary constants

As a developer,
I want the proxy address and placeholder key defined in exactly one file,
So that a change to the network contract cannot silently drift between components.

**Acceptance Criteria:**

**Given** a fresh install
**When** the CLI first runs
**Then** it creates `/etc/secureai/compose.env` (0640 root) containing `SECUREAI_SUBNET`, `SECUREAI_GATEWAY`, `SECUREAI_PROXY_IP`, `SECUREAI_PROXY_PORT`, and `SECUREAI_PLACEHOLDER_KEY`
**And** the CLI is the file's **sole writer**; re-running preserves existing values rather than overwriting them
**And** `install-fedora.sh` creates the file but never populates it (AD-11)
**And** `docker-compose.yml` references all five values as `${...}` only, with no literal copy of any of them anywhere else in the tree
**And** the CLI sources the same file before invoking `docker compose`
**And** the placeholder exists as one constant, `sk-secureai-placeholder`, never duplicated as a literal

### Story 2.2: Keys stored, synced, and never displayed

As a developer,
I want to store API keys securely and list them without ever seeing the value,
So that my key cannot leak through terminal scrollback or a screenshot.

**Acceptance Criteria:**

**Given** no stored key for a host
**When** I run `secureai keys set api.anthropic.com <key>`
**Then** it is written to `/etc/secureai/keys.yaml` (0640 root:root) in `host: key` form with the value quoted
**And** the CLI syncs `keys.yaml` to `/var/lib/secureai` (0700), which is the proxy's only read path
**And** the proxy **never writes** config — its mount of `/var/lib/secureai` is read-only (AD-13)
**And** when I run `secureai keys list`
**Then** every value is masked
**And** no flag on any command reveals a raw key value

### Story 2.3: Proxy runs in the trusted zone, pinned to runc

As a developer,
I want the process that holds my keys to be structurally outside the agent's VM,
So that the trust boundary is enforced by runtime placement, not by configuration.

**Acceptance Criteria:**

**Given** the compose file
**When** the proxy service is defined
**Then** it declares `runtime: runc` explicitly (AD-6)
**And** a declared runtime that is not registered on the host fails the session with a readable message naming the missing runtime
**And** the CLI asserts this pin before starting a session
**And** the proxy mounts `/var/lib/secureai` read-only, labelled per consumer count (`:z` shared, `:Z` single-consumer)
**And** `proxy/addon.py` is world-readable in the proxy image, because the mitmproxy entrypoint gosu-drops to the `mitmproxy` user
**And** the base is `mitmproxy/mitmproxy:12.2.3` — Debian-based (trixie), not Alpine

### Story 2.4: The proxy injects the real key and logs only a fingerprint

As a developer,
I want the proxy to swap my placeholder for the real key on the way out,
So that the agent can use my subscription without ever holding the credential.

**Acceptance Criteria:**

**Given** an outbound request carrying the placeholder in its authorization header
**When** the proxy receives it
**Then** it substitutes the real key for the matching host before the request leaves the proxy
**And** substitution rewrites **request** headers only — never response headers
**And** injection is logged exactly as `authz=injected provider=<host> fp=<4-char sha256 prefix>` (AD-10)
**And** no log line ever contains key material, and the addon never logs header values
**And** the real key appears in no built image, no compose file, no guest environment, and not in `compose.env`
**And** the guest's own environment contains the placeholder and nothing else, so keys never flow from the trusted zone back into the guest
**And** with `content-type: text/event-stream` on the response, `.stream = True` is set in `responseheaders` **before any body is read**, so tokens are emitted rather than buffered until opencode's timeout (AD-3)

### Story 2.5: The guest trusts the proxy

As a developer,
I want the agent to trust the proxy,
So that interception works for the demo.

**Acceptance Criteria:**

**Given** the built guest image
**When** I inspect the trust configuration
**Then** the mitm CA is present in the **system** trust store and satisfies `curl`
**And** the standard CA env points at the shared public cert
**And** a public HTTPS call from inside the guest — such as `git clone` from `github.com` — succeeds, proving public HTTPS still works
**And** a model call through the agent is the concept proof (curl alone is the smoke test)

### Story 2.6: Proxy health gates the guest

As a developer,
I want the agent to start only once the proxy is actually ready,
So that my first request does not fail against a proxy that has not finished booting.

**Acceptance Criteria:**

**Given** the proxy service
**When** it is defined
**Then** it carries a healthcheck that exercises the proxy port
**And** the opencode service waits on `depends_on: proxy: condition: service_healthy`
**And** a proxy that never becomes healthy surfaces a readable error rather than an indefinite hang

## Epic 3: Sealing Egress to an Approved List

A developer confines the agent's egress to a reviewed set of hosts. Allowlisted services work;
anything else receives a readable HTTP 403; plaintext `http://` is gated on transport scheme as
well as host; and unreadable or missing configuration fails closed rather than passing traffic.
The operator can add a domain and see it take effect in a running session without restarting
anything, and the list itself is a single owned baseline with no host literals buried in code.

### Story 3.1: The proxy is the only enforcement point

As a reviewer of this system's security,
I want a single place where the allowlist decision is made,
So that there is exactly one answer to the question of where this is enforced.

**Acceptance Criteria:**

**Given** a request through the proxy to a host that is not on the allowlist
**When** the decision is made
**Then** the client receives HTTP 403 with body `blocked by secureai`
**And** the decision acts at `requestheaders` — `tls_clienthello` reads SNI for policy and logging and takes **no action** (AD-2)
**And** no pre-2023 mitmproxy API is called: `Context.kill()` and `Connection.kill()` do not exist, and only `Flow.kill()` or `http.Response.make(403)` is used
**And** all three hooks — `tls_clienthello`, `requestheaders`, `responseheaders` — live in one addon class in `proxy/addon.py`
**And** per-flow state such as observed SNI lives in `flow.metadata`; addon instance attributes are process-global and never carry request state
**And** the guest receives no policy and filters nothing — `HTTPS_PROXY` is honored because the runtime honors it, **not** as a control (AD-1)
**And** the addon contains no default-allow and no host literal — the allowlist file is the only policy source (AD-15)
**And** with `--set flow_detail=0` and `-q`, one line per blocked flow carries host, scheme, and decision, and the addon never logs header values

### Story 3.2: Plaintext HTTP is gated on scheme, not just on host

As a reviewer of this system's security,
I want plaintext `http://` egress filtered too,
So that an unencrypted scheme does not remain an unguarded path around the host check.

**Acceptance Criteria:**

**Given** a plaintext `http://` request through the proxy
**When** the destination is not on the allowlist
**Then** it receives HTTP 403 with body `blocked by secureai`
**And** the decision considers the **transport scheme as well as** SNI/Host — a host check alone is insufficient (AR3)
**And** `$HTTP_PROXY` and `$HTTPS_PROXY` are both set in the guest environment, so neither scheme is left uncovered by configuration
**And** a request whose scheme and host cannot both be determined fails closed

### Story 3.3: The proxy fails closed

As a developer,
I want a misconfigured or unreadable allowlist to block traffic rather than permit it,
So that a broken configuration never becomes an open door.

**Acceptance Criteria:**

**Given** config that is unreadable, unparseable, or missing
**When** a request arrives
**Then** it receives HTTP 403, never passthrough (AD-13)
**And** given an allowlisted host with no key configured
**When** a request arrives
**Then** it receives HTTP 403, never an unauthenticated passthrough
**And** given an unknown host
**When** a request arrives
**Then** it receives HTTP 403
**And** the log distinguishes a configuration failure from a policy block, so an operator can tell misconfiguration from intent

### Story 3.4: The allowlist is a seeded, owned baseline

As a developer,
I want a reviewed default set of allowed hosts,
So that a fresh install works out of the box and my agent's attack surface is one list I can read.

**Acceptance Criteria:**

**Given** a fresh install
**When** the CLI seeds `/etc/secureai/whitelist.yaml`
**Then** it contains exactly `api.anthropic.com`, `github.com`, `*.githubusercontent.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`, `models.dev`, `models.opencode.ai`, `opencode.ai`, and `astral.sh` (AD-15)
**And** the file holds one host per line, and `*.example.com` matches subdomains
**And** narrowing the baseline is an edit to this file rather than a code change
**And** the seeded baseline matches the documented default allowlist, so documentation and behavior cannot disagree

### Story 3.5: Live allowlist updates without a restart

As a developer,
I want to add a domain and have it take effect immediately in my running session,
So that I do not have to end and restart my work to unblock a legitimate dependency.

**Acceptance Criteria:**

**Given** a running proxy session
**When** I run `secureai whitelist add <domain>`
**Then** the domain is written to `/etc/secureai/whitelist.yaml`, synced to `/var/lib/secureai`, and an audit line is recorded (AD-13)
**And** the addon re-reads config **per flow** with an mtime-bounded cache (default 5s), so the new domain is permitted without restarting the proxy
**And** the CLI never signals the proxy to reload, because the proxy never accepts instructions — it only reads config
**And** the already-running session picks up the change; no new session is required

### Story 3.6: Inspect the allowlist

As a developer,
I want to see the current allowlist,
So that I know what my agent is permitted to reach.

**Acceptance Criteria:**

**Given** a configured allowlist
**When** I run `secureai whitelist list`
**Then** the current entries are printed

> **Honesty constraint carried into this epic (AR26):** `curl https://google.com` failing
> proves **the proxy enforces**, because curl honors the proxy environment variables. It does
> **not** prove the guest cannot route around the proxy — a guest process that ignores
> `http_proxy`/`https_proxy` bypasses enforcement entirely, and no host-side egress filter is in
> scope for this MVP. No acceptance criterion in this epic may claim otherwise. Whether the
> bridge's `internal: true` closes that gap is the BLOCKING question carried into Epic 5.

## Epic 4: Session Lifecycle & Operator Control

A developer's session starts as a self-contained pair, ends the moment they leave it, and leaves
nothing behind. `secureai opencode` brings the proxy up first, waits for it to be healthy, and
drops the user straight into the agent; leaving tears both containers down. `secureai status` and
`secureai down` report and control the stack without the user typing `docker` once.

### Story 4.1: Launch an interactive session, not an exec

As a developer,
I want one command that drops me straight into the agent,
So that I can start working without learning Docker or attaching to anything myself.

**Acceptance Criteria:**

**Given** a configured and healthy stack
**When** I run `secureai opencode`
**Then** the proxy starts first, the opencode container is started via `docker compose run --rm -it`, and I land **inside** opencode (AD-14)
**And** the user is attached interactively — not `docker exec`, not `compose up`
**And** the CLI passes no `--runtime` flag, so the compose file's `runtime: kata` declaration governs
**And** a failure to bring the proxy up reports the cause instead of attaching the user to a session that cannot work
**And** the command requires no Docker knowledge from me — no `docker` is typed by the user at any point (NFR7)

### Story 4.2: The proxy dies with the agent

As a developer,
I want the whole stack to disappear when I leave,
So that nothing keeps running on my machine and no orphaned container survives my session.

**Acceptance Criteria:**

**Given** an active session
**When** I exit the agent
**Then** both containers are torn down
**And** both services use `--rm` semantics (AD-14, F10)
**And** a shell `trap` in the CLI tears the session down on INT and TERM
**And** **no systemd unit auto-starts the proxy** — that would contradict the per-session self-containment of F12
**And** after teardown, no proxy or opencode container remains on the host

### Story 4.3: Stop and inspect the stack

As a developer,
I want to see whether the stack is running and be able to stop it,
So that I can recover from an interrupted session without hunting for containers.

**Acceptance Criteria:**

**Given** an active session
**When** I run `secureai status`
**Then** it reports whether the proxy and opencode containers are running
**And** it asserts the host's Docker/Kata version pairing, so an unsupported combination surfaces here (Story 1.4)
**And** when I run `secureai down`
**Then** the session containers are stopped and removed
**And** both commands work whether or not a session was ever started, without erroring obscurely
**And** neither command requires the user to know Docker

### Story 4.4: Measure the time to a ready session — REMOVED (out of scope for concept demo)

Produces a number, not a demo. Dropped permanently 2026-10-13.

## Epic 5: Proving the Security Claim

A reviewer can see, rather than assume, that the isolation holds. `secureai verify` shows the API
host in the proxy log, shows injection with a fingerprint, shows a non-allowlisted host failing
through the proxy, and shows an HTTPS call succeeding from the runtime inside the guest rather
than from curl. The same pass establishes that the guest genuinely runs in a VM with its own
kernel, and records — as a measured finding rather than an assumption — whether the guest can
reach the internet around the proxy. The three open architecture questions are answered here,
including the blocking one.

### Story 5.1: Gate (a) — the proxy sees the API host

As a reviewer,
I want proof the agent's traffic actually reaches the proxy,
So that a working-looking session is not mistaken for an enforced one.

**Acceptance Criteria:**

**Given** a live session making a model request
**When** I run `secureai verify`
**Then** the proxy log is shown containing the API host
**And** a silently ignored `HTTPS_PROXY` is reported as a **failure**, not a pass — a clean session start proves nothing
**And** the check is performed against the proxy's own log, not inferred from the agent's behaviour

### Story 5.2: Gate (b) — injection occurred, as a fingerprint

As a reviewer,
I want proof the real key was substituted, without the proof itself exposing the key,
So that I can confirm the mechanism works safely.

**Acceptance Criteria:**

**Given** a live session making a model request
**When** I run `secureai verify`
**Then** the log line `authz=injected provider=<host> fp=<4-char sha256 prefix>` is shown (AD-10)
**And** no verify output ever contains key material — a fingerprint only
**And** the same key produces the same fingerprint across runs, so the check is stable rather than coincidental

### Story 5.3: Gate (c) — a non-allowlisted host fails

As a reviewer,
I want proof that the allowlist blocks,
So that the enforcement claim rests on an observation rather than on reading the addon.

**Acceptance Criteria:**

**Given** a live session
**When** `secureai verify` attempts a request to a host outside the allowlist
**Then** it fails with HTTP 403 and body `blocked by secureai`
**And** the failure occurs **through the proxy**, so it proves the proxy enforces
**And** the output **explicitly states the limit of this gate**: it does not prove the guest cannot reach that host by another path (AR26)
**And** the output does not claim network-level enforcement, which is out of scope for this MVP

### Story 5.4: Gate (d) — REMOVED (out of scope for concept demo)

Bun-runtime cert proof. Dropped permanently 2026-10-13; a model call through the agent is the concept proof.

### Story 5.5: Network backstop investigation — REMOVED (out of scope for concept demo)

Enforcement is cooperative by design (AD-1). No `internal: true` claim is made. Dropped permanently 2026-10-13.

### Story 5.6: Prove the guest is actually in a VM with its own kernel

As a reviewer of this system's isolation,
I want to see that the agent runs under a separate kernel rather than a shared container runtime,
So that the VM-isolation claim is observed rather than inferred from a config field.

**Acceptance Criteria:**

**Given** a live session
**When** I verify the runtime the opencode container is actually executing under
**Then** it reports `kata`, and not `runc` or any other fallback runtime
**And** the guest reports a kernel identifier distinct from the host's, establishing a separate kernel rather than a shared one (NFR3)
**And** a mismatch, a fallback, or an unreadable runtime declaration is reported as a **failure**, not a pass
**And** the check is performed against the **running container**, not against `docker-compose.yml` or `daemon.json`, because those are declarations rather than observations
**And** a failure here invalidates the isolation premise of the entire system and is escalated rather than worked around

### Story 5.7: Bypass-path measurement — REMOVED (out of scope for concept demo)

Produces a finding, not a demo. Dropped permanently 2026-10-13.