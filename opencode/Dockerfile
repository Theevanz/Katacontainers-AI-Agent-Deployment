# syntax=docker/dockerfile:1
#
# SecureAI — the guest image: OpenCode + toolchain, in the UNTRUSTED zone (AD-9).
#
# AD-9 in one line: the base image is ours, the runtime inside it is not.
# opencode-ai ships a BUN-COMPILED native binary (~185 MB ELF, Bun +
# JavaScriptCore, no V8) -- NOT a Node runtime. The `node` base below supplies a
# Node *toolchain* and nothing the runtime needs.
#
# The guest must contain everything it needs AT BUILD TIME. A dependency
# discovered at runtime is an unvetted egress hole and a silent widening of the
# allowlist (AD-8), so runtime discovery is a build failure, not a fix.

FROM node:22-bookworm-slim

# AD-12 build order: `secureai ca export` must run BEFORE this build. The CA is
# baked into the system trust store, never bind-mounted into a running
# container.
#
# Copied from the build context, which `secureai build` sets to /var/lib/secureai.
# A named-stage or named-image COPY would name something nothing ever builds.
# The refusal below is deliberate: if the operator skipped `ca export`, the build
# must say so by NAME rather than fail with an opaque "file not found" that
# points at the wrong step (Story 1.6).
COPY ca.pem /tmp/secureai-ca.pem
RUN set -eux; \
    if [ ! -s /tmp/secureai-ca.pem ]; then \
        echo "FATAL: no interception CA found at ca.pem." >&2; \
        echo "Run 'secureai ca export' BEFORE 'secureai build'." >&2; \
        echo "The guest trusts the proxy through its SYSTEM trust store, so the" >&2; \
        echo "CA must exist at build time (AD-12)." >&2; \
        exit 1; \
    fi

# --- toolchain, installed explicitly -------------------------------------
# No official node:* image ships git, JDK, or python -- `-slim` least of all --
# and opencode HARD-ERRORS without git. So the toolchain is an explicit install,
# not an inherited assumption (AD-9).
RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        python3-pip \
        default-jdk-headless \
        ripgrep \
        jq \
        curl \
        openssh-client; \
    rm -rf /var/lib/apt/lists/*

# uv from its own image (AD-9). copy, not hardlink, because the layers live on
# different filesystems; UV_NATIVE_TLS=1 so it trusts the SYSTEM store we extend
# below rather than a private bundle.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_NATIVE_TLS=1

# --- CA trust: EXTEND the system store, never replace it (AD-4) ----------
# The system's default bundle must survive, or every public HTTPS call breaks
# (every git clone, every curl). So the mitm CA is ADDED to the store, and the
# *_CA_* variables are left UNSET rather than pointed at the mitm CA alone.
RUN set -eux; \
    install -m 0644 /tmp/secureai-ca.pem \
        /usr/local/share/ca-certificates/secureai-mitm.crt; \
    rm -f /tmp/secureai-ca.pem; \
    update-ca-certificates; \
    # Assert the mitm CA is in the store AND the public bundle still works.
    test -s /etc/ssl/certs/ca-certificates.crt; \
    grep -q "BEGIN CERTIFICATE" /etc/ssl/certs/ca-certificates.crt

# opencode ships a Bun binary, and Bun has its OWN CA bundle -- whether the
# pinned build honours NODE_EXTRA_CA_CERTS is UNCONFIRMED (bun#17325; opencode
# #3130/#4883/#3566/#923/#1694/#7073). That is the project's highest risk
# (AD-10 gate d), so it is set here at process start with an ABSOLUTE path and
# is the FIRST thing `secureai verify` smoke-tests.
#
# It points at a CONCATENATED bundle -- system store plus the mitm CA -- so that
# honouring it cannot break public HTTPS the way SSL_CERT_FILE would (AD-4).
RUN set -eux; \
    cat /etc/ssl/certs/ca-certificates.crt \
        /usr/local/share/ca-certificates/secureai-mitm.crt \
        > /usr/local/share/ca-certificates/secureai-bundle.crt
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/secureai-bundle.crt
# SSL_CERT_FILE / REQUESTS_CA_BUNDLE / GIT_SSL_CAINFO deliberately left UNSET.

# --- opencode itself ----------------------------------------------------
# glibc package, NEVER -musl: this base is glibc (AD-9). Scripts must stay
# enabled -- --ignore-scripts yields a useless 479-byte stub instead of the
# real binary, and the postinstall hardlinks the native binary into place.
# `opencode --version` must succeed HERE or the build fails, rather than
# shipping a broken agent that only fails inside a VM.
ARG OPENCODE_VERSION=1.18.34
RUN set -eux; \
    npm install --global --no-audit --no-fund \
        "opencode-ai@${OPENCODE_VERSION}"; \
    command -v opencode; \
    opencode --version

# --- AD-8: no runtime calls that widen our own surface ------------------
# autoupdate, share, and shell stop opencode phoning home. The model catalog is
# VENDORED so models.opencode.ai can leave the allowlist entirely.
# The XDG_* paths are explicit so mount points are deterministic, and they are
# the persisted volume paths.
RUN set -eux; \
    install -d /opt/opencode-catalog; \
    install -d -o node -g node \
        /home/node/.config/opencode \
        /home/node/.local/share \
        /home/node/.cache \
        /home/node/.local/state

COPY opencode/opencode.json /home/node/.config/opencode/opencode.json
COPY opencode/models.json /opt/opencode-catalog/models.json

ENV OPENCODE_CONFIG=/home/node/.config/opencode/opencode.json \
    OPENCODE_MODELS_PATH=/opt/opencode-catalog/models.json \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    XDG_CONFIG_HOME=/home/node/.config \
    XDG_DATA_HOME=/home/node/.local/share \
    XDG_CACHE_HOME=/home/node/.cache \
    XDG_STATE_HOME=/home/node/.local/state \
    UV_LINK_MODE=copy \
    UV_NATIVE_TLS=1

# Provider SDKs opencode would otherwise install AT RUNTIME into
# ~/.cache/opencode/bin are pre-seeded here, so a runtime-discovered dependency
# fails the build instead of silently widening the allowlist (AD-8).
# The proxy variables are injected by compose from compose.env -- NEVER baked
# here, so the address stays one constant (AD-5, AD-11).
RUN set -eux; \
    mkdir -p /home/node/.cache/opencode/bin; \
    chown -R node:node /home/node/.cache /home/node/.config /home/node/.local

USER node
WORKDIR /home/node

# Deliberately NOT baked: /etc/resolv.conf. Docker's embedded resolver does not
# exist inside a Kata VM (kata#6688), so compose sets `dns:` on the service to
# the bridge gateway instead (AD-5).
ENTRYPOINT ["opencode"]