# SecureAI — Kata-Containerized OpenCode

> **Proof of concept. Fedora only.**
> This repository demonstrates the concept on Fedora. It is not production-hardened
> and is not supported on any other platform. Do not rely on it for production use.

## What it is

SecureAI runs an AI coding agent ([OpenCode](https://opencode.ai)) inside a Kata
Containers VM, behind a forward proxy that:

- holds the real API keys, so the agent never sees them
- swaps a placeholder key (`sk-secureai-placeholder`) for the real key at runtime
- enforces a DNS-based egress whitelist, so the agent can only reach approved
  services

The goal is VM-level isolation for untrusted AI-assisted coding workflows.

## Requirements

- Fedora (tested on current releases only)
- `sudo` access
- A host with nested virtualization enabled (for `kata-qemu`)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/<org>/secureai/main/install-fedora.sh | sudo bash
```

## Use

```bash
secureai opencode                 # launch an interactive session
secureai whitelist add example.com # allow an extra domain
secureai whitelist list            # show current allowlist
secureai keys set api.anthropic.com sk-ant-...   # store a key (root-only, 0600)
secureai status                   # check if the stack is running
secureai down                     # stop and remove containers
```

## How it works

```
secureai opencode
  ├── starts proxy container (normal container, --rm)
  │     • holds real API keys (read-only mount, never in the agent's container)
  │     • injects real keys at runtime
  │     • validates SNI/Host against the whitelist
  └── starts opencode container (kata-qemu runtime, --rm, interactive)
        • sees only the placeholder key
        • can reach the network only via the proxy
```

Both containers share a private Docker network and are removed on exit.

## Default allowlist

```
api.anthropic.com
github.com
*.githubusercontent.com
registry.npmjs.org
pypi.org
files.pythonhosted.org
models.dev
opencode.ai
astral.sh
```

## Layout

```
secureai/
├── install-fedora.sh   # one-shot installer
├── secureai            # CLI → /usr/local/bin/secureai
├── docker-compose.yml  # opencode (kata) + proxy
├── opencode/Dockerfile # minimal node + uv + opencode image
├── proxy/              # mitmproxy + addon.py (key injection, allowlist)
└── config/             # whitelist.yaml, settings.yaml, keys.yaml
```

## Limitations

Not addressed in this proof of concept:

- no automated tests or CI
- no upgrade or migration path
- no multi-user key management
- no performance tuning (Kata VM boot is slow)
- HTTP/2 and certificate pinning unverified
- Fedora only

## Documentation

- Requirements: [`prd-workspace/prd.md`](prd-workspace/prd.md)
- Implementation plan: [`secureai-kata-opencode.md`](secureai-kata-opencode.md)