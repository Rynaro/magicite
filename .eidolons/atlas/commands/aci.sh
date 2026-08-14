#!/usr/bin/env bash
# commands/aci.sh — wire the atlas-aci MCP server into a consumer project.
#
# Ships from Rynaro/ATLAS and is installed by ATLAS's install.sh into
# ./.eidolons/atlas/commands/aci.sh. Auto-surfaced by the nexus dispatcher
# (cli/src/dispatch_eidolon.sh) as `eidolons atlas aci [OPTIONS]`.
#
# Full decision-ready spec: docs/specs/atlas-aci-integration.md in the
# Rynaro/eidolons nexus repo. Section anchors throughout this file point
# at the governing clauses.
#
# INVARIANT: on container index failure, exit BEFORE writing any host-wiring
# file. The verbatim "No MCP config files were modified." string is pinned
# by tests. Violating this is a P0 bug (D6 / R6 in atlas-aci-mcp-install-fix).
#
# ═══════════════════════════════════════════════════════════════════════════
# IMPORTANT INVARIANTS (violating these is a P0 bug):
#   - Layer-2 write boundary (P4 / D3): NEVER write outside $PWD.
#     Especially NOT ~/Library/Application Support/, NOT ~/.config/,
#     NOT ~/.claude/, NOT ~/.cursor/, NOT $EIDOLONS_HOME.
#   - Idempotency model per file type (§4.7):
#       .mcp.json / .cursor/mcp.json : jq merge on mcpServers."atlas-aci"
#       .github/agents/*.agent.md    : yq merge on list entry name: atlas-aci
#       .gitignore                   : append-if-absent on line '.atlas/'
#   - All progress to stderr (P6). Stdout stays empty on wire /
#     remove success; dry-run stdout emits CREATE|MODIFY|REMOVE|INDEX|BUILD.
#   - Bash 3.2 compatible (P5): no associative arrays, no ${var,,},
#     no readarray/mapfile, no &>>. Atomic tmpfile + mv everywhere.
#   - TOML idempotency (codex host): awk slices on [mcp_servers.atlas-aci]
#     heading, line-bounded by next [*] heading or EOF. Atomic tmpfile+mv.
#     On detect of a deviant existing body, warn + refuse (R2 mitigation).
#   - Container mode (positional runtime after action): builds atlas-aci image
#     locally from git URL, pins by sha256 digest, idempotent across runs.
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Pinned atlas-aci upstream (v1: commit SHA per G3 / D4) ───────────────
# When atlas-aci cuts its first tagged release, bump to a version string
# and revisit D4 (see §9 follow-up F1 in the spec).
#
# Pin captured 2026-04-28 from `gh api repos/Rynaro/atlas-aci/commits/main`
# (post-merge of atlas-aci#1, which lock-respects transitive deps in the
# production Dockerfile and pins tree-sitter-language-pack <1.6.3).
# Revisit when F1 triggers (atlas-aci cuts its first tagged release) or on
# every ATLAS release per §6 R4 — whichever comes first.
ATLAS_ACI_REPO="https://github.com/Rynaro/atlas-aci"
ATLAS_ACI_PIN="8ce17f0e69f135f9324dad718415043276029eb4"

# ─── Pinned atlas-aci git ref for container builds (D2 = build-locally) ──
# Used by `<runtime> build ... <URL>#<ATLAS_ACI_REF>:mcp-server`.
# Bump together with ATLAS_ACI_PIN on every ATLAS release that touches
# atlas-aci. Pinned to the same HEAD as ATLAS_ACI_PIN above.
#
# Pin captured 2026-04-28 from `git -C atlas-aci log -1 --format=%H`.
ATLAS_ACI_REF="8ce17f0e69f135f9324dad718415043276029eb4"

# ─── GHCR registry-prefixed image reference (T4 / spec §D-NEW-3) ──────────
# The canonical registry for atlas-aci. Combined with ATLAS_ACI_IMAGE_DIGEST
# below to form the full pull reference: ${ATLAS_ACI_IMAGE_REF}@${ATLAS_ACI_IMAGE_DIGEST}.
#
# ghcr.io lowercases the org segment per registry convention.
ATLAS_ACI_IMAGE_REF="ghcr.io/rynaro/atlas-aci"

# Pin: ghcr.io/rynaro/atlas-aci v0.2.2 (first signed GHCR publish, multi-arch, cosign + SBOM + provenance, Trivy gate green).
ATLAS_ACI_IMAGE_DIGEST="sha256:386677f06b0ce23cb4883f6c0f91d8eac22328cd7d9451ae241e2f183207ad96"

# ATLAS version — used as the local image tag (atlas-aci:<ATLAS_VERSION>).
# Substituted from install.sh EIDOLON_VERSION at install time via sed.
# DO NOT edit manually — the placeholder is replaced during `bash install.sh`.
ATLAS_VERSION="1.13.0"

# ─── Logging (mirrors cli/src/lib.sh — P6: everything to stderr) ──────────
# Kept local so this script is self-sufficient when the dispatcher exec's
# it with cwd at the consumer project root (no nexus lib is sourced).
if [ -t 2 ]; then
  _C_B=$'\033[1m'; _C_G=$'\033[32m'; _C_Y=$'\033[33m'
  _C_R=$'\033[31m'; _C_C=$'\033[36m'; _C_RST=$'\033[0m'
else
  _C_B=""; _C_G=""; _C_Y=""; _C_R=""; _C_C=""; _C_RST=""
fi
say()  { printf "%s▸%s %s\n" "$_C_B"  "$_C_RST" "$*" >&2; }
ok()   { printf "%s✓%s %s\n" "$_C_G"  "$_C_RST" "$*" >&2; }
info() { printf "%s·%s %s\n" "$_C_C"  "$_C_RST" "$*" >&2; }
warn() { printf "%s⚠%s %s\n" "$_C_Y"  "$_C_RST" "$*" >&2; }
err()  { printf "%s✗%s %s\n" "$_C_R"  "$_C_RST" "$*" >&2; }

# Hard exits — each maps to a §4.8 exit code.
exit_usage()     { err "$*"; exit 2; }  # usage error
exit_no_atlas()  { err "$*"; exit 3; }  # ATLAS not installed
exit_no_host()   { err "$*"; exit 4; }  # no MCP-capable host
exit_prereq()    { err "$*"; exit 5; }  # prereq missing
exit_index_fail(){ err "$*"; exit 6; }  # atlas-aci index failed
exit_no_runtime(){ err "$*"; exit 7; }  # no container runtime on PATH
exit_build_fail(){ err "$*"; exit 8; }  # image build failed
exit_need_rt()   { err "$*"; exit 9; }  # --non-interactive without --runtime
die()            { err "$*"; exit 1; }  # unexpected runtime error

# ─── Args ─────────────────────────────────────────────────────────────────
ACTION="wire"          # wire | remove | index
DRY_RUN=false
NON_INTERACTIVE=false
HOSTS_EXPLICIT=""      # CSV of user-specified hosts
RUNTIME_POSITIONAL=""  # positional: docker | podman (absent = host mode)
# CONTAINER_MODE and RUNTIME are derived below from RUNTIME_POSITIONAL
CONTAINER_MODE=false
RUNTIME=""

usage() {
  cat <<'EOF'
eidolons atlas aci — wire atlas-aci MCP server into this project

Usage: eidolons atlas aci [ACTION] [runtime] [OPTIONS]

Actions (positional, mutually exclusive):
  wire      (default) Verify prereqs, run atlas-aci index, append .atlas/
            to .gitignore, and write MCP config for atlas-aci into every
            detected MCP-capable host in cwd.
  index     Re-run atlas-aci index against the current project. Reuses
            the existing installation — does NOT rebuild the image, does
            NOT modify MCP configs or .gitignore. Mode (host vs
            container) is auto-detected from what's installed locally.
  remove    Remove atlas-aci entries from MCP config in cwd. Idempotent.
            Does NOT delete .atlas/.

Runtime (optional positional after ACTION):
  docker    Use docker for container mode.
  podman    Use podman for container mode.
  (absent)  Host mode — use atlas-aci binary directly (no container).

Options:
  --index / --remove     Flag forms of the index/remove actions.
  --host HOST            Restrict to one host: claude-code, cursor,
                         copilot, codex. Repeat for multiple. Overrides
                         auto-detection. Ignored by `index`.
  --dry-run              Print every file that would be created /
                         modified / removed. Touch no disk state.
                         Does not run `atlas-aci index` or `docker build`.
  --non-interactive      Fail on any prompt (for CI).
  -h, --help             Show this help.

Exit codes:
  0  success / no-op
  2  usage error
  3  ATLAS not installed in this project
  4  no MCP-capable host detected and --host not provided
  5  atlas-aci prereq missing (uv, rg, python3>=3.11, atlas-aci binary,
     or — for `index` auto-detect — neither host nor container present)
  6  atlas-aci index failed
  7  requested container runtime not on PATH
  8  image build failed (container mode only)
  1  unexpected runtime error

Scope: project-local files only. Never writes outside $PWD.
User-level Claude Desktop config is deferred to a future nexus
built-in (see docs/atlas-aci.md in Rynaro/eidolons).
EOF
}

_action_seen=false
_add_host() {
  case "$1" in
    claude-code|cursor|copilot|codex) ;;
    *) exit_usage "Unknown --host value: $1 (want: claude-code, cursor, copilot, codex)" ;;
  esac
  if [ -z "$HOSTS_EXPLICIT" ]; then
    HOSTS_EXPLICIT="$1"
  else
    HOSTS_EXPLICIT="$HOSTS_EXPLICIT,$1"
  fi
}

# Positional action subcommand: peek $1 before the flag loop.
# Allows `eidolons atlas aci wire` / `... index` / `... remove`.
# The legacy `install` positional is a hard usage error (renamed v1.8.0).
_runtime_seen=false
case "${1:-}" in
  install)
    err "Unknown action: install (did you mean 'wire'? — renamed in ATLAS v1.8.0; see CHANGELOG)"
    exit 2 ;;
  wire|index|remove)
    ACTION="$1"; _action_seen=true; shift ;;
esac

