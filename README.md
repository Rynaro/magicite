# Magicite

A local-first, plasticity-inspired skill router speaking MCP over stdio.

Magicite treats a directory of `.egr.md` "engram" files as the source of
truth for a skill registry, backs it with an embedded SQLite (WAL) index
that is fully rebuildable from those files, and exposes a 16-tool MCP
surface for retrieval, signal capture, and (approval-gated) learning.

**Status:** v1 under active implementation. See
`.spectra/changes/magicite-v1-implementation/spec.md` for the normative
construction spec and `docs/` for the design corpus.

## Development

```bash
uv sync --all-extras
uv run magicite tools          # print the 16-tool manifest
uv run magicite serve --project-root .
uv run pytest -q
```

`MAGICITE_EMBEDDING_PROVIDER=hashing` selects the deterministic, offline
embedder used by the test suite (no model download required).

## License

Apache-2.0. See `LICENSE`.
