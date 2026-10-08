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
COPY --chmod=0644 proxy/addon.py /opt/secureai/addon.py

# NOTE: this Dockerfile deliberately does NOT re-map the `mitmproxy` user.
# The upstream `docker-entrypoint.sh` is responsible for re-pinning that user's
# uid/gid to the owner of /home/mitmproxy/.mitmproxy at container start, so any
# host-side gid we assert would race with that step and make the next startup
# fail with "usermod: group '<base-gid>' does not exist". Instead the host
# state is shaped to MATCH the base image's canonical uid/gid (1000), so the
# entrypoint's re-pin is a no-op and gosu can drop cleanly into mitmproxy.

# Bind-mounted at /config, owned root:1000 0750 on the host and mounted
# :z:ro, with /config/ca mounted separately read-write. Read-only because the
# proxy NEVER writes config (AD-13): /etc/secureai is the sole source of truth
# and the CLI is its only writer. The CA subdirectory is the single exception
# -- mitmproxy must persist a generated keypair across restarts, or it would
# mint a new CA every start and the guest's baked-in trust would stop
# validating.
ENV SECUREAI_CONFIG_DIR=/config

EXPOSE 8080