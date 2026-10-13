---
title: 'Fix workspace cwd mount overwrite'
type: 'bugfix'
created: '2026-10-13'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `secureai opencode` always mounts `/var/lib/secureai/workspace` instead of the launch directory, so host files never appear in `/workspace` and guest files never appear on the host.

**Approach:** Preserve the per-launch `SECUREAI_WORKSPACE=pwd -P` export across `load_compose_env`/`compose()` so the Kata guest bind-mounts the actual cwd.

</frozen-after-approval>

## Implementation Notes
- Fixed `secureai:110-126` `load_compose_env` to save/restore `SECUREAI_WORKSPACE` when it is a real dir, so `compose()` no longer clobbers `cmd_opencode` cwd export with compose.env default. Verified with bash simulation (cwd preserved) and `bash -n`.
- Existing failures unchanged: 8 in `test_installer_cli.py` pre-exist on HEAD (kata static flavour), 1 in `test_addon.py` is qcow2 binary false-positive (repo root 5GB image scanned).
- Review patches: pinned-marker to respect operator re-pin, warn on missing cwd fallback, refuse /etc/secureai|/var/lib/secureai|/opt/secureai|/etc prefixes, refuse SUDO_USER home under sudo, warn on non-1000 cwd owner (never chown user dir).

## Review Triage Log
- operator re-pin overwritten by default preserve: high, confirmed via first-load _saved=default vs file custom — patched with SECUREAI_WORKSPACE_PINNED marker.
- cwd never chowned/prepared, EACCES as node 1000: medium, confirmed cmd_init_config only prepares default — patched with owner warn, no chown.
- missing cwd silently falls back: low, confirmed no message on -d fail — patched with warn.
- sensitive prefixes (/etc/secureai, /var/lib/..., /etc) mount keys into guest: high, confirmed guard only $HOME and / — patched with case refusal.
- sudo slips home guard (~=/root): high, confirmed _home=/root under sudo — patched with SUDO_USER home check.
- spec missing repro/AC/matrix: false, oneshot template deletes those sections by design.
- no regression test: medium but needs new bash harness, not simple correction — deferred to deferred-work.md.

