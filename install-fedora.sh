#!/usr/bin/env bash
# install-fedora.sh -- one-shot, machine-wide SecureAI bootstrap (Story 1.1).
#
# Invoked as:  curl -fsSL <url>/install-fedora.sh | sudo bash
#
# There is no `git clone` prerequisite (AD-16), so this script either carries
# the application tree inline (SECUREAI_PAYLOAD_DIR set) or fetches it file by
# file over HTTPS from the same host that served this script.
#
# Three hard rules this file exists to uphold:
#   AD-6  This is the ONLY writer of /etc/docker/daemon.json.  It registers one
#         runtime named `kata` and deliberately leaves `default-runtime`
#         ABSENT -- setting it to kata would drag the key-holding proxy into
#         the guest VM and silently destroy the trust zone (AD-1).
#   AD-7  Every host path that a bind mount will touch is labelled, and the
#         label is ASSERTED with ls -Z rather than assumed from exit status.
#   AD-16 The install is machine-wide at five conventional paths; the
#         repository checkout is never referenced at runtime.
#
# Every precondition is checked before the first package install or file write,
# so a host that cannot work fails here and not later (Story 1.2).

set -euo pipefail

# Versions the installer will run on. Default is everything this build has been
# reasoned through for; tighten or widen via this environment variable
# (SECUREAI_FEDORA_VERSIONS="38" install-fedora.sh ships F38 support without
# committing to F43/F44 assumption-only acceptance). This is the one knob a
# tester uses to prove the guard works without editing source text:
#
#   SECUREAI_FEDORA_VERSIONS="99" bash install-fedora.sh
#
# A wider list that INCLUDES the host's version makes the installer proceed;
# anything narrower refuses with a readable message.
readonly SECUREAI_FEDORA_VERSIONS="${SECUREAI_FEDORA_VERSIONS:-38 43 44}"
readonly SECUREAI_APP_DIR="/opt/secureai"
readonly SECUREAI_BIN="/usr/local/bin/secureai"
readonly SECUREAI_ETC="/etc/secureai"
readonly SECUREAI_VAR="/var/lib/secureai"
readonly SECUREAI_DAEMON_JSON="/etc/docker/daemon.json"

# Payload: directory of an already-materialised tree, or a base URL to fetch
# each file from. Empty means "look next to this script", then give up.
SECUREAI_PAYLOAD_DIR="${SECUREAI_PAYLOAD_DIR:-}"
SECUREAI_REPO_BASE="${SECUREAI_REPO_BASE:-}"

# Must stay in step with the guest Dockerfile's COPY paths and with the
# compose build contexts -- a file missing here is a file missing from
# /opt/secureai, which is a build failure much later.
readonly PAYLOAD_FILES=(
  docker-compose.yml
  opencode/Dockerfile
  opencode/opencode.json
  opencode/models.json
  proxy/Dockerfile
  proxy/addon.py
  config/compose.env
  config/whitelist.yaml
  config/settings.yaml
  config/keys.yaml.template
)