# Optional runtime positional (docker|podman) immediately after the action.
if [ "$_action_seen" = "true" ] && [ "$_runtime_seen" = "false" ] && [ "$#" -gt 0 ]; then
  case "${1:-}" in
    docker|podman)
      RUNTIME_POSITIONAL="$1"; _runtime_seen=true; shift ;;
    --*)
      # Next token is a flag — no runtime positional given.
      ;;
    *)
      # Non-flag, non-runtime token — hard usage error.
      err "Unknown runtime: $1 (allowed: docker, podman; omit for host mode)"
      exit 2 ;;
  esac
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install)
      err "Unknown option: --install (did you mean 'wire'? — flag form removed in ATLAS v1.8.0)"
      exit 2 ;;
    --container)
      err "Unknown option: --container"
      exit 2 ;;
    --runtime|--runtime=*)
      err "Unknown option: --runtime"
      exit 2 ;;
    --index)
      if [ "$_action_seen" = "true" ] && [ "$ACTION" != "index" ]; then
        exit_usage "Conflicting actions: --index and $ACTION"
      fi
      ACTION="index"; _action_seen=true; shift ;;
    --remove)
      if [ "$_action_seen" = "true" ] && [ "$ACTION" != "remove" ]; then
        exit_usage "Conflicting actions: --remove and $ACTION"
      fi
      ACTION="remove"; _action_seen=true; shift ;;
    --dry-run)         DRY_RUN=true; shift ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --host)
      [ "$#" -ge 2 ] || exit_usage "--host requires a value"
      _add_host "$2"; shift 2 ;;
    --host=*)
      _add_host "${1#--host=}"; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) exit_usage "Unknown option: $1" ;;
  esac
done

# Derive CONTAINER_MODE and RUNTIME from the positional.
if [ -n "$RUNTIME_POSITIONAL" ]; then
  CONTAINER_MODE=true
  RUNTIME="$RUNTIME_POSITIONAL"
fi

# ─── Early awk guard (before any awk call in the script body) ─────────────
# awk is a POSIX baseline but guard defensively so the user gets exit 5
# (prereq missing) rather than a confusing "127: command not found" crash.
if ! command -v awk >/dev/null 2>&1; then
  exit_prereq "atlas-aci prereq missing: 'awk' not on PATH.
  awk is a POSIX baseline tool — install it via your OS package manager."
fi

# ─── §4.3 first read: refuse to run if ATLAS is not installed ─────────────
# This runs BEFORE prereq checks so the user gets the clearest possible
# error ("you installed the wrong thing first") instead of a prereq nag.
if [ ! -f "./.eidolons/atlas/install.manifest.json" ]; then
  exit_no_atlas "atlas-aci: ATLAS is not installed in this project.
  Expected: ./.eidolons/atlas/install.manifest.json
  Fix:      eidolons sync   (with atlas in eidolons.yaml)"
fi

# ─── Helpers ──────────────────────────────────────────────────────────────

# python3_at_least MIN_MAJOR MIN_MINOR — returns 0 if python3 --version
# reports at least MIN_MAJOR.MIN_MINOR. Stays in bash-3.2 territory by
# avoiding ${var,,} and arithmetic on string slices.
python3_at_least() {
  local want_major="$1" want_minor="$2"
  local raw major minor
  raw="$(python3 --version 2>&1 | awk '{print $2}')"
  [ -n "$raw" ] || return 1
  major="$(echo "$raw" | awk -F. '{print $1}')"
  minor="$(echo "$raw" | awk -F. '{print $2}')"
  # Reject non-numeric (defensive — upstream python3 always emits numeric).
  case "$major" in ''|*[!0-9]*) return 1 ;; esac
  case "$minor" in ''|*[!0-9]*) return 1 ;; esac
  if [ "$major" -gt "$want_major" ]; then return 0; fi
  if [ "$major" -lt "$want_major" ]; then return 1; fi
  if [ "$minor" -ge "$want_minor" ]; then return 0; fi
  return 1
}

# yq_is_mikefarah — distinguishes mikefarah/yq (Go) from kislyuk/yq (Python
# wrapper). Their CLIs differ materially; we only support mikefarah/yq
# for the frontmatter edits because kislyuk/yq cannot do in-place YAML
# round-tripping through a Markdown preamble.
yq_is_mikefarah() {
  yq --version 2>&1 | grep -qi 'mikefarah'
}

# Dry-run channel: stdout gets `CREATE|MODIFY|REMOVE|INDEX|BUILD <path>` lines
# (§4.9). Everything else (progress, warnings) goes to stderr via say/info.
emit_action() {
  if [ "$DRY_RUN" = "true" ]; then
    printf "%s %s\n" "$1" "$2"
  fi
}

# Atomic write helper — writes $2 content to temp file in same dir as
# target $1, then mv's. Bash-3.2-safe.
atomic_write() {
  local dest="$1" content="$2"
  local dir tmp
  dir="$(dirname "$dest")"
  mkdir -p "$dir"
  tmp="$(mktemp "${dir}/.atlas-aci-XXXXXX")" || die "mktemp failed in $dir"
  printf "%s" "$content" > "$tmp" || { rm -f "$tmp"; die "write failed: $tmp"; }
  mv "$tmp" "$dest" || { rm -f "$tmp"; die "atomic rename failed: $dest"; }
}

# ─── §4.2 Prereq checks (install path only) ───────────────────────────────
check_prereqs() {
  if ! command -v jq >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: 'jq' not on PATH.
  Install with: brew install jq   # macOS
           or:  apt-get install jq # Debian/Ubuntu"
  fi
  if ! command -v yq >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: 'yq' not on PATH.
  Install with: brew install yq   # macOS, or see https://github.com/mikefarah/yq/releases"
  fi
  if ! yq_is_mikefarah; then
    exit_prereq "atlas-aci prereq: 'yq' must be mikefarah/yq (Go).
  Detected: $(yq --version 2>&1 | head -n 1)
  Install with: brew install yq   (macOS) / see https://github.com/mikefarah/yq/releases"
  fi

  if [ "$CONTAINER_MODE" = "true" ]; then
    check_prereqs_container
  else
    check_prereqs_uv
  fi
}

check_prereqs_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: 'uv' not on PATH.
  Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  fi
  if ! command -v rg >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: 'rg' (ripgrep) not on PATH.
  Install with: brew install ripgrep   # macOS
           or:  apt-get install ripgrep # Debian/Ubuntu"
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: python3 not on PATH.
  Install Python 3.11+ via uv or your OS package manager."
  fi
  if ! python3_at_least 3 11; then
    local raw
    raw="$(python3 --version 2>&1 | awk '{print $2}')"
    exit_prereq "atlas-aci prereq: python3 >= 3.11 required (have: ${raw:-unknown}).
  Install Python 3.11+ via uv or your OS package manager."
  fi
  if ! command -v atlas-aci >/dev/null 2>&1; then
    exit_prereq "atlas-aci prereq missing: 'atlas-aci' binary not on PATH.
  Install with:
    git clone ${ATLAS_ACI_REPO} && cd atlas-aci/mcp-server && uv sync && uv tool install ."
  fi
}

check_prereqs_container() {
  # git is required for `<runtime> build <git-url>#<ref>:mcp-server` to
  # resolve the context — Docker/Podman delegates to git for URL schemes.
  if ! command -v git >/dev/null 2>&1; then
    exit_prereq "atlas-aci container prereq missing: 'git' not on PATH.
  Install with: brew install git   # macOS
           or:  apt-get install git # Debian/Ubuntu"
  fi

  # RUNTIME is always set from the positional (RUNTIME_POSITIONAL) before
  # check_prereqs_container is called. Verify it's on PATH.
  if ! command -v "$RUNTIME" >/dev/null 2>&1; then
    err "atlas-aci: requested runtime '$RUNTIME' is not on PATH. Install it or omit the positional to use host mode."
    exit 7
  fi
}

# ─── Runtime selection ────────────────────────────────────────────────────
# RUNTIME is always set from the positional before select_runtime is called
# in container mode. This function is now a no-op guard.
select_runtime() {
  # RUNTIME was set from the positional — nothing to do.
  [ -n "$RUNTIME" ] && return 0
  # Should never reach here in container mode (RUNTIME_POSITIONAL is
  # validated at parse time); guard defensively.
  exit_no_runtime "atlas-aci: container mode requires a runtime positional (docker or podman)."
}

# ─── Container image management ───────────────────────────────────────────
# image_tag — the local tag we build into.
image_tag() { printf "%s:%s" "$ATLAS_ACI_IMAGE_REF" "$ATLAS_VERSION"; }

# image_full_ref — the registry-prefixed digest reference used in all
# canonical bodies (spec T4). Composed from ATLAS_ACI_IMAGE_REF and
# ATLAS_ACI_IMAGE_DIGEST.
image_full_ref() { printf "%s@%s" "$ATLAS_ACI_IMAGE_REF" "$ATLAS_ACI_IMAGE_DIGEST"; }

# _atlas_aci_selinux_enforcing — returns 0 on Linux+SELinux Enforcing; 1 otherwise.
# Bash 3.2 safe; no associative arrays, no ${var,,}, no readarray.
_atlas_aci_selinux_enforcing() {
  case "$(uname -s)" in
    Linux) ;;
    *) return 1 ;;
  esac
  command -v getenforce >/dev/null 2>&1 || return 1
  [ "$(getenforce 2>/dev/null)" = "Enforcing" ]
}

# _atlas_aci_volume_opts BASE — composes the volume option string.
# BASE is "ro" or empty. Returns ":ro,Z" / ":Z" / ":ro" / "" depending on
# SELinux state. Mount syntax: "host:container[:opts]" with comma-separated
# opts. ':Z' relabels the bind with a private MCS so the container can
# read/write under SELinux Enforcing.
_atlas_aci_volume_opts() {
  local base="$1" z=""
  if _atlas_aci_selinux_enforcing; then z="Z"; fi
  if [ -n "$base" ] && [ -n "$z" ]; then
    printf ":%s,%s" "$base" "$z"
  elif [ -n "$base" ]; then
    printf ":%s" "$base"
  elif [ -n "$z" ]; then
    printf ":%s" "$z"
  else
    printf ""
  fi
}

