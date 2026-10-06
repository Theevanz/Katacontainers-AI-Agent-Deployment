---
title: "SecureAI: Kata-Containerized OpenCode with Secure Forward Proxy"
status: final
created: 2026-10-06
updated: 2026-10-06
reviewed: 2026-10-06
---

# SecureAI: Kata-Containerized OpenCode with Secure Forward Proxy

> **Proof of concept — Fedora only.** This project demonstrates the concept on
> Fedora. It is not production-hardened and is not supported on other platforms.

## Vision

Enable developers in the org to use AI coding agents (OpenCode) in a secure, isolated environment. The system provides VM-level isolation via Kata Containers, ensures API keys are never exposed to the agent, and restricts egress to approved services only. "Done" means any developer can run `secureai opencode` and immediately work in a secure environment without understanding the underlying infrastructure.

## Goals

- One-click Fedora installation of Kata Containers + Docker + SecureAI CLI
- OpenCode runs inside a Kata Containers VM for strong isolation
- Forward proxy holds real API keys; OpenCode only sees placeholder keys
- Proxy enforces configurable DNS-based egress whitelist
- Global `secureai` CLI in PATH for easy invocation
- SELinux-compatible with `:Z` labels on all bind mounts

## Scope

**MVP for showcase purposes.** Not production-hardened. Goal is to demonstrate the concept works end-to-end: Kata isolation + proxy key injection + egress whitelist + clean CLI.

### Explicitly out of MVP scope

- Production hardening and edge-case handling
- Automated testing and CI
- Upgrade/migration path
- Multi-user key management
- Performance optimization (Kata boot time is acceptable)
- Comprehensive documentation

## Non-Goals

- Multi-platform support (Fedora only)
- Production-grade reliability and edge cases
- GUI or web interface
- Orchestrators other than Docker Compose

## Features

| # | Feature | Priority | Description |
|---|---------|----------|-------------|
| F1 | One-click Fedora install script | Must | Installs Kata, Docker, SecureAI CLI, dirs, SELinux labels |
| F2 | OpenCode runs in Kata Containers VM | Must | Uses kata-qemu runtime for VM-level isolation |
| F3 | Forward proxy with API key injection | Must | mitmproxy addon swaps placeholder keys for real keys at runtime |
| F4 | DNS-based egress whitelist | Must | Proxy validates SNI/Host against configurable domain list |
| F5 | `secureai` CLI in PATH | Must | Installed to /usr/local/bin/secureai |
| F6 | `secureai opencode` launches interactive session | Must | Brings user directly into OpenCode (not docker exec) |
| F7 | `secureai whitelist add <domain>` | Should | Live config update, no restart needed |
| F8 | `secureai keys set <host> <key>` | Should | Store API keys securely (root-only 0600) |
| F9 | SELinux `:Z` labels on mounts | Must | All bind mounts use private SELinux labels |
| F10 | Auto-cleanup containers on exit (`--rm`) | Must | No orphaned containers after session |
| F11 | Pre-built OpenCode image with all dependencies | Must | Dockerfile with node, uv, git, ripgrep, jq, python3, JDK, opencode-ai |
| F12 | Proxy shuts down when OpenCode exits | Must | Both containers self-contained per session |

## Concerns

- **Security**: API keys must never be exposed to the OpenCode container
- **Isolation**: Kata Containers provides VM-level isolation for OpenCode
- **Usability**: One-click install, simple CLI
- **SELinux**: Fedora enforces SELinux; must handle labels correctly
- **Maintainability**: Bash CLI, standard Docker Compose, minimal custom code

## NFRs (MVP scope)

- **Security**: API keys never exposed to OpenCode container, never logged
- **Isolation**: OpenCode runs in Kata VM (VM-level, not just container)
- **Startup**: SecureAI stack ready within 30s of `secureai opencode`
- **UX**: Zero Docker knowledge required — CLI abstracts all infrastructure
- **SELinux**: All bind mounts use `:Z` labels, no permission denied errors

## Assumptions

- Target users are developers in the org who want a secure OpenCode environment
- Users have sudo access for installation
- Kata QEMU runtime is acceptable performance-wise
- mitmproxy is the right tool for TLS interception + key injection
- `/etc/secureai` and `/var/lib/secureai` are acceptable system paths
- MVP/showcase quality bar — not production-hardened

## Success Criteria

- [ ] `secureai opencode` launches interactive OpenCode session in Kata VM
- [ ] Proxy injects real API keys correctly (OpenCode sees only placeholder)
- [ ] Whitelist blocks non-allowlisted domains (e.g. curl to google.com fails)
- [ ] Whitelist allows pypi.org, npmjs.org, api.anthropic.com
- [ ] Containers auto-remove after session ends
- [ ] SELinux labels prevent permission denied errors on Fedora
- [ ] Another developer can run install script and use `secureai opencode` without Docker knowledge

## Open Questions

- Should the proxy support HTTP/2 for all targets?
- How to handle certificate pinning in OpenCode?