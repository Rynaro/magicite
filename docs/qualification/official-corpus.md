# Official SkillRet corpus freeze

This slice acquires the complete public `ThakiCloud/SKILLRET` v1.1 dataset at revision `6583d7d2ed07644d0fb8938ed8178f3a7dc42a12`. The prospective source lock is [skillret-v1.1-source.json](../evaluation/v1/skillret-v1.1-source.json). Seven primary JSONL files total 714,420,871 bytes; the card, taxonomy and Git attributes are also preserved. Each object must match its publisher LFS SHA256 or Git blob SHA1; acquisition additionally records raw SHA256 and lengths.

Run from a clean committed checkout with the supported project dependencies:

```sh
PYTHONPATH="$PWD/src" python scripts/qualify_skillret_corpus.py \
  --acquire --raw /tmp/skillret-pinned-raw --output /tmp/skillret-freeze
PYTHONPATH="$PWD/src" python scripts/qualify_skillret_corpus.py \
  --verify --output /tmp/skillret-freeze
```

Omit `--acquire` to use previously acquired pinned bytes. Acquisition ignores ambient proxies and supplies no account token or netrc credentials. Conversion runs twice in separate fresh child processes with a network and subprocess audit denial. Both output seals must enumerate all generated files and agree byte-for-byte by SHA256 and length. Operational commands, machine paths and runtime information remain outside canonical converted trees. Output directories must be new and external to the checkout.

The master contains 17,810 skills, train 10,123 and test 6,006; 1,681 master-only skills are retained without enlarging either official candidate pool. Train has 63,259 queries and 127,190 qrels; test has 4,392 and 7,187. Conversion derives these counts from all rows and verifies split-local relations and card counts. Same-content skill overlap is retained and reported; query split identities remain distinct. Duplicate display names do not collapse identities. UUID normalization and shortened runtime-ID collisions, conflicting aliases/content, missing records and contradictory targets fail without a ready seal.

Every complete source `skill_md` survives as exact decoded UTF8 bytes in a sidecar, including frontmatter, Unicode and trailing whitespace. A deterministic generated native wrapper carries skill-only metadata and the untouched source text. UUID mappings are reversible; all wrappers pass the native parser. Original strict-import rejection and detected Markdown relative references are recorded separately. Referenced assets remain unavailable and unfetched; detection is not an exhaustive dependency inventory. Parser compatibility does not establish custody admission, executable completeness or safe skill execution.

Runtime queries contain only namespaced query ID, text and empty compatibility context. Full upstream annotations and qrels remain in separate scoring files. CorpusManifest v1 retains its existing query-content identity semantics; a separate aggregate seal binds the entire body, mapping, metadata and split inventory. Bookkeeping groups explicitly make no independence claim; no negative labels are invented.

Per-skill license, attribution and source URLs are retained as supplied. The immutable dataset revision does not authenticate each original repository revision or settle licensing rights. No embedding model is loaded, no query is routed or ranked, and no E3/E6 quality, performance, external/operator, human-release or GA acceptance follows from this codec freeze. Raw bodies and full conversion trees remain outside Git; qualification evidence binds the exact committed converter and actual retained packet.