# _resolve_pinned_image_ref — echoes the fully-qualified image ref to use
# for the current install. Resolution order (D3 from atlas-aci-mcp-install-fix):
#   1. Parse <cwd>/.mcp.json for a ghcr.io/rynaro/atlas-aci@sha256:... arg
#      (written by a prior `eidolons mcp atlas-aci` run — most up-to-date).
#   2. Optional: query `eidolons mcp atlas-aci --print-pinned-ref` if the
#      nexus CLI is on PATH (T2 from atlas-aci-mcp-install-fix). Absent or
#      non-zero exit is benign — treated as fallback.
#   3. Fallback: compose from ATLAS_ACI_IMAGE_REF + ATLAS_ACI_IMAGE_DIGEST
#      constants (always safe, mirrors nexus source-of-truth at pin time).
# Bash 3.2 safe: no associative arrays, no ${var,,}, no readarray.
_resolve_pinned_image_ref() {
  local _ref=""

  # 1. Parse .mcp.json for a pinned digest ref.
  if [ -f "./.mcp.json" ] && command -v jq >/dev/null 2>&1; then
    _ref="$(jq -r '
      .mcpServers["atlas-aci"].args[]?
      | select(test("^ghcr\\.io/rynaro/atlas-aci@sha256:"))
    ' "./.mcp.json" 2>/dev/null | head -n1)"
  fi

  # 2. Optional nexus CLI helper (T2 / atlas-aci-mcp-install-fix PR #1).
  if [ -z "$_ref" ] && command -v eidolons >/dev/null 2>&1; then
    _ref="$(eidolons mcp atlas-aci --print-pinned-ref 2>/dev/null)" || _ref=""
  fi

  # 3. Constant fallback (always present).
  if [ -z "$_ref" ]; then
    _ref="$(image_full_ref)"
  fi

  printf "%s" "$_ref"
}

# image_exists — returns 0 if the local tagged image exists.
image_exists() {
  "$RUNTIME" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
    | grep -q "^$(image_tag)$"
}

# capture_registry_digest — echoes the registry digest (sha256:... form)
# from the ATLAS_ACI_IMAGE_DIGEST constant (spec T4: registry digest, not
# local image ID). Parent spec D3 (local image ID capture) is replaced by
# this constant. Returns 1 if the constant is the placeholder.
capture_registry_digest() {
  printf "%s" "$ATLAS_ACI_IMAGE_DIGEST"
}

# build_image — runs `<runtime> build` with the git URL context.
# Emits BUILD action verb in dry-run. Exits 8 on failure.
build_image() {
  local build_url
  build_url="${ATLAS_ACI_REPO}.git#${ATLAS_ACI_REF}:mcp-server"

  if [ "$DRY_RUN" = "true" ]; then
    emit_action "BUILD" "$(image_tag)"
    return 0
  fi

  say "Building $(image_tag) from ${build_url}"
  say "(this may take several minutes on first run)"

  local build_log
  build_log="$(mktemp "./.atlas-aci-build-XXXXXX")" || die "mktemp failed"

  if ! "$RUNTIME" build -t "$(image_tag)" "$build_url" > "$build_log" 2>&1; then
    err "Container image build failed. Build log:"
    cat "$build_log" >&2
    rm -f "$build_log"
    exit_build_fail "atlas-aci: '$RUNTIME build' exited non-zero.
  Check network access to ${ATLAS_ACI_REPO}
  Build context: ${build_url}"
  fi
  rm -f "$build_log"
  ok "Built $(image_tag)"
}

# ensure_image — checks if the image is present; builds if absent.
# Sets LOCAL_DIGEST global to the registry digest (ATLAS_ACI_IMAGE_DIGEST).
# The digest is the registry pin constant, not captured from the local store.
# (spec T4: replaces parent spec D3 local image ID capture.)
#
# Image-reuse short-circuit (P-B / atlas-aci-mcp-install-fix S1):
# Before building, inspect the pinned digest ref via `docker image inspect`.
# If the image is already loaded (e.g. from a prior `eidolons mcp atlas-aci pull`),
# skip the build entirely. This avoids a redundant multi-minute rebuild that
# produces the same digest as the already-loaded image.
ensure_image() {
  # LOCAL_DIGEST holds the full sha256:... string from the registry constant.
  LOCAL_DIGEST="$(capture_registry_digest)"

  # First: check the locally-tagged image (existing fast path).
  if image_exists; then
    info "Image $(image_tag) already present (registry digest: ${LOCAL_DIGEST})"
    return 0
  fi

  # Second: check the pinned digest ref directly (handles the pull-then-install
  # case where `eidolons mcp atlas-aci pull` loaded the image by digest but the
  # local tag may not exist yet).
  if [ "$DRY_RUN" = "false" ]; then
    local _pinned_ref
    _pinned_ref="$(_resolve_pinned_image_ref)"
    if "$RUNTIME" image inspect "$_pinned_ref" >/dev/null 2>&1; then
      info "image already loaded — skipping build (${_pinned_ref})"
      IMAGE_REF="$_pinned_ref"
      return 0
    fi
  fi

  # Image absent — build it.
  build_image
  [ "$DRY_RUN" = "true" ] && { LOCAL_DIGEST="$ATLAS_ACI_IMAGE_DIGEST"; return 0; }

  ok "Image digest: ${LOCAL_DIGEST}"
}

# ─── Container canonical bodies ───────────────────────────────────────────
# These produce the JSON fragment that goes under mcpServers."atlas-aci"
# for container mode.

container_json_fragment() {
  # $1 = runtime (docker|podman), $2 = digest (full sha256:... string)
  # The image reference is registry-prefixed: ghcr.io/rynaro/atlas-aci@sha256:<hex>
  # (spec T4: replaces bare atlas-aci@sha256:<digest> which resolved to
  #  docker.io/library/atlas-aci — a non-existent image that 404s).
  # Host-side bind paths are written as absolute literals at install
  # time (the cwd is the project root). Earlier releases used the
  # `${workspaceFolder}` VSCode-style placeholder, which Cursor expands
  # natively but Claude Code does not — Claude Code treats `${VAR}` as
  # an env-var lookup and emits a "Missing environment variables:
  # workspaceFolder" warning, after which the docker `-v` mount
  # dereferences the literal string and fails. Absolute paths are
  # unambiguous across hosts; the trade-off is per-machine .mcp.json
  # bodies (re-run `eidolons atlas aci wire` after relocating).
  # -u UID:GID baked at install time so the serve container writes
  # .atlas/ files with host user ownership (atlas-aci-container-uid-perm-fix).
  # :Z appended to binds when SELinux Enforcing (private MCS relabel).
  local rt="$1" digest="$2"
  local image_ref="${ATLAS_ACI_IMAGE_REF}@${digest}"
  local _uid _gid _repo_mount _memex_mount
  _uid="$(id -u)"
  _gid="$(id -g)"
  _repo_mount="${PWD}:/repo$(_atlas_aci_volume_opts "ro")"
  _memex_mount="${PWD}/.atlas/memex:/memex$(_atlas_aci_volume_opts "")"
  jq -n \
    --arg rt "$rt" \
    --arg image_ref "$image_ref" \
    --arg uid_gid "${_uid}:${_gid}" \
    --arg repo_mount "$_repo_mount" \
    --arg memex_mount "$_memex_mount" \
    '{
    command: $rt,
    args: [
      "run",
      "--rm",
      "-i",
      "--read-only",
      "-u",
      $uid_gid,
      "-e",
      "HOME=/tmp",
      "-v",
      $repo_mount,
      "-v",
      $memex_mount,
      "--cap-drop",
      "ALL",
      "--security-opt",
      "no-new-privileges",
      $image_ref,
      "serve",
      "--repo",
      "/repo",
      "--memex-root",
      "/memex"
    ]
  }'
}

# container_canonical_json — the expected JSON string for the mcpServers."atlas-aci"
# entry in .mcp.json / .cursor/mcp.json.
container_canonical_json() {
  container_json_fragment "$1" "$2"
}

# ─── §4.2 Prereq checks (install path only) ───────────────────────────────
# (moved jq/yq checks to the top of check_prereqs above so they run for
#  both modes)

# ─── Host selection ───────────────────────────────────────────────────────
# If --host was supplied, honor it verbatim. Otherwise sniff cwd and
# pick only the MCP-capable hosts (claude-code, cursor, copilot).
# opencode is NOT included: its MCP capability is not confirmed in this
# spec revision (§2.1 G2).
detect_hosts_mcp() {
  local hosts=""
  if [ -f "CLAUDE.md" ] || [ -d ".claude" ]; then
    hosts="claude-code"
  fi
  if [ -d ".github" ] || [ -f "AGENTS.md" ]; then
    if [ -n "$hosts" ]; then hosts="${hosts},copilot"; else hosts="copilot"; fi
  fi
  if [ -d ".cursor" ] || [ -f ".cursorrules" ]; then
    if [ -n "$hosts" ]; then hosts="${hosts},cursor"; else hosts="cursor"; fi
  fi
  if [ -d ".codex" ] || [ -f "AGENTS.md" ]; then
    if [ -n "$hosts" ]; then hosts="${hosts},codex"; else hosts="codex"; fi
  fi
  echo "$hosts"
}

resolve_hosts() {
  if [ -n "$HOSTS_EXPLICIT" ]; then
    echo "$HOSTS_EXPLICIT"
    return 0
  fi
  detect_hosts_mcp
}

# ─── .gitignore handling (§4.4, §4.7) ─────────────────────────────────────
# Append-only, line-match on '.atlas/'. Whitespace-insensitive: we match
# the *.atlas/* token regardless of surrounding whitespace, but NOT
# trailing comments on the same line (because .gitignore does not
# support inline comments — a `.atlas/ # foo` line is literally the
# path `.atlas/ # foo`). The match rule is:
#   - Trim leading / trailing whitespace.
#   - Match exactly `.atlas/` or `.atlas` (tolerate missing slash).
gitignore_has_atlas_entry() {
  [ -f ".gitignore" ] || return 1
  # Use awk to trim and compare — portable on bash 3.2.
  awk '
    { sub(/^[ \t]+/, ""); sub(/[ \t]+$/, "");
      if ($0 == ".atlas/" || $0 == ".atlas") { found=1; exit }
    }
    END { exit (found ? 0 : 1) }
  ' .gitignore
}

