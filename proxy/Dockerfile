# SecureAI — the proxy image (Story 2.3).
#
# Debian-based (trixie), NOT Alpine: mitmproxy ships a specific glibc and its
# wheels are built against it. The proxy is the key-holding process, so this
# image is deliberately minimal.
#
# Note the tag: SECUREAI_PROXY_IMAGE is OUR built image, not the upstream
# mitmproxy tag. mitmdump is invoked with `-s /opt/secureai/addon.py`, and that
# file exists only because the COPY below put it there. Running the upstream
# image would leave mitmdump pointing at a path that does not exist, and the
# proxy would exit at startup on every session.

FROM mitmproxy/mitmproxy:12.2.3

# The entrypoint gosu-drops to the unprivileged `mitmproxy` user and forces
# HOME=/home/mitmproxy. So the addon must be world-readable or the drop fails
# with a permission error that looks like a broken proxy (Story 2.3).
COPY --chmod=0644 addon.py /opt/secureai/addon.py

# Pin the unprivileged user's GID to SECUREAI_PROXY_GID (10001).
#
# This exists so the host can grant the proxy read access to its config WITHOUT
# making that config world-readable. Bind mounts preserve the host's uid, gid
# and mode, so a 0700 root-owned /var/lib/secureai would be unreadable by the
# process that must read the allowlist and the keys -- which, because the addon
# fails closed on unreadable config (AD-13), would mean every request 403s.
# Instead: the dir is root:10001 0750 and the files root:10001 0640, so the
# proxy reads exactly what it needs and the host's other users read nothing.
ARG SECUREAI_PROXY_GID=10001
RUN set -eux; \
    groupmod -g "${SECUREAI_PROXY_GID}" mitmproxy; \
    usermod -g "${SECUREAI_PROXY_GID}" mitmproxy; \
    install -d -m 0750 -o root -g "${SECUREAI_PROXY_GID}" /config/ca

USER mitmproxy

# Bind-mounted at /config, owned root:10001 0750 on the host and mounted
# :z:ro, with /config/ca mounted separately read-write. Read-only because the
# proxy NEVER writes config (AD-13): /etc/secureai is the sole source of truth
# and the CLI is its only writer. The CA subdirectory is the single exception --
# mitmproxy must persist a generated keypair across restarts, or it would mint
# a new CA every start and the guest's baked-in trust would stop validating.
ENV SECUREAI_CONFIG_DIR=/config

EXPOSE 8080