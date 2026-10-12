"""SecureAI enforcement addon -- the ONLY whitelist decision point (AD-1).

Shape rules this file obeys, and why:

* All three hooks live in one class (AD-2).  Per-flow state goes to
  ``flow.metadata``; the one instance attribute (``_cache``) is process-global
  config cache and never carries request state.
* ``tls_clienthello`` receives a ``Context``, not a ``Flow``.  ``Flow.kill()``
  is therefore NOT callable there, and ``Context.kill()`` / ``Connection.kill()``
  do not exist in current mitmproxy at all.  So this hook only records SNI for
  logging and takes no action (AD-2).  Enforcement happens at
  ``requestheaders`` with ``http.Response.make(403)``.
* ``responseheaders`` sets ``.stream = True`` for ``text/event-stream`` before
  any body is read, or mitmproxy buffers an entire LLM response and the stream
  never emits a token (AD-3).
* The addon fails CLOSED for policy.  Unreadable / unparseable whitelist, an
  unknown host, or a placeholder-presented-without-key all produce 403.  There
  is no default-allow (AD-13, AD-15).  Missing keys.yaml or an empty keys file
  simply means zero keys: unauthenticated baseline hosts (git, pip, npm)
  still pass through, only authenticated hosts 403 with `nokey`.
* There is no host literal anywhere in this file.  The allowlist file is the
  only policy source (AD-15).
* Log lines never contain key material and never contain header values.  The
  injection line carries a 4-char sha256 fingerprint only (AD-10).

mitmproxy is started with ``-q`` (termlog_verbosity=error), so the lines the
``secureai verify`` gates grep for are emitted at WARNING level on purpose:
INFO from an addon is suppressed by ``-q`` and the gates would never see it.
"""

import hashlib
import logging
import os
import time

import yaml

import mitmproxy.http

logger = logging.getLogger("secureai")
# Emit WARNING+ to stderr explicitly. mitmproxy's own terminal log only shows
# records routed through its event queue -- a bare stdlib logger depends on
# root-handler fallback that is NOT guaranteed inside the mitmdump process,
# which is how enforcement could act (403) while leaving docker logs and
# /var/lib/secureai/proxy.log EMPTY (and `secureai verify` blind). An
# explicit handler makes the decision log unconditional.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.WARNING)
    _handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.WARNING)

# --- names, not values ---------------------------------------------------
# Config file names are referenced by name only; their directory comes from
# the environment so the same code runs in the proxy container and in tests.
WHITELIST_BASENAME = "whitelist.yaml"
KEYS_BASENAME = "keys.yaml"
SETTINGS_BASENAME = "settings.yaml"

DEFAULT_CONFIG_DIR = "/etc/secureai"
PLACEHOLDER_ENV = "SECUREAI_PLACEHOLDER_KEY"

# Config is re-read per flow, bounded by mtime and by this TTL, so
# `secureai whitelist add` takes effect in a running session without any
# signal to the proxy (AD-13, F7).  The proxy never accepts instructions.
DEFAULT_CACHE_TTL_SECONDS = 5.0
MAX_CACHE_TTL_SECONDS = 300.0

# The one error shape in the whole system.
BLOCK_STATUS = 403
BLOCK_BODY = b"blocked by secureai"

# Header names that may carry the placeholder.  Order is irrelevant because at
# most one of them matches a given client.
AUTH_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key")
SECURE_SCHEMES = ("http", "https")


class ConfigError(Exception):
    """Raised internally when config cannot be read or parsed.  Never escapes."""


def config_dir():
    return os.environ.get("SECUREAI_CONFIG_DIR", DEFAULT_CONFIG_DIR)


def placeholder():
    # Read at call time, never cached at import: the placeholder is a constant
    # owned by /etc/secureai/compose.env and must not be duplicated as a
    # literal in code (AD-11).
    return os.environ.get(PLACEHOLDER_ENV, "") or ""


