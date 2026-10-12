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

    def test_installer_code_contains_no_backtick_substitution(self):
        """A `...` inside double quotes runs as a command, not as a quote.

        The package-failure message once read `kata-containers` in backticks,
        so every installer failure ALSO printed `kata-containers: command not
        found` on the die line, misdirecting the diagnosis at the exact
        moment a readable error mattered most. Backticks survive only in
        comments and quoted heredocs, where the shell never expands them.
        """
        lines = read(INSTALLER).splitlines()
        delimiter = None
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            if delimiter is not None:
                if stripped == delimiter:
                    delimiter = None
                continue
            match = re.search(r"<<'(\w+)'", line)
            if match:
                delimiter = match.group(1)
                continue
            if not stripped or stripped.startswith("#"):
                continue
            self.assertNotIn(
                "`", line,
                "backtick on line %d runs as command substitution" % n,
            )


class TestInstallPackagesCapabilityFirst(unittest.TestCase):
    """The package phase installs only what is missing, on any Fedora.

    A bare `docker` package name resolves to moby-engine, which conflicts
    with docker-ce-cli -- so an unconditional `dnf -y install ... docker`
    fatally breaks every working Docker-CE host (observed on F38: the
    installer died although Kata, Docker, and Compose all ran). These run
    the real `install_packages` with stubbed docker/rpm/dnf/systemctl.
    """

    def _run(self, present_pkgs, docker_present=True, dnf_fails=False):
        stub_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            stub_dir, ignore_errors=True))
        present_file = os.path.join(stub_dir, "present")
        with open(present_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(present_pkgs) + "\n")
        record = os.path.join(stub_dir, "dnf-args")
        helpers = {
            "docker": "#!/bin/bash\nexit 0\n",
            "rpm": ("#!/bin/bash\n"
                    '[ "$1" = "-q" ] || exit 1\n'
                    '/usr/bin/grep -qx "$2" "$STUB_PRESENT"\n'),
            "dnf": ("#!/bin/bash\n"
                    "printf '%s\\n' \"$@\" >> \"$STUB_DNF_RECORD\"\n"
                    'for arg in "$@"; do\n'
                    '  if [ "$arg" = "docker" ]; then\n'
                    "    printf '#!/bin/bash\\nexit 0\\n' > \"$STUB_DIR/docker\"\n"
                    "    /usr/bin/chmod 755 \"$STUB_DIR/docker\"\n"
                    "  fi\n"
                    "done\n"
                    + ("exit 1\n" if dnf_fails else "exit 0\n")),
            "systemctl": "#!/bin/bash\nexit 0\n",
        }
        for name, content in helpers.items():
            if name == "docker" and not docker_present:
                continue
            path = os.path.join(stub_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(path, 0o755)
        script = "\n".join([
            "set -euo pipefail",
            'log()  { printf "log: %s\\n" "$*"; }',
            'ok()   { printf "ok: %s\\n" "$*"; }',
            'warn() { printf "warn: %s\\n" "$*" >&2; }',
            'die()  { printf "error: %s\\n" "$*" >&2; exit 1; }',
            function_body(INSTALLER, "install_packages") + "\n}",
            "install_packages",
        ])
        env = {"PATH": stub_dir, "STUB_PRESENT": present_file,
               "STUB_DNF_RECORD": record, "STUB_DIR": stub_dir}
        return subprocess.run(["/bin/bash", "-c", script], capture_output=True,
                              text=True, timeout=60, env=env), record

    ALL_PKGS = ["kata-containers", "policycoreutils",
                "policycoreutils-python-utils", "dbus", "dbus-daemon"]

    def _recorded(self, record):
        if not os.path.exists(record):
            return []
        with open(record, encoding="utf-8") as fh:
            return [l.strip() for l in fh.read().splitlines() if l.strip()]

    def test_all_present_installs_nothing(self):
        result, record = self._run(self.ALL_PKGS)
        self.assertEqual(result.returncode, 0, result.stderr)
        for pkg in self.ALL_PKGS + ["docker"]:
            self.assertNotIn(pkg, self._recorded(record),
                             "%s is present and must not be reinstalled" % pkg)
        self.assertIn("already present", result.stdout)

    def test_only_missing_packages_are_requested(self):
        present = [p for p in self.ALL_PKGS if p != "kata-containers"]
        result, record = self._run(present)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self._recorded(record)
        self.assertIn("kata-containers", recorded)
        for pkg in present + ["docker"]:
            self.assertNotIn(pkg, recorded,
                             "%s is present and must not be reinstalled" % pkg)

    def test_absent_docker_engine_names_the_docker_package(self):
        result, record = self._run(self.ALL_PKGS, docker_present=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker", self._recorded(record))

    def test_failure_names_packages_without_spurious_command_errors(self):
        result, _ = self._run([], dnf_fails=True)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("kata-containers", output)
        self.assertNotIn("command not found", output)


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

    def test_kata_runtime_uses_an_absolute_shim_path_not_the_class(self):
        source = read(INSTALLER)
        # The runtimeType carries the absolute Go-shim path, not the
        # containerd class: class-form resolves the binary via PATH and is
        # hijackable, `path`-form is OCI-mapped by Docker onto kata-runtime
        # (which lost its OCI verbs in 2.x), and `path` + `runtimeType`
        # together are rejected by dockerd. The class literal survives only
        # in comments explaining why it is not written.
        # The bare shim binary NAME is still rejected (that never worked):
        #   failed to resolve runtime path: invalid runtime name
        #   containerd-shim-kata-v2, correct runtime name should be either
        #   format like `io.containerd.runc.v2` or a full path to the binary
        self.assertIn('"runtimeType": kata_shim', source)
        self.assertNotIn('"runtimeType": "containerd-shim-kata-v2"', source)
        self.assertNotIn('"runtimeType": "io.containerd.kata.v2"', source)
        # And the installer must assert the pin after writing it, must refuse
        # a `path` key (bricks the daemon), and must observe what Docker
        # actually loaded.
        self.assertIn("""grep -q '"runtimeType"[[:space:]]""", source)
        self.assertIn("containerd-shim-kata-v2", source)
        self.assertIn("secureai-bak", source)
        self.assertIn("docker info --format", source)
        # `kata-qemu` is a NAME, never an executable to invoke.
        self.assertNotIn("/usr/bin/kata-qemu", source)
        self.assertNotIn("/usr/libexec/kata-qemu", source)

    def test_kata_shim_binary_is_pinned_from_the_package_db(self):
        """The shim path is absolute, rpm-derived, and lives in runtimeType.

        Observed on F38: class-form resolves via PATH (a /usr/local/bin
        runtime-rs shim hijacked the boot), `path`-form is OCI-mapped by
        Docker onto kata-runtime (no OCI verbs since 2.x), and
        `path` + `runtimeType` together brick dockerd. The merge must
        therefore write the rpm-derived absolute path AS the runtimeType
        with no `path` key -- derived from `rpm -ql kata-containers`,
        never per-release -- and assert it after writing.
        """
        body = function_body(INSTALLER, "write_daemon_json")
        self.assertIn("kata_shim_path", body)
        self.assertIn("rpm -ql kata-containers", body)
        self.assertIn("containerd-shim-kata-v2$", body)
        self.assertIn('"runtimeType": kata_shim', body)
        self.assertNotIn('"path": kata_shim', body)
        # The backup lives past the merge block (after function_body's first
        # lone-brace cut), so it is asserted against the whole file.
        self.assertIn("secureai-bak", read(INSTALLER))

    def test_daemon_json_merge_pins_shim_and_keeps_shape(self):
        """EXECUTES the merge heredoc: pins, ConfigPath, no default-runtime.

        A source-text check cannot prove the written JSON carries the pin
        while preserving unrelated keys and dropping default-runtime, so the
        real heredoc runs against a fixture daemon.json here.
        """
        source = read(INSTALLER)
        start = source.index("<<'PY'")
        lines = source[start:].splitlines()[1:]
        python_lines = []
        for line in lines:
            if line == "PY":
                break
            python_lines.append(line)
        else:
            self.fail("merge heredoc terminator PY not found")
        python_src = "\n".join(python_lines)
        self.assertIn('"runtimeType": kata_shim', python_src)
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "daemon.json")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write('{"default-runtime": "runc", "debug": true,'
                         ' "runtimes": {"other": {"path": "/x"}}}')
            script = os.path.join(tmp, "merge.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(python_src)
            result = subprocess.run(
                ["python3", script, target, "false",
                 "/cfg/configuration.toml", "/usr/bin/containerd-shim-kata-v2"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            import json as _json
            with open(target, encoding="utf-8") as fh:
                merged = _json.load(fh)
        kata = merged["runtimes"]["kata"]
        self.assertEqual(kata["runtimeType"], "/usr/bin/containerd-shim-kata-v2")
        self.assertEqual(kata["options"]["ConfigPath"], "/cfg/configuration.toml")
        self.assertNotIn("path", kata,
                         "a path key beside runtimeType bricks dockerd")
        self.assertNotIn("default-runtime", merged)
        self.assertTrue(merged["debug"])
        # `runtimes` is replaced wholesale (only kata survives): the merge
        # owns that key so a stale second writer cannot linger in it.
        self.assertEqual(list(merged["runtimes"]), ["kata"])


class TestKataRuntimeTypeGate(unittest.TestCase):
    """The CLI's pre-session check on daemon.json's runtimeType.

    EXECUTED, not structural: the function is extracted from the CLI and run
    against fixture daemon.json files, because the whole point is that Docker
    reports a bad runtimeType as *registered* and only rejects it when a
    container is created.
    """

    def _run(self, daemon_json):
        # function_body stops before the closing brace, so it is closed here.
        body = function_body(CLI, "assert_kata_runtime_type") + "\n}"
        script = "\n".join([
            "die() { printf 'error: %s\\n' \"$*\" >&2; exit 1; }",
            "ok()  { printf 'ok: %s\\n' \"$*\"; }",
            body,
            "assert_kata_runtime_type \"$1\"",
        ])
        return subprocess.run(
            ["bash", "-c", script, "bash", daemon_json],
            capture_output=True, text=True, timeout=30,
        )

    def _fixture(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_runtime_class_form_passes(self):
        result = self._run(self._fixture(
            '{"runtimes": {"kata": {"runtimeType": "io.containerd.kata.v2"}}}'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_binary_path_passes(self):
        # Docker's other accepted form; the gate must not touch it.
        result = self._run(self._fixture(
            '{"runtimes": {"kata": {"runtimeType": "/usr/local/bin/containerd-shim-kata-v2"}}}'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shim_binary_name_is_rejected_with_a_named_fix(self):
        result = self._run(self._fixture(
            '{"runtimes": {"kata": {"runtimeType": "containerd-shim-kata-v2"}}}'))
        self.assertEqual(result.returncode, 1)
        self.assertIn("io.containerd.kata.v2", result.stderr)
        self.assertIn("systemctl restart docker", result.stderr)

    def test_missing_daemon_json_is_not_a_failure(self):
        # Nothing to assert means nothing to block; a die here would break
        # every rootless/unmanaged host.
        self.assertEqual(self._run("/nonexistent/daemon.json").returncode, 0)

    def test_opencode_runs_the_gate_before_starting_anything(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("assert_kata_runtime_type", body)


class KataGuestAssetsMixin:
    """Fixtures for the Kata guest kernel/initrd checks.

    Kata boots a real VM, so it needs a guest kernel and initrd that nothing in
    Docker ever checks. Fedora builds them with kata-osbuilder-generate.service,
    which it ships DISABLED, so a host can install cleanly and still fail every
    session with `file /var/cache/kata-containers/vmlinuz.container does not
    exist`.

    These tests EXECUTE the real functions against fixture configs and a fake
    `systemctl`, because the behaviour that matters -- calling the generator
    only when something is missing, not hanging when it cannot help, and not
    reporting a present-but-dangling symlink as usable -- cannot be seen by
    matching source text.
    """

    # The functions are byte-identical in both files by design; the installer
    # adds the build step, the CLI adds the pre-session gate.
    ASSET_FNS = ("kata_guest_assets", "kata_missing_assets")

    def _harness(self, source, functions, prelude=""):
        """A bash script with the CLI's output helpers, a fake systemctl, and
        the named functions extracted from the real file."""
        helpers = "\n".join([
            'die()  { printf "error: %s\\n" "$*" >&2; exit 1; }',
            'ok()   { printf "ok: %s\\n" "$*"; }',
            'warn() { printf "warn: %s\\n" "$*" >&2; }',
            'log()  { printf "==> %s\\n" "$*"; }',
            'sleep() { :; }',
            'KATA_GENERATOR_RUNS=0',
            'systemctl() {',
            '  case "$1" in',
            '    cat) [[ -n "$FAKE_UNIT" ]] && return 0 || return 1 ;;',
            '    is-active) [[ -n "$FAKE_UNIT_ACTIVE" ]] && return 0 || return 1 ;;',
            '  esac',
            '  KATA_GENERATOR_RUNS=$((KATA_GENERATOR_RUNS+1))',
            '  [[ -n "$FAKE_BUILD" ]] && { touch "$FAKE_BUILD";',
            '    for p in $FAKE_FIX; do rm -f "$p"; touch "$p"; done; }',
            '  return 0',
            '}',
        ])
        bodies = "\n".join(function_body(source, name) + "\n}" for name in functions)
        return "\n".join([helpers, prelude, bodies])

    def _config(self, kernel, image=None):
        """A Kata configuration.toml fixture with the given assets."""
        lines = ["[hypervisor.qemu]",
                 "  # Path to the kernel image to boot.",
                 '  kernel = "%s"' % kernel]
        if image:
            lines.append('  image = "%s"' % image)
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def _absent(self):
        """A path that cannot exist, so the fixture never depends on the box."""
        fd, path = tempfile.mkstemp()
        os.close(fd)
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def _present(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def _install(self, cfg, env, script):
        return subprocess.run(
            ["bash", "-c", script, "bash", cfg],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, **env},
        )


class TestKataGuestAssetParsing(KataGuestAssetsMixin, unittest.TestCase):
    """Reading the asset paths out of Kata's own configuration."""

    def _parse(self, cfg):
        script = self._harness(INSTALLER, self.ASSET_FNS) + \
            '\nkata_guest_assets "$1"\n'
        return subprocess.run(
            ["bash", "-c", script, "bash", cfg],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()

    def test_kernel_and_image_are_read_from_the_config(self):
        cfg = self._config("/var/cache/kata-containers/vmlinuz.container",
                           "/var/cache/kata-containers/kata-containers-initrd.img")
        self.assertEqual(
            self._parse(cfg),
            ["/var/cache/kata-containers/vmlinuz.container",
             "/var/cache/kata-containers/kata-containers-initrd.img"],
        )

    def test_relative_paths_are_skipped_not_guessed_at(self):
        # Kata resolves a relative kernel against its own runtime path, so
        # asserting an absolute path here would either pass wrongly or fail
        # wrongly. Skipping is the only honest answer.
        cfg = self._config("vmlinux", "initrd.img")
        self.assertEqual(self._parse(cfg), [])

    def test_a_commented_out_kernel_is_not_read(self):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('# kernel = "/nope/vmlinuz.container"\n'
                     '[hypervisor.qemu]\n  kernel = "/real/vmlinuz.container"\n')
        self.addCleanup(os.unlink, path)
        self.assertEqual(self._parse(path), ["/real/vmlinuz.container"])

    def test_a_dangling_symlink_counts_as_missing(self):
        # What a kernel update leaves behind: the symlink still resolves to a
        # path name, but /lib/modules/<old-kernel> is gone. `-e` follows the
        # link, so this is reported missing; testing -L would not.
        target = self._absent()
        link = self._present() + ".link"
        self.addCleanup(lambda: os.path.islink(link) and os.unlink(link))
        os.symlink(target, link)
        cfg = self._config(link)
        script = self._harness(INSTALLER, self.ASSET_FNS) + \
            '\nkata_missing_assets "$1"\n'
        out = subprocess.run(
            ["bash", "-c", script, "bash", cfg],
            capture_output=True, text=True, timeout=30,
        ).stdout
        self.assertIn(link, out)


class TestInstallerBuildsKataGuestAssets(KataGuestAssetsMixin, unittest.TestCase):
    """The installer's remediation, with systemctl faked."""

    def _run(self, cfg, env):
        script = self._harness(
            INSTALLER,
            self.ASSET_FNS + ("ensure_kata_guest_assets",),
            prelude='SECUREAI_KATA_CONFIG_PATH="$1"\n',
        ) + ('\nensure_kata_guest_assets "$SECUREAI_KATA_CONFIG_PATH"\n'
              'printf "GENERATOR_RUNS=%s\\n" "$KATA_GENERATOR_RUNS"\n')
        return self._install(cfg, env, script)

    def test_present_assets_are_left_alone(self):
        # An installer re-run must not rebuild a multi-minute initrd.
        cfg = self._config(self._present(), self._present())
        result = self._run(cfg, {"FAKE_UNIT": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GENERATOR_RUNS=0", result.stdout)

    def test_missing_assets_trigger_the_generator(self):
        kernel, image = self._absent(), self._absent()
        cfg = self._config(kernel, image)
        result = self._run(cfg, {
            "FAKE_UNIT": "1", "FAKE_BUILD": image, "FAKE_FIX": kernel,
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GENERATOR_RUNS=1", result.stdout)
        self.assertIn("built", result.stdout)

    def test_a_failed_generator_is_fatal_not_a_warning(self):
        # Proceeding here produces an installed system whose every session dies
        # with 'does not exist' -- the installer must not call that success.
        cfg = self._config(self._absent(), self._absent())
        result = self._run(cfg, {"FAKE_UNIT": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("still missing", result.stderr)

    def test_no_generator_on_the_host_fails_immediately(self):
        # Nothing can produce the assets, so waiting would only burn time
        # before the same failure. The fake `sleep` makes a hang detectable.
        cfg = self._config(self._absent(), self._absent())
        result = self._run(cfg, {"FAKE_UNIT": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("kata-osbuilder-generate.service", result.stderr)
        self.assertNotIn("GENERATOR_RUNS", result.stdout)

    def test_main_calls_it_after_the_daemon_json_is_written(self):
        # It reads the config path that write_daemon_json discovers, so it must
        # run after it.
        body = function_body(INSTALLER, "main")
        self.assertLess(body.index("write_daemon_json"),
                        body.index("ensure_kata_guest_assets"))


class TestCliGuestAssetGate(KataGuestAssetsMixin, unittest.TestCase):
    """The CLI's per-session gate, which is what catches the post-upgrade case."""

    def _run(self, daemon_json, env=None):
        script = self._harness(
            CLI,
            ("kata_config_path",) + self.ASSET_FNS
            + ("kata_missing_guest_assets", "assert_kata_guest_assets"),
        ) + '\nassert_kata_guest_assets\nprintf "GATE_PASSED\\n"\n'
        # The CLI resolves the path from $SECUREAI_DAEMON_JSON, which is what
        # makes the gate overridable for this test at all.
        return self._install(daemon_json,
                             {"SECUREAI_DAEMON_JSON": daemon_json, **(env or {})},
                             script)

    def _daemon_json(self, config_path=None):
        opts = '' if config_path is None else \
            ', "options": {"ConfigPath": "%s"}' % config_path
        return self._present_json(
            '{"runtimes": {"kata": {"runtimeType": "io.containerd.kata.v2"%s}}}'
            % opts)

    def _present_json(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_present_assets_pass(self):
        cfg = self._config(self._present(), self._present())
        result = self._run(self._daemon_json(cfg))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GATE_PASSED", result.stdout)

    def test_missing_assets_fail_with_the_exact_command(self):
        missing = self._absent()
        cfg = self._config(missing)
        result = self._run(self._daemon_json(cfg))
        self.assertEqual(result.returncode, 1)
        self.assertIn("systemctl restart kata-osbuilder-generate.service",
                      result.stderr)
        self.assertIn(missing, result.stderr)

    def test_a_host_without_a_configured_path_is_not_blocked(self):
        # Nothing can be determined, so nothing is asserted. A die here would
        # break every host that lets Kata use its own defaults.
        result = self._run(self._daemon_json(None))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GATE_PASSED", result.stdout)

    def test_opencode_checks_before_starting_anything(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("assert_kata_guest_assets", body)
        self.assertLess(body.index("assert_kata_guest_assets"),
                        body.index("Starting OpenCode"))

    def test_status_reports_the_guest_kernel(self):
        # `status` is where an operator looks first, so absence must be visible
        # there and not only as a die during a session.
        body = function_body(CLI, "cmd_status")
        self.assertIn("kata_missing_guest_assets", body)
        self.assertIn("kata guest kernel", body)


class TestGuestBootWatchdog(unittest.TestCase):
    """Kata boots a real VM, so `compose run -it` goes silent before the attach.

    Before this watchdog a guest that never booted left the session blocked
    forever on an attach that would never produce output, with nothing on
    screen to say so. These tests EXECUTE the real watchdog against a fake
    docker on a virtual clock, because the properties that matter -- staying
    silent on success, not firing when the agent merely exited early, and
    actually removing the container so the attach returns -- are all invisible
    to source matching.
    """

    def _harness(self, state_after, limit, removed=False):
        events = tempfile.mktemp(suffix=".events")
        self.addCleanup(lambda: os.path.exists(events) and os.unlink(events))
        for suffix in (".marker", ".secs"):
            path = events + suffix
            self.addCleanup(lambda p=path: os.path.exists(p) and os.unlink(p))

        script = "\n".join([
            'warn() { printf " warn %s\n" "$*" >&2; }',
            # Passthrough: the CLI wraps docker calls in `timeout` so a stuck
            # shim cannot wedge the watchdog. A real `timeout` binary would
            # bypass this harness's fake `docker` function (external binaries
            # cannot see shell functions), so fake it -- production still uses
            # the real coreutils timeout for hang protection.
            'timeout() { shift; "$@"; }',
            'SECUREAI_OPENCODE_CONTAINER=secureai-opencode',
            'kata_config_path() { printf "%%s\\n" /dev/null; }',
            'SECONDS=0',
            ': >"$EVENTS"',
            # A sleep that advances the clock, so `waited` and a virtual boot
            # deadline agree without the suite taking minutes.
            'sleep() { SECONDS=$((SECONDS+5)); }',
            'boot_at() { (( SECONDS >= %d )) && printf running || printf created; }' % state_after,
            'docker() {',
            '  if [[ "$1" == "inspect" ]]; then',
            '    %s && return 1' % ("true" if removed else "false"),
            '    boot_at; return 0',
            '  fi',
            '  if [[ "$1" == "rm" ]]; then printf "REMOVED\\n" >>"$EVENTS"; fi',
            '  return 0',
            '}',
            'pgrep() { printf "1\\n"; }',
            'ls() { [[ "$1" == /run/vc/sandbox ]] && { printf "sandbox1\\n"; return 0; }; command ls "$@"; }',
            function_body(CLI, "guest_boot_diagnostics") + "\n}",
            function_body(CLI, "guest_boot_watchdog") + "\n}",
            'GUEST_BOOT_MARKER="$EVENTS.marker"',
            'GUEST_BOOT_SECONDS_FILE="$EVENTS.secs"',
            'guest_boot_watchdog "%d"' % limit,
            '[[ -s "$GUEST_BOOT_MARKER" ]] && printf "MARKER=failed\\n" || printf "MARKER=ok\\n"',
            'printf "boot_s=%s\\n" "$(cat "$GUEST_BOOT_SECONDS_FILE" 2>/dev/null || echo none)"',
            'printf "removed=%s\\n" "$(grep -c REMOVED "$EVENTS" || true)"',
        ])
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            timeout=90, env={**os.environ, "EVENTS": events},
        )

    def test_a_successful_boot_is_silent(self):
        # Anything the watchdog printed here would land on the agent's TUI and
        # corrupt it, so success must produce no output at all.
        result = self._harness(state_after=20, limit=180)
        self.assertEqual(result.stderr, "", result.stderr)

    def test_a_successful_boot_records_its_duration(self):
        result = self._harness(state_after=20, limit=180)
        self.assertIn("MARKER=ok", result.stdout)
        self.assertIn("boot_s=20", result.stdout)
        self.assertIn("removed=0", result.stdout)

    def test_a_slow_boot_under_the_limit_is_not_an_error(self):
        # The limit must be generous enough for a real first VM boot.
        result = self._harness(state_after=75, limit=180)
        self.assertIn("MARKER=ok", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_a_guest_that_never_boots_is_reported_and_unblocked(self):
        result = self._harness(state_after=9999, limit=30)
        self.assertIn("MARKER=failed", result.stdout)
        # Removing the container is the whole point: it is what lets the
        # foreground attach return instead of hanging forever.
        self.assertIn("removed=1", result.stdout)
        self.assertIn("has not reached 'running'", result.stderr)
        self.assertIn("journalctl -u containerd", result.stderr)

    def test_an_early_agent_exit_is_not_reported_as_a_failed_boot(self):
        # With --rm the container is gone the moment the agent exits. That is
        # an ordinary session end, not a VM that failed to boot.
        result = self._harness(state_after=9999, limit=180, removed=True)
        self.assertIn("MARKER=ok", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_the_marker_is_not_empty(self):
        # The foreground path tests the marker with -s. An empty `: >` marker
        # reads as "no failure", which restores the original silent hang --
        # this caught exactly that during development.
        body = function_body(CLI, "guest_boot_watchdog")
        self.assertNotIn(': >"$GUEST_BOOT_MARKER"', body)
        self.assertIn('>"$GUEST_BOOT_MARKER"', body)
        result = self._harness(state_after=9999, limit=30)
        self.assertIn("MARKER=failed", result.stdout)

    def test_teardown_stops_the_watchdog(self):
        # A watchdog outliving the session would remove containers and print
        # diagnostics out of context during the NEXT session.
        body = function_body(CLI, "teardown_session")
        self.assertIn("GUEST_WATCHDOG_PID", body)
        # And its scratch files must not accumulate in /tmp, one pair per session.
        self.assertIn("GUEST_BOOT_MARKER", body)
        self.assertIn("GUEST_BOOT_SECONDS_FILE", body)

    def test_the_failure_path_uses_if_blocks_not_bare_and_lists(self):
        # The CLI runs under `set -euo pipefail`. A bare `[[ cond ]] && assign`
        # is exempt from set -e only by a subtlety of list semantics, and the
        # whole file uses `if` everywhere else -- on the path whose failure we
        # are trying to report, that is not a subtlety worth relying on.
        body = function_body(CLI, "cmd_opencode")
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("[[") and "]] &&" in stripped:
                self.fail("bare &&-list in cmd_opencode under set -e: %s" % stripped)

    def test_the_wait_is_explained_before_the_attach(self):
        # The silence is inherent; being told so is the difference between
        # "booting" and "broken".
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("SECUREAI_GUEST_BOOT_TIMEOUT", body)
        self.assertIn("stays blank", body)

    def test_the_session_is_closed_rather_than_left_hanging(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("guest_never_started", body)
        self.assertIn("die \"The Kata guest VM never started", body)

    def test_selinux_label_is_asserted_with_ls_Z_not_assumed(self):
        source = read(INSTALLER)
        self.assertIn("semanage fcontext -a -t container_file_t", source)
        self.assertIn("restorecon", source)
        self.assertIn("ls -Zd", source, "the resulting label must be observed, not assumed")
        # Permissive/disabled warns and continues rather than failing.
        self.assertIn("not Enforcing", source)

    def test_label_assertion_compares_the_type_not_the_whole_context(self):
        # `ls -Zd` field 1 is the full context
        # (system_u:object_r:container_file_t:s0), which can never equal the
        # bare type string -- so comparing it to "container_file_t" warned on
        # every correctly labelled host. Field 3 is the type.
        body = function_body(INSTALLER, "label_paths")
        self.assertIn("cut -d: -f3", body)
        self.assertIn("!= \"container_file_t\"", body)

    def test_installer_labels_the_app_tree_container_file_t(self):
        # The tree in /opt/secureai is the build context for BOTH images, so
        # every payload file baked into an image originates here. Left at the
        # default /opt label it is not container-readable.
        body = function_body(INSTALLER, "label_paths")
        self.assertIn("SECUREAI_APP_DIR", body)

    def test_proxy_addon_is_created_in_a_layer_not_copied_into_the_image(self):
        # A file copied from a build context can carry that context's SELinux
        # label into the image layer (containerd image store preserves xattrs),
        # and a container_t process cannot getattr such a file -- mitmproxy
        # reports that as "No such script" because os.path.isfile returns False
        # on EACCES. Writing the file inside a RUN gives it the image's own
        # context label instead.
        dockerfile = read(os.path.join(REPO_ROOT, "proxy", "Dockerfile"))
        self.assertNotIn("COPY --chmod=0644 proxy/addon.py /opt/secureai/addon.py", dockerfile,
                         "the addon must be written by a RUN, not copied into the image")
        self.assertIn("/opt/secureai/addon.py", dockerfile)
        self.assertIn("install -m 0644", dockerfile)

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

    def test_guest_run_does_not_restart_the_health_gated_proxy(self):
        # `compose run` starts the target service's dependencies unless it is
        # told not to, and the proxy was already created by hand with the same
        # container name -- so compose's attempt to start it collides with it
        # ("The container name /secureai-proxy is already in use").
        body = function_body(CLI, "cmd_opencode")
        guest_run_idx = body.index('opencode || rc=$?')
        guest_run = body[guest_run_idx - 400:guest_run_idx]
        self.assertIn("--no-deps", guest_run,
                      "the guest run must pass --no-deps; the CLI has already "
                      "started and health-gated the proxy itself")

    def test_session_start_removes_leftover_containers_first(self):
        # A session killed mid-flight (power loss, SIGKILL) leaves containers
        # holding these names, and the next session then dies on a name
        # conflict. Nothing else may hold them: the proxy holds the real keys.
        body = function_body(CLI, "cmd_opencode")
        # Anchor on the first EXECUTED compose run, not on the earlier comment
        # that merely mentions `compose run`.
        head = body[:body.index("compose run --rm")]
        for name in ('"$SECUREAI_PROXY_CONTAINER"', '"$SECUREAI_OPENCODE_CONTAINER"'):
            self.assertIn(name, head)
        self.assertIn("docker rm -f", head,
                      "leftovers must be cleared before the first compose run")

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
        # ca.pem arrives from the build context (/var/lib/secureai staged into
        # app dir), not /opt; the others are installer-shipped tree files whose
        # paths mirror the context's /opt/secureai layout.
        self.assertEqual(
            local_sources, {"ca.pem", "opencode/opencode.json", "opencode/models.json"},
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


class TestProxyDeathVisibility(unittest.TestCase):
    """The proxy ran with `--rm`, so a startup exit auto-removes the container.

    Structural coverage of the two failure modes that made a dead proxy
    undebuggable: the CA key permissions mitmdump needs to stay alive, and
    log capture that survives the container's auto-removal.
    """

    def test_ca_export_chmods_the_key_mitmproxy_actually_writes(self):
        body = function_body(CLI, "cmd_ca_export")
        self.assertIn('chmod 0640 "${SECUREAI_VAR}/ca/mitmproxy-ca.pem"', body,
                      "the combined private key must be group-readable")
        # mitmproxy 12.x never writes this name; chmodding it is a no-op that
        # left the real key at 0600 and killed the proxy at startup.
        code = "\n".join(
            line for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#"))
        self.assertNotIn("mitmproxy-ca-cert-key.pem", code)

    def test_ca_export_repins_permissions_after_the_generate_reuse_branch(self):
        body = function_body(CLI, "cmd_ca_export")
        reuse_start = body.index('if [[ -f "$existing" ]]')
        branch_end = body.index("\n  fi\n", reuse_start)
        after_branch = body[branch_end:]
        self.assertIn("mitmproxy-ca.pem", after_branch,
                      "the re-pin must run after the generate/reuse branch so "
                      "an install pinned by an older CLI is repaired on reuse")

    def test_ca_export_p12_chmod_is_guarded(self):
        # The combined .pem is existence-checked, the .p12 is not; under
        # `set -e` an unguarded chmod of a missing .p12 would abort the
        # export with a raw error after the CA was already exported.
        body = function_body(CLI, "cmd_ca_export")
        self.assertIn('chmod 0640 "${SECUREAI_VAR}/ca/mitmproxy-ca.p12" 2>/dev/null || true',
                      body)

    def test_ca_export_dies_if_the_private_key_is_missing(self):
        body = function_body(CLI, "cmd_ca_export")
        self.assertIn("mitmproxy-ca.pem is missing", body)

    def test_opencode_streams_proxy_logs_from_creation(self):
        body = function_body(CLI, "cmd_opencode")
        # Backgrounded and appending: a foreground `docker logs -f` would hang
        # the session, and a truncating single-writer snapshot cannot survive
        # the container's auto-removal.
        self.assertIn('docker logs -f "$SECUREAI_PROXY_CONTAINER" '
                      '>>"${SECUREAI_VAR}/proxy.log" 2>/dev/null &', body)
        self.assertIn("LOG_STREAM_PID=$!", body)

    def test_opencode_treats_a_vanished_container_as_death(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("vanished", body,
                      "with --rm, `docker inspect` failing is the only "
                      "observable form of an early proxy exit")

    def test_failure_path_prints_the_captured_log_not_docker_logs(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn('tail -20 "${SECUREAI_VAR}/proxy.log"', body)
        self.assertNotIn('docker logs "$SECUREAI_PROXY_CONTAINER" 2>&1 | tail', body,
                         "by the time the failure path runs, --rm may have "
                         "already removed the container")

    def test_cli_is_syntactically_valid_bash(self):
        # Executable, not structural: the restructured session functions must
        # at least parse.
        result = subprocess.run(["bash", "-n", CLI], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_proxy_image_installs_the_addons_dependencies(self):
        # addon.py imports yaml; the upstream mitmproxy image does not ship
        # PyYAML, so mitmdump dies at startup with ModuleNotFoundError unless
        # the image adds it. Verified live: with correct CA permissions the
        # proxy reaches script load and fails on exactly this import.
        dockerfile = read(os.path.join(REPO_ROOT, "proxy", "Dockerfile"))
        self.assertIn("pip install", dockerfile)
        self.assertIn("pyyaml", dockerfile.lower())
        addon = read(os.path.join(REPO_ROOT, "proxy", "addon.py"))
        self.assertIn("import yaml", addon,
                      "if the addon drops the yaml import, drop the image "
                      "dependency and this test with it")

    def test_teardown_does_not_clobber_the_streamed_log(self):
        body = function_body(CLI, "teardown_session")
        snapshot_idx = body.index('>"${SECUREAI_VAR}/proxy.log"')
        before_snapshot = body[:snapshot_idx]
        self.assertIn("docker inspect", before_snapshot,
                      "the snapshot must be guarded by a container-existence "
                      "check so it cannot overwrite the streamed log with "
                      "the daemon's 'No such container' error")
        self.assertIn('kill "$LOG_STREAM_PID"', before_snapshot,
                      "the stream must be stopped before the snapshot, or its "
                      "appends race the truncated file")



class TestWhitelistList(unittest.TestCase):
    """Story 3-6: `whitelist list` prints what the proxy actually reads.

    EXECUTED against the real function with fixture dirs, because the
    properties that matter -- dying on missing, warning on empty, and
    preferring the runtime copy -- are invisible to source matching.
    """

    def _run(self, etc_files=None, var_files=None):
        import tempfile as _tf
        tmp = _tf.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        etc_dir = tmp + "/etc"
        var_dir = tmp + "/var"
        os.makedirs(etc_dir, exist_ok=True)
        os.makedirs(var_dir, exist_ok=True)
        for name, content in (etc_files or {}).items():
            with open(os.path.join(etc_dir, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        for name, content in (var_files or {}).items():
            with open(os.path.join(var_dir, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        body_yaml = function_body(CLI, "yaml_list") + "\n}"
        body_list = function_body(CLI, "cmd_whitelist_list") + "\n}"
        helpers = "\n".join([
            'die()  { printf "error: %s\\n" "$*" >&2; exit 1; }',
            'ok()   { printf "ok: %s\\n" "$*"; }',
            'warn() { printf "warn: %s\\n" "$*" >&2; }',
            'log()  { printf "==> %s\\n" "$*"; }',
            'cmd_init_config() { return 0; }',
        ])
        script = "\n".join([helpers, body_yaml, body_list, 'cmd_whitelist_list'])
        env = {**os.environ, "SECUREAI_ETC": etc_dir, "SECUREAI_VAR": var_dir}
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=30, env=env)

    def test_missing_allowlist_dies_naming_whitelist_add(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0,
                            "missing allowlist must exit non-zero")
        self.assertIn("whitelist add", result.stderr + result.stdout)

    def test_empty_allowlist_warns_that_every_host_is_blocked(self):
        result = self._run(var_files={"whitelist.yaml": "# nothing here\n"})
        self.assertEqual(result.returncode, 0,
                         "empty allowlist warns, it does not die: %s" % result.stderr)
        out = result.stdout + result.stderr
        self.assertIn("every host", out)
        self.assertIn("blocked", out)

    def test_prefers_the_runtime_copy_and_prints_its_source(self):
        result = self._run(
            etc_files={"whitelist.yaml": '- "etc-only.example"\n'},
            var_files={"whitelist.yaml": '- "var-only.example"\n'},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout + result.stderr
        self.assertIn("var-only.example", out,
                      "the listing must reflect the synced copy the proxy reads")
        self.assertNotIn("etc-only.example", out,
                         "the stale /etc copy must not be listed when a runtime copy exists")
        self.assertIn("var", out, "the runtime source path must be printed")

    def test_lists_the_etc_copy_when_no_runtime_copy_exists(self):
        result = self._run(etc_files={"whitelist.yaml": '- "etc-only.example"\n'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("etc-only.example", result.stdout + result.stderr)

    def test_whitelist_list_is_structurally_synced_and_audited(self):
        body = function_body(CLI, "cmd_whitelist_list")
        self.assertIn("SECUREAI_VAR", body, "must prefer the runtime copy")
        self.assertIn("whitelist add", body, "missing case must name the fix")


class TestSessionTiming(unittest.TestCase):
    """Story 4-4: timing labels cold/warm, judges the total, keeps the proxy subverdict."""

    def test_cold_and_warm_are_separate_records_never_overwritten(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("--warm", body)
        self.assertIn("--cold", body)
        self.assertIn("session_kind", body)
        self.assertIn("last-session-timing-${session_kind}.txt", body,
                      "cold and warm records must be preserved separately")
        self.assertIn("session-timing-history.log", body,
                      "history must be appended, never truncated blindly")

    def test_total_invocation_to_ready_is_judged_not_proxy_only(self):
        body = function_body(CLI, "cmd_opencode")
        self.assertIn("total_invocation_to_ready_s", body)
        self.assertIn("prd_budget_30s_total", body,
                      "the total verdict must be recorded against the 30s budget")
        self.assertIn("proxy_ready_subverdict_30s", body,
                      "the proxy-ready subverdict must be preserved separately")
        # The judged value is the total, derived from invocation to ready.
        self.assertIn("guest_start_s", body,
                      "total must derive from invocation-to-ready, not session end alone")
        self.assertRegex(body, r"if\s*\(\(\s*total_ready_s\s*>\s*30\s*\)\)",
                         "the 30s verdict must judge the total")

    def test_timing_records_decomposition_with_one_host_note(self):
        body = function_body(CLI, "cmd_opencode")
        for field in ("proxy_to_healthy_s", "kata_guest_boot_s",
                      "total_invocation_to_ready_s", "total_session_s"):
            self.assertIn(field, body, "%s must be recorded" % field)
        self.assertIn("one host, one day", body)
        self.assertIn("not a guarantee", body)
        self.assertIn("miss", body.lower())
        self.assertIn("NFR14", body, "a miss must point at a PRD amendment, not a code fix")

    def test_timing_uses_if_blocks_not_bare_and_lists(self):
        body = function_body(CLI, "cmd_opencode")
        # Timing verdicts run under set -euo pipefail; bare &&-lists are
        # exempt only by list-semantics subtlety this file avoids elsewhere.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("[[") and "]] &&" in stripped:
                self.fail("bare &&-list in cmd_opencode under set -e: %s" % stripped)


class TestSessionTimingExecuted(unittest.TestCase):
    """Story 4-4 executed: the real timing block runs with fixture clocks.

    Substring checks above pass on comments alone; an inverted verdict arm,
    a total computed from session end, a `>` truncating history, or a dropped
    `cp` latest all keep them green while recording wrong numbers. This runs
    the actual block extracted from `cmd_opencode` so behavior is observed.
    """

    def _block(self):
        body = function_body(CLI, "cmd_opencode")
        start = body.index("local total_ready_s=")
        end = (body.index("session-timing-history.log\"")
               + len("session-timing-history.log\""))
        return body[start:end]

    def _run(self, kind, guest_boot, guest_start=105, session_start=100,
             proxy_s=5, total_session=200, tmp=None, history_seed=None):
        block = self._block()
        var_dir = tmp or tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            var_dir, ignore_errors=True))
        if history_seed is not None:
            with open(os.path.join(var_dir, "session-timing-history.log"),
                      "w", encoding="utf-8") as fh:
                fh.write(history_seed)
        script = "\n".join([
            "set -euo pipefail",
            "run_timing() {",
            '  local session_kind="$1" guest_boot_s="$2" guest_start_s="$3"'
            ' session_start_s="$4" proxy_s="$5" total_session_s="$6"'
            ' SECUREAI_VAR="$7"',
            block,
            "}",
            'run_timing "%s" "%s" "%s" "%s" "%s" "%s" "%s"' % (
                kind, guest_boot, guest_start, session_start,
                proxy_s, total_session, var_dir),
        ])
        result = subprocess.run(["bash", "-c", script], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0,
                         "timing block must exit 0: %s" % result.stderr)
        return var_dir

    def _read(self, var_dir, name):
        with open(os.path.join(var_dir, name), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_total_met_and_proxy_subverdict(self):
        var_dir = self._run("cold", "10")
        content = self._read(var_dir, "last-session-timing-cold.txt")
        self.assertIn("total_invocation_to_ready_s: 15", content)
        self.assertIn("prd_budget_30s_total: MET", content)
        self.assertIn("proxy_ready_subverdict_30s: MET", content)
        self.assertIn("kind: cold", content)
        latest = self._read(var_dir, "last-session-timing.txt")
        self.assertIn("kind: cold", latest)

    def test_total_over_budget_reports_not_met(self):
        var_dir = self._run("warm", "40")
        content = self._read(var_dir, "last-session-timing-warm.txt")
        self.assertIn("total_invocation_to_ready_s: 45", content)
        self.assertIn("prd_budget_30s_total: NOT MET", content)
        self.assertIn("proxy_ready_subverdict_30s: MET", content)

    def test_unknown_boot_is_not_met(self):
        var_dir = self._run("cold", "unknown")
        content = self._read(var_dir, "last-session-timing-cold.txt")
        self.assertIn("total_invocation_to_ready_s: unknown", content)
        self.assertIn("prd_budget_30s_total: NOT MET", content)

    def test_cold_and_warm_preserved_with_appended_history(self):
        var_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            var_dir, ignore_errors=True))
        self._run("cold", "10", tmp=var_dir)
        self._run("warm", "40", tmp=var_dir)
        cold = self._read(var_dir, "last-session-timing-cold.txt")
        warm = self._read(var_dir, "last-session-timing-warm.txt")
        self.assertIn("kind: cold", cold)
        self.assertIn("kind: warm", warm)
        self.assertIn("total_invocation_to_ready_s: 15", cold)
        self.assertIn("total_invocation_to_ready_s: 45", warm)
        history = self._read(var_dir, "session-timing-history.log")
        self.assertIn("kind: cold", history)
        self.assertIn("kind: warm", history)


class TestProxyHealthPolicyGate(unittest.TestCase):
    """Story 2-6: health reflects policy loaded in-process, not just a socket."""

    def test_compose_healthcheck_proves_policy_loads(self):
        compose = read(COMPOSE)
        self.assertIn("is_healthy", compose,
                      "the healthcheck must exercise the addon policy, not just a socket")
        self.assertIn("import addon", compose)
        # The raw-socket proof is kept: the proxy must still answer on its port.
        self.assertIn("CONNECT", compose)
        self.assertIn("timeout: 5s", compose)

    def test_compose_keeps_service_healthy_as_declarative_backup(self):
        compose = read(COMPOSE)
        cli = read(CLI)
        self.assertIn("condition: service_healthy", compose)
        # The CLI poll loop is the enforcing gate under --no-deps; both files
        # must document it so a future removal of either is caught.
        self.assertIn("--no-deps", compose,
                      "compose must document the --no-deps enforcing-gate contract")
        self.assertIn("--no-deps", cli,
                      "the CLI must document the --no-deps enforcing gate it relies on")
        self.assertRegex(compose, r"(?i)enforcing gate|ENFORCING",
                         "compose must document the CLI poll loop as the enforcing gate")

    def test_addon_startup_hooks_exist(self):
        source = read(os.path.join(REPO_ROOT, "proxy", "addon.py"))
        for hook in ("def configure", "def is_healthy"):
            self.assertIn(hook, source, "%s must exist for eager health gating" % hook)
        self.assertIn("_ready", source)


class TestProxyHealthcheckExecuted(unittest.TestCase):
    """Story 2-6 executed: the composed healthcheck runs, not just reads.

    Substring checks above stay green if the socket guard is deleted, the
    `sys.path` points elsewhere, or `${SECUREAI_PROXY_PORT}` never
    interpolates. This runs the exact `-c` body from `docker-compose.yml`
    (port substituted; the container path `/opt/secureai` mapped to the repo
    `proxy/` dir as the only documented test adaptation, with a mitmproxy
    stub standing in for the image's installed mitmproxy) against a loopback
    CONNECT stub and fixture configs, asserting exit codes.
    """

    def _snippet(self):
        compose = read(COMPOSE)
        self.assertIn("${SECUREAI_PROXY_PORT}", compose,
                      "the healthcheck port must interpolate from compose.env")
        start = compose.index("import socket,sys;")
        end = (compose.index("is_healthy() else 1)")
               + len("is_healthy() else 1)"))
        # YAML `>-` folds newlines to spaces at runtime; mimic the fold so the
        # executed form matches what the container runs.
        return " ".join(compose[start:end].split())

    def _stub_mitmproxy(self, root):
        pkg = os.path.join(root, "mitmproxy")
        os.makedirs(pkg, exist_ok=True)
        with open(os.path.join(pkg, "__init__.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("")
        with open(os.path.join(pkg, "http.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("class Response:\n"
                     "    def __init__(self, status_code=200, content=b\"\"):\n"
                     "        self.status_code = status_code\n"
                     "        self.content = content\n"
                     "    @classmethod\n"
                     "    def make(cls, status_code=200, content=b\"\"):\n"
                     "        return cls(status_code, content)\n")

    def _write_config(self, root, whitelist=None):
        os.makedirs(root, exist_ok=True)
        wl = whitelist if whitelist is not None else ["api.example.test"]
        with open(os.path.join(root, "whitelist.yaml"), "w",
                  encoding="utf-8") as fh:
            for host in wl:
                fh.write('- "%s"\n' % host)
        with open(os.path.join(root, "keys.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write('"api.example.test": "test-key"\n')
        with open(os.path.join(root, "settings.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("cache_ttl_seconds: 5\n")

    def _serve(self):
        import socket as _socket
        import threading as _threading
        srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(5)
        stop = _threading.Event()

        def _loop():
            srv.settimeout(0.5)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except _socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    conn.settimeout(3)
                    try:
                        conn.recv(256)
                    except OSError:
                        pass
                    try:
                        conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
                    except OSError:
                        pass
                finally:
                    conn.close()

        thread = _threading.Thread(target=_loop, daemon=True)
        thread.start()
        self.addCleanup(stop.set)
        self.addCleanup(srv.close)
        return port

    def _run(self, port, config_dir):
        import tempfile as _tf
        stub_root = _tf.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            stub_root, ignore_errors=True))
        self._stub_mitmproxy(stub_root)
        proxy_dir = os.path.join(REPO_ROOT, "proxy")
        body = self._snippet().replace("${SECUREAI_PROXY_PORT}", str(port))
        body = body.replace("/opt/secureai", proxy_dir)
        env = {**os.environ,
               "SECUREAI_CONFIG_DIR": config_dir,
               "SECUREAI_PLACEHOLDER_KEY": "sk-secureai-placeholder",
               "PYTHONPATH": stub_root + os.pathsep + os.environ.get(
                   "PYTHONPATH", "")}
        return subprocess.run(["python3", "-c", body], capture_output=True,
                              text=True, timeout=30, env=env)

    def test_healthy_when_socket_answers_and_policy_loads(self):
        import tempfile as _tf
        config_dir = _tf.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            config_dir, ignore_errors=True))
        self._write_config(config_dir)
        result = self._run(self._serve(), config_dir)
        self.assertEqual(result.returncode, 0,
                         "socket + policy must exit 0: %s" % result.stderr)

    def test_unhealthy_when_socket_refuses(self):
        import socket as _socket
        import tempfile as _tf
        probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        config_dir = _tf.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            config_dir, ignore_errors=True))
        self._write_config(config_dir)
        result = self._run(port, config_dir)
        self.assertNotEqual(result.returncode, 0,
                            "refused socket must exit non-zero")

    def test_unhealthy_when_policy_missing(self):
        import tempfile as _tf
        config_dir = _tf.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            config_dir, ignore_errors=True))
        os.makedirs(config_dir, exist_ok=True)
        result = self._run(self._serve(), config_dir)
        self.assertNotEqual(result.returncode, 0,
                            "missing whitelist must exit non-zero")


if __name__ == "__main__":
    unittest.main()
