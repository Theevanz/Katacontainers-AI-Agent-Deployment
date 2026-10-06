# SecureAI: Kata-Containerized OpenCode with Secure Forward Proxy

## Overview

A one-click install script that sets up Kata Containers on Fedora, then launches a Docker Compose stack with two services:

1. **opencode** — runs inside a Kata Containers VM (strong isolation)
2. **forward-proxy** — runs in a normal container, holds real API keys, injects them at runtime, and enforces a DNS-based egress whitelist

A global CLI (`secureai`) is installed to PATH so users can run `secureai opencode` from anywhere.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Host (Fedora + Kata Containers)                             │
│                                                             │
│  /usr/local/bin/secureai  ← CLI entrypoint                  │
│  /etc/secureai/           ← Config (root 0600)              │
│  /var/lib/secureai/       ← Runtime data (root 0600)        │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Docker Network: secureai_net                         │   │
│  │                                                      │   │
│  │  ┌─────────────────────┐    ┌────────────────────┐   │   │
│  │  │ opencode            │    │ forward-proxy      │   │   │
│  │  │ (kata-qemu runtime) │───▶│ (mitmproxy)        │   │   │
│  │  │                     │    │                    │   │   │
│  │  │ • node:22 + uv      │    │ • TLS intercept    │   │   │
│  │  │ • opencode-ai       │    │ • Key injection    │   │   │
│  │  │ • placeholder keys  │    │ • DNS allowlist    │   │   │
│  │  │ • --rm on exit      │    │ • --rm on exit     │   │   │
│  │  └─────────────────────┘    └────────────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## File Tree

```
secureai/
├── install-fedora.sh              # One-shot installer
├── secureai                       # Bash CLI → /usr/local/bin/secureai
├── docker-compose.yml             # 2 services + network
├── opencode/
│   └── Dockerfile                 # Minimal node+uv+opencode image
├── proxy/
│   ├── Dockerfile                 # mitmproxy + addon
│   ├── addon.py                   # TLS intercept, key inject, DNS allowlist
│   └── ca.pem                     # Generated on first run
├── config/
│   ├── whitelist.yaml             # Base + user domains
│   ├── settings.yaml              # Placeholder key, log level, etc.
│   └── keys.yaml.template         # Example (empty values)
└── systemd/
    └── secureai-proxy.service     # Optional: auto-start proxy
```

---

## Component Details

### 1. `install-fedora.sh`

**Purpose**: One-shot setup for Fedora systems.

**Installs**:
- Kata Containers (`kata-runtime`, `kata-qemu`)
- Docker Engine + Compose plugin
- `secureai` CLI → `/usr/local/bin/secureai`
- Directories: `/etc/secureai` (0700), `/var/lib/secureai` (0700)
- SELinux policy module (if needed) + `:Z` labels on mounts
- Generates mitmproxy CA, trusts it in opencode container
- Writes default `whitelist.yaml` and `settings.yaml`

**Usage**: `curl -fsSL <url>/install-fedora.sh | sudo bash`

---

### 2. `secureai` CLI (Bash)

**Commands**:
- `secureai opencode` — Launch the stack (proxy + opencode)
- `secureai whitelist add <domain>` — Add domain to allowlist
- `secureai whitelist list` — Show current allowlist
- `secureai keys set <host> <key>` — Store API key for a host
- `secureai keys list` — Show configured hosts (keys masked)
- `secureai status` — Check if stack is running
- `secureai down` — Stop and remove containers

**Key behaviors**:
- Uses `docker run --rm -it` for opencode (interactive, auto-cleanup)
- Uses `docker run --rm -d` for proxy (background, auto-cleanup)
- Both containers share `secureai_net` Docker network
- SELinux: `:Z` on all bind mounts

---

### 3. `docker-compose.yml`

```yaml
services:
  opencode:
    build: ./opencode
    runtime: kata-qemu
    networks:
      - secureai_net
    environment:
      - OPENAI_API_KEY=sk-secureai-placeholder
      - ANTHROPIC_API_KEY=sk-secureai-placeholder
    volumes:
      - /var/lib/secureai/workspace:/workspace:Z
      - /var/lib/secureai/opencode-config:/home/node/.config/opencode:Z
      - /var/lib/secureai/opencode-data:/home/node/.local/share/opencode:Z
    depends_on:
      - proxy

  proxy:
    build: ./proxy
    networks:
      - secureai_net
    volumes:
      - /var/lib/secureai/keys.yaml:/data/keys.yaml:Z,ro
      - /var/lib/secureai/whitelist.yaml:/data/whitelist.yaml:Z,ro
      - /var/lib/secureai/ca.pem:/data/ca.pem:Z,ro

networks:
  secureai_net:
    driver: bridge
```

