# Protected benchmark workflow

The benchmark invokes actual routing, live authenticated eligibility, composition,
and the production finalizer. Its frozen evaluation calibration is an integrity
binding, **not production approval**. Every local result remains nonqualifying;
E2, E3, E6 and GA remain `UNEVALUATED` even when a numeric diagnostic passes.

Prepare a calibration-data packet and initial freeze as described in
[calibration-data.md](calibration-data.md). The preregistered grouped protocol must
include the four comparison policies, the common allowance, seed and experiment
identity, development source groups and declared power assumptions. Initial
`power_plan` is null. Missing groups or degenerate development effects cannot
support a power report and prevent fitting.

The internal `protected_benchmark.make_plan` API constructs a strict plan from the
freeze, actual router, frozen model manifest and common `ComparisonBudget`. Save it
with immutable publication before development. Plans bind source, model, current
custody, registry, generation, configuration, profile, depth and all query inputs.
Unknown fields and unsupported policies or budgets fail before scoring.

```sh
python -m magicite.eval benchmark-develop \
  --project-root /project --model-cache /offline-model \
  --model-manifest model.json --rank-depth 5 \
  --freeze planning-freeze.json --plan benchmark-plan.json \
  --group-floors 30,40,60 --repetitions 200 --output development.json

python -m magicite.eval benchmark-fit \
  --project-root /project --model-cache /offline-model \
  --model-manifest model.json --rank-depth 5 \
  --freeze planning-freeze.json --development development.json \
  --output commitment.json

python -m magicite.eval benchmark-final \
  --project-root /project --model-cache /offline-model \
  --model-manifest model.json --rank-depth 5 \
  --commitment commitment.json --output final.json
```

Development compares dense, sparse, trigger and hybrid RRF under the same allowance.
The strongest simple incumbent is chosen across dense/sparse/trigger by complete
all-query Hit@1, with the preregistered order resolving ties. Hybrid RRF is the
candidate; dense remains a separately reported reference. The existing adaptive
blend runs as a required uncapped research leg. Its work is explicitly
noncomparative and cannot support same-budget E2 or E5 promotion.

The existing grouped power API consumes bound development observations. A new,
linked successor freeze augments only the power projection and selected-incumbent
binding; it retains the same original packet root and input bytes. Fitting reads
only preparation-materialized calibration labels, freezes one artifact per
comparison arm, and never opens final labels. An inconclusive but complete
numerical power report permits descriptive mechanics; it does not establish
statistical support. Unsupported or missing development power stops fitting.

Final evaluation owns the entire experiment. Packet-root ownership and durable
intent precede final label decoding and scoring. Interrupted or failed access
remains exposed. A different legacy consumer, experiment, fit, model, source,
custody, budget or query binding cannot claim a fresh final partition. Exact-bound
replay uses `--replay`; it retains exposure and validates original completed result
bytes and trace digests. Output filenames never define final ownership.

Reports include complete per-arm raw slates, actual usable selections, actual
finalizer confidence or a null reason, grouped bounds, paired statistics, selected
population ECE, proposed-top1 diagnostics, call timing, process and work counters,
source/model identity and profile envelopes. Operational errors count as misses;
no-match errors receive conservative penalties. Missing cold-process repetitions,
reference hardware, RSS, index build and payload measurements stay missing. Local
single-process call times do not become warm-cache performance claims.

`run_benchmark_matrix.py --quality-evidence final.json` may attach a compatible
completed local result. It checks the full frozen completion, exact arm membership,
source, loaded model bytes/libraries, environment, dependencies and profile. It
preserves the matrix's existing measurement and eligibility logic. Mismatched or
unverifiable references fail; the attachment grants no E6 or GA eligibility.

A separate read-only witness observes an **already approved ordinary production**
probability calibration:

```sh
python -m magicite.eval benchmark-production-witness \
  --project-root /project --model-cache /offline-model \
  --model-manifest model.json --rank-depth 5 --profile small-100 \
  --queries runtime-queries.json --output production-witness.json
```

This command uses neither an evaluation context nor a comparison-budget override.
Missing approved compatible state fails explicitly. It never admits, approves or
activates calibration. Synthetic test custody and fixture vectors only demonstrate
workflow mechanics. Authentic datasets, production deployment witnesses, reference
hardware and the later GA decision remain separate release obligations.
