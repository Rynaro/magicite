# syntax=docker/dockerfile:1
#
# magicite — local-first, plasticity-inspired skill router speaking MCP
# over stdio. Production runtime image (spec §8 "Distribution & Packaging";
# AC-026, AC-031; Risk R4). Sibling-MCP pattern verified against this
# project's own `.mcp.json` (crystalium/atomos/atlas-aci/tonberry): a
# non-root, UID-pinned user; `--cap-drop ALL --security-opt
# no-new-privileges` at `docker run` time; digest-pinnable in production.
#
# ─────────────────────────────────────────────────────────────────────────
# The fastembed ONNX model (BAAI/bge-small-en-v1.5) is baked into the image
# at BUILD time below — the one legitimate network-touching step (`magicite
# fetch-model`) — so the container never reaches the network at runtime.
# MAGICITE_EMBEDDING_OFFLINE=1 is set as a hard runtime default: it is
# threaded through to fastembed's `local_files_only` kwarg
# (embeddings/fastembed_provider.py), so a cache miss raises immediately
# instead of ever attempting a fetch — the guarantee AC-026's "no network
# access" clause depends on, not just a documented convention.
#
# ─────────────────────────────────────────────────────────────────────────
# PRIVILEGE-BOUNDARY NOTE — read before deploying against a real registry.
#
# This image's default user is `magicite`, UID 10001 (a safe, never-root
# default for a bare `docker run image` with no `--user` override). But
# magicite is NOT a read-only tool the way atlas-aci is: register()/sync()/
# Dream write real files under the mounted project's `.magicite/` tree
# (engrams, the DB, dream.lock, approvals) — and `magicite serve` calls
# `Config.ensure_dirs()` UNCONDITIONALLY AT EVERY BOOT (creating
# `.magicite/{archive,approvals,runtime}` if absent), not only when a write
# tool is later called.
#
# CONFIRMED EMPIRICALLY (`tests/acceptance/test_docker_smoke.py`, M7): a
# bind mount preserves the HOST's file ownership, so a container started
# WITHOUT overriding `--user` to your own host uid:gid runs as UID 10001
# against a `project_root` it does not own — `ensure_dirs()` hits a bare
# `PermissionError` and the server never even completes `initialize`. This
# is stronger than a mere ownership-hygiene concern: **the container
# cannot function at all** against a real, host-owned project directory
# without the `--user` override. (If you pre-create/chown `.magicite/` to
# UID 10001 yourself, or the mount happens to be world-writable, boot will
# succeed but every file magicite creates is then owned by UID 10001, not
# you — the same privilege boundary FORGE's threat model (docs/02-
# architecture.md) assumes does NOT exist between the MCP client and the
# MCP server, since you can no longer freely edit/delete your own
# `.egr.md` files without `sudo chown` afterward.)
#
# The fix is the invocation, not the image: ALWAYS pass
# `--user "$(id -u):$(id -g)"` (or the literal `--user 1000:1000` this
# project's own `.mcp.json` and docs/adapters/claude-code.md use) when
# mounting a real project directory. That overrides the image's UID 10001
# default with your own, making the container process the SAME OS
# principal as the host client — no boundary introduced, matching every
# other project-file-touching sibling MCP (atomos, atlas-aci, tonberry).
# ─────────────────────────────────────────────────────────────────────────
#
# Quickstart (the safe, house-pattern invocation):
#
#   docker run --rm -i \
#       --user "$(id -u):$(id -g)" \
#       --cap-drop ALL --security-opt no-new-privileges \
#       -v "$PWD":"$PWD":z -w "$PWD" \
#       ghcr.io/rynaro/magicite@sha256:<digest> \
#       serve --project-root "$PWD"
#
# ─────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Build the wheel directly. README.md is copied because pyproject.toml's
# `readme = "README.md"` field makes hatchling require it at build time.
RUN uv build --wheel --out-dir /dist

# Export the locked transitive deps (production, no dev extras) so the
# runtime stage installs the exact versions captured in uv.lock, not
# whatever pip resolves fresh against PyPI at image-build time.
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        --output-file /dist/requirements.txt

# ─────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="magicite" \
      org.opencontainers.image.description="Local-first, plasticity-inspired skill router speaking MCP over stdio" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Rynaro/magicite"

# Dedicated unprivileged user. UID pinned to 10001 so volume ownership is
# predictable across rebuilds (this is the DEFAULT, not the recommended
# runtime `--user` — see the privilege-boundary note above).
RUN useradd --create-home --uid 10001 magicite

COPY --from=builder /dist/requirements.txt /dist/*.whl /tmp/
# Install transitive deps from the lockfile-derived requirements.txt
# first, then the project wheel itself with --no-deps so pip cannot
# re-resolve and undo the lock-respecting install.
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/*.whl \
 && rm /tmp/*.whl /tmp/requirements.txt

# Bake the fastembed ONNX model at build time (spec §8, R4): the container
# must never reach the network at runtime. Root still owns the filesystem
# here, so fetch as root, then hand ownership to the runtime user — the
# baked cache only needs to be READABLE by whichever uid ends up running
# the container (UID 10001 by default, or an overriding --user), never
# written to at runtime (MAGICITE_EMBEDDING_OFFLINE=1 below forbids it).
ENV FASTEMBED_CACHE_PATH=/opt/magicite/models
RUN mkdir -p "$FASTEMBED_CACHE_PATH" \
 && magicite fetch-model \
 && chmod -R a+rX "$FASTEMBED_CACHE_PATH"

RUN mkdir -p /project && chown magicite:magicite /project

USER magicite
WORKDIR /project

VOLUME ["/project"]

ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAGICITE_EMBEDDING_OFFLINE=1

# MCP stdio handles SIGINT cleanly; make `docker stop` fast by mapping
# SIGTERM → SIGINT instead of the default 10s grace-then-KILL.
STOPSIGNAL SIGINT

ENTRYPOINT ["magicite"]
CMD ["serve", "--project-root", "/project"]