ensure_gitignore() {
  if gitignore_has_atlas_entry; then
    info ".gitignore already contains .atlas/ — skipping"
    return 0
  fi
  if [ "$DRY_RUN" = "true" ]; then
    if [ -f ".gitignore" ]; then
      emit_action "MODIFY" ".gitignore"
    else
      emit_action "CREATE" ".gitignore"
    fi
    return 0
  fi
  if [ -f ".gitignore" ]; then
    # Append, preserving an existing trailing newline (add one if
    # missing). Atomic: write a new file, then mv.
    local tmp
    tmp="$(mktemp "./.atlas-aci-gi-XXXXXX")" || die "mktemp failed"
    cat ".gitignore" > "$tmp"
    # Ensure trailing newline before append.
    if [ -s "$tmp" ] && [ "$(tail -c 1 "$tmp" | od -An -c | tr -d ' ')" != "\\n" ]; then
      printf "\n" >> "$tmp"
    fi
    printf ".atlas/\n" >> "$tmp"
    mv "$tmp" ".gitignore" || { rm -f "$tmp"; die "rename .gitignore failed"; }
    ok "Appended .atlas/ to .gitignore"
  else
    atomic_write ".gitignore" ".atlas/"$'\n'
    ok "Created .gitignore with .atlas/"
  fi
}

# ─── atlas-aci index (§4.4 side effect) ───────────────────────────────────
# Runs BEFORE any MCP config writes so an index failure aborts cleanly
# (A13). Install path skips when .atlas/manifest.yaml exists (T24).
# The `index` action passes force=true to bypass that gate — re-indexing
# is the whole point.
run_index() {
  local force="${1:-false}"
  if [ "$force" != "true" ] && [ -f "./.atlas/manifest.yaml" ]; then
    info ".atlas/manifest.yaml present — skipping re-index (delete .atlas/ to force)"
    return 0
  fi
  if [ "$DRY_RUN" = "true" ]; then
    emit_action "INDEX" ".atlas/"
    return 0
  fi

  if [ "$CONTAINER_MODE" = "true" ]; then
    run_index_container
  else
    run_index_uv
  fi
}

run_index_uv() {
  say "Indexing project with atlas-aci (first run can take minutes on large repos)"
  if ! atlas-aci index \
         --repo "$PWD" \
         --langs ruby,python,javascript,typescript >&2; then
    exit_index_fail "atlas-aci index failed — aborting before MCP config writes.
  No MCP config files were modified."
  fi
  ok "Indexed → .atlas/"
}

run_index_container() {
  say "Indexing project with atlas-aci container (first run can take minutes)"

  # Pre-create .atlas/memex/ on the HOST before the docker run so the daemon
  # cannot create it as root-owned (P-A / atlas-aci-mcp-install-fix S2).
  # .atlas/ itself is also pre-created because codegraph.py writes
  # /repo/.atlas/graph.db — the container must be able to create that path,
  # and a host-owned parent directory prevents a root-owned bind-shadow.
  local memex_dir
  memex_dir="$PWD/.atlas/memex"
  if [ ! -d "$memex_dir" ]; then
    mkdir -p "$memex_dir"
  fi

  # Use registry-prefixed image reference (spec T4).
  local _index_image_ref
  _index_image_ref="$(image_full_ref)"

  # Q1 resolution (probe a — read upstream Python source):
  # atlas_aci/codegraph.py CodeGraph.__init__ sets
  #   self.db_path = self.repo / ".atlas" / "graph.db"
  # and calls self.db_path.parent.mkdir(...). The `index` CLI command does NOT
  # accept --memex-root (only `serve` does). Therefore the ONLY write target for
  # `index` is /repo/.atlas/graph.db — i.e., the repo bind mount.
  # Consequence: /repo must be WRITABLE (no :ro) for the index run.
  # /memex is mounted for future-proofing and consistency with the serve config,
  # but index does not write there. --tmpfs /tmp is NOT needed (no /tmp writes
  # observed in the source). --read-only is NOT added (removed in this fix).
  #
  # INVARIANT: on non-zero exit from this docker run, exit_index_fail is called
  # BEFORE any MCP config file is written (A13 / D6). The verbatim strings
  # "atlas-aci container index failed — aborting before MCP config writes." and
  # "No MCP config files were modified." are pinned by tests.
  #
  # :Z appended when SELinux Enforcing (private MCS relabel for bind mounts —
  # atlas-aci-container-uid-perm-fix-2026-05-05 P-B).
  local _opts_repo _opts_memex
  _opts_repo="$(_atlas_aci_volume_opts "")"
  _opts_memex="$(_atlas_aci_volume_opts "")"

  local _stderr_log
  _stderr_log="$(mktemp "./.atlas-aci-index-XXXXXX")" || die "mktemp failed"

  # Capture stdout+stderr to a tempfile so we can (a) stream it to the user
  # and (b) scan it for silent-success indicators after the run. We cannot
  # use a `cmd | tee file` pipeline under set -euo pipefail because
  # PIPESTATUS is clobbered by the `|| true` needed to suppress early exit.
  # Pattern `cmd && rc=0 || rc=$?` is bash 3.2 safe and preserves exit code
  # without triggering set -e (the whole expression evaluates to 0).
  local _rc
  "$RUNTIME" run --rm \
       -u "$(id -u):$(id -g)" \
       -e HOME=/tmp \
       -v "${PWD}:/repo${_opts_repo}" \
       -v "${PWD}/.atlas/memex:/memex${_opts_memex}" \
       --cap-drop ALL \
       --security-opt no-new-privileges \
       "$_index_image_ref" \
       index \
       --repo /repo \
       --langs ruby,python,javascript,typescript \
       >"$_stderr_log" 2>&1 && _rc=0 || _rc=$?
  cat "$_stderr_log" >&2

  if [ "$_rc" -ne 0 ]; then
    rm -f "$_stderr_log"
    exit_index_fail "atlas-aci container index failed — aborting before MCP config writes.
  No MCP config files were modified."
  fi

  # INVARIANT (atlas-aci-container-uid-perm-fix-2026-05-05 P-B): green checkmark
  # requires files_indexed > 0 OR (files_indexed == 0 AND no parse_failed). A
  # silent green on UID/SELinux failures broke real users (Fedora rootful,
  # 2026-05-05). Do NOT remove without consulting the spec.
  if grep -q 'files_indexed=0' "$_stderr_log" 2>/dev/null && \
     grep -q 'parse_failed' "$_stderr_log" 2>/dev/null; then
    rm -f "$_stderr_log"
    exit_index_fail "atlas-aci indexed 0 files but emitted parse_failed warnings — likely UID/SELinux bind-mount mismatch.
  Diagnostic: ${RUNTIME} run --rm -v \"$PWD:/repo\" -u \"$(id -u):$(id -g)\" ${_index_image_ref} sh -c 'id; ls /repo | head'
  No MCP config files were modified."
  fi

  rm -f "$_stderr_log"
  ok "Indexed → .atlas/"
}

# ─── JSON host writes: .mcp.json, .cursor/mcp.json (§4.4, §4.5) ──────────
# Idempotency primitive: object-key match on mcpServers."atlas-aci".
# jq merge on install; jq del on remove. Peer keys preserved (A11).
#
# For container mode, the canonical body uses the container fragment.
# The fail-closed comparator (wire_codex R2 equivalent for JSON) is
# implemented by comparing the current body against BOTH canonical forms.
json_server_fragment() {
  # uv-mode canonical fragment. Absolute project path baked at install
  # time — same rationale as container_json_fragment (Claude Code does
  # not expand `${workspaceFolder}`).
  jq -n --arg ws "$PWD" '{
    command: "atlas-aci",
    args: [
      "serve",
      "--repo", $ws,
      "--memex-root", ($ws + "/.atlas/memex")
    ]
  }'
}

# _json_body_matches_uv FILE — returns 0 if the atlas-aci entry in FILE
# matches the uv-mode canonical body.
_json_body_matches_uv() {
  local target="$1"
  [ -f "$target" ] || return 1
  local actual canonical
  actual="$(jq -S '.mcpServers["atlas-aci"] // empty' "$target" 2>/dev/null)"
  [ -n "$actual" ] || return 1
  canonical="$(json_server_fragment | jq -S .)"
  [ "$actual" = "$canonical" ]
}

# _json_body_matches_container FILE RUNTIME DIGEST — returns 0 if the
# atlas-aci entry matches the container canonical body for the given
# runtime and digest (registry-prefixed form, spec T4).
_json_body_matches_container() {
  local target="$1" rt="$2" digest="$3"
  [ -f "$target" ] || return 1
  local actual canonical
  actual="$(jq -S '.mcpServers["atlas-aci"] // empty' "$target" 2>/dev/null)"
  [ -n "$actual" ] || return 1
  canonical="$(container_json_fragment "$rt" "$digest" | jq -S .)"
  [ "$actual" = "$canonical" ]
}

# _json_body_matches_container_legacy FILE RUNTIME DIGEST — transition-window
# comparator (spec T4): accepts the OLD bare-ref form
# ("atlas-aci@sha256:<hex>") written by versions before 1.3.0.
# Once 1.3.0 ships and consumers re-run --container, the bare-ref body
# is overwritten; this matcher can be dropped in a follow-up release.
# DIGEST is the full sha256:... string; legacy bodies used the hex portion only.
_json_body_matches_container_legacy() {
  local target="$1" rt="$2" digest="$3"
  [ -f "$target" ] || return 1
  local actual hex_only legacy_canonical
  actual="$(jq -S '.mcpServers["atlas-aci"] // empty' "$target" 2>/dev/null)"
  [ -n "$actual" ] || return 1
  # Strip "sha256:" prefix to get bare hex for legacy comparison.
  hex_only="$(printf '%s' "$digest" | sed 's/^sha256://')"
  legacy_canonical="$(jq -n --arg rt "$rt" --arg hex "$hex_only" '{
    command: $rt,
    args: [
      "run", "--rm", "-i", "--read-only",
      "-v", "${workspaceFolder}:/repo:ro",
      "-v", "${workspaceFolder}/.atlas/memex:/memex",
      ("atlas-aci@sha256:" + $hex),
      "serve", "--repo", "/repo", "--memex-root", "/memex"
    ]
  }' | jq -S .)"
  [ "$actual" = "$legacy_canonical" ]
}

