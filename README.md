# Magicite

A local-first, plasticity-inspired skill router speaking MCP over stdio.

Magicite treats a directory of `.egr.md` "engram" files as the source of
truth for a skill registry, backs it with an embedded SQLite (WAL) index
that is fully rebuildable from those files, and exposes a 16-tool MCP
surface for retrieval, signal capture, and (approval-gated) learning. It
never executes anything on your behalf and never phones home: routing,
learning, and consolidation all happen inside your own project, offline.

**Status:** 0.1.0 — first release, container-only. See
`.spectra/changes/archive/2026-08-15-magicite-v1-implementation/spec.md` for
the normative construction spec (archived, with its five recorded amendments)
and `docs/` for the design corpus (start at
`docs/01-vision-and-hypotheses.md`).

**Honest limits, up front (docs/01 Falsification Record, measured 2026-08-15):** At 70 skills with lexically independent queries, plain dense-embedding retrieval (baseline b: Hit@1 0.5476) remains stronger than the full Magicite pipeline (baseline d: Hit@1 0.5333). The gap is statistically indistinguishable (3 queries out of 210; prior measurement 0.4619 was 18-query gap, p = 0.00053). Full Magicite is not significantly better than native lexical matching (p = 0.19). The predicted ~50-skill break-even where Magicite's routing machinery should "pay off" remains **unevidenced** — a single unreplicated crossing on a 39-query core slice does not sustain that claim. *Caveats: these results come from a single-author corpus and queries, single annotator, single embedder (bge-small-en-v1.5), and uniform learning workload; see docs/01 "What the evidence licenses" for limitations and docs/07 §5–§6 for the mechanism.* Magicite ships as a **verified skill router with a portable format, lifecycle governance, and composition-plan expansion, whose graph and learning layers are not yet demonstrated to improve routing** — and whose actual design claim (spreading activation over declared edges, not re-derived embeddings) has never been tested. The improvement from 0.4619 to 0.5333 is mechanism repair (declared-edges amendment and inhib_gain recalibration fixed defects that were inhibiting measurement), not validation of the design hypothesis. `magicite doctor` reports your registry size and flags the cold-start case honestly: the ~50-skill number is a reference size from docs/07's original (pre-falsification) heuristic, never an asserted break-even — crossing it is not reported as evidence that hierarchy-aware routing pays off, consistent with this measurement — see [§ Diagnostics](#diagnostics-magicite-doctor).

## Quickstart — Docker (recommended)

The published image follows the same hardened, sibling-MCP pattern this
project's own `.mcp.json` uses for `crystalium`/`atomos`/`atlas-aci`/
`tonberry`: non-root, capability-dropped, digest-pinnable, and (per AC-026)
able to complete its MCP handshake with **zero network access** because the
`fastembed` ONNX model (`BAAI/bge-small-en-v1.5`) is baked into the image at
build time.

```bash
docker run --rm -i \
    --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD":"$PWD":z -w "$PWD" \
    ghcr.io/rynaro/magicite@sha256:d4de4eacbadea6f7e8fa73506dceae8e3d465088a2590c3d892deb096e03dc34 \
    serve --project-root "$PWD"
```

**`--user "$(id -u):$(id -g)"` is required, not optional.** `magicite serve`
creates `.spectra/{archive,approvals,runtime}` at every boot
(`Config.ensure_dirs()`), and a bind mount preserves the *host's* file
ownership — without this flag the container runs as the image's baked-in
default (UID 10001), which cannot write to a directory it does not own, and
the server never completes its handshake against a real project. See the
`Dockerfile`'s own `PRIVILEGE-BOUNDARY NOTE` and
`tests/acceptance/test_docker_smoke.py` for the mechanically-verified
finding behind this.

### `.mcp.json` entry

```json
{
  "mcpServers": {
    "magicite": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i", "--user", "1000:1000",
        "--label", "eidolons.project=<project>",
        "-v", "<project_root>:<project_root>:z", "-w", "<project_root>",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "ghcr.io/rynaro/magicite@sha256:d4de4eacbadea6f7e8fa73506dceae8e3d465088a2590c3d892deb096e03dc34",
        "serve", "--project-root", "<project_root>"
      ]
    }
  }
}
```

Replace `1000:1000` with your own `$(id -u):$(id -g)` if different. The digest
above is the published `v0.1.0` image; newer pinned digests appear on the
[releases page](https://github.com/Rynaro/magicite/releases). See
`docs/adapters/claude-code.md` for the fuller adapter walkthrough, including
optional Tier-2 hook acceleration via `MAGICITE_HOOK_TOKEN`.

## Quickstart — pip (development)

> **Not on PyPI yet.** 0.1.0 ships as a container only; the wheel job is
> gated behind `PUBLISH_TO_PYPI` until trusted publishing is registered.
> Install from source in the meantime.

```bash
git clone https://github.com/Rynaro/magicite && cd magicite
uv sync --all-extras
uv run magicite fetch-model     # pre-download the ONNX embedding model once
uv run magicite serve --project-root .
```

`MAGICITE_EMBEDDING_PROVIDER=hashing` selects a deterministic, zero-download
embedder for tests/CI (lower routing quality — not for production use).
`MAGICITE_EMBEDDING_OFFLINE=1` refuses any network fetch at runtime once the
model has been pre-downloaded (matches the Docker image's default).

## Diagnostics: `magicite doctor`

```bash
magicite doctor --project-root .
```

A deliberately unflattering environment check (spec M7, risks R7/R9): it
warns when `.spectra/` sits on a filesystem class where `fcntl.flock()`-based
single-writer locking is known to degrade (NFS/CIFS/etc.), and it reports
the registry's cold-start standing honestly rather than overselling —
`registry_size` below the ~50-skill reference size is called out as a
warning, not a footnote, and crossing it is never reported as an asserted
break-even (docs/01's Falsification Record measured a 70-skill, above-
reference registry where the full pipeline still trailed plain dense
embedding by 3 queries in 210 — statistically indistinguishable, but not
ahead). See `docs/operations.md` §8 for the full lock-semantics discussion.

## Development

```bash
uv sync --all-extras
uv run magicite tools          # print the 16-tool manifest
uv run magicite serve --project-root .
uv run pytest -q
uv run ruff check . && uv run mypy src
```

`MAGICITE_EMBEDDING_PROVIDER=hashing` selects the deterministic, offline
embedder used by the test suite (no model download required). The full ESL
`verify` bar (spec §7.2) — the exact sequence Kupo runs, including the
containerised handshake — is:

```bash
uv sync --all-extras
uv run ruff check . && uv run mypy src
uv run pytest -q --cov=src/magicite --cov-fail-under=70
uv run pytest -q -m acceptance
uv run magicite tools | jq '.tools | length'   # == 16
docker build -t magicite:verify . && uv run pytest tests/acceptance/test_docker_smoke.py
```

Dev-loop container (pytest/ruff/mypy preinstalled, no ONNX model bake):

```bash
docker build -f Dockerfile.dev -t magicite-dev .
docker run --rm -it -v "$PWD":/work magicite-dev
```

## License

Apache-2.0. See `LICENSE`.
