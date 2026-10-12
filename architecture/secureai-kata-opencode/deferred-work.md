# Deferred Work

- source_spec: `architecture/secureai-kata-opencode/spec-continue-sprint-from-status.md`
  summary: >-
    Guest sessions cannot complete on Fedora 38 (kata 3.0.2 Go): with runtime
    resolution fixed, a Docker-managed bridge veth fails inside the sandbox
    with `conflicts with existing route`, while `--network none` boots a QEMU
    Q35 guest in ~3s. Guest end-to-end (stories 1-7 boot leg, Epic 5 live
    gates) needs a Fedora 43/44 host (kata 3.25/3.26) to confirm the Go
    tcfilter path works there against Docker-managed bridges.
  evidence: >-
    `docker run --rm --runtime kata` on docker0 AND on secureai_net
    (10.211.0.0/24) both fail programming the veth IP against the
    pre-existing bridge-subnet route; `--network none` and `ctr run
    --runtime io.containerd.kata.v2` both boot. Runtime wiring itself is
    settled: daemon.json carries `runtimeType` as the absolute rpm-derived
    Go-shim path (class-form is PATH-hijackable, `path`-form is OCI-mapped
    by Docker onto kata-runtime which lost its verbs in 2.x, and
    `path`+`runtimeType` bricks dockerd -- each observed live, see
    install-fedora.sh write_daemon_json). The pre-17:11 daemon.json that
    booted one session was overwritten without a backup before this was
    understood; the installer now keeps daemon.json.secureai-bak.

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
