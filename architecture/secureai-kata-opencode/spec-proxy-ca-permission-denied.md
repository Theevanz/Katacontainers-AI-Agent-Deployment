---
title: 'Proxy CA Permission Denied on stale GID'
type: 'bugfix'
created: '2026-10-13'
status: 'in-progress'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `sudo secureai opencode` dies at proxy startup with `PermissionError: /config/ca/mitmproxy-ca.pem` on hosts installed before the GID 10001→1000 change, because existing `/etc/secureai/compose.env` still pins the old GID while the proxy image runs as 1000:1000.

**Approach:** Migrate any stale `SECUREAI_PROXY_GID` to the image-canonical 1000 and re-pin ownership/permissions on the compose env and runtime state, so the next CLI or installer run repairs the host instead of failing.

</frozen-after-approval>

## Implementation Notes
- Root cause: commit 8ddf72f changed SECUREAI_PROXY_GID 10001→1000 and dropped the image-side groupmod, but existing /etc/secureai/compose.env was preserved by design (AD-11), so `ca export` kept chowning the 0640 private key to 10001 while the image runs as 1000:1000. Reproduced in-container: 0640 root:10001 → Permission denied; 0640 root:1000 → READ_OK.
- Changed `secureai` ensure_compose_env to migrate any non-1000 GID to 1000 (sed one line, other edits preserved); changed cmd_init_config to re-pin /var/lib/secureai/ca dir and CA files on every run (no regeneration, baked trust stays valid); changed install-fedora.sh install_layout to migrate an existing compose.env the same way (installer still never populates, only migrates the one stale value).
- Verified: bash -n clean on both files; pytest 114 + 54 pass; migration snippet moves 10001→1000 and leaves 1000 alone.
