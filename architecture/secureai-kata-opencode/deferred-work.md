# Deferred Work

- source_spec: `architecture/secureai-kata-opencode/spec-fix-proxy-vanishes-on-startup.md`
  summary: The generate-path die in `cmd_ca_export` names the wrong image.
  evidence: >-
    secureai cmd_ca_export's "CA generation produced no ..." die says "Check
    that the proxy image ${SECUREAI_PROXY_IMAGE} could be pulled", but the
    generation container deliberately runs the UPSTREAM tag
    mitmproxy/mitmproxy:12.2.3 (the project image cannot exist yet at that
    point in the bootstrap order). Pre-existing message bug, surfaced during
    review of the proxy-startup fix; a correct message would name the
    upstream tag.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-proxy-vanishes-on-startup.md`
  summary: install-fedora.sh's SELinux label assertion warns spuriously.
  evidence: >-
    install-fedora.sh compares `ls -Zd` field 1 (the full context, e.g.
    system_u:object_r:container_file_t:s0) against the bare type string
    "container_file_t", so the warning fires even when the type is correct.
    Observed live in the user's install transcript on Fedora 44. Warn-only,
    no functional impact; the fix is to compare field 3 of the context.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-addon-unreadable-in-image.md`
  summary: The guest image bakes payload files from the build context the same way the proxy did.
  evidence: >-
    opencode/Dockerfile COPYs opencode/opencode.json, opencode/models.json and
    ca.pem from the build context. The proxy's equivalent COPY is what made
    addon.py unreadable on an SELinux + containerd-store host (see
    spec-fix-addon-unreadable-in-image.md), so the same class of failure is
    plausible for the guest image. It has never been exercised -- the session
    never got past proxy startup -- so there is no evidence either way and no
    speculative fix was made.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-addon-unreadable-in-image.md`
  summary: RESOLVED -- the spurious SELinux label warning recorded above.
  evidence: >-
    Fixed alongside the /opt/secureai labeling: the assertion now compares the
    context's type field (cut -d: -f3) instead of the whole context string, so
    a correctly labelled host no longer warns.
