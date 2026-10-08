"""Unit tests for proxy/addon.py -- the one suite runnable without Kata or root.

Runs the I/O & edge-case matrix of the spec against SecureAIAddon directly.
mitmproxy is stubbed when it is not installed, so the enforcement logic is
testable on any machine; when the real mitmproxy is present, the stub is skipped
and the real `mitmproxy.http.Response.make` is exercised instead.
"""

import importlib.util
import logging
import os
import re
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PATH = os.path.join(REPO_ROOT, "proxy", "addon.py")


# --------------------------------------------------------------------------
# mitmproxy stub (only when the real thing is absent)
# --------------------------------------------------------------------------
class CaseInsensitiveDict(dict):
    def __init__(self, items=None):
        super().__init__()
        for key, value in (items or {}).items() if isinstance(items, dict) else (items or []):
            self[key] = value

    def __setitem__(self, key, value):
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class StubResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = CaseInsensitiveDict(headers or {})
        self.stream = False

    @classmethod
    def make(cls, status_code=200, content=b"", headers=()):
        return cls(status_code, content, headers)


def _install_mitmproxy_stub():
    if importlib.util.find_spec("mitmproxy") is not None:
        return False
    import types

    mitmproxy = types.ModuleType("mitmproxy")
    addons = types.ModuleType("mitmproxy.addons")

    class Addon:
        def load(self, loader):
            return None

    addons.Addon = Addon
    http = types.ModuleType("mitmproxy.http")
    http.Response = StubResponse
    mitmproxy.addons = addons
    mitmproxy.http = http
    sys.modules["mitmproxy"] = mitmproxy
    sys.modules["mitmproxy.addons"] = addons
    sys.modules["mitmproxy.http"] = http
    return True


STUBBED = _install_mitmproxy_stub()
sys.path.insert(0, os.path.dirname(ADDON_PATH))
import addon  # noqa: E402  (import must follow the stub)


# --------------------------------------------------------------------------
# test doubles
# --------------------------------------------------------------------------
class FakeRequest:
    def __init__(self, host="", scheme="https", headers=None):
        self.host = host
        self.scheme = scheme
        self.headers = CaseInsensitiveDict(headers or {})


class FakeFlow:
    def __init__(self, request):
        self.request = request
        self.response = None
        self.metadata = {}


class FakeClientHello:
    def __init__(self, sni):
        self.sni = sni


class FakeContext:
    def __init__(self):
        self.metadata = {}


class FakeClientHelloData:
    def __init__(self, sni):
        self.client_hello = FakeClientHello(sni)
        self.context = FakeContext()


class FakeResponse:
    def __init__(self, content_type=None):
        self.headers = CaseInsensitiveDict()
        if content_type is not None:
            self.headers["content-type"] = content_type
        self.stream = False


PLACEHOLDER = "sk-secureai-placeholder"
API_HOST = "api.example.test"          # not a real host: test data only
OTHER_HOST = "other.example.test"
REAL_KEY = "sk-ant-test-key-material"


def write_config(root, whitelist=None, keys=None, settings=None, whitelist_raw=None):
    """Write the three config files the way the CLI writes them.

    Entries go through yaml.safe_dump rather than string formatting because a
    bare `- *.example.com` is a YAML *scanner error* (`*` starts an alias).
    Every writer of this file -- the CLI and this helper -- must quote it.
    """
    import yaml as _yaml

    os.makedirs(root, exist_ok=True)
    wl = whitelist if whitelist is not None else ["%s" % API_HOST]
    with open(os.path.join(root, "whitelist.yaml"), "w", encoding="utf-8") as fh:
        if whitelist_raw is not None:
            fh.write(whitelist_raw)
        else:
            fh.write(_yaml.safe_dump(list(wl), default_flow_style=False, sort_keys=False))
    if keys is None:
        keys = {API_HOST: REAL_KEY}
    with open(os.path.join(root, "keys.yaml"), "w", encoding="utf-8") as fh:
        fh.write(_yaml.safe_dump(dict(keys), default_flow_style=False, sort_keys=False))
    with open(os.path.join(root, "settings.yaml"), "w", encoding="utf-8") as fh:
        for key, value in (settings or {"cache_ttl_seconds": 5}).items():
            fh.write("%s: %s\n" % (key, value))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class AddonTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_dir = os.path.join(self.tmp.name, "config")
        self.prev = {
            "SECUREAI_CONFIG_DIR": os.environ.get("SECUREAI_CONFIG_DIR"),
            "SECUREAI_PLACEHOLDER_KEY": os.environ.get("SECUREAI_PLACEHOLDER_KEY"),
        }
        os.environ["SECUREAI_CONFIG_DIR"] = self.config_dir
        os.environ["SECUREAI_PLACEHOLDER_KEY"] = PLACEHOLDER
        self.addon = addon.SecureAIAddon()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, value in self.prev.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def request(self, host=API_HOST, scheme="https", headers=None, addon_obj=None):
        if headers is None:
            headers = {"authorization": "Bearer %s" % PLACEHOLDER}
        flow = FakeFlow(FakeRequest(host, scheme, headers))
        (addon_obj or self.addon).requestheaders(flow)
        return flow


