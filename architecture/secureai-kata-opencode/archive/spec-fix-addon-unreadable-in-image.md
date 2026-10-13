---
title: 'Fix: addon.py unreadable in the proxy image (context SELinux label carried into the layer)'
type: 'bugfix'
created: '2026-10-12'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On an SELinux host using the containerd image store, the proxy died at startup with `mitmdump: No such script: /opt/secureai/addon.py` — while every other image file read fine. The addon is the one file baked in from the *build context* (`COPY proxy/addon.py`), and the containerd image store preserves xattrs, so `/opt/secureai`'s default SELinux label rides into the image layer; a `container_t` process cannot even `stat` that file, and `os.path.isfile()` returns `False` on `EACCES`, which mitmproxy reports as "No such script". DAC and permissions were provably correct — `0644` in the image, and the proxy reads everything else.

**Approach:** Write the addon inside a `RUN` layer (`COPY` to a temp path, `install -m 0644` into place, remove the temp) so the file is created by the image build and carries the image's own context label instead of the build context's. Label `/opt/secureai` as `container_file_t` in the installer so the whole payload tree — the build context for both images — is container-readable from the start, and fix the label assertion to compare the context's type field rather than the whole context string, which made it warn on every correctly labelled host.

</frozen-after-approval>

## Implementation Notes

- Red/green: three new tests (`test_proxy_addon_is_created_in_a_layer_not_copied_into_the_image`, `test_installer_labels_the_app_tree_container_file_t`, `test_label_assertion_compares_the_type_not_the_whole_context`) watched fail, then implemented. Suite 83/83, `bash -n install-fedora.sh` clean.
- `proxy/Dockerfile`: `COPY proxy/addon.py /tmp/secureai-addon.py` + `RUN install -d -m 0755 /opt/secureai && install -m 0644 … && rm -f …`.
- `install-fedora.sh` `label_paths`: `$SECUREAI_APP_DIR` added to both the `semanage`/`restorecon` loop and the assertion loop; assertion now `awk '{print $1}' | cut -d: -f3` (field 1 is the full context, field 3 is the type). This also resolves the spurious SELinux warning deferred from `spec-fix-proxy-vanishes-on-startup`.
- Verified locally: rebuilt image has `opt/secureai/addon.py` at `0644 root:root` with **no** xattrs (temp file whiteout-deleted), mitmdump stays up, healthcheck CONNECT probe passes. Cannot reproduce the denial locally — this box's docker 26 uses the graphdriver store, whose BuildKit drops context xattrs, which is why the same image worked here and not on the user's moby 29 + containerd store.
- Known unfixed exposure, same class: `opencode/Dockerfile` also `COPY`s payload files from the context (`opencode/opencode.json`, `opencode/models.json`, `ca.pem`) into the guest image. The guest has never run, so there is no evidence either way; recorded in `deferred-work.md` rather than speculatively changed.

## Review Triage Log

- No review layer run: the change is three mechanical edits plus tests, each verified by an executed check (layer tar contents, live proxy start, full suite).