json_install() {
  local target="$1" existed
  if [ -f "$target" ]; then existed=true; else existed=false; fi

  if [ "$DRY_RUN" = "true" ]; then
    if [ "$existed" = "true" ]; then
      emit_action "MODIFY" "$target"
    else
      emit_action "CREATE" "$target"
    fi
    return 0
  fi

  local dir tmp merged base frag
  dir="$(dirname "$target")"
  mkdir -p "$dir"
  tmp="$(mktemp "${dir}/.atlas-aci-mcp-XXXXXX")" || die "mktemp failed"

  if [ "$CONTAINER_MODE" = "true" ]; then
    frag="$(container_json_fragment "$RUNTIME" "$LOCAL_DIGEST")"
  else
    frag="$(json_server_fragment)"
  fi

  if [ "$existed" = "true" ]; then
    # Validate existing JSON before we touch it.
    if ! jq empty "$target" >/dev/null 2>&1; then
      rm -f "$tmp"
      die "Existing $target is not valid JSON — refusing to overwrite. Fix manually."
    fi

    # R2 mitigation for JSON: if atlas-aci entry exists and matches neither
    # uv canonical nor container canonical (including legacy bare-ref form),
    # refuse (fail-closed). Spec T4: accept BOTH registry-prefixed AND legacy
    # bare-ref form during the 1.3.0 transition window.
    if jq -e '.mcpServers["atlas-aci"] // empty' "$target" >/dev/null 2>&1; then
      local _uv_match=false _ct_match=false _ct_legacy_match=false
      _json_body_matches_uv "$target" && _uv_match=true || true
      if [ "$CONTAINER_MODE" = "true" ]; then
        _json_body_matches_container "$target" "$RUNTIME" "$LOCAL_DIGEST" && _ct_match=true || true
        _json_body_matches_container_legacy "$target" "$RUNTIME" "$LOCAL_DIGEST" && _ct_legacy_match=true || true
      fi
      # If the existing entry matches the intended canonical (or legacy), it's acceptable.
      if [ "$CONTAINER_MODE" = "true" ] && [ "$_ct_match" = "true" ]; then
        rm -f "$tmp"
        info "$target already has correct container atlas-aci entry — skipping"
        return 0
      fi
      if [ "$CONTAINER_MODE" = "true" ] && [ "$_ct_legacy_match" = "true" ]; then
        # Legacy bare-ref form detected — overwrite with registry-prefixed form.
        info "$target has legacy bare-ref container entry — upgrading to registry-prefixed form"
      elif [ "$CONTAINER_MODE" = "false" ] && [ "$_uv_match" = "true" ]; then
        rm -f "$tmp"
        info "$target already has correct uv atlas-aci entry — skipping"
        return 0
      elif [ "$_uv_match" = "false" ] && [ "$_ct_match" = "false" ] && [ "$_ct_legacy_match" = "false" ]; then
        # Neither matches — this is a hand-edited or cross-mode body.
        # We still overwrite in cross-mode (uv→container or container→uv)
        # because the user explicitly requested the new mode. We only
        # refuse on truly foreign edits (neither canonical).
        if [ "$CONTAINER_MODE" = "true" ]; then
          # Container install: also accept if it was a valid uv canonical
          # (mode switch scenario — G11).  Since _uv_match=false here,
          # the body is genuinely foreign.
          warn "$target: atlas-aci entry exists but matches neither uv nor container canonical."
          warn "Overwriting (to change runtime mode, this is expected)."
        else
          warn "$target: atlas-aci entry exists but matches neither known canonical."
          warn "Overwriting with uv canonical."
        fi
      fi
    fi

    base="$(cat "$target")"
    merged="$(echo "$base" | jq --argjson s "$frag" \
      '.mcpServers = (.mcpServers // {}) | .mcpServers["atlas-aci"] = $s' \
      --indent 2)"
  else
    merged="$(jq -n --argjson s "$frag" \
      '{mcpServers: {"atlas-aci": $s}}' --indent 2)"
  fi

  printf "%s\n" "$merged" > "$tmp" || { rm -f "$tmp"; die "write failed: $tmp"; }
  mv "$tmp" "$target" || { rm -f "$tmp"; die "rename failed: $target"; }

  if [ "$existed" = "true" ]; then
    ok "Merged atlas-aci into $target"
  else
    ok "Created $target"
  fi
}

json_remove() {
  local target="$1"
  [ -f "$target" ] || { info "$target absent — nothing to remove"; return 0; }

  if ! jq empty "$target" >/dev/null 2>&1; then
    warn "$target is not valid JSON — skipping (manual fix required)"
    return 0
  fi

  # If atlas-aci key not present, nothing to do.
  if ! jq -e '.mcpServers["atlas-aci"] // empty' "$target" >/dev/null 2>&1; then
    info "$target has no mcpServers.atlas-aci entry — nothing to remove"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$target"
    return 0
  fi

  local dir tmp after
  dir="$(dirname "$target")"
  tmp="$(mktemp "${dir}/.atlas-aci-mcp-XXXXXX")" || die "mktemp failed"
  after="$(jq 'del(.mcpServers["atlas-aci"])' "$target" --indent 2)"
  # If mcpServers is now an empty object, leave the empty object in
  # place rather than deleting the key — some hosts expect the key to
  # exist. This is deliberate: do not reshape beyond what we added.
  printf "%s\n" "$after" > "$tmp" || { rm -f "$tmp"; die "write failed: $tmp"; }
  mv "$tmp" "$target" || { rm -f "$tmp"; die "rename failed: $target"; }
  ok "Removed atlas-aci from $target"
}

# ─── Copilot host writes: .github/agents/*.agent.md (§4.4, §4.6) ─────────
# YAML frontmatter split: the file is a Markdown document with a leading
# `---\n<yaml>\n---\n<markdown body>`. We operate on the frontmatter in
# isolation (yq eval), then splice it back. Bodies are preserved byte-
# for-byte (T15).
#
# Idempotency: list-entry match on `name: atlas-aci` under
# tools.mcp_servers. yq replaces in place; peer entries preserved (T9c).
copilot_split_file() {
  # Reads $1; writes frontmatter to $2, body to $3. Returns 0 if a
  # well-formed frontmatter was found, 1 otherwise (caller treats as
  # "skip with a warning" per R6).
  local src="$1" fm_out="$2" body_out="$3"
  # A valid frontmatter is: line 1 is exactly '---', there's a second
  # '---' line later, both matched exactly.
  if [ "$(head -n 1 "$src")" != "---" ]; then
    return 1
  fi
  # Find the closing '---' line number (starting from line 2).
  local close_ln
  close_ln="$(awk 'NR>1 && $0=="---" { print NR; exit }' "$src")"
  [ -n "$close_ln" ] || return 1
  # Write frontmatter lines 2..(close_ln-1) to fm_out.
  local inner_end=$((close_ln - 1))
  if [ "$inner_end" -lt 2 ]; then
    # Empty frontmatter — yq can handle `{}` but we emit a blank.
    : > "$fm_out"
  else
    sed -n "2,${inner_end}p" "$src" > "$fm_out"
  fi
  # Body is everything after the closing ---.
  local body_start=$((close_ln + 1))
  # tail's -n +N means "starting at line N". If body is empty, produce
  # an empty file (not an error).
  tail -n "+${body_start}" "$src" > "$body_out" || true
  return 0
}

copilot_list_all_agents() {
  # Emit every .agent.md under .github/agents/ (newline-separated).
  # Safe if the dir or files don't exist.
  if [ ! -d ".github/agents" ]; then return 0; fi
  # shellcheck disable=SC2012
  ls -1 .github/agents 2>/dev/null | awk '/\.agent\.md$/ {print ".github/agents/"$0}'
}

_copilot_command_array() {
  # $1 = mode: "uv" or "container"
  # $2 (container only) = runtime
  # $3 (container only) = digest (full sha256:... string)
  # Absolute project path baked at install time — see notes on
  # container_json_fragment for why.
  if [ "$1" = "uv" ]; then
    jq -n --arg ws "$PWD" \
      '["atlas-aci", "serve", "--repo", $ws, "--memex-root", ($ws + "/.atlas/memex")]'
  else
    local rt="$2" digest="$3"
    # Registry-prefixed image reference (spec T4).
    local image_ref="${ATLAS_ACI_IMAGE_REF}@${digest}"
    jq -n --arg rt "$rt" --arg image_ref "$image_ref" --arg ws "$PWD" \
      '[$rt, "run", "--rm", "-i", "--read-only",
        "-e", "HOME=/tmp",
        "-v", ($ws + ":/repo:ro"),
        "-v", ($ws + "/.atlas/memex:/memex"),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        $image_ref,
        "serve", "--repo", "/repo", "--memex-root", "/memex"]'
  fi
}

