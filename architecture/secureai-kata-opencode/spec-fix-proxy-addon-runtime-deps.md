---
title: 'Fix: proxy image cannot run the addon (missing PyYAML, fictional Addon base class)'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With CA permissions repaired, mitmdump still exited at startup — reproduced live in a local container: (1) `ModuleNotFoundError: No module named 'yaml'` — `proxy/addon.py` imports PyYAML, which the upstream mitmproxy image does not ship; (2) after adding it, `AttributeError: module 'mitmproxy.addons' has no attribute 'Addon'` — the addon inherited from a base class that exists only in the `test_addon.py` stub, not in real mitmproxy 12. The stub masked both defects, so the proxy had never successfully started with the addon on any host.

**Approach:** Install PyYAML in `proxy/Dockerfile` (unpinned — the python:3.14-slim base has no compiler, so the install needs a wheel for the interpreter; a pin without one turns the layer into an impossible source build). Make `SecureAIAddon` a plain class (mitmproxy 12 discovers hooks by name; there is no `Addon` base) and drop the fictional base from the test stub so any future invented mitmproxy API fails the suite.

</frozen-after-approval>

## Implementation Notes

- Red/green: removed `addons.Addon` from the `test_addon.py` stub first → suite red on `import mitmproxy.addons` in `addon.py`; fixed the addon → 80/80 green. Added `test_proxy_image_installs_the_addons_dependencies` (Dockerfile must pip-install pyyaml while the addon imports yaml).
- Live verification (local docker, exact compose invocation): before fix — exit 1 on the two errors above; after fix — mitmdump stayed up past a 12s timeout, and the compose healthcheck's exact CONNECT probe against 127.0.0.1:8080 returned PASS.
- The user's host showed two EARLIER errors this round does not explain: `Permission denied: /config/ca/mitmproxy-ca.pem` despite locally-verified-correct DAC (0640 root:1000, container gid 1000), and `No such script: /opt/secureai/addon.py` despite the file provably existing and loading in the same image. Both are consistent with SELinux denials on that host (`os.path.isfile` returns False on EACCES). Host diagnostics (`ausearch -m avc`, `ls -lZ`, docker SecurityOptions) requested from the user; local docker here has SELinux integration off, so this cannot be reproduced locally.

## Review Triage Log

- No review layer findings (change is two lines plus tests, verified live end-to-end with the exact production invocation).