def host_matches(host, entry):
    """Allowlist matching.  `*.example.test` matches subdomains, not the apex.

    Matched on the suffix *after* the leading `*.`, so `raw.example.test`
    matches `*.example.test` while bare `example.test` does not, and
    `notexample.test` does not either.  Real baseline hosts are deliberately
    absent from this file (AD-15), so the examples here are not real hosts.
    """
    if not host or not entry:
        return False
    host = host.strip().lower().rstrip(".")
    entry = entry.strip().lower().rstrip(".")
    if not host or not entry:
        return False
    if entry.startswith("*."):
        suffix = entry[1:]  # ".example.com"
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == entry


def fingerprint(key):
    """4-char sha256 prefix.  A fingerprint, never key material (AD-10)."""
    if not key:
        return "none"
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:4]


def parse_bool(value, default=False):
    """Strictly parse a YAML scalar as a boolean.

    ``bool()`` is wrong here and dangerously so: in Python every non-empty
    string is truthy, so a settings file reading ``allow_plaintext_http: "false"``
    -- quoted, which is a very natural thing to write -- would evaluate TRUE and
    open plaintext egress for every host while the file appears to say the
    opposite. YAML's own 1.1 vocabulary is accepted so the file can use the
    spellings a YAML author expects.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("true", "yes", "on", "1"):
            return True
        if token in ("false", "no", "off", "0", ""):
            return False
    raise ValueError("not a boolean: %r" % (value,))


# NO base class: mitmproxy 12 addons are plain objects -- hooks are discovered
# by name, and `mitmproxy.addons.Addon` does not exist (it was a fiction of the
# old test stub; inheriting from it made mitmdump exit at startup).
class SecureAIAddon:
    """The single enforcement point: allowlist, key injection, SSE streaming."""

    # Process-global config cache: (signature, loaded_at, ttl, policy, error).
    # Process-global by nature, and deliberately free of request state.
    _cache = None

    def __init__(self):
        # Readiness for health gating (Story 2.6): False until the first
        # successful policy load. Request handling itself stays fail-closed
        # via _policy() returning None, so this flag is advisory for health
        # and never a second policy source.
        self._ready = False

    # ------------------------------------------------------------------
    # startup eager load (Story 2.6)
    # ------------------------------------------------------------------
    def _eager_load(self):
        """Load policy once at startup so health cannot pass before keys.

        This does NOT replace the per-flow mtime-bounded reread in _policy():
        startup load closes the healthy-before-keys race, while reread keeps
        `whitelist add` live without a restart (AD-13).
        """
        try:
            policy, error = self._policy()
        except Exception as exc:  # never let startup crash on config
            self._ready = False
            logger.warning(
                "policy not ready reason=%s",
                ("%s" % exc).replace("\n", " ")[:160] or "startup-error",
            )
            return None, "startup-error"
        self._ready = policy is not None
        if policy is None:
            logger.warning(
                "policy not ready reason=%s",
                (error or "-").replace("\n", " ")[:160],
            )
        else:
            logger.warning("policy loaded hosts=%d", len(policy["hosts"]))
        return policy, error

    def configure(self, updated=None):
        """mitmproxy startup hook: eager-load before the proxy listens.

        Because mitmproxy loads addons before listening, a healthcheck that
        requires listening cannot pass before this has attempted a load.
        Until a load succeeds every request fails closed via _policy().
        """
        self._eager_load()

    def load(self, loader=None):
        """Older mitmproxy startup entry: same eager load as configure."""
        self._eager_load()

    def start(self):
        """Late startup entry (some runners call start, not configure)."""
        self._eager_load()

    def is_healthy(self):
        """True only when policy loads in-process (Story 2.6).

        Health must never report healthy before keys are in-process: a first
        request must not race key loading. Missing keys.yaml is valid
        (zero-keys mode, unauthenticated hosts still pass); an unreadable or
        unparseable whitelist is config-error and reports unhealthy.
        Records the outcome in _ready so the flag and the verdict cannot
        diverge for a future caller.
        """
        policy, _ = self._policy()
        self._ready = policy is not None
        return self._ready

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def _path(self, basename):
        return os.path.join(config_dir(), basename)

    def _signature(self):
        """Cheap change detector: one stat per config file, per flow."""
        sig = []
        for basename in (WHITELIST_BASENAME, KEYS_BASENAME, SETTINGS_BASENAME):
            path = self._path(basename)
            try:
                st = os.stat(path)
                sig.append((basename, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((basename, None, None))
        return tuple(sig)

    def _read_yaml(self, basename, default):
        path = self._path(basename)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except FileNotFoundError:
            raise ConfigError("%s is missing" % basename)
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError("%s is unreadable: %s" % (basename, exc.__class__.__name__))
        except yaml.YAMLError as exc:
            raise ConfigError("%s is unparseable: %s" % (basename, str(exc).splitlines()[0]))
        if data is None:
            return default
        return data

    def _load_policy(self, signature):
        # settings.yaml is tunables, not policy: a missing file means defaults
        # (TTL 5s, plaintext denied), never a full block. Only an unreadable
        # or mis-shaped file fails closed.
        settings_path = self._path(SETTINGS_BASENAME)
        if not os.path.exists(settings_path):
            settings = {}
        else:
            settings = self._read_yaml(SETTINGS_BASENAME, {}) or {}
        if not isinstance(settings, dict):
            raise ConfigError("%s is not a mapping" % SETTINGS_BASENAME)

        ttl = settings.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)
        try:
            ttl = float(ttl)
        except (TypeError, ValueError):
            raise ConfigError("cache_ttl_seconds is not a number")
        ttl = max(0.0, min(ttl, MAX_CACHE_TTL_SECONDS))

        # `allow_plaintext_http: false` means scheme http is denied for every
        # host, allowlisted or not: the transport scheme is gated as well as
        # the host (AR3, spine Open Questions).
        try:
            allow_plaintext = parse_bool(
                settings.get("allow_plaintext_http", False), default=False
            )
        except ValueError as exc:
            # An unreadable gate is a closed gate, never an open one.
            raise ConfigError("allow_plaintext_http is not a boolean: %s" % exc)

        wl = self._read_yaml(WHITELIST_BASENAME, None)
        if wl is None:
            raise ConfigError("%s is empty" % WHITELIST_BASENAME)
        if isinstance(wl, dict):
            wl = wl.get("hosts")
        if not isinstance(wl, list):
            raise ConfigError("%s is not a list of hosts" % WHITELIST_BASENAME)
        hosts = []
        for item in wl:
            if not isinstance(item, str):
                raise ConfigError("%s holds a non-string entry" % WHITELIST_BASENAME)
            entry = item.strip()
            if entry and not entry.startswith("#"):
                hosts.append(entry.lower().rstrip("."))
        if not hosts:
            raise ConfigError("%s lists no host" % WHITELIST_BASENAME)

        # keys.yaml is optional: missing or empty means zero keys (no-keys
        # mode). Unauthenticated baseline hosts still pass; only a request
        # presenting the placeholder without a stored key 403s with `nokey`.
        # An unreadable or mis-shaped keys file still fails closed.
        keys_path = self._path(KEYS_BASENAME)
        if not os.path.exists(keys_path):
            keys_raw = {}
        else:
            keys_raw = self._read_yaml(KEYS_BASENAME, {}) or {}
        if not isinstance(keys_raw, dict):
            raise ConfigError("%s is not a mapping" % KEYS_BASENAME)
        keys = {}
        for host, value in keys_raw.items():
            if not isinstance(host, str):
                raise ConfigError("%s holds a non-string host" % KEYS_BASENAME)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ConfigError("%s holds a non-string value" % KEYS_BASENAME)
            value = value.strip()
            if value:
                keys[host.strip().lower().rstrip(".")] = value

        return {
            "hosts": tuple(hosts),
            "keys": keys,
            "allow_plaintext_http": allow_plaintext,
            "cache_ttl_seconds": ttl,
        }, ttl

    def _policy(self):
        """Return (policy, error).  policy is None on any config failure."""
        now = time.monotonic()
        signature = self._signature()
        cached = self._cache
        if cached is not None:
            _, loaded_at, ttl, policy, error = cached
            if cached[0] == signature and (now - loaded_at) < ttl:
                return policy, error
        try:
            policy, ttl = self._load_policy(signature)
            error = None
        except ConfigError as exc:
            policy, ttl, error = None, DEFAULT_CACHE_TTL_SECONDS, str(exc)
        self._cache = (signature, now, ttl, policy, error)
        return policy, error

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------
    def tls_clienthello(self, data):
        """Observe SNI only.  NO ACTION (AD-2).

        A blocked host has therefore already completed the TLS handshake --
        inherent to enforcing at the HTTP layer.  `Flow.kill()` is not callable
        here because this hook receives a Context.
        """
        sni = None
        try:
            sni = data.client_hello.sni
        except AttributeError:
            pass
        try:
            data.context.metadata["secureai_sni"] = sni
        except AttributeError:
            pass
        return None

    def requestheaders(self, flow):
        """The only place policy acts (AD-1, AD-2)."""
        request = flow.request
        host = (getattr(request, "host", "") or "").strip().lower()
        scheme = (getattr(request, "scheme", "") or "").strip().lower()

        # Log the observed SNI at the point of decision, not only at capture in
        # tls_clienthello. SNI is evidence about what the client actually
        # negotiated; recording it and never reading it makes the hook dead
        # weight, and a blocked flow then cannot be diagnosed after the fact.
        sni = self._metadata_sni(flow)

        policy, error = self._policy()
        if policy is None:
            self._deny(flow, host, scheme, "config-error", error)
            return

        if not host or not scheme:
            self._deny(flow, host, scheme, "undeterminable", "host or scheme absent")
            return

        # The CONNECT target, when present, must ALSO be allowlisted. Without
        # this, a client could CONNECT to an off-list host and present an
        # allowlisted Host header, and the request would be forwarded to the
        # CONNECT target while the policy check only ever saw the header.
        # Recorded in tls_clienthello, so it is the value the TLS handshake
        # actually negotiated rather than anything the client asserted later.
        if sni and sni != host and not any(
            host_matches(sni, entry) for entry in policy["hosts"]
        ):
            self._deny(flow, host, scheme, "blocked",
                       "CONNECT target not on allowlist", sni)
            return

        if not any(host_matches(host, entry) for entry in policy["hosts"]):
            self._deny(flow, host, scheme, "blocked", "host not on allowlist")
            return

        if scheme not in SECURE_SCHEMES:
            self._deny(flow, host, scheme, "blocked", "scheme not gated")
            return

        if scheme == "http" and not policy["allow_plaintext_http"]:
            self._deny(flow, host, scheme, "blocked", "plaintext http denied by scheme gate")
            return

        self._inject(flow, host, scheme, policy)

    def responseheaders(self, flow):
        """SSE must stream, or no token is ever emitted (AD-3)."""
        response = flow.response
        if response is None:
            return
        headers = getattr(response, "headers", None) or {}
        content_type = (headers.get("content-type", "") or "").strip().lower()
        if content_type.startswith("text/event-stream"):
            response.stream = True
            return
        # AD-3 makes streaming load-bearing: if a provider streams tokens under
        # some other content type, mitmproxy buffers the whole body, opencode's
        # timeout aborts it, and the core demo fails as a SILENT HANG rather
        # than as a proxy error. So a streamed-looking response that did not
        # match is surfaced at WARNING -- which both the operator's log and
        # `secureai verify` read -- instead of being swallowed.
        if any(token in content_type for token in ("stream", "ndjson", "json-seq")):
            logger.warning(
                "stream=unrecognised content-type=%s (not text/event-stream; "
                "body may buffer until opencode's timeout)",
                content_type[:120],
            )

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    @staticmethod
    def _metadata_sni(flow):
        """The SNI recorded in tls_clienthello, or ''.

        Read defensively: `flow.metadata` is a mitmproxy object with a dict-like
        interface, and a stub or a future version may not support `.get`.
        """
        try:
            metadata = getattr(flow, "metadata", None)
            value = metadata.get("secureai_sni", "") if metadata is not None else ""
        except (AttributeError, TypeError):
            return ""
        return (value or "").strip().lower()

    def _deny(self, flow, host, scheme, decision, reason, sni=""):
        """403 with the one error body.  Logged as exactly one structured line."""
        flow.response = mitmproxy.http.Response.make(BLOCK_STATUS, BLOCK_BODY)
        try:
            flow.metadata["secureai"] = {
                "decision": decision,
                "host": host,
                "scheme": scheme,
                "sni": sni,
            }
        except AttributeError:
            pass
        # WARNING, not INFO: the proxy runs with -q, and `secureai verify`
        # greps this stream.  `decision` separates misconfiguration from a
        # policy decision so an operator can tell them apart.  Header VALUES are
        # never logged -- only host, scheme, and the observed SNI.
        logger.warning(
            "whitelist host=%s scheme=%s sni=%s decision=%s reason=%s",
            host or "-",
            scheme or "-",
            sni or "-",
            decision,
            (reason or "-").replace("\n", " ")[:160],
        )

    def _inject(self, flow, host, scheme, policy):
        """Swap the placeholder for the real key on the matching host (AD-10).

        Rewrites REQUEST headers only, so it stays compatible with AD-3
        streaming.  Every auth header carrying the placeholder is rewritten,
        not just the first, so no placeholder can reach the upstream host
        alongside a real key.

        On credentials: a request presenting the placeholder with no key stored
        for its host is DENIED, never forwarded unauthenticated (AD-13). A
        request presenting NO credential at all -- a `git clone`, an npm or pip
        download -- is passed through untouched, because those hosts genuinely
        need no key and denying them would make the baseline allowlist
        unusable. That is a deliberate distinction, not a hole: the placeholder
        is what signals "this host's key is held by the proxy", and a request
        without it is not asking for a key. This is what lets the stack run
        with zero keys configured: unauthenticated hosts work out of the box,
        and only authenticated hosts 403 with `nokey` until a key is stored.
        """
        # NB: not named `placeholder` -- a local of that name would shadow the
        # module-level placeholder() for the whole function body.
        headers = flow.request.headers
        # No-keys mode: a request with no auth headers at all is not asking
        # for a key (git clone, npm/pip download). Pass through untouched,
        # even if the placeholder constant is absent -- this keeps the
        # baseline working with zero keys configured.
        has_auth = False
        for name in AUTH_HEADERS:
            try:
                if name in headers:
                    has_auth = True
                    break
            except (TypeError, KeyError):
                continue
        if not has_auth:
            return
        placeholder_value = placeholder()
        if not placeholder_value:
            self._deny(flow, host, scheme, "config-error", "placeholder constant absent")
            return
        for name in AUTH_HEADERS:
            try:
                if name not in headers:
                    continue
                value = headers[name] or ""
            except (TypeError, KeyError):
                continue
            if placeholder_value not in value:
                continue
            # A key may be stored for the exact host OR for a wildcard pattern
            # covering it (e.g. '*.cdn.test' for 'raw.cdn.test'). Exact match
            # first, then pattern -- so a wildcard entry is not silently ignored.
            key = policy["keys"].get(host, "")
            if not key:
                for pattern, candidate in policy["keys"].items():
                    if pattern.startswith("*.") and host_matches(host, pattern):
                        key = candidate
                        break
            if not key:
                self._deny(flow, host, scheme, "nokey", "placeholder presented, no key stored")
                return
            headers[name] = value.replace(placeholder_value, key)
            logger.warning(
                "authz=injected provider=%s fp=%s", host, fingerprint(key)
            )
            try:
                flow.metadata["secureai"] = {
                    "decision": "injected",
                    "host": host,
                    "scheme": scheme,
                    "fp": fingerprint(key),
                }
            except AttributeError:
                pass
            # Do NOT return here: a request may carry the placeholder in more
            # than one auth header, and leaving a second one unreplaced would
            # forward the placeholder upstream alongside the real key.
            continue


# mitmproxy only runs hooks from objects in this module-level list. A bare
# class definition is loaded but enforces NOTHING -- every flow passes
# through uninspected and unbelogged, which is a silent fail-open of the
# entire trust boundary (AD-1). This line is what makes the proxy enforce.
addons = [SecureAIAddon()]