class TestWildcardMatching(unittest.TestCase):
    def test_subdomain_matches_but_apex_does_not(self):
        self.assertTrue(addon.host_matches("raw.githubusercontent.com", "*.githubusercontent.com"))
        self.assertFalse(addon.host_matches("githubusercontent.com", "*.githubusercontent.com"))
        self.assertFalse(addon.host_matches("notgithubusercontent.com", "*.githubusercontent.com"))

    def test_exact_and_case_and_trailing_dot(self):
        self.assertTrue(addon.host_matches("Example.TEST", "example.test"))
        self.assertTrue(addon.host_matches("example.test.", "example.test"))
        self.assertFalse(addon.host_matches("example.test", "other.test"))
        self.assertFalse(addon.host_matches("", "example.test"))
        self.assertFalse(addon.host_matches("example.test", ""))

    def test_deeper_subdomains_match(self):
        self.assertTrue(addon.host_matches("a.b.example.test", "*.example.test"))


class TestAllowAndInject(AddonTestCase):
    def test_allowlisted_host_with_key_is_injected(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            flow = self.request()
        self.assertIsNone(flow.response, "allowed request must not be answered by the addon")
        self.assertEqual(
            flow.request.headers["authorization"],
            "Bearer %s" % REAL_KEY,
            "placeholder must be replaced with the real key",
        )

    def test_injection_log_is_exactly_the_contract_line(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            self.request()
        line = "\n".join(logs.output)
        self.assertRegex(
            line,
            r"authz=injected provider=%s fp=[0-9a-f]{4}" % re.escape(API_HOST),
        )

    def test_no_log_line_contains_key_material(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            self.request()
        line = "\n".join(logs.output)
        self.assertNotIn(REAL_KEY, line)
        self.assertNotIn(PLACEHOLDER, line)

    def test_x_api_key_header_is_also_handled(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request(headers={"x-api-key": PLACEHOLDER})
        self.assertEqual(flow.request.headers["x-api-key"], REAL_KEY)

    def test_request_without_credential_passes_through_untouched(self):
        write_config(self.config_dir)
        # An allowlisted request carrying no credential at all is not an
        # injection event and must log nothing -- so assert *no* logs rather
        # than using assertLogs (which fails when nothing is emitted).
        with self.assertNoLogs("secureai", level="WARNING"):
            flow = self.request(headers={"user-agent": "git/2.0"})
        self.assertIsNone(flow.response)

    def test_fingerprint_is_stable_and_key_specific(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as first:
            self.request()
        with self.assertLogs("secureai", level="WARNING") as second:
            self.request(addon_obj=addon.SecureAIAddon())
        pattern = r"fp=([0-9a-f]{4})"
        fp1 = re.search(pattern, "\n".join(first.output)).group(1)
        fp2 = re.search(pattern, "\n".join(second.output)).group(1)
        self.assertEqual(fp1, fp2, "same key must fingerprint identically across runs")
        self.assertEqual(fp1, addon.fingerprint(REAL_KEY)[:4])
        self.assertNotEqual(fp1, addon.fingerprint("another-key")[:4])


class TestFailClosed(AddonTestCase):
    def assertBlocked(self, flow, decision):
        self.assertIsNotNone(flow.response, "expected a 403")
        self.assertEqual(flow.response.status_code, 403)
        self.assertEqual(flow.response.content, b"blocked by secureai")
        self.assertEqual(flow.metadata["secureai"]["decision"], decision)

    def test_allowlisted_host_without_key_is_denied_not_passthrough(self):
        write_config(self.config_dir, keys={})
        with self.assertLogs("secureai", level="WARNING") as logs:
            flow = self.request()
        self.assertBlocked(flow, "nokey")
        self.assertIn("decision=nokey", "\n".join(logs.output))

    def test_non_allowlisted_host_is_denied(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            flow = self.request(host=OTHER_HOST)
        self.assertBlocked(flow, "blocked")
        line = "\n".join(logs.output)
        self.assertIn("host=%s" % OTHER_HOST, line)
        self.assertIn("scheme=https", line)
        self.assertIn("decision=blocked", line)

    def test_plaintext_http_is_denied_by_the_scheme_gate(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            flow = self.request(scheme="http")
        self.assertBlocked(flow, "blocked")
        self.assertIn("scheme=http", "\n".join(logs.output))
        self.assertIn("plaintext http denied by scheme gate", "\n".join(logs.output))

    def test_plaintext_http_can_be_enabled_by_config(self):
        write_config(self.config_dir, settings={"cache_ttl_seconds": 5, "allow_plaintext_http": True})
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request(scheme="http")
        self.assertIsNone(flow.response)

    def test_unknown_scheme_is_denied(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request(scheme="gopher")
        self.assertBlocked(flow, "blocked")

    def test_missing_host_or_scheme_is_denied(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request(host="", scheme="")
        self.assertBlocked(flow, "undeterminable")

    def test_unparseable_whitelist_is_denied_as_config_error(self):
        write_config(self.config_dir, whitelist_raw="- %s\n  bad: [unclosed\n" % API_HOST)
        with self.assertLogs("secureai", level="WARNING") as logs:
            flow = self.request()
        self.assertBlocked(flow, "config-error")
        self.assertIn("decision=config-error", "\n".join(logs.output))

    def test_config_error_and_blocked_are_distinguishable(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING") as logs:
            self.request(host=OTHER_HOST)
        self.assertIn("decision=blocked", "\n".join(logs.output))
        self.assertNotIn("config-error", "\n".join(logs.output))

    def test_whitelist_of_wrong_shape_is_denied(self):
        write_config(self.config_dir, whitelist_raw="hosts: not-a-list\n")
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request()
        self.assertBlocked(flow, "config-error")

    def test_empty_whitelist_is_denied(self):
        write_config(self.config_dir, whitelist_raw="# nothing here\n")
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request()
        self.assertBlocked(flow, "config-error")

    def test_missing_config_files_are_denied(self):
        os.makedirs(self.config_dir, exist_ok=True)
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request()
        self.assertBlocked(flow, "config-error")

    def test_unreadable_config_is_denied(self):
        write_config(self.config_dir)
        os.chmod(os.path.join(self.config_dir, "whitelist.yaml"), 0o000)
        self.addCleanup(os.chmod, os.path.join(self.config_dir, "whitelist.yaml"), 0o644)
        if os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request()
        self.assertBlocked(flow, "config-error")

    def test_absent_placeholder_constant_fails_closed(self):
        write_config(self.config_dir)
        os.environ.pop("SECUREAI_PLACEHOLDER_KEY")
        with self.assertLogs("secureai", level="WARNING"):
            flow = self.request()
        self.assertBlocked(flow, "config-error")


class TestWildcardEnforcement(AddonTestCase):
    def test_wildcard_entry_allows_subdomain_but_not_apex(self):
        write_config(self.config_dir, whitelist=["*.cdn.test"], keys={"raw.cdn.test": REAL_KEY})
        with self.assertLogs("secureai", level="WARNING"):
            allowed = self.request(host="raw.cdn.test")
        self.assertIsNone(allowed.response)
        with self.assertLogs("secureai", level="WARNING"):
            denied = self.request(host="cdn.test")
        self.assertIsNotNone(denied.response)


class TestLiveConfigReread(AddonTestCase):
    def test_change_is_picked_up_when_mtime_moves(self):
        write_config(self.config_dir)
        with self.assertLogs("secureai", level="WARNING"):
            self.assertIsNotNone(self.request(host=OTHER_HOST).response)
        write_config(self.config_dir, whitelist=[API_HOST, OTHER_HOST], keys={})
        with self.assertNoLogs("secureai", level="WARNING"):
            flow = self.request(host=OTHER_HOST, headers={"user-agent": "x"})
        self.assertIsNone(flow.response, "whitelist add must take effect without a restart")

    def test_cache_is_bounded_by_ttl_even_when_mtime_is_coarse(self):
        """A same-size edit with a restored mtime must not fool the cache."""
        write_config(self.config_dir, settings={"cache_ttl_seconds": 5})
        with self.assertLogs("secureai", level="WARNING"):
            self.assertIsNotNone(self.request(host=OTHER_HOST).response)
        path = os.path.join(self.config_dir, "settings.yaml")
        stat = os.stat(path)
        # 5 -> 7 is byte-for-byte the same length, so only the TTL changes.
        write_config(self.config_dir, settings={"cache_ttl_seconds": 7})
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(os.stat(path).st_size, stat.st_size)
        with self.assertLogs("secureai", level="WARNING"):
            cached = self.request(host=OTHER_HOST, headers={"user-agent": "x"})
        self.assertIsNotNone(cached.response, "unchanged signature must not invalidate the cache")
        # Simulate the TTL elapsing; the re-read must pick up the new TTL,
        # which is what bounds the cache when mtime is too coarse to help.
        _sig, _at, _ttl, _policy, _error = self.addon._cache
        self.addon._cache = (_sig, time.monotonic() - 10_000, _ttl, _policy, _error)
        with self.assertLogs("secureai", level="WARNING"):
            self.request(host=OTHER_HOST, headers={"user-agent": "x"})
        _sig2, _at2, ttl2, _policy2, _error2 = self.addon._cache
        self.assertEqual(ttl2, 7.0, "TTL must bound the cache independently of mtime")

    def test_ttl_is_bounded(self):
        write_config(self.config_dir, settings={"cache_ttl_seconds": 10**9})
        with self.assertLogs("secureai", level="WARNING"):
            self.request()
        _sig, _at, ttl, _policy, _error = self.addon._cache
        self.assertLessEqual(ttl, addon.MAX_CACHE_TTL_SECONDS)


class TestSNIHook(AddonTestCase):
    def test_sni_is_recorded_and_no_action_is_taken(self):
        data = FakeClientHelloData(API_HOST)
        self.assertIsNone(self.addon.tls_clienthello(data))
        self.assertEqual(data.context.metadata["secureai_sni"], API_HOST)
        self.assertFalse(hasattr(data.context, "flow"))
        self.assertEqual(len(data.context.metadata), 1)


class TestResponseHeaders(AddonTestCase):
    def test_event_stream_is_streamed(self):
        flow = FakeFlow(FakeRequest(API_HOST))
        flow.response = FakeResponse("text/event-stream")
        self.addon.responseheaders(flow)
        self.assertTrue(flow.response.stream)

    def test_event_stream_with_charset_is_streamed(self):
        flow = FakeFlow(FakeRequest(API_HOST))
        flow.response = FakeResponse("text/event-stream; charset=utf-8")
        self.addon.responseheaders(flow)
        self.assertTrue(flow.response.stream)

    def test_json_response_is_not_streamed(self):
        flow = FakeFlow(FakeRequest(API_HOST))
        flow.response = FakeResponse("application/json")
        self.addon.responseheaders(flow)
        self.assertFalse(flow.response.stream)

    def test_absent_response_is_tolerated(self):
        flow = FakeFlow(FakeRequest(API_HOST))
        self.assertIsNone(self.addon.responseheaders(flow))


class TestSourceContract(unittest.TestCase):
    """Static checks mirroring the spec's acceptance criteria."""

    @classmethod
    def setUpClass(cls):
        with open(ADDON_PATH, "r", encoding="utf-8") as fh:
            cls.source = fh.read()

    def test_no_context_or_connection_kill(self):
        """No ``kill`` *call* anywhere -- AST, not text.

        A raw substring check would also reject the docstrings that explain
        *why* those APIs are avoided, so this inspects real call nodes: the
        rule is about what the code calls, not about what it names.
        """
        import ast

        calls = set()
        for node in ast.walk(ast.parse(self.source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.add(func.attr)
            elif isinstance(func, ast.Name):
                calls.add(func.id)
        self.assertNotIn("kill", calls, "no kill() call may exist (AD-2)")

    def test_no_host_literal_in_policy_code(self):
        for token in ("anthropic", "githubusercontent", "github", "npmjs",
                      "pythonhosted", "pypi", "models.dev", "opencode.ai", "astral"):
            self.assertNotIn(token, self.source, "%s must live only in config" % token)

    def test_no_placeholder_literal(self):
        self.assertNotIn("sk-secureai-placeholder", self.source)

    def test_all_three_hooks_live_in_one_class(self):
        import ast

        tree = ast.parse(self.source)
        hooks = {"tls_clienthello", "requestheaders", "responseheaders"}
        carriers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {
                    child.name for child in node.body if isinstance(child, ast.FunctionDef)
                }
                if hooks & methods:
                    self.assertEqual(node.name, "SecureAIAddon")
                    self.assertTrue(hooks <= methods, "all three hooks in SecureAIAddon")
                    carriers.add(node.name)
        self.assertEqual(carriers, {"SecureAIAddon"})

    def test_enforcement_acts_at_requestheaders_only(self):
        self.assertIn("def requestheaders", self.source)
        self.assertIn("self._deny(flow", self.source)
        # tls_clienthello must not deny or set a response
        hook = self.source.split("def tls_clienthello", 1)[1].split("def ", 1)[0]
        self.assertNotIn("_deny", hook)
        self.assertNotIn("Response.make", hook)


class TestTreeContract(unittest.TestCase):
    """Static checks over the SHIPPED artifacts only.

    Planning docs (README, the spine, epics, the memlog) legitimately *discuss*
    strings like the Docker embedded resolver, because explaining the kata#6688
    workaround requires naming it. The contract is about runtime artifacts, so
    these checks walk the shipped tree and nothing else -- otherwise satisfying
    them would mean deleting the design rationale.
    """

    SHIPPED = ("install-fedora.sh", "secureai", "docker-compose.yml", "opencode",
               "proxy", "config")

    def _shipped_files(self):
        out = []
        for entry in self.SHIPPED:
            path = os.path.join(REPO_ROOT, entry)
            if os.path.isfile(path):
                out.append(path)
            elif os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    child = os.path.join(path, name)
                    if os.path.isfile(child):
                        out.append(child)
        return out

    def test_no_docker_embedded_resolver_anywhere(self):
        # Assembled from parts so this test file does not itself contain the
        # literal the shipped-tree grep looks for.
        needle = "127.0.0." + "11"
        offenders = [
            os.path.relpath(p, REPO_ROOT)
            for p in self._shipped_files()
            if needle in _read(p)
        ]
        self.assertEqual(offenders, [])

    def test_cross_boundary_constants_appear_only_as_references(self):
        """The five SECUREAI_* values may exist in exactly ONE shipped file.

        AD-11: compose.env is the single source of the cross-boundary constants,
        and config/compose.env is the seed the CLI copies from it. Every other
        shipped artifact must reference them as ${...} and never repeat a value.
        """
        seed = os.path.join("config", "compose.env")
        offenders = [
            os.path.relpath(p, REPO_ROOT)
            for p in self._shipped_files()
            if "10.211.0." in _read(p) and os.path.relpath(p, REPO_ROOT) != seed
        ]
        self.assertEqual(offenders, [], "only config/compose.env may hold the values")
        # And the seed must actually hold all five names.
        seed_text = _read(os.path.join(REPO_ROOT, seed))
        for name in ("SECUREAI_SUBNET", "SECUREAI_GATEWAY", "SECUREAI_PROXY_IP",
                     "SECUREAI_PROXY_PORT", "SECUREAI_PLACEHOLDER_KEY"):
            self.assertIn(name, seed_text)

    def test_no_docker_resolver_or_runtime_flag_in_compose(self):
        compose = _read(os.path.join(REPO_ROOT, "docker-compose.yml"))
        self.assertNotIn("127.0.0." + "11", compose)
        self.assertIn("runtime: kata", compose)
        self.assertIn("runtime: runc", compose)
        self.assertNotIn("default-runtime", compose)
        self.assertNotIn("context=", compose)

    def test_cli_passes_no_runtime_flag(self):
        """No executable line may pass a runtime-override flag.

        Comments may name the flag -- in fact the CLI ships a guard that
        explains why it must never be passed -- so only code lines are checked.
        """
        flag = "--" + "runtime"
        offenders = []
        for n, line in enumerate(_read(os.path.join(REPO_ROOT, "secureai")).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if flag in line:
                offenders.append(n)
        self.assertEqual(offenders, [], "CLI must pass no runtime-override flag")

    def test_installer_is_the_only_writer_of_daemon_json(self):
        """No shipped artifact may WRITE daemon.json (AD-6).

        Reading it is legitimate -- `secureai status` asserts the version
        pairing from it -- so this looks for write constructs, not for the
        word.
        """
        root_files = [f for f in os.listdir(REPO_ROOT) if os.path.isfile(os.path.join(REPO_ROOT, f))]
        self.assertIn("install-fedora.sh", root_files)
        writers = (">", ">>", "tee ", "install ", "mv ", "cp ", "sed -i")
        offenders = []
        for name in root_files:
            if name == "install-fedora.sh":
                continue
            path = os.path.join(REPO_ROOT, name)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if "daemon.json" not in line:
                        continue
                    if line.strip().startswith("#"):
                        continue
                    # Discard noise redirections first: `2>/dev/null` is not a
                    # write to daemon.json, but it does contain ">".
                    probe = re.sub(r"\d?>>?\s*/dev/null", "", line)
                    probe = re.sub(r"\d?>&\d?", "", probe)
                    if any(tok in probe for tok in writers):
                        offenders.append("%s:%d" % (name, n))
        self.assertEqual(offenders, [], "only install-fedora.sh may write daemon.json")


if __name__ == "__main__":
    unittest.main()