copilot_install_one() {
  local target="$1"
  local fm body merged rebuilt tmp
  fm="$(mktemp "./.atlas-aci-fm-XXXXXX")" || die "mktemp failed"
  body="$(mktemp "./.atlas-aci-body-XXXXXX")" || die "mktemp failed"

  if ! copilot_split_file "$target" "$fm" "$body"; then
    rm -f "$fm" "$body"
    warn "$target has no YAML frontmatter — skipping (R6 fail-closed)"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$target"
    rm -f "$fm" "$body"
    return 0
  fi

  if [ "$CONTAINER_MODE" = "true" ]; then
    local cmd_array
    cmd_array="$(_copilot_command_array container "$RUNTIME" "$LOCAL_DIGEST")"
    merged="$(yq eval '
      .tools = (.tools // {}) |
      .tools.mcp_servers = (.tools.mcp_servers // []) |
      .tools.mcp_servers = ([.tools.mcp_servers[] | select(.name != "atlas-aci")] + [{
        "name": "atlas-aci",
        "transport": "stdio",
        "command": env(CMD_ARRAY)
      }])
    ' "$fm" 2>/dev/null)" || {
      # yq env() approach may not work on all versions; use a temp file approach
      local cmd_tmp
      cmd_tmp="$(mktemp "./.atlas-aci-cmd-XXXXXX")"
      printf '%s' "$cmd_array" > "$cmd_tmp"
      merged="$(CMD_ARRAY="$cmd_array" yq eval '
        .tools = (.tools // {}) |
        .tools.mcp_servers = (.tools.mcp_servers // []) |
        .tools.mcp_servers = ([.tools.mcp_servers[] | select(.name != "atlas-aci")] + [{
          "name": "atlas-aci",
          "transport": "stdio",
          "command": env(CMD_ARRAY)
        }])
      ' "$fm")" || { rm -f "$fm" "$body" "$cmd_tmp"; die "yq merge failed on $target"; }
      rm -f "$cmd_tmp"
    }
  else
    # uv-mode now follows the same env-injected pattern as the container
    # branch above, so the absolute project path is interpolated by jq
    # rather than baked into a literal yq expression.
    local cmd_array
    cmd_array="$(_copilot_command_array uv)"
    merged="$(CMD_ARRAY="$cmd_array" yq eval '
      .tools = (.tools // {}) |
      .tools.mcp_servers = (.tools.mcp_servers // []) |
      .tools.mcp_servers = ([.tools.mcp_servers[] | select(.name != "atlas-aci")] + [{
        "name": "atlas-aci",
        "transport": "stdio",
        "command": env(CMD_ARRAY)
      }])
    ' "$fm")" || { rm -f "$fm" "$body"; die "yq merge failed on $target"; }
  fi

  # Splice back. Body preservation: we re-emit the body byte-for-byte.
  tmp="$(mktemp "./.atlas-aci-agent-XXXXXX")" || { rm -f "$fm" "$body"; die "mktemp failed"; }
  {
    printf -- "---\n"
    printf "%s\n" "$merged"
    printf -- "---\n"
    cat "$body"
  } > "$tmp" || { rm -f "$fm" "$body" "$tmp"; die "rebuild failed for $target"; }

  # Sanity: trailing newline in body was preserved by `cat`. Sanity:
  # yq output never carries a leading `---` (we strip that role).
  mv "$tmp" "$target" || { rm -f "$fm" "$body" "$tmp"; die "rename failed: $target"; }
  rm -f "$fm" "$body"
  ok "Merged atlas-aci into $target"
}

copilot_remove_one() {
  local target="$1"
  local fm body merged tmp
  fm="$(mktemp "./.atlas-aci-fm-XXXXXX")" || die "mktemp failed"
  body="$(mktemp "./.atlas-aci-body-XXXXXX")" || die "mktemp failed"

  if ! copilot_split_file "$target" "$fm" "$body"; then
    rm -f "$fm" "$body"
    info "$target has no YAML frontmatter — nothing to remove"
    return 0
  fi

  # If no atlas-aci entry exists, no-op.
  if ! yq eval '.tools.mcp_servers[]? | select(.name == "atlas-aci")' "$fm" \
       | grep -q '.' ; then
    rm -f "$fm" "$body"
    info "$target has no atlas-aci MCP entry — nothing to remove"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$target"
    rm -f "$fm" "$body"
    return 0
  fi

  merged="$(yq eval '
    .tools.mcp_servers = ([.tools.mcp_servers[] | select(.name != "atlas-aci")])
  ' "$fm")" || { rm -f "$fm" "$body"; die "yq del failed on $target"; }

  tmp="$(mktemp "./.atlas-aci-agent-XXXXXX")" || { rm -f "$fm" "$body"; die "mktemp failed"; }
  {
    printf -- "---\n"
    printf "%s\n" "$merged"
    printf -- "---\n"
    cat "$body"
  } > "$tmp" || { rm -f "$fm" "$body" "$tmp"; die "rebuild failed for $target"; }
  mv "$tmp" "$target" || { rm -f "$fm" "$body" "$tmp"; die "rename failed: $target"; }
  rm -f "$fm" "$body"
  ok "Removed atlas-aci from $target"
}

# ─── Codex host writes: .codex/config.toml (§4.4, D4, D6) ───────────────
# Idempotency primitive: line-bounded awk slice on [mcp_servers.atlas-aci]
# table heading. Terminates at the next [*] or [[*]] heading or EOF.
# Atomic tmpfile + mv identical to JSON/YAML branches. POSIX awk only
# (no gawk extensions). Bash 3.2 safe throughout.
#
# Canonical TOML body the writer produces:
#   uv mode (3 lines):
#     [mcp_servers.atlas-aci]
#     command = "atlas-aci"
#     args = ["serve", "--repo", "."]
#
#   container mode (3 lines):
#     [mcp_servers.atlas-aci]
#     command = "<RUNTIME>"
#     args = ["run", "--rm", "-i", "--read-only", "-v",
#             "<ABS_PROJECT_PATH>:/repo:ro", "-v",
#             "<ABS_PROJECT_PATH>/.atlas/memex:/memex",
#             "atlas-aci@sha256:<DIGEST>", "serve", "--repo",
#             "/repo", "--memex-root", "/memex"]
#
#   <ABS_PROJECT_PATH> is the cwd at install time (literal absolute
#   path baked into the file). Earlier releases used `${workspaceFolder}`
#   here too — see container_json_fragment for the rationale on the
#   switch (Claude Code does not expand that placeholder).
#
# R2 mitigation: if the file already contains [mcp_servers.atlas-aci] and
# its body deviates from BOTH the uv canonical AND the container canonical
# for the current runtime+digest, refuse and warn.

_CODEX_TOML="./.codex/config.toml"

# _codex_canonical_body_uv — canonical TOML body for uv mode (no heading).
_codex_canonical_body_uv() {
  printf 'command = "atlas-aci"\nargs = ["serve", "--repo", "."]\n'
}

# _codex_canonical_body_container RUNTIME DIGEST — canonical TOML body
# for container mode (no heading). Absolute project path baked at
# install time — same rationale as container_json_fragment.
# DIGEST is the full sha256:... string; image ref is registry-prefixed
# (spec T4): ghcr.io/rynaro/atlas-aci@sha256:<hex>.
_codex_canonical_body_container() {
  local rt="$1" digest="$2"
  local image_ref="${ATLAS_ACI_IMAGE_REF}@${digest}"
  printf 'command = "%s"\n' "$rt"
  printf 'args = ["run", "--rm", "-i", "--read-only", "-e", "HOME=/tmp", "-v", "%s:/repo:ro", "-v", "%s/.atlas/memex:/memex", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "%s", "serve", "--repo", "/repo", "--memex-root", "/memex"]\n' "$PWD" "$PWD" "$image_ref"
}

# _codex_canonical_body_container_legacy RUNTIME DIGEST — the OLD bare-ref
# TOML body written before 1.3.0 (spec T4 transition-window comparator).
# DIGEST is the full sha256:... string; legacy bodies used bare hex and
# the ${workspaceFolder} placeholder (pre-1.2.1 ATLAS). Used only by the
# R2 comparator to detect and upgrade stale installs.
_codex_canonical_body_container_legacy() {
  local rt="$1" digest="$2"
  local hex_only
  hex_only="$(printf '%s' "$digest" | sed 's/^sha256://')"
  printf 'command = "%s"\n' "$rt"
  printf 'args = ["run", "--rm", "-i", "--read-only", "-v", "${workspaceFolder}:/repo:ro", "-v", "${workspaceFolder}/.atlas/memex:/memex", "atlas-aci@sha256:%s", "serve", "--repo", "/repo", "--memex-root", "/memex"]\n' "$hex_only"
}

# _codex_canonical_body — returns the canonical body for the current mode.
_codex_canonical_body() {
  if [ "$CONTAINER_MODE" = "true" ]; then
    _codex_canonical_body_container "$RUNTIME" "$LOCAL_DIGEST"
  else
    _codex_canonical_body_uv
  fi
}

# _codex_table_body FILE — extracts the body lines of [mcp_servers.atlas-aci]
# from FILE (if present). Prints only the body (not the heading itself).
# Prints nothing if the heading is absent. POSIX awk only.
_codex_table_body() {
  awk '
    /^\[mcp_servers\.atlas-aci\]\r?$/ { in_block=1; next }
    in_block {
      if (/^\[/) { exit }
      # Strip CR for CRLF tolerance.
      gsub(/\r$/, "")
      print
    }
  ' "$1"
}

# _codex_has_table FILE — returns 0 if [mcp_servers.atlas-aci] heading found.
_codex_has_table() {
  grep -q '^\[mcp_servers\.atlas-aci\]' "$1" 2>/dev/null
}

wire_codex() {
  local target="$_CODEX_TOML"
  local existed
  if [ -f "$target" ]; then existed=true; else existed=false; fi

  if [ "$DRY_RUN" = "true" ]; then
    if [ "$existed" = "true" ]; then
      emit_action "MODIFY" "$target"
    else
      emit_action "CREATE" "$target"
    fi
    return 0
  fi

  mkdir -p "$(dirname "$target")"

  # R2 mitigation: if the heading already exists, compare against all
  # canonical forms (registry-prefixed + legacy bare-ref for transition
  # window, spec T4). Accept if any matches. Refuse only if none match.
  if [ "$existed" = "true" ] && _codex_has_table "$target"; then
    local actual_body uv_body ct_body ct_legacy_body
    actual_body="$(_codex_table_body "$target")"
    uv_body="$(_codex_canonical_body_uv)"
    if [ "$CONTAINER_MODE" = "true" ]; then
      ct_body="$(_codex_canonical_body_container "$RUNTIME" "$LOCAL_DIGEST")"
      ct_legacy_body="$(_codex_canonical_body_container_legacy "$RUNTIME" "$LOCAL_DIGEST")"
      if [ "$actual_body" = "$ct_body" ]; then
        # Already correctly installed as container mode (registry-prefixed).
        info "codex: [mcp_servers.atlas-aci] already correct (container) in $target — skipping"
        return 0
      fi
      if [ "$actual_body" = "$ct_legacy_body" ]; then
        # Legacy bare-ref form — upgrade to registry-prefixed form.
        info "codex: upgrading legacy bare-ref container entry to registry-prefixed form in $target"
      elif [ "$actual_body" = "$uv_body" ]; then
        # Was uv mode — now switching to container. Allow overwrite.
        info "codex: switching from uv mode to container mode in $target"
      else
        # Body matches neither canonical — fail-closed.
        warn "codex: .codex/config.toml has [mcp_servers.atlas-aci] with non-canonical body."
        warn "codex: Refusing to overwrite. Run with --remove first, then re-install."
        warn "codex: Existing body:"
        printf '%s\n' "$actual_body" >&2
        return 0
      fi
    else
      # uv mode
      if [ "$actual_body" = "$uv_body" ]; then
        info "codex: [mcp_servers.atlas-aci] already correct (uv) in $target — skipping"
        return 0
      fi
      # Check if it matches container canonical for any runtime — we
      # don't have RUNTIME set in uv mode but we can do a looser check.
      # For simplicity: if it doesn't match uv canonical, refuse.
      warn "codex: .codex/config.toml already has [mcp_servers.atlas-aci] with a non-canonical body."
      warn "codex: Refusing to overwrite. Run with --remove first, then re-install."
      warn "codex: Existing body:"
      printf '%s\n' "$actual_body" >&2
      return 0
    fi
    # Fall through to rewrite (mode switch).
  fi

  local tmp
  tmp="$(mktemp "./.atlas-aci-codex-XXXXXX")" || die "mktemp failed"

  if [ "$existed" = "true" ]; then
    # If we're here after mode-switch detection, remove the existing block
    # first so we can re-append the canonical one.
    if _codex_has_table "$target"; then
      awk '
        /^\[mcp_servers\.atlas-aci\]\r?$/ { in_block=1; next }
        in_block {
          if (/^\[/) { in_block=0 }
          else { next }
        }
        { gsub(/\r$/, ""); print }
      ' "$target" > "$tmp" || { rm -f "$tmp"; die "awk mode-switch remove failed: $target"; }
      mv "$tmp" "$target" || { rm -f "$tmp"; die "atomic rename failed: $target (mode-switch)"; }
      tmp="$(mktemp "./.atlas-aci-codex-XXXXXX")" || die "mktemp failed"
    fi

    # Rewrite: copy all lines, then append canonical block.
    awk '{ gsub(/\r$/, ""); print }' "$target" > "$tmp" || {
      rm -f "$tmp"; die "awk copy failed: $target"
    }
    # Add trailing newline if the file is non-empty and lacks one.
    if [ -s "$tmp" ]; then
      local last_char
      last_char="$(tail -c 1 "$tmp" | od -An -c | tr -d ' \n')"
      if [ "$last_char" != "\\n" ]; then
        printf '\n' >> "$tmp"
      fi
    fi
    {
      printf '[mcp_servers.atlas-aci]\n'
      _codex_canonical_body
    } >> "$tmp" || { rm -f "$tmp"; die "write codex block failed"; }
    mv "$tmp" "$target" || { rm -f "$tmp"; die "atomic rename failed: $target"; }
    ok "Merged atlas-aci into $target"
  else
    # Fresh file: emit only our block.
    {
      printf '[mcp_servers.atlas-aci]\n'
      _codex_canonical_body
    } > "$tmp" || { rm -f "$tmp"; die "write failed: $tmp"; }
    mv "$tmp" "$target" || { rm -f "$tmp"; die "atomic rename failed: $target"; }
    ok "Created $target"
  fi
}

unwire_codex() {
  local target="$_CODEX_TOML"
  [ -f "$target" ] || { info "$target absent — nothing to remove"; return 0; }

  if ! _codex_has_table "$target"; then
    info "$target has no [mcp_servers.atlas-aci] entry — nothing to remove"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$target"
    return 0
  fi

  local tmp
  tmp="$(mktemp "./.atlas-aci-codex-XXXXXX")" || die "mktemp failed"

  # Awk: copy all lines EXCEPT those in the [mcp_servers.atlas-aci] block.
  # Terminator: next [*] or [[*]] heading or EOF.
  # CRLF: strip \r on input; emit Unix LF on output.
  awk '
    /^\[mcp_servers\.atlas-aci\]\r?$/ { in_block=1; next }
    in_block {
      if (/^\[/) { in_block=0 }
      else { next }
    }
    { gsub(/\r$/, ""); print }
  ' "$target" > "$tmp" || { rm -f "$tmp"; die "awk remove failed: $target"; }

  # Ensure the result ends with a newline (handles no-trailing-newline input).
  if [ -s "$tmp" ]; then
    local last_char
    last_char="$(tail -c 1 "$tmp" | od -An -c | tr -d ' \n')"
    if [ "$last_char" != "\\n" ]; then
      printf '\n' >> "$tmp"
    fi
  fi

  mv "$tmp" "$target" || { rm -f "$tmp"; die "atomic rename failed: $target"; }
  ok "Removed atlas-aci from $target"
}

# ─── Claude Code subagent tools allowlist ─────────────────────────────────
# .claude/agents/atlas.md ships from `install.sh` with a `tools:` line
# that grants only Read/Grep/Glob/Bash. Without this extension, even
# though the atlas-aci MCP server is wired into .mcp.json, Claude Code
# refuses to expose its tools to the ATLAS subagent — the agent silently
# falls back to native Read+Grep instead of using indexed-graph queries.
#
# This module rewrites just the `tools:` line in the YAML frontmatter on
# install/remove. Idempotent (same canonical strings produce byte-
# identical output). Body of the file is untouched.
#
# Canonical tool lists are kept here (and not in install.sh) so the
# install→aci-install→aci-remove cycle is symmetric:
#   - install.sh writes the BASE list.
#   - aci wire extends it to the WITH-MCP list.
#   - aci remove restores BASE.
# If install.sh's list ever changes, _subagent_canonical_tools_base
# below must follow.

_SUBAGENT_FILE="./.claude/agents/atlas.md"

_subagent_canonical_tools_base() {
  printf 'Read, Grep, Glob, Write, Bash(rg:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(shasum:*), Bash(wc:*)'
}

_subagent_canonical_tools_with_mcp() {
  printf 'Read, Grep, Glob, Write, Bash(rg:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(shasum:*), Bash(wc:*), '
  printf 'mcp__atlas-aci__view_file, mcp__atlas-aci__list_dir, '
  printf 'mcp__atlas-aci__search_text, mcp__atlas-aci__search_symbol, '
  printf 'mcp__atlas-aci__graph_query, mcp__atlas-aci__test_dry_run, '
  printf 'mcp__atlas-aci__memex_read'
}

# _subagent_set_tools_line FILE NEW_TOOLS
# Replace the `tools: …` line in the first YAML frontmatter block of
# FILE with `tools: NEW_TOOLS`. Atomic via temp file + mv. No-op if
# the file does not exist (subagent absent → not our concern). Bash-
# 3.2-safe; awk-only, no yq dep.
_subagent_set_tools_line() {
  local file="$1" new_tools="$2"
  [ -f "$file" ] || return 0
  local dir tmp
  dir="$(dirname "$file")"
  tmp="$(mktemp "${dir}/.atlas-aci-subagent-XXXXXX")" || die "mktemp failed in $dir"
  awk -v new_tools="$new_tools" '
    BEGIN { fm_count = 0; replaced = 0 }
    /^---$/ { fm_count++; print; next }
    fm_count == 1 && /^tools:/ && !replaced {
      print "tools: " new_tools
      replaced = 1
      next
    }
    { print }
  ' "$file" > "$tmp" || { rm -f "$tmp"; die "subagent tools rewrite failed: $file"; }
  mv "$tmp" "$file" || { rm -f "$tmp"; die "atomic rename failed: $file"; }
}

# subagent_extend_tools — called after json_install for claude-code.
# Adds the seven mcp__atlas-aci__* entries to the subagent allowlist.
# Idempotent.
subagent_extend_tools() {
  [ -f "$_SUBAGENT_FILE" ] || {
    info "claude-code subagent file absent — skipping tools extension ($_SUBAGENT_FILE)"
    return 0
  }
  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$_SUBAGENT_FILE"
    return 0
  fi
  _subagent_set_tools_line "$_SUBAGENT_FILE" "$(_subagent_canonical_tools_with_mcp)"
}

# subagent_restore_tools — called after json_remove for claude-code.
# Restores the BASE allowlist (matches install.sh).
subagent_restore_tools() {
  [ -f "$_SUBAGENT_FILE" ] || return 0
  if [ "$DRY_RUN" = "true" ]; then
    emit_action "MODIFY" "$_SUBAGENT_FILE"
    return 0
  fi
  _subagent_set_tools_line "$_SUBAGENT_FILE" "$(_subagent_canonical_tools_base)"
}

# ─── Per-host dispatch ────────────────────────────────────────────────────
apply_host_install() {
  case "$1" in
    claude-code)
      json_install "./.mcp.json"
      subagent_extend_tools
      ;;
    cursor)      json_install "./.cursor/mcp.json" ;;
    codex)       wire_codex ;;
    copilot)
      # If no .agent.md files exist, skip with info (T14).
      local agents files_found=false
      agents="$(copilot_list_all_agents)"
      if [ -z "$agents" ]; then
        info "copilot: no .github/agents/*.agent.md files found — skipping"
        return 0
      fi
      # IFS-split on newlines, bash-3.2-safe.
      local old_IFS="$IFS"
      IFS='
'
      for agent in $agents; do
        files_found=true
        IFS="$old_IFS"
        copilot_install_one "$agent"
        IFS='
'
      done
      IFS="$old_IFS"
      [ "$files_found" = "true" ] || info "copilot: no agent files processed"
      ;;
    *) warn "Unknown host: $1 (skipping)" ;;
  esac
}

apply_host_remove() {
  case "$1" in
    claude-code)
      json_remove "./.mcp.json"
      subagent_restore_tools
      ;;
    cursor)      json_remove "./.cursor/mcp.json" ;;
    codex)       unwire_codex ;;
    copilot)
      local agents
      agents="$(copilot_list_all_agents)"
      if [ -z "$agents" ]; then
        info "copilot: no .github/agents/*.agent.md files found — nothing to remove"
        return 0
      fi
      local old_IFS="$IFS"
      IFS='