---

### 4. `opencode/Dockerfile`

**Requirements** (from user spec):
- Base: `node:22-bookworm-slim` + `uv` from `ghcr.io/astral-sh/uv`
- Multi-stage `COPY --from=uv`
- System pkgs: git, ca-certificates, curl, openssh-client, ripgrep, jq, python3, pip, venv, default-jdk-headless
- `opencode-ai@latest` via npm
- Non-root `node` user (UID 1000)
- Volumes: `/workspace`, `~/.config/opencode`, `~/.local/share/opencode`
- Env: `HOME=/home/node`, `UV_LINK_MODE=copy`, `UV_NATIVE_TLS=1`
- Healthcheck: `opencode --version`
- Default CMD: `opencode`

---

### 5. `proxy/Dockerfile`

**Base**: `mitmproxy/mitmproxy:latest`

**Adds**:
- `addon.py` — custom addon for key injection + allowlist
- `ca.pem` — generated CA cert (trusted by opencode container)

---

### 6. `proxy/addon.py`

**Responsibilities**:
1. **TLS Interception**: Generate certs on-the-fly, intercept HTTPS
2. **Key Injection**: Match target host → swap `Authorization: Bearer sk-secureai-placeholder` → real key from `keys.yaml`
3. **DNS Allowlist**: Validate SNI/Host against `whitelist.yaml` → drop non-matching connections
4. **Logging**: Scrub keys from access logs

**Config** (mounted read-only):
- `/data/keys.yaml` — `host: key` mapping
- `/data/whitelist.yaml` — allowed domains (supports `*.example.com`)

---

### 7. Config Files

#### `whitelist.yaml`
```yaml
# Base whitelist + user additions
# One host per line. "*.example.com" matches subdomains.
domains:
  - api.anthropic.com
  - github.com
  - "*.githubusercontent.com"
  - registry.npmjs.org
  - pypi.org
  - files.pythonhosted.org
  - models.dev
  - opencode.ai
  - astral.sh        # uv
  - github.com       # uv (already listed)
```

#### `settings.yaml`
```yaml
placeholder_key: sk-secureai-placeholder
log_level: info
```

#### `keys.yaml.template`
```yaml
# Copy to keys.yaml and fill in real keys
# Format: host: key
api.anthropic.com: ""
models.dev: ""
```

---

## API Key Storage

| Location | Purpose | Permissions |
|----------|---------|-------------|
| `/etc/secureai/keys.yaml` | Source of truth | `root:root 0600` |
| `/var/lib/secureai/keys.yaml` | Runtime copy (proxy reads) | `root:root 0600` |
| Proxy container | Mounts read-only | `ro` inside container |

**Flow**:
```
User: secureai keys set api.secureai.com sk-ant-xxx
  → writes to /etc/secureai/keys.yaml (0600)
  → syncs to /var/lib/secureai/keys.yaml
  → proxy container mounts it read-only

Opencode → sends Authorization: Bearer sk-secureai-placeholder
Proxy → matches target host → swaps in real key
```

**Keys are never**:
- In the opencode image or container
- In docker-compose.yml
- In environment variables (except placeholder)
- Logged (proxy scrubs them)

---

## SELinux Handling

- All bind mounts use `:Z` label (private, unshared)
- Install script checks SELinux status
- If enforcing: builds and loads a policy module for Kata + Docker
- If permissive/disabled: logs a warning but continues

---

## Test Checklist

- [ ] `install-fedora.sh` runs clean on fresh Fedora
- [ ] `secureai opencode` launches both containers
- [ ] Opencode container runs under `kata-qemu` runtime
- [ ] Proxy intercepts and injects API keys correctly
- [ ] Whitelist blocks non-allowlisted domains
- [ ] Whitelist allows `*.example.com` subdomains
- [ ] `secureai whitelist add` works and applies live
- [ ] `secureai keys set` stores keys securely (0600)
- [ ] Containers auto-remove on exit (`--rm`)
- [ ] SELinux labels prevent permission denied errors
- [ ] `secureai down` stops and removes all containers
- [ ] `secureai status` shows running state

---

## Implementation Order

1. `opencode/Dockerfile` — build and test image
2. `proxy/Dockerfile` + `addon.py` — build and test proxy
3. `docker-compose.yml` — wire services together
4. `secureai` CLI — implement commands
5. `install-fedora.sh` — one-shot installer
6. Config files — defaults and templates
7. Test full stack end-to-end
8. SELinux policy module (if needed)
