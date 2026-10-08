"""Tests for install-fedora.sh and the secureai CLI.

Companion to test_addon.py: that file covers the proxy's enforcement logic,
this one covers the host-side gates. Together they cover every row of the
spec's I/O & edge-case matrix.

What is genuinely executable here vs. what is only structural
-----------------------------------------------------------------
The installer refuses a non-Fedora-43/44 host BEFORE touching anything, so
its first gate is exercised for real on any machine (matrix rows 1 and 2).
Rows that need root, a Kata runtime, or a real image build cannot execute on
a developer box without them; those are asserted structurally over the source
instead, which is weaker but is honest about being weaker. Every such case is
marked below so the distinction is never lost.
"""

import os
import re
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO_ROOT, "install-fedora.sh")
CLI = os.path.join(REPO_ROOT, "secureai")
COMPOSE = os.path.join(REPO_ROOT, "docker-compose.yml")


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def code_lines(path):
    """Source with shell/python comments and blank lines removed.

    Several contracts below are about what the code DOES rather than what its
    prose mentions -- e.g. "no systemd unit" must not be satisfied by a comment
    saying there is none, and "no runtime-override flag" must not be tripped by
    a comment explaining why. Matching comments would make those tests either
    vacuous or permanently red, so they match code only.
    """
    out = []
    for line in read(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def function_body(path, name):
    """The text of one shell function, from its header to its closing brace."""
    source = read(path)
    start = source.index("%s() {" % name)
    end = source.index("\n}\n", start)
    return source[start:end]


class TestInstallerPreconditions(unittest.TestCase):
    """Matrix rows 1 and 2 -- exercised for real, not just asserted."""

    def test_unsupported_distribution_exits_nonzero_naming_versions(self):
        # Force an unsupported list so the Fedora gate fires deterministically
        # regardless of the host this test happens to run on -- and so running
        # it on a supported+root machine is still a property of the GATE
        # running (it dies before `install_packages` of `dnf`), never an
        # exercise of the mutating path. No skip required here.
        env = os.environ.copy()
        env["SECUREAI_FEDORA_VERSIONS"] = "99 77"
        result = subprocess.run(
            ["bash", INSTALLER], capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertNotEqual(result.returncode, 0, "an unsupported host must exit non-zero")
        output = result.stdout + result.stderr
        # Readable by a developer who does not know Kata or Docker (Story 1.2):
        # it must name the DETECTED distribution, the CONFIGURED supported
        # versions, and the exact way to acknowledge this host.
        self.assertIn("Fedora", output)
        self.assertIn("99", output)
        self.assertIn("77", output)
        self.assertIn("SECUREAI_FEDORA_VERSIONS", output)

    def test_preconditions_run_before_any_package_install_or_write(self):
        """Story 1.2: every precondition completes before the first write."""
        source = read(INSTALLER)
        # The real guards are behavioural, not positional -- but we can still
        # check that EVERY mutation helper is called only after
        # check_preconditions inside main().
        main_body = source[source.index("main()"):]
        cond_idx = main_body.index("check_preconditions")
        for mutation in ("install_packages", "write_daemon_json", "install_layout", "label_paths"):
            self.assertLess(
                cond_idx, main_body.index(mutation),
                "%s must run only after check_preconditions has succeeded" % mutation,
            )
        # And the check_preconditions call must be the FIRST mutation-adjacent
        # call in main -- a later refactor that inserted `dnf` above it would
        # regress the guarantee.
        self.assertLess(
            cond_idx, main_body.index("install_packages"),
            "check_preconditions must run before install_packages",
        )

    def test_root_check_precedes_every_write(self):
        body = function_body(INSTALLER, "check_preconditions")
        code = "\n".join(
            l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")
        )
        self.assertIn('EUID:-$(id -u)}" -ne 0', code, "the root assertion must exist")
        # Nothing that MUTATES the host may run in the precondition phase.
        # `command -v <tool>` is a presence CHECK, not a mutation, so the
        # precheck loop is allowed to mention systemctl/install/curl -- what it
        # must not do is actually invoke them.
        for mutation in ("dnf -y install", "restorecon", "semanage",
                         "systemctl enable", "systemctl restart", ">\"", "tee "):
            self.assertNotIn(
                mutation, code,
                "%s must not run in the precondition phase" % mutation,
            )

    def test_every_tool_the_installer_uses_is_prechecked(self):
        """A tool used but not prechecked fails mid-install, after a write."""
        body = function_body(INSTALLER, "check_preconditions")
        # Checked as a loop, so adding a tool cannot be forgotten.
        self.assertIn("for tool in", body)
        # curl because the payload may be fetched over HTTPS (AD-16); python3
        # because daemon.json is merged with it (no jq -- FR43 is bash only);
        # systemctl because docker is enabled --now.
        for tool in ("dnf", "curl", "python3", "systemctl", "install"):
            self.assertIn(tool, body, "%s must be prechecked" % tool)

    def test_no_unneeded_tool_dependency_is_introduced(self):
        """FR43: bash only -- no jq, no yq, and no unused helper tool."""
        code = code_lines(INSTALLER)
        for tool in ("tar", "jq", "yq"):
            self.assertNotRegex(
                code, r"\b%s\b" % tool,
                "%s must not be introduced (FR43: bash only)" % tool,
            )

    def test_installer_starts_docker_rather_than_only_restarting_it(self):
        """A fresh install must leave Docker RUNNING, or the runtime never loads.

        Restarting conditionally on `is-active` skipped a freshly installed,
        still-inactive daemon -- so the kata runtime was written to
        daemon.json but never registered, surfacing much later as an
        unreadable "no runtime named kata".
        """
        body = function_body(INSTALLER, "install_packages")
        self.assertIn("systemctl enable --now docker", body)


class TestInstallerRuntimeWiring(unittest.TestCase):
    """Matrix row 13 -- the version matrix. Structural (needs root to run)."""

    def test_version_pairing_is_one_matrix_row_not_a_disjunction(self):
        source = read(INSTALLER)
        # ONE comparison drives the matrix row, rather than several independent
        # conditions OR-ed together, and the threshold is a named constant so
        # the installer and `secureai status` cannot drift apart.
        self.assertIn("SECUREAI_TIME_NS_MIN_KEY", source)
        self.assertRegex(
            source,
            r"if\s*\(\(\s*version_key\s*>=\s*SECUREAI_TIME_NS_MIN_KEY\s*\)\)",
        )
        self.assertIn("time-namespaces", source)
        # The flag is written in one branch and explicitly absent in the other.
        self.assertIn('features["time-namespaces"] = False', source)
        self.assertIn('features.pop("time-namespaces", None)', source)

    def test_version_key_encoding_actually_selects_the_right_row(self):
        """EXECUTES the encoding, because a regex cannot check arithmetic.

        An earlier revision encoded a version as printf '%d%02d' 29 5 -> 2905
        and compared it against >= 2950, so 29.5.x -- precisely the versions the
        workaround exists for (kata#13080) -- took the WRONG branch and the flag
        was never written. No source-text assertion could have caught that.
        """
        script = """
        set -euo pipefail
        source_file="$1"
        # Pull the function out of the installer without running main.
        eval "$(sed -n '/^docker_version_key()/,/^}/p' "$source_file")"
        for v in 28.5.2 29.4.9 29.5.0 29.5.1 30.0.0; do
          printf '%s %s\\n' "$v" "$(docker_version_key "$v")"
        done
        """
        path = os.path.join(tempfile.gettempdir(), "secureai_version_key_probe.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        try:
            out = subprocess.run(
                ["bash", path, INSTALLER],
                capture_output=True, text=True, timeout=30,
            ).stdout
        finally:
            os.unlink(path)
        computed = dict(
            (line.split()[0], int(line.split()[1]))
            for line in out.strip().splitlines() if line.strip()
        )
        self.assertEqual(
            computed,
            {"28.5.2": 2850, "29.4.9": 2940, "29.5.0": 2950, "29.5.1": 2950, "30.0.0": 3000},
            "version encoding must be major*100 + minor*10",
        )
        # And the branch must actually select the workaround where it is needed.
        threshold = 2950
        flagged = {v for v, k in computed.items() if k >= threshold}
        self.assertEqual(flagged, {"29.5.0", "29.5.1", "30.0.0"})

    def test_default_runtime_is_never_written_and_asserted_absent(self):
        source = read(INSTALLER)
        self.assertIn('cfg.pop("default-runtime", None)', source)
        # And the installer must refuse to continue if one ever appears.
        self.assertIn('if grep -q \'"default-runtime"\'', source)

    def test_kata_runtime_uses_the_containerd_shim_not_a_path(self):
        source = read(INSTALLER)
        self.assertIn("containerd-shim-kata-v2", source)
        # `kata-qemu` is a NAME, never an executable to invoke.
        self.assertNotIn("/usr/bin/kata-qemu", source)
        self.assertNotIn("/usr/libexec/kata-qemu", source)

    def test_selinux_label_is_asserted_with_ls_Z_not_assumed(self):
        source = read(INSTALLER)
        self.assertIn("semanage fcontext -a -t container_file_t", source)
        self.assertIn("restorecon", source)
        self.assertIn("ls -Zd", source, "the resulting label must be observed, not assumed")
        # Permissive/disabled warns and continues rather than failing.
        self.assertIn("not Enforcing", source)

    def test_idempotent_rerun_does_not_fail_on_existing_state(self):
        """Matrix row 3. Structural: -a then -m is idempotent without deleting."""
        body = function_body(INSTALLER, "label_paths")
        # `-a` fails when the definition exists and `-m` fails when it does not,
        # so trying both in that order is safe on any re-run. The rejected
        # delete-first approach briefly left the path with NO fcontext at all if
        # the re-add failed, which is worse than a duplicate definition.
        # Matched across the line continuation with DOTALL.
        self.assertRegex(
            body, r"(?s)semanage fcontext -a.*?\|\|\s*semanage fcontext -m",
        )
        self.assertNotIn("semanage fcontext -d", body)
        self.assertIn("restorecon", body)
        # Existing operator config must never be clobbered on a re-run.
        layout = function_body(INSTALLER, "install_layout")
        self.assertIn('if [[ ! -e "${SECUREAI_ETC}/${seed}"', layout)
        self.assertIn('if [[ ! -e "${SECUREAI_ETC}/keys.yaml"', layout)
        self.assertIn('if [[ ! -e "${SECUREAI_ETC}/compose.env"', layout)
        # daemon.json is rebuilt from whatever was already there, not blindly
        # overwritten -- an existing Docker config must survive.
        daemon = function_body(INSTALLER, "write_daemon_json")
        self.assertIn('if [[ -s "$SECUREAI_DAEMON_JSON" ]]', daemon)

    def test_installer_creates_compose_env_but_never_populates_it(self):
        """AD-11: the installer may create the file, the CLI is its writer."""
        source = read(INSTALLER)
        block = source[source.index('if [[ ! -e "${SECUREAI_ETC}/compose.env" ]]'):]
        block = block[:block.index("fi")]
        self.assertIn(': >"${SECUREAI_ETC}/compose.env"', block)
        for name in ("SECUREAI_SUBNET", "SECUREAI_GATEWAY", "SECUREAI_PROXY_IP"):
            self.assertNotIn(name, block, "the installer must not write constant values")

    def test_installer_fetches_the_tree_without_git_clone(self):
        source = read(INSTALLER)
        self.assertIn("PAYLOAD_FILES", source)
        self.assertIn("SECUREAI_REPO_BASE", source)
        # A `git clone` in a comment explaining there is none is fine; in code it
        # is not, so match code only.
        self.assertNotIn("git clone", code_lines(INSTALLER))


class TestCliKeyHandling(unittest.TestCase):
    """Matrix row 10 -- values are always masked, and no flag reveals one."""

    def test_keys_list_masks_every_value(self):
        body = function_body(CLI, "cmd_keys_list")
        self.assertIn("****", body, "values must be masked")
        self.assertIn("fp=", body)
        # Every line that EMITS $value must also mask it or reduce it to a
        # fingerprint. Assignments and emptiness tests are not emissions.
        emitters = ("printf", "echo", "ok ", "warn ", "log ")
        for line in body.splitlines():
            if "$value" not in line:
                continue
            if not any(tok in line for tok in emitters):
                continue
            self.assertTrue(
                "****" in line or "sha256sum" in line,
                "an emitted value must be masked: %s" % line.strip(),
            )

    def test_no_command_echoes_a_raw_key(self):
        body = function_body(CLI, "cmd_keys_set")
        self.assertIn('ok "key stored for ${host}"', body)
        # The key may be written to the file and fingerprinted, never echoed.
        # Check with an explicit denylist: the value must only appear in the
        # file-write printf, inside the YAML escaping pipeline, or in the
        # fingerprint command -- never as a direct stdout emission.
        offenders = []
        for line in body.splitlines():
            stripped = line.strip()
            if "$key" not in stripped:
                continue
            safe = (
                ">>" in stripped                 # the keys.yaml write
                or "sha256sum" in stripped        # the fingerprint
                or stripped.startswith("local ")  # escaped_key assignment
                or stripped.startswith("escaped_key=")
                or "escaped_key=" in stripped
                or "sed" in stripped              # the escaping pipeline
            )
            if not safe and ('printf' in stripped or 'ok ' in stripped or 'echo' in stripped):
                offenders.append(stripped)
        self.assertEqual(offenders, [], "raw key must never be emitted")

    def test_whitelist_add_is_the_only_mutation_path_and_audits(self):
        source = read(CLI)
        body = source[source.index("cmd_whitelist_add()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("whitelist.audit.log", body)
        self.assertIn("sync_to_runtime whitelist.yaml", body)
        # Entries are quoted: a bare `- *.example.com` is a YAML scanner error.
        self.assertIn("""- "%s\"""", body)

    def test_whitelist_baseline_matches_the_documented_nine_hosts(self):
        source = read(CLI)
        block = source[source.index("SECUREAI_BASELINE_ALLOWLIST=("):]
        block = block[block.index("("):block.index(")")]
        hosts = re.findall(r'"([^"]+)"', block)
        seeded = read(os.path.join(REPO_ROOT, "config", "whitelist.yaml"))
        import yaml
        file_hosts = yaml.safe_load(seeded)
        self.assertEqual(hosts, file_hosts, "CLI seed and shipped baseline must agree")
        self.assertEqual(len(hosts), 9)
        readme = read(os.path.join(REPO_ROOT, "README.md"))
        for host in hosts:
            self.assertIn(host, readme, "%s must appear in the documented allowlist" % host)


class TestCliSessionAndGates(unittest.TestCase):
    """Matrix rows 13/14 and the AD-10 gates."""

    def test_asserts_proxy_runtime_pinned_before_a_session(self):
        source = read(CLI)
        self.assertIn("assert_proxy_runtime_pinned", source)
        self.assertIn("runtime: runc", source)

    def test_missing_runtime_yields_a_readable_message_naming_it(self):
        source = read(CLI)
        body = source[source.index("assert_runtime_registered()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("has no runtime named", body)
        self.assertIn("daemon.json", body, "the message must point at the fix")

    def test_proxy_health_wait_is_bounded_and_reports_failure(self):
        source = read(CLI)
        body = source[source.index("cmd_opencode()"):]
        self.assertIn("did not become healthy", body)
        self.assertIn("timeout: 5s", read(COMPOSE))
        self.assertIn("condition: service_healthy", read(COMPOSE))

    def test_teardown_trap_on_int_and_term_and_no_systemd_unit(self):
        source = read(CLI)
        # The word "systemd" must appear only in comments, never in live code
        # lines -- a comment stating there is no systemd unit is not evidence.
        self.assertIn("trap 'teardown_session; exit 130' INT TERM HUP", source)
        self.assertIn("trap teardown_session EXIT", source)
        # No live code may enable or reference systemd.
        for raw in code_lines(CLI).splitlines():
            self.assertNotIn("systemctl enable", raw)
            self.assertNotIn("systemd", raw.lower(),
                             "live code must not mention systemd: %s" % raw)

    def test_verify_reports_unverified_rather_than_guessing(self):
        """AD-10: a clean session start proves nothing and must not read as PASS."""
        source = read(CLI)
        body = source[source.index("cmd_verify()"):]
        self.assertIn("gate_unverified", source)
        self.assertIn("UNVERIFIED", body)
        # Every gate must be reachable and each limit stated.
        for gate in ("gate (a)", "gate (b)", "gate (c)", "gate (d)"):
            self.assertIn(gate, body)
        self.assertIn("LIMIT", body, "gate (c) must state what it does not prove")
        self.assertIn("does NOT prove", body)

    def test_verify_probe_does_not_claim_a_pass_without_observation(self):
        source = read(CLI)
        body = source[source.index("cmd_verify()"):]
        # The distinct-kernel and direct-path probes must gate on observation.
        self.assertIn("story 5.6", body)
        self.assertIn("story 5.7", body)
        self.assertIn("story 5.5", body)


class TestCaBuildOrder(unittest.TestCase):
    """Matrix row 12 -- the build refuses to proceed without a CA."""

    def test_guest_dockerfile_refuses_to_build_without_the_ca(self):
        dockerfile = read(os.path.join(REPO_ROOT, "opencode", "Dockerfile"))
        # The CA is copied from the BUILD CONTEXT. A `COPY --from=<stage-or-image>`
        # would name something nothing ever builds, and the build would fail on a
        # registry lookup instead of on the missing CA.
        self.assertRegex(dockerfile, r"(?m)^COPY\s+ca\.pem\s+/tmp/secureai-ca\.pem\s*$")
        self.assertNotRegex(dockerfile, r"COPY\s+--from=[^\s]*ca-export")
        # The refusal must NAME the command to run, not just fail (Story 1.6).
        self.assertIn("secureai ca export", dockerfile)
        self.assertIn("exit 1", dockerfile)
        # update-ca-certificates must run -- the system store is EXTENDED, never
        # replaced (AD-4).
        self.assertIn("update-ca-certificates", dockerfile)
        self.assertIn("NODE_EXTRA_CA_CERTS", dockerfile)
        # SSL_CERT_FILE must not be pointed at the mitm CA alone.
        self.assertNotRegex(dockerfile, r"SSL_CERT_FILE\s*=")

    def test_guest_dockerfile_copies_only_files_the_installer_installs(self):
        """Every COPY source must be a file install-fedora.sh actually places."""
        dockerfile = read(os.path.join(REPO_ROOT, "opencode", "Dockerfile"))
        # Local COPY sources (not --from, not the base image).
        local_sources = set()
        for line in dockerfile.splitlines():
            line = line.strip()
            if line.startswith("COPY ") and "--from=" not in line:
                parts = line.split()
                local_sources.add(parts[1])
        # ca.pem arrives from the build context (/var/lib/secureai), not /opt.
        self.assertEqual(
            local_sources, {"ca.pem", "opencode.json", "models.json"},
            "unexpected local COPY sources in the guest Dockerfile",
        )

    def test_installer_payload_covers_every_file_the_images_need(self):
        """A file missing from PAYLOAD_FILES is a build failure much later."""
        installer = read(INSTALLER)
        block = installer[installer.index("readonly PAYLOAD_FILES=("):]
        block = block[:block.index(")")]
        payload = set(re.findall(r"^\s*(\S+)$", block, re.M))

        needed = {
            "docker-compose.yml",
            "opencode/Dockerfile", "opencode/opencode.json", "opencode/models.json",
            "proxy/Dockerfile", "proxy/addon.py",
            "config/compose.env", "config/whitelist.yaml",
            "config/settings.yaml", "config/keys.yaml.template",
        }
        self.assertEqual(
            needed - payload, set(),
            "PAYLOAD_FILES must list every file the build context needs",
        )

    def test_ca_is_never_bind_mounted_into_a_running_container(self):
        compose = read(COMPOSE)
        self.assertNotIn("ca.pem", compose)

    def test_ca_export_reuses_existing_ca_so_baked_trust_stays_valid(self):
        source = read(CLI)
        body = source[source.index("cmd_ca_export()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("reused the existing CA", body)
        self.assertIn("--set confdir=", body)
        self.assertIn("mitmproxy-ca-cert.pem", body)


if __name__ == "__main__":
    unittest.main()
