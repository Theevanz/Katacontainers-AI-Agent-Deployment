# Deferred Work

- source_spec: `architecture/secureai-kata-opencode/spec-continue-sprint-from-status.md`
  summary: >-
    Guest sessions cannot complete on Fedora 38 (kata 3.0.2 Go) with any
    Docker-managed network. Guest end-to-end (stories 1-7 boot leg, Epic 5
    live gates) needs a Fedora 43/44 host (kata 3.25/3.26) to confirm the Go
    tcfilter path works there against Docker-managed bridges.
  evidence: >-
    Exhausted live, each form run separately: class-form resolves via PATH
    (a /usr/local/bin runtime-rs shim hijacked the boot); `path`-form is
    OCI-mapped by Docker onto kata-runtime, which lost its verbs in 2.x;
    `path`+`runtimeType` bricks dockerd; `runtimeType` as the absolute
    rpm-derived Go-shim path boots deterministically. With wiring settled,
    every Docker-managed bridge (docker0 and secureai_net) fails programming
    the veth (`conflicts with existing route`, kata 3.0.2 tcfilter not
    idempotent vs libnetwork); disable_new_netns+none fails the same way in
    the host netns; host-network mode boots with loopback only; macvtap is
    impossible on this WiFi-only uplink. `--network none` and `ctr run
    --runtime io.containerd.kata.v2` both boot a QEMU Q35 guest, so
    hypervisor, assets, and config are proven good. A Fedora-38
    version-conditional config variant was built, proven live to warn,
    derive, and wire correctly -- then removed again with its tests when the
    remedy itself failed (kept in history, not in the tree). The pre-17:11
    daemon.json that booted one session was overwritten without a backup
    before this was understood; the installer now keeps
    daemon.json.secureai-bak.

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

- source_spec: `architecture/secureai-kata-opencode/spec-fix-kata-qemu-pid-denied.md`
  summary: Hardcoded /run/vc/sandbox path in guest_boot_diagnostics ignores fixture overrides.
  evidence: Pre-existing line untouched by this change; guest path already honors SECUREAI_KATA_VM_DIR while the sandbox listing does not.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-kata-qemu-pid-denied.md`
  summary: No retry path when a live-audit2allow module is refused on an older host policy (medium-unverified).
  evidence: Refusal demonstrated only for the baseline io_uring block on F38; settles with a live-denial rebuild run on an old-policy host showing the same refusal.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-secureai-kata-vm-boot-failure.md`
  summary: >-
    A/B the RPM Go path against the upstream static Kata 4.2 Rust bundle.
  evidence: >-
    A manual VM using the upstream static Kata 4.2 Rust bundle with nerdctl
    boots fine on the same class of host where every RPM-path boot (Fedora
    44, kata 3.26 Go + Docker 29.7, QEMU gone ~5s after launch, shim vsock
    ...:1024 timeout) fails. This spec repairs the RPM path in place and
    deliberately does not switch runtimes; a controlled static-bundle A/B on
    one host and one network would isolate packaging from configuration, but
    that comparison is out of scope here.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-secureai-kata-vm-boot-failure.md`
  summary: >-
    Bare-bisect runbook for vsock-timeout boots (debug config, minimal Kata config).
  evidence: >-
    Debug config (enable_debug, agent debug) is a document-only runbook in
    this spec: the CLI reads the active Kata configuration.toml for
    diagnostics but must never auto-edit it. A future runbook -- enable
    debug, capture the guest console, bisect a minimal config -- would
    shorten the next vsock-timeout investigation, but no live failing VM
    exists to validate it against, so it stays deferred.

- source_spec: `architecture/secureai-kata-opencode/spec-fix-secureai-kata-vm-boot-failure.md`
  summary: Scope fast QEMU-exit detection to the session sandbox instead of global pgrep.
  evidence: Watchdog counts all host qemu-system processes, so an unrelated QEMU masks detection until the 180s limit; scoping needs sandbox-ID plumbing the spec does not settle.
- source_spec: `architecture/secureai-kata-opencode/spec-fix-secureai-kata-vm-boot-failure.md`
  summary: Settle QEMU-exit threshold and agent-crash distinction live (medium-unverified).
  evidence: The >=10s grace and exited-with-QEMU-running mapping are unit-tested with stubs; settles with a live F44 run showing fast QEMU exit vs slow shim start and agent-crash behavior.
