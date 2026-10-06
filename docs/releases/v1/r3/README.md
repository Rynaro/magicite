# V1 r3 qualification: NO-GA

Candidate `64831a6dc2b46d1baa487d9bf247d09871c878e4` contains the approval replay and read-only custody diagnostic
repairs. The current supported Python 3.12 run (**1741 passed, 9 skipped, 1 deselected;
86.98% coverage**) and separate-agent product review are
in [evidence](evidence/checks.json). Five Docker-image checks skipped because the
image was not built; four unpublished channel reservations skipped. The one
wall-clock benchmark is deliberately excluded by the blocking CI test command.
Those skips/deselection are not PASS evidence. [All 17 whole gates](gate-table.md) remain
UNEVALUATED: this bounded assessment neither imports r2 PASS labels nor declares
17 new failures. [The criterion ledger](criterion-ledger.json) preserves the original
76 + additive 12 frozen texts plus 14 r3 milestone criteria. Unproven whole criteria
remain UNEVALUATED even when their tests pass. Independent package review:
VIGIL /root/verify_r3. Milestone criteria AC-R3-01 through AC-R3-14 pass under
the stated bounded verification; all 88 original release/trust criteria remain
UNEVALUATED. AC-R3-14 verifies tracked diff and porcelain status preservation;
no baseline hashes of untracked contents were captured. Native role review is not
external acceptance or live Gauge acceptance.

The [current privacy map](privacy-data-map.md) includes trust authority, protected
custody, enrollment, transport and approval audit as well as evidence surfaces.
PR #51 sources are pinned to `68725d4120569b246503d790bfcaaf85b7a11f0f` and explicitly unmerged; the High
findings and TH-A01 old-reader residual still require named human decisions.
AC-TH-10 retains `tests/integration/test_th10_old_reader_downgrade.py`, TH-A01/F-10
and its pre-hardening binary residual. Remaining real crash/lost-reply/OS/transport,
fuzz and isolated fence gaps are recorded in [provenance](evidence/inherited-provenance.json).
The Linux 9692735 run is base-only. No publication, tag, RC pair, external validation,
separate-user deployment or maintainer security/GA sign-off is claimed.

Reproduction from a clean candidate checkout with Python 3.12 and all locked extras:

```sh
uv sync --frozen --all-extras --python 3.12
python -m pytest -q --cov=src/magicite --cov-fail-under=70 -m 'not benchmark'
python docs/releases/v1/r3/evidence/build_r3_package.py --verify --root . --candidate 64831a6dc2b46d1baa487d9bf247d09871c878e4
python scripts/check_release_manifest.py docs/releases/v1/r3/release-manifest.json --root .
```

The integrity check must succeed. The native release validator must exit **1**,
with `eligible: false` and only enumerated unmet whole-gate, RC, external and
maintainer requirements. It does not verify Git HEAD; the successor generator
checks exact non-package source-tree equality with C. A later evidence-only E
commit may be used when its non-package tracked tree is proven identical to C.
Run `--negative-controls` to witness source-mismatch and bound-artifact rejection.
Independent test sources are archived byte-for-byte as `.py.txt` evidence; copy
them to temporary `.py` files when rerunning the recorded pytest commands.
Raw collection commands, actual runtime, start/end times and log hashes are in
`evidence/checks.json`; rebuild with `--raw <collected-dir>`, `--candidate C`, and
optional `--review <independent-package-review.json>`. Private temporary paths in
recorded commands describe the actual run; use your equivalent isolated environment.

Primary preservation evidence covers unchanged tracked diff bytes and porcelain
status; no stronger untracked-content byte baseline was captured. Historical r2
is unchanged. This release remains blocked on human security review, deployment,
empirical performance/quality, the full supported RC/distribution matrix, external
evidence and explicit maintainer sign-off.