# --- output helpers ------------------------------------------------------
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    ok  %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m error\033[0m %s\n' "$*" >&2; exit 1; }

# =========================================================================
# PHASE 1 -- preconditions.  Nothing below this point runs until all of
# these pass, so an unsupported host is never modified (Story 1.2).
# =========================================================================
check_preconditions() {
  log "Checking that this host can run SecureAI"

  # 1. Fedora 38/43/44 only -- 41 and 42 are EOL. If the host is not a
  #    supported Fedora at all, say so and stop before touching anything.
  if [[ ! -r /etc/os-release ]]; then
    die "SecureAI needs one of: ${SECUREAI_FEDORA_VERSIONS}, but /etc/os-release could not be read,
     so the distribution could not be identified. Unsupported platform. Set
     SECUREAI_FEDORA_VERSIONS=<supported list> to acknowledge other Fedoras."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  local detected="${NAME:-unknown} ${VERSION_ID:-unknown}"
  if [[ "${ID:-}" != "fedora" ]]; then
    die "SecureAI supports Fedora ${SECUREAI_FEDORA_VERSIONS}, detected ${detected}.
     This is a proof of concept and has not been ported to your platform."
  fi
  if [[ " ${SECUREAI_FEDORA_VERSIONS} " != *" ${VERSION_ID:-} "* ]]; then
    die "SecureAI supports Fedora ${SECUREAI_FEDORA_VERSIONS}, detected ${detected}.
     Set SECUREAI_FEDORA_VERSIONS='${VERSION_ID:-}' to acknowledge this host, at
     your own risk -- Kata/Docker package versions on ${VERSION_ID:-unknown} are
     not covered by the version matrix in AD-6."
  fi
  ok "Fedora ${VERSION_ID}"

  # 2. Root.  Asserted here so the first thing a non-root user sees is the
  #    requirement, not a wall of permission errors.
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "SecureAI must be installed as root.
     Re-run with sudo:   curl -fsSL <url>/install-fedora.sh | sudo bash
     Nothing on this host has been changed."
  fi
  ok "running as root"

  # 3. Nested virtualization -- Kata runs the guest as a real VM, so without
  #    hardware virtualization it cannot boot. This is the single most common
  #    reason a first install fails, so name how to fix it.
  if ! grep -qwE '(vmx|svm)' /proc/cpuinfo 2>/dev/null; then
    if [[ -e /dev/kvm ]]; then
      warn "/dev/kvm exists but no vmx/svm CPU flag was found; continuing."
    else
      die "Nested or hardware virtualization is disabled, so a Kata VM cannot boot.
       SecureAI needs one of the vmx (Intel) or svm (AMD) CPU flags, and
       /dev/kvm to be present.
       On a hypervisor host, enable nested virtualization there first.
       On bare metal, enable VT-x (Intel) or AMD-V in the BIOS.
       Nothing on this host has been changed."
    fi
  else
    ok "hardware virtualization available"
  fi
  [[ -e /dev/kvm ]] || warn "/dev/kvm is not present; Kata may fail to start until it is."

  # 4. Every tool the installer itself uses must exist BEFORE it is used.
  #    curl because the payload may be fetched over HTTPS; python3 because
  #    daemon.json is merged with it (no jq dependency -- FR43 is bash only).
  local tool
  for tool in dnf curl python3 install systemctl; do
    command -v "$tool" >/dev/null 2>&1 \
      || die "'${tool}' is required by the installer but is not installed."
  done
  ok "required tools present"
}

# =========================================================================
# PHASE 2 -- packages
# =========================================================================
install_packages() {
  log "Installing Kata Containers, Docker Engine, and Docker Compose v2"
  # runtime-rs is default upstream from Kata 4.0.0, but the Fedora package
  # ships the Go runtime; the stack table pins F43=3.25.0 / F44=3.26.0.
  dnf -y install kata-containers docker docker-compose-plugin policycoreutils \
    policycoreutils-python-utils || \
    die "Package installation failed. Check that this Fedora release still
     carries kata-containers, docker, and docker-compose-plugin."
  ok "packages installed"

  # Docker must be RUNNING, not merely installed. Without this, a fresh install
  # leaves the daemon inactive and the kata runtime is never registered, so the
  # failure surfaces much later as an unreadable "no runtime named kata".
  systemctl enable --now docker \
    || die "Could not enable or start the Docker service.
     The kata runtime cannot be registered until Docker is running:
       sudo systemctl enable --now docker"
  ok "docker enabled and running"
}

# =========================================================================
# PHASE 3 -- Docker runtime wiring (AD-6).  Sole writer of daemon.json.
# =========================================================================

# Docker version -> a single comparable integer, as major*100 + minor*10.
#
#   28.5.2 -> 2805      29.5.1 -> 2950      30.0.0 -> 3000
#
# Both the installer and `secureai status` must agree on this encoding, so it is
# written once here and documented at the comparison below. An earlier encoding
# of major*1000 + minor (29.5 -> 2905) silently failed the >= 2950 test on
# exactly the versions that need the workaround.
docker_version_key() {
  local raw="${1:-}"
  [[ -n "$raw" ]] || return 1
  raw="${raw%%-*}"                      # strip -ce / -rc suffixes
  local major minor
  IFS=. read -r major minor _ <<<"$raw"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || return 1
  printf '%d\n' $(( 10#$major * 100 + 10#$minor * 10 ))
}

# The threshold, as one matrix row (Story 1.4) -- NOT a disjunction of
# independent conditions:
#   Docker >= 29.5.0 -> features.time-namespaces must be false, or Kata fails to
#                       start with 'invalid namespace type'
#                       (kata#13080, reproduced on F44 + Docker 29.5.1).
#   Docker <= 28.5.2 -> the flag must NOT be written at all.
readonly SECUREAI_TIME_NS_MIN_KEY=2950

write_daemon_json() {
  log "Registering the Kata runtime in ${SECUREAI_DAEMON_JSON}"

  local raw_version version_key time_ns_flag
  raw_version="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "")"
  local kata_config_path=""
  for candidate in \
      /usr/share/kata-containers/configuration.toml \
      /usr/share/defaults/kata-containers/vm.json \
      /usr/share/kata-containers/configuration-qemu-pkg-forced.toml \
      /usr/share/kata-containers/runtime-rs.json \
      /opt/kata/configuration.toml \
      /opt/kata/share/defaults/kata-containers/qemu/configuration.toml; do
    if [[ -f "$candidate" ]]; then
      kata_config_path="$candidate"
      break
    fi
  done
  if [[ -z "$kata_config_path" ]]; then
    die "Kata's runtime config was not found in any known location.
     After kata-containers is installed, locate it with:
       rpm -ql kata-containers | grep configuration
     and re-run the installer, exporting SECUREAI_KATA_CONFIG_PATH explicitly:"
  fi
  # F43/F44 go-runtime packages put it at /usr/share/kata-containers/...; F38
  # (Kata 3.0.1) ships it under /usr/share/defaults/. The check above searches
  # both -- widen the candidate list before ever hard-coding.
  SECUREAI_KATA_CONFIG_PATH="${SECUREAI_KATA_CONFIG_PATH:-${kata_config_path}}"
  if version_key="$(docker_version_key "$raw_version")"; then
    if (( version_key >= SECUREAI_TIME_NS_MIN_KEY )); then
      time_ns_flag="true"
      ok "Docker ${raw_version} >= 29.5 -- writing features.time-namespaces: false"
    else
      time_ns_flag="false"
      ok "Docker ${raw_version} < 29.5 -- omitting the time-namespaces workaround"
    fi
  else
    time_ns_flag="false"
    warn "could not determine the Docker version; omitting the time-namespaces
     workaround. If Docker is >= 29.5, Kata will fail with 'invalid namespace
     type' -- run 'secureai status' once Docker is up."
  fi

  install -d -m 0755 /etc/docker

  # Start from what is there so an existing Docker configuration is preserved,
  # then replace our own keys. No jq dependency (FR43: bash only).
  local tmp
  tmp="$(mktemp)"
  if [[ -s "$SECUREAI_DAEMON_JSON" ]]; then
    cp "$SECUREAI_DAEMON_JSON" "$tmp"
  else
    printf '{}\n' >"$tmp"
  fi
  python3 - "$tmp" "$time_ns_flag" <<PY
# (unquoted heredoc on purpose: bash expands $SECUREAI_KATA_CONFIG_PATH
# inside the python before python parses it; the block itself contains no
# other `$`, and the ConfigPath value is validated by the loop above.)
import json, sys

path, time_ns_flag = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    try:
        cfg = json.load(fh)
    except ValueError:
        cfg = {}
if not isinstance(cfg, dict):
    cfg = {}

# The one runtime we register. `kata-qemu` is a NAME (hypervisor config
# basename), never an executable path -- Docker points at the containerd shim.
cfg["runtimes"] = {
    "kata": {
        "runtimeType": "containerd-shim-kata-v2",
        "options": {"ConfigPath": "$SECUREAI_KATA_CONFIG_PATH"},
    }
}

# AD-6: default-runtime is intentionally ABSENT. Docker's own default stays in
# force so that the key-holding proxy (pinned to runc in compose) can never be
# drawn into the Kata VM. Do not add this key.
cfg.pop("default-runtime", None)

features = cfg.get("features")
if not isinstance(features, dict):
    features = {}
if time_ns_flag == "true":
    features["time-namespaces"] = False
else:
    features.pop("time-namespaces", None)
cfg["features"] = features

with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
  install -m 0644 "$tmp" "$SECUREAI_DAEMON_JSON"
  rm -f "$tmp"

  # Assert what we wrote rather than trusting the write.
  grep -q '"kata"' "$SECUREAI_DAEMON_JSON" || die "daemon.json has no kata runtime after writing it."
  if grep -q '"default-runtime"' "$SECUREAI_DAEMON_JSON"; then
    die "daemon.json contains default-runtime. This would move the key-holding
     proxy into the Kata VM and destroy the trust boundary. Refusing to continue."
  fi
  ok "kata runtime registered, default-runtime absent"

  # Restart so the new configuration is actually loaded. Skipping this is why an
  # earlier revision could install successfully and still fail at first session.
  if systemctl is-active --quiet docker; then
    systemctl restart docker || warn "could not restart docker; run 'systemctl restart docker'."
  fi
}

# =========================================================================
# PHASE 4 -- installed layout (AD-16).  Five paths, nothing else.
# =========================================================================

# Fetch one payload file. `install` only copies LOCAL files, so an http(s)
# REPO_BASE must be fetched with curl -- the earlier revision passed a URL to
# install, which cannot work and broke the documented curl|sudo bash path.
fetch_one() {
  local rel="$1" dest="$2"
  if [[ -n "$SECUREAI_PAYLOAD_DIR" && -f "${SECUREAI_PAYLOAD_DIR}/${rel}" ]]; then
    install -m 0644 "${SECUREAI_PAYLOAD_DIR}/${rel}" "$dest"
    return 0
  fi
  local sibling="${BASH_SOURCE[0]%/*}/${rel}"
  if [[ -f "$sibling" ]]; then
    install -m 0644 "$sibling" "$dest"
    return 0
  fi
  if [[ -n "$SECUREAI_REPO_BASE" ]]; then
    local tmp
    tmp="$(mktemp)"
    if curl -fsSL --retry 3 -o "$tmp" "${SECUREAI_REPO_BASE%/}/${rel}"; then
      install -m 0644 "$tmp" "$dest"
      rm -f "$tmp"
      return 0
    fi
    rm -f "$tmp"
    die "Could not fetch ${rel} from ${SECUREAI_REPO_BASE}"
  fi
  return 1
}

fetch_payload() {
  local rel dest
  for rel in "${PAYLOAD_FILES[@]}"; do
    dest="${SECUREAI_APP_DIR}/${rel}"
    install -d -m 0755 "$(dirname "$dest")"
    fetch_one "$rel" "$dest" || die \
"Cannot obtain ${rel}: this installer was piped in without its application tree.
 Re-run with the tree supplied, e.g.
   SECUREAI_REPO_BASE=https://raw.githubusercontent.com/<org>/secureai/main \\
     bash install-fedora.sh"
  done
  ok "application tree installed to ${SECUREAI_APP_DIR}"
}

install_layout() {
  log "Installing the application tree and CLI"
  install -d -m 0755 "$SECUREAI_APP_DIR" /usr/local/bin

  fetch_payload

  # The addon is read by the mitmproxy container, whose entrypoint gosu-drops
  # to the unprivileged `mitmproxy` user -- so it must be world-readable.
  chmod 0644 "${SECUREAI_APP_DIR}/proxy/addon.py"

  # config/compose.env holds the cross-boundary constants, so it must NOT be
  # world-readable in the app tree: /etc/secureai/compose.env is meant to be the
  # one copy (AD-11), seeded from this template by the CLI.
  chmod 0640 "${SECUREAI_APP_DIR}/config/compose.env"

  # Sole writer of the CLI (AD-16). No per-user variant, no ~/bin shim.
  if ! fetch_one secureai "$SECUREAI_BIN"; then
    die "Cannot obtain the secureai CLI from ${SECUREAI_REPO_BASE:-the local tree}."
  fi
  chmod 0755 "$SECUREAI_BIN"

  # 0700 on both: these hold key material and must not be traversable by the
  # unprivileged proxy container. The proxy's read access to its runtime copy is
  # granted by GROUP, not by relaxing these -- see cmd_init_config.
  install -d -m 0700 "$SECUREAI_ETC" "$SECUREAI_VAR"

  # AD-11: the installer may CREATE compose.env but must never POPULATE it.
  # The CLI is its sole writer. Seeded empty so the file exists and is owned.
  if [[ ! -e "${SECUREAI_ETC}/compose.env" ]]; then
    : >"${SECUREAI_ETC}/compose.env"
    chown root:root "${SECUREAI_ETC}/compose.env"
    chmod 0640 "${SECUREAI_ETC}/compose.env"
  fi

  # Seed config files into /etc/secureai without clobbering operator edits.
  local seed
  for seed in whitelist.yaml settings.yaml; do
    if [[ ! -e "${SECUREAI_ETC}/${seed}" && -f "${SECUREAI_APP_DIR}/config/${seed}" ]]; then
      install -m 0640 "${SECUREAI_APP_DIR}/config/${seed}" "${SECUREAI_ETC}/${seed}"
    fi
  done
  if [[ ! -e "${SECUREAI_ETC}/keys.yaml" && -f "${SECUREAI_APP_DIR}/config/keys.yaml.template" ]]; then
    install -m 0640 "${SECUREAI_APP_DIR}/config/keys.yaml.template" "${SECUREAI_ETC}/keys.yaml"
  fi
  # /etc/secureai stays root:root 0700 -- it is the source of truth, and the
  # proxy never reads it directly. Only the runtime COPY under /var/lib is
  # re-grouped for the proxy, and it stays deny-all to everyone else (AD-7).
  chown -R root:root "$SECUREAI_ETC"
  chmod 0700 "$SECUREAI_ETC"
  install -d -m 0750 -o root -g 10001 "$SECUREAI_VAR"
  install -d -m 2770 -o root -g 10001 "${SECUREAI_VAR}/ca"
  chown -R root:10001 "$SECUREAI_VAR"
  chmod 0750 "$SECUREAI_VAR"
  chmod 2770 "${SECUREAI_VAR}/ca"

  ok "${SECUREAI_BIN} on PATH, ${SECUREAI_ETC} and ${SECUREAI_VAR} created"
}

# =========================================================================
# PHASE 5 -- SELinux labelling (AD-7).  Asserted, not assumed.
# =========================================================================
label_paths() {
  log "Labelling host state for SELinux"

  if ! command -v getenforce >/dev/null 2>&1; then
    warn "SELinux tooling is absent; skipping labelling."
    return 0
  fi

  local mode
  mode="$(getenforce 2>/dev/null || echo Disabled)"
  if [[ "$mode" != "Enforcing" ]]; then
    warn "SELinux is ${mode}, not Enforcing. Labels are not required, so this is
     not an error -- but a production host should be Enforcing."
    return 0
  fi

  command -v semanage >/dev/null 2>&1 \
    || die "SELinux is Enforcing but 'semanage' is not installed.
     Install policycoreutils-python-utils and re-run."

  local path
  for path in "$SECUREAI_ETC" "$SECUREAI_VAR"; do
    # `-a` fails when the definition already exists, and `-m` fails when it does
    # not. Trying `-a` then `-m` is idempotent WITHOUT the delete-first dance,
    # which briefly leaves the path with no fcontext at all if the re-add fails.
    semanage fcontext -a -t container_file_t "${path}(/.*)?" 2>/dev/null \
      || semanage fcontext -m -t container_file_t "${path}(/.*)?" \
      || die "Could not set the SELinux fcontext for ${path}.
     The path currently has no usable label; later bind mounts would be refused."
    restorecon -RF "${path}" || warn "restorecon failed for ${path}."
  done

  # AD-7: assert the resulting label. A zero exit from semanage does not mean
  # the label landed; `ls -Z` is the observation that counts.
  for path in "$SECUREAI_ETC" "$SECUREAI_VAR"; do
    local label
    label="$(ls -Zd "$path" 2>/dev/null | awk '{print $1}' || true)"
    if [[ "$label" != "container_file_t" ]]; then
      warn "${path} carries '${label:-unknown}', expected container_file_t.
     If a bind mount is later refused with 'permission denied', inspect
     denials with:  ausearch -m avc -ts recent"
    else
      ok "${path} is container_file_t"
    fi
  done
}

# =========================================================================
verify_install() {
  log "Verifying the installation"
  [[ -x "$SECUREAI_BIN" ]] || die "${SECUREAI_BIN} is not executable."
  [[ -d "$SECUREAI_APP_DIR" ]] || die "${SECUREAI_APP_DIR} is missing."
  [[ -d "$SECUREAI_ETC" ]] || die "${SECUREAI_ETC} is missing."
  # PATH is checked with a warning, not a die: the file is installed correctly
  # and a shell that has not re-hashed /usr/local/bin is a login-shell detail,
  # not a broken installation.
  case ":${PATH}:" in
    *":/usr/local/bin:"*) ok "secureai is on PATH" ;;
    *) warn "${SECUREAI_BIN} is installed, but /usr/local/bin is not on your PATH.
     Add it to your shell profile, or call the binary by full path." ;;
  esac
  ok "installed layout present"
}

main() {
  check_preconditions
  install_packages
  write_daemon_json
  install_layout
  label_paths
  verify_install

  printf '\n\033[1;32mSecureAI installed.\033[0m\n\n'
  cat <<'EOF'
  Next steps (all of these are `secureai` commands -- you never type docker):

    1. Produce the interception CA, then build both images:
         sudo secureai ca export
         sudo secureai build

    2. Store a key. The value is masked by every read-back:
         sudo secureai keys set api.anthropic.com sk-ant-...

    3. Start a session:
         sudo secureai opencode

    4. Prove the isolation rather than assuming it:
         sudo secureai verify

  See: secureai status | secureai whitelist list | secureai down
EOF
}

main "$@"