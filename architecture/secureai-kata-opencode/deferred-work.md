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
