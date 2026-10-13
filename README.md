# SecureAI — Kata-Containerized OpenCode

> **Proof of concept. Fedora only.**
> This repository demonstrates the concept on Fedora. It is not production-hardened
> and is not supported on any other platform. Do not rely on it for production use.

> **Status: designed and implemented as a build substrate.** The prose in this
> file is the product contract; the code lives in the repo root under
> `install-fedora.sh`, `secureai`, `docker-compose.yml`, `opencode/`, `proxy/`,
> `config/`, and `tests/`. AD-16 pins exactly five runtime paths in
> `/usr/local/bin`, `/opt/secureai`, `/etc/secureai`, `/var/lib/secureai`, and
> Docker's own state directory — the checkout is never referenced at runtime.

## What it is

SecureAI runs an AI coding agent ([OpenCode](https://opencode.ai)) inside a Kata
Containers VM, behind a forward proxy that:

- holds the real API keys, so the agent never sees them
- swaps a placeholder key (`sk-secureai-placeholder`) for the real key at runtime
- enforces an egress whitelist at the HTTP layer, so approved services work and
  anything else gets a readable `403 blocked by secureai`

The goal is VM-level isolation for untrusted AI-assisted coding workflows.

### What this does and does not guarantee

Worth stating plainly, because it is the one thing this design does not do:

**Enforcement is cooperative.** The whitelist is enforced by the proxy, not by the
network. The guest is given `HTTPS_PROXY` and the agent's runtime honors it — but a
guest process that ignores `http_proxy`/`https_proxy` reaches the internet directly.
There is no host-side egress filter and no iptables backstop; that is out of MVP scope.

This is why `curl https://google.com` failing proves **the proxy enforces** — because
curl honors the proxy variables — and does *not* prove the guest cannot route around
it. Whether the bridge's `internal: true` actually reaches the Kata VM's tap device is
the blocking open question in the architecture spine; until it is answered, treat the
"only via the proxy" property as unverified.

## Requirements

- Fedora 38, 43, or 44 (asserted by the installer, which refuses anything else)
- `sudo` access
- A host with nested virtualization enabled

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/<org>/secureai/main/install-fedora.sh | sudo bash
```

Installs machine-wide and root-owned. There is no `git clone` prerequisite and no
per-user variant — the checkout is never referenced at runtime. Re-running is
idempotent.

After install, the image build needs the CA first. Privileged setup needs
sudo; everyday sessions do not (docker group membership is enough):

```bash
sudo secureai ca export   # produce the interception CA (privileged, once)
sudo secureai build       # build proxy + guest images (privileged, once)
secureai opencode    # start a session (no sudo)
```

## Use

```bash
secureai opencode                 # launch an interactive session (no sudo)
sudo secureai whitelist add example.com # allow an extra domain (live, no restart)
secureai whitelist list            # show current allowlist
sudo secureai keys set api.anthropic.com sk-ant-...   # store a key
secureai keys list                 # show hosts, values masked
sudo secureai ca export            # export the mitm CA (must run before image build)
sudo secureai build                # build proxy + guest images
secureai verify               # run the gates and the open-question probes
secureai status                    # check if the stack is running
secureai down                 # stop and remove containers
```

The user never types `docker`. Config lives in `/etc/secureai` (`0640 root:root`) and
is synced to `/var/lib/secureai`, which the proxy mounts read-only.

## How it works

Two zones split by a trust boundary. The only path across it is the proxy, which holds
what neither zone may hold.

```
secureai opencode
  ├── starts the proxy FIRST, waits for its healthcheck
  │     • trusted zone — runc container, runtime pinned explicitly
  │     • holds the real API keys (read-only mount)
  │     • injects the real key per upstream host, logs a 4-char fingerprint only
  │     • validates request host + scheme at requestheaders → 403; SNI for context only
  └── then starts opencode interactively (docker compose run --rm -it)
        • untrusted zone — Kata VM, its own kernel
        • sees only the placeholder key
        • reaches the proxy by fixed IP, not by Docker service DNS
        • honors HTTPS_PROXY; nothing stops it ignoring it (see above)
```

Both containers are removed when you leave the session. The runtime split is load-bearing:
`docker-compose.yml` declares `runtime: kata` on `opencode` and `runtime: runc` on
`proxy`, and `default-runtime` in `daemon.json` is deliberately left untouched so the
key-holding proxy can never be drawn into the Kata VM.

The guest is addressed by fixed IP because Docker's embedded resolver
(`nameserver 127.0.0.11`) does not exist inside a Kata VM.

## Default allowlist

One owned list, seeded by the CLI. The addon holds no host literals and no
default-allow, so narrowing this list is an edit to a config file, not to code.

```
api.anthropic.com
github.com
*.githubusercontent.com
registry.npmjs.org
pypi.org
files.pythonhosted.org
models.dev
models.opencode.ai
opencode.ai
astral.sh
```

## Layout

**Source tree** — the repository, where artifacts are authored. This is not what runs.

```
secureai/
├── install-fedora.sh     # only writer of daemon.json; SELinux labelling
├── secureai              # bash CLI — the sole writer of /etc/secureai
├── docker-compose.yml    # runtime declarations; consumes ${...} from compose.env
├── opencode/Dockerfile   # toolchain + system CA + vendored model catalog
├── proxy/
│   ├── Dockerfile        # mitmproxy 12.2.3
│   └── addon.py          # the only enforcement point
└── config/               # whitelist.yaml, settings.yaml, keys.yaml.template
```

**Installed layout** — what actually runs, after install. Every absolute path a bind
mount or the CLI uses is one of these five.

```
/usr/local/bin/secureai   # the single entry point, the only thing on your PATH
/opt/secureai/            # root-owned app tree: compose, Dockerfiles, addon, config
/etc/secureai/            # 0711 traverse-only — sole source of truth, CLI-written
/var/lib/secureai/        # 0700 — runtime copy, ca.pem, session workspace
```

Bind mounts carry SELinux labels by consumer count: `:Z` for a single consumer, `:z` for
two or more.

## Open questions

Design decisions that are recorded but not yet settled. These are the honest edges of
the system, and each is carried as a story rather than an assumption.

- **BLOCKING — does `internal: true` apply to the Kata VM's tap device?** If it does,
  the guest has no route out and this system is *stronger* than documented. If it does
  not, the setting is false comfort. AD-1's accepted risk stays provisional until tested.
- **Plaintext `http://` egress.** The addon must gate on transport scheme as well as
  SNI/Host. Until designed, scheme `http` is an unguarded path.
- **Does the pinned Bun build honor `NODE_EXTRA_CA_CERTS`?** The system trust store
  satisfies curl and git only; Node and Bun ship their own CA bundles. This is the first
  thing to smoke-test.
- **DNS-level enforcement beyond SNI.** SNI is the enforcement signal; upstream
  resolution is guest-local and carries no enforcement meaning.

## Limitations

Not addressed in this proof of concept:

- no CI pipeline (the unit suite in `tests/` is runnable locally, but no hosted
  CI is wired)
- no upgrade or migration path
- no multi-user key management
- no performance tuning (Kata VM boot is slow; the PRD's 30s readiness budget is in
  acknowledged tension with this and is untested)
- no network-level egress backstop
- HTTP/2 support unverified
- Fedora 38/43/44 only

Certificate pinning is *not* on this list: it was checked and opencode performs no TLS
pinning, which is what makes MITM substitution viable here.

## Documentation

- Requirements: [`prd-workspace/prd.md`](prd-workspace/prd.md)
- Architecture spine (the build contract): [`architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md`](architecture/secureai-kata-opencode/ARCHITECTURE-SPINE.md)
- Epics and stories: [`architecture/secureai-kata-opencode/epics.md`](architecture/secureai-kata-opencode/epics.md)
- Sprint tracking: [`architecture/secureai-kata-opencode/sprint-status.yaml`](architecture/secureai-kata-opencode/sprint-status.yaml)
- Design log: [`architecture/secureai-kata-opencode/.memlog.md`](architecture/secureai-kata-opencode/.memlog.md)
- Original plan: [`secureai-kata-opencode.md`](secureai-kata-opencode.md)