'
      for agent in $agents; do
        IFS="$old_IFS"
        copilot_remove_one "$agent"
        IFS='
'
      done
      IFS="$old_IFS"
      ;;
    *) warn "Unknown host: $1 (skipping)" ;;
  esac
}

# ─── Idempotency check for container mode (G18) ───────────────────────────
# Returns 0 (noop) if all target host configs already have the exact
# container canonical body for the current RUNTIME + LOCAL_DIGEST.
# Returns 1 if any host config needs updating.
_container_configs_up_to_date() {
  local hosts_csv="$1"
  local old_IFS="$IFS"
  IFS=','
  for h in $hosts_csv; do
    IFS="$old_IFS"
    case "$h" in
      claude-code)
        [ -f ".mcp.json" ] || { IFS=','; return 1; }
        _json_body_matches_container ".mcp.json" "$RUNTIME" "$LOCAL_DIGEST" || { IFS=','; return 1; }
        ;;
      cursor)
        [ -f ".cursor/mcp.json" ] || { IFS=','; return 1; }
        _json_body_matches_container ".cursor/mcp.json" "$RUNTIME" "$LOCAL_DIGEST" || { IFS=','; return 1; }
        ;;
      codex)
        [ -f "$_CODEX_TOML" ] || { IFS=','; return 1; }
        local actual_body ct_body
        actual_body="$(_codex_table_body "$_CODEX_TOML")"
        ct_body="$(_codex_canonical_body_container "$RUNTIME" "$LOCAL_DIGEST")"
        [ "$actual_body" = "$ct_body" ] || { IFS=','; return 1; }
        ;;
      copilot)
        # Copilot check: at least one agent file exists and has atlas-aci entry.
        # For simplicity in idempotency, if agents exist we always re-run
        # (yq install_one is idempotent itself). Return 1 to proceed.
        { IFS=','; return 1; }
        ;;
    esac
    IFS=','
  done
  IFS="$old_IFS"
  return 0
}

