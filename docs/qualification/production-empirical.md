# Local production retrieval diagnostic

This runner measures the actual FastEmbed `BAAI/bge-small-en-v1.5` provider and actual `dense-v1` / `experimental/adaptive-blend-v1` router policies on all thirty tracked first-party skill bodies. Sixteen independently authored development queries are frozen before results. Labels stay on the scoring side; runtime receives query identity, text and compatibility context only. The adaptive policy is experimental. No default or active policy is changed.

The legacy `magicite-bench run-retrieval --provider production` and paired-policy production mode explicitly refuse their unsupported synthetic path. Their hashing fixtures remain available and labelled synthetic; the paired command already disclosed its seed-perturbed fixture arm. Actual production measurements use this diagnostic runner.

Use a supported Python environment with the dependency lock and candidate source. Acquire the public model only into a disposable task cache, with implicit Hugging Face credentials disabled; keep `HF_HOME` and `HOME` task-owned. `magicite.embeddings.fastembed_provider.fetch_model(cache_dir=...)` is the explicit acquisition operation. Before any corpus or query embedding, save `magicite.eval.production.freeze_model(cache)` to an expected JSON manifest. It records every downloaded model/tokenizer/config byte and observed library/revision identity. These are expected bytes for this run, not an authenticated publisher trust pin. Do not overwrite the manifest after observing outputs.

Commit the final source first, then run:

```sh
PYTHONPATH=/absolute/candidate/src /path/to/python scripts/qualify_production_empirical.py \
  --oracle-freeze docs/qualification/evidence/production-empirical/independent-oracle-freeze.json \
  --model-manifest /private/tmp/empirical/expected-model.json \
  --model-cache /private/tmp/empirical/model-cache \
  --output /private/tmp/empirical/fresh-results
/path/to/python scripts/qualify_production_empirical.py \
  --output /private/tmp/empirical/fresh-results --verify
```

Output must be fresh and outside the checkout. Referenced independent files are copied byte-for-byte here; original transport paths in the freeze record remain historical. If those paths are absent, the runner resolves the same verified bytes beside the freeze file.

One actual admission bootstrap creates a disposable simulated custody registry. Its closed database, admitted content, authority and full file snapshot are hashed before queries. Every policy/measurement process receives a byte-identical copy with the same registry identity. The simulation does not qualify production custody deployment. Model bytes are checked before provider construction and after each run; workers are offline and reject attempted network connections.

Each policy produces one sixteen-query quality run and three fresh-process timing runs, each with fifty warm-up routes and one thousand fixed-order rotated operations. Timings include actual routing, embedding and prediction JSON serialization. Cache observations report actual graph-index hits/misses and production embedding call counts; repeated operations are not independent quality samples. RSS is process peak including startup; data-directory size includes trust and logs, not solely the vector index.

The report retains raw ordered candidates/scores, selections/abstention, exclusions and runtime identities, separate expected-model manifest hashes, per-operation timings, descriptive metric numerators/denominators and actual machine/dependency/command records. The raw router `model_digest` currently names its model/config identity and must not be interpreted as downloaded-byte authenticity. Confidence is null without an authenticated calibration. Poor results remain visible without tuning the frozen oracle or policies.

This is an exploratory thirty-body development diagnostic on the actual local machine. Official SkillRet and temporal holdouts, power/group thresholds, hybrid-RRF evaluation, reference Linux 100/1000/10k budgets, F16 upstream authenticity, F17 scanner policy, external host outcomes, publication and human security approval remain separate unevaluated obligations. It does not establish E3/E6, GA or Gauge acceptance. Raw results/model caches may remain task-owned temporary artifacts; their durability and availability must be disclosed in the qualification handoff. Historical release and host packets are preserved.