# ─── Main ─────────────────────────────────────────────────────────────────
# ─── `index` action: re-run atlas-aci index against the current project ──
# Reuses the existing installation — does NOT rebuild the image, does NOT
# touch MCP configs or .gitignore. Mode (host vs container) is auto-
# detected from what's installed locally; positional [runtime] forces it.
#
# Auto-detect order (when no runtime positional is given):
#   1. command -v atlas-aci → host mode (simpler, faster, no daemon)
#   2. Digest-first: docker image inspect @sha256:... (P3 — no tag needed)
#   3. Ditto podman
#   4. Tag fallback: docker/podman images grep for atlas-aci:<ATLAS_VERSION>
#      (covers self-built images with a version tag)
#   5. Stale-tag probe: any older tag → distinguished exit-5 mismatch message
#   6. Neither → exit 5 with clear "no installation detected" message
detect_index_mode() {
  local _digest_ref
  _digest_ref="${ATLAS_ACI_IMAGE_REF}@${ATLAS_ACI_IMAGE_DIGEST}"

  if [ "$CONTAINER_MODE" = "true" ]; then
    # Runtime was set from positional; verify it's on PATH.
    if ! command -v "$RUNTIME" >/dev/null 2>&1; then
      err "atlas-aci: requested runtime '$RUNTIME' is not on PATH. Install it or omit the positional to use host mode."
      exit 7
    fi
    # Verify the image is present (digest-first, then tag fallback).
    if "$RUNTIME" image inspect "$_digest_ref" >/dev/null 2>&1; then
      return 0
    fi
    if "$RUNTIME" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
         | grep -q "^$(image_tag)$"; then
      return 0
    fi
    exit_prereq "atlas-aci: runtime '$RUNTIME' has no atlas-aci image.
  Digest probe: ${_digest_ref}
  Tag probe:    $(image_tag)
  Fix:    eidolons atlas aci wire $RUNTIME                       # build + wire
       or eidolons mcp atlas-aci pull                            # pull pre-built image"
  fi

  # No runtime positional — auto-detect.

  # 1. Host mode: atlas-aci binary on PATH.
  if command -v atlas-aci >/dev/null 2>&1; then
    CONTAINER_MODE=false
    return 0
  fi

  # 2+3. Digest-first probe across available runtimes.
  for rt in docker podman; do
    if command -v "$rt" >/dev/null 2>&1; then
      if "$rt" image inspect "$_digest_ref" >/dev/null 2>&1; then
        CONTAINER_MODE=true
        RUNTIME="$rt"
        return 0
      fi
    fi
  done

  # 4. Tag fallback: self-built images with atlas-aci:<ATLAS_VERSION> tag.
  for rt in docker podman; do
    if command -v "$rt" >/dev/null 2>&1; then
      if "$rt" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
           | grep -q "^$(image_tag)$"; then
        CONTAINER_MODE=true
        RUNTIME="$rt"
        return 0
      fi
    fi
  done

  # 5. Stale-tag probe: any older atlas-aci tag present → distinguished message.
  local _found_tag=""
  for rt in docker podman; do
    if command -v "$rt" >/dev/null 2>&1; then
      _found_tag="$("$rt" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
        | grep "^${ATLAS_ACI_IMAGE_REF}:" | head -n1)" || true
      if [ -n "$_found_tag" ]; then
        local _found_ver="${_found_tag#${ATLAS_ACI_IMAGE_REF}:}"
        exit_prereq "atlas-aci: image present but version mismatch (found ${_found_ver}, expected ${ATLAS_VERSION}). Re-run 'eidolons mcp atlas-aci pull' to refresh."
      fi
    fi
  done

  # 6. Nothing found anywhere.
  exit_prereq "atlas-aci: no installation detected.
  Tried:  command -v atlas-aci                              (host mode)
          docker image inspect ${ATLAS_ACI_IMAGE_REF}@<digest>
          podman image inspect ${ATLAS_ACI_IMAGE_REF}@<digest>
          docker/podman images ${ATLAS_ACI_IMAGE_REF}:<tag>  (tag fallback)
  Fix:    eidolons atlas aci wire                           # host mode
       or eidolons atlas aci wire <docker|podman>           # container mode
       or eidolons mcp atlas-aci pull                       # pull pre-built image"
}

main_index() {
  detect_index_mode

  # Container mode uses the registry digest constant as LOCAL_DIGEST for
  # the @sha256:<digest> pin in run_index_container (spec T4: registry
  # digest, not local image ID).
  if [ "$CONTAINER_MODE" = "true" ] && [ "$DRY_RUN" != "true" ]; then
    LOCAL_DIGEST="$(capture_registry_digest)" || \
      exit_prereq "atlas-aci: $(image_tag) image disappeared from $RUNTIME between detection and indexing.
  Fix: eidolons atlas aci wire $RUNTIME"
  fi

  if [ "$CONTAINER_MODE" = "true" ]; then
    info "Mode: container ($RUNTIME, $(image_tag))"
  else
    info "Mode: host (atlas-aci on PATH)"
  fi
  [ "$DRY_RUN" = "true" ] && info "Dry-run mode — no files will be modified"

  # force=true so we bypass the install-path .atlas/manifest.yaml gate.
  run_index true

  [ "$DRY_RUN" = "true" ] && return 0
  ok "Re-index complete."
}

main_wire() {
  check_prereqs

  local hosts_csv
  hosts_csv="$(resolve_hosts)"
  if [ -z "$hosts_csv" ]; then
    exit_no_host "No MCP-capable host detected in this project, and --host was not supplied.
  Detectable hosts: claude-code, cursor, copilot
  Fix: run with e.g. --host claude-code"
  fi

  info "Hosts: $hosts_csv"
  [ "$DRY_RUN" = "true" ] && info "Dry-run mode — no files will be modified"

  if [ "$CONTAINER_MODE" = "true" ]; then
    main_wire_container "$hosts_csv"
  else
    main_wire_uv "$hosts_csv"
  fi
}

main_wire_uv() {
  local hosts_csv="$1"
  # Ordering: .gitignore first (cheapest), then index (slowest — aborts
  # early if broken), then MCP writes. §A13 requires index failure to
  # precede config writes; that is enforced by this ordering.
  ensure_gitignore
  run_index

  local old_IFS="$IFS"
  IFS=','
  for h in $hosts_csv; do
    IFS="$old_IFS"
    apply_host_install "$h"
    IFS=','
  done
  IFS="$old_IFS"

  [ "$DRY_RUN" = "true" ] && return 0
  ok "atlas-aci (uv) wired into $hosts_csv"
}

main_wire_container() {
  local hosts_csv="$1"

  # Step 1: runtime is already set from the positional; select_runtime is a guard.
  select_runtime

  # Step 2: build or verify image. Sets LOCAL_DIGEST.
  ensure_image

  # Step 3 (G18): if image existed and all host configs already match,
  # this is a true no-op.
  if [ "$DRY_RUN" = "false" ]; then
    if _container_configs_up_to_date "$hosts_csv"; then
      info "All host configs already match digest ${LOCAL_DIGEST} — no-op"
      ok "atlas-aci container already up-to-date in $hosts_csv"
      return 0
    fi
  fi

  # Step 4: mkdir .atlas/memex before first run (D6 / R8 mitigation).
  if [ "$DRY_RUN" = "false" ]; then
    mkdir -p ".atlas/memex"
  fi

  # Step 5: .gitignore.
  ensure_gitignore

  # Step 6: index (uses container).
  run_index

  # Step 7: host config writes.
  local old_IFS="$IFS"
  IFS=','
  for h in $hosts_csv; do
    IFS="$old_IFS"
    apply_host_install "$h"
    IFS=','
  done
  IFS="$old_IFS"

  [ "$DRY_RUN" = "true" ] && return 0
  ok "atlas-aci (container/$RUNTIME) wired into $hosts_csv"
}

main_remove() {
  # No prereq checks on remove: user may be removing BECAUSE a prereq
  # is broken. Only the ATLAS-installed guard (above) is required.
  local hosts_csv
  hosts_csv="$(resolve_hosts)"
  if [ -z "$hosts_csv" ]; then
    # On remove, "no host detected" is slightly different: the user
    # may have already cleaned up the .claude/.cursor/.github dirs.
    # Walk all known host files and no-op if absent. This keeps
    # `remove` idempotent even from a mostly-clean state.
    info "No MCP-capable host detected — sweeping known paths anyway"
    hosts_csv="claude-code,cursor,copilot,codex"
  fi

  info "Hosts: $hosts_csv"
  [ "$DRY_RUN" = "true" ] && info "Dry-run mode — no files will be modified"

  local old_IFS="$IFS"
  IFS=','
  for h in $hosts_csv; do
    IFS="$old_IFS"
    apply_host_remove "$h"
    IFS=','
  done
  IFS="$old_IFS"

  [ "$DRY_RUN" = "true" ] && return 0
  ok "atlas-aci removed from $hosts_csv (.atlas/ left on disk — delete manually if unwanted)"
}

case "$ACTION" in
  wire)    main_wire ;;
  install)
    err "Unknown action: install (did you mean 'wire'? — renamed in ATLAS v1.8.0; see CHANGELOG)"
    exit 2 ;;
  index)   main_index ;;
  remove)  main_remove ;;
  *)       exit_usage "Unknown action: $ACTION" ;;
esac
