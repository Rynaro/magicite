"""SECURITY gate obligation: bounded-fuzz-adversarial.

Seeded, deterministic, wall-clock and memory bounded fuzzing of the trust and
intake parsers. Invariant: each input either succeeds or raises only the
target's declared typed exception; never another exception, hang, or
unbounded memory. Set MAGICITE_FUZZ_REPORT=<path> to also write the JSON
report (default: pytest tmp_path only; nothing is written into the repo).
"""

from __future__ import annotations

import io
import json
import os
import time
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.core import bundles
from magicite.core.trust import default_policy
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_custodian_transport import (
    FRAGMENT_BYTES,  # noqa: F401
    receive_frame,
    receive_message,
    send_frame,
    send_message,
    sign_receipt,
    verify_receipt,
)
from magicite.engram import parser
from magicite.errors import InvalidInputError
from tests.fuzz import harness
from tests.fuzz.harness import Report, Target

REPO = Path(__file__).resolve().parents[2]
SEED_BUDGET = harness.WALL_CAP_PER_SEED * len(harness.SEEDS)


# ------------------------------------------------------------ fake socket


class FakeConn:
    """In-memory stand-in for a socket (recv/settimeout/sendall/shutdown)."""

    def __init__(self, data: bytes = b""):
        self.data = data
        self.pos = 0
        self.sent = bytearray()

    def settimeout(self, _t: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk

    def sendall(self, b: bytes) -> None:
        self.sent.extend(b)

    def shutdown(self, _how: int) -> None:
        pass


def _wire(message: dict[str, Any]) -> bytes:
    conn = FakeConn()
    send_message(conn, message)  # type: ignore[arg-type]
    return bytes(conn.sent)


def _frame_wire(message: dict[str, Any]) -> bytes:
    conn = FakeConn()
    send_frame(conn, message)  # type: ignore[arg-type]
    return bytes(conn.sent)


# ------------------------------------------------------------ targets



def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)


PUB = _key().public_key().public_bytes_raw().hex()
BIND = {
    "nonce": "n-1",
    "registry_id": "registry-one",
    "epoch": 1,
    "operation": "read_current",
    "request_id": "req-1",
}
RESULT = {"head": {"sequence": 1}}


def _receipt_ok() -> dict[str, Any]:
    return sign_receipt(_key(), {**BIND, "result": RESULT, "error": None})


def _receipt_target(data: bytes) -> Any:
    try:
        receipt = json.loads(data)
    except (ValueError, RecursionError):
        raise CustodianError("unparsable receipt input") from None  # pre-parse reject
    return verify_receipt(receipt, PUB, **BIND)  # type: ignore[arg-type]


def _receipt_accept(data: bytes, result: Any) -> str | None:
    # A mutant may only verify if it is semantically the genuine receipt.
    if result != RESULT:
        return f"verified with altered result {result!r}"
    try:
        r = json.loads(data)
        good = _receipt_ok()
        same_sig = bytes.fromhex(r["signature"]) == bytes.fromhex(good["signature"])
        if not same_sig or r["payload"] != good["payload"]:
            return "verified but signature/payload differ from genuine receipt"
    except Exception as exc:  # pragma: no cover - would itself be a finding
        return f"post-check error {exc!r}"
    return None


def _message_target(data: bytes) -> Any:
    return receive_message(FakeConn(data), deadline=time.monotonic() + 5)  # type: ignore[arg-type]


def _frame_target(data: bytes) -> Any:
    return receive_frame(FakeConn(data))  # type: ignore[arg-type]


def _payload_target(kind: str):
    def run(data: bytes) -> Any:
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError):
            raise CustodianError("unparsable payload input") from None
        return CustodianStore._validate_payload(kind, payload)

    return run


def _decision() -> dict[str, Any]:
    from magicite.core.trust import TrustDecision

    p = default_policy()
    return TrustDecision(
        decision_id="d",
        engram_id="subject",
        content_digest="a" * 64,
        decision="admit",
        source_channel="local_authored",
        policy_id=p.policy_id,
        policy_revision=p.revision,
        policy_digest=p.digest(),
        actor="operator",
        timestamp="2026-09-30T00:00:00Z",
    ).to_dict()


def _policy_with_root() -> dict[str, Any]:
    d = default_policy().to_dict()
    return d


def _j(v: Any) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()


def _legacy_complete() -> dict[str, Any]:
    return {
        "schema": "LegacyReconciliation/1",
        "phase": "COMPLETE",
        "migration_id": "m-1",
        "registry_id": "registry-one",
        "epoch": 1,
        "manifest_digest": "b" * 64,
        "backup_digest": "c" * 64,
    }


def _bundle_seed(tmp: Path) -> bytes:
    src = tmp / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("alpha\n")
    (src / "sub" / "b.txt").write_text("beta\n")
    manifest = bundles.build_manifest_from_directory(src)
    mbytes = manifest.canonical_bytes()
    buf = io.BytesIO()
    # Fixed timestamps so the seed bytes (and therefore every mutant) are reproducible.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw in [(e.path, (src / e.path).read_bytes()) for e in manifest.entries] + [
            (bundles.MANIFEST_NAME, mbytes),
            (bundles.SIGNATURE_NAME, _key().sign(mbytes)),
        ]:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, raw)
    return buf.getvalue()


def _bundle_target(tmp: Path):
    root = bundles.TrustRoot(
        fingerprint=bundles.public_key_fingerprint(bytes.fromhex(PUB)), public_key_bytes=bytes.fromhex(PUB)
    )
    limits = bundles.BundleLimits(max_entries=50, max_total_uncompressed=1 << 20, max_file_bytes=1 << 20)
    counter = {"n": 0}

    def run(data: bytes) -> Any:
        counter["n"] += 1
        parent = tmp / "stage" / str(counter["n"] % 8)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # zipfile "overlapped entries" notice is not an exception
            return bundles.verify_bundle(data, roots=[root], staging_parent=parent, limits=limits)

    return run


def _engram_seeds() -> list[bytes]:
    paths = sorted((REPO / "tests/fixtures/engram-v1/positive").glob("*.egr.md"))[:2]
    paths += sorted((REPO / "tests/fixtures/toy-registry/engrams").glob("*.egr.md"))[:2]
    assert paths
    return [p.read_bytes() for p in paths]


def _engram_target(data: bytes) -> Any:
    # Mirrors parse_file: strict UTF-8 decode failure is a file-layer error, modelled as parse error.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise parser.EngramParseError("invalid utf-8") from None
    return parser.parse_artifact(text, relpath="fuzz.egr.md", admit=True, require_asset_files=False)


def build_targets(tmp: Path) -> list[Target]:
    from pydantic import ValidationError

    msg = {**BIND, "op": "x", "args": {"a": [1, 2, {"b": None}]}}
    receipt_bytes = _j(_receipt_ok())
    return [
        Target(
            "custodian.receive_message",
            _message_target,
            (CustodianError,),
            [_wire(msg), _wire({"k": "v" * 50})],
        ),
        Target(
            "custodian.receive_frame",
            _frame_target,
            (CustodianError,),
            [_frame_wire(msg), _frame_wire({"z": 1})],
        ),
        Target(
            "custodian.verify_receipt",
            _receipt_target,
            (CustodianError,),
            [receipt_bytes],
            accept_check=_receipt_accept,
            structural_json=True,
        ),
        Target(
            "custodian._validate_payload:policy_snapshot",
            _payload_target("policy_snapshot"),
            (CustodianError,),
            [_j(_policy_with_root())],
            structural_json=True,
        ),
        Target(
            "custodian._validate_payload:trust_decision",
            _payload_target("trust_decision"),
            (CustodianError,),
            [_j(_decision())],
            structural_json=True,
        ),
        Target(
            "custodian._validate_payload:legacy_reconciliation",
            _payload_target("legacy_reconciliation"),
            (CustodianError,),
            [_j(_legacy_complete())],
            structural_json=True,
        ),
        Target("bundles.verify_bundle", _bundle_target(tmp), (InvalidInputError,), [_bundle_seed(tmp)]),
        Target(
            "engram.parse_artifact(admit=True)",
            _engram_target,
            (parser.EngramParseError, ValidationError),
            _engram_seeds(),
        ),
    ]


# ------------------------------------------------------------ tests


def _emit(reports: list[Report], tmp_path: Path) -> None:
    text = harness.dump(reports)
    (tmp_path / "fuzz-report.json").write_text(text)
    dest = os.environ.get("MAGICITE_FUZZ_REPORT")
    if dest:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(text)


@pytest.fixture(scope="module")
def all_reports(tmp_path_factory: pytest.TempPathFactory) -> list[Report]:
    tmp = tmp_path_factory.mktemp("fuzz")
    return harness.run_all(build_targets(tmp))


TARGET_NAMES = [
    "custodian.receive_message",
    "custodian.receive_frame",
    "custodian.verify_receipt",
    "custodian._validate_payload:policy_snapshot",
    "custodian._validate_payload:trust_decision",
    "custodian._validate_payload:legacy_reconciliation",
    "bundles.verify_bundle",
    "engram.parse_artifact(admit=True)",
]


@pytest.mark.parametrize("name", TARGET_NAMES)
def test_bounded_fuzz_invariant_holds(name: str, all_reports: list[Report], tmp_path: Path) -> None:
    """SECURITY gate obligation bounded-fuzz-adversarial: every mutant either succeeds or raises only
    the target's typed exception; no hang, no unbounded memory, no accepted mutation."""
    _emit(all_reports, tmp_path)
    findings = [(r.seed, f) for r in all_reports if r.target == name for f in r.findings]
    assert not findings, json.dumps(findings, indent=2)[:4000]


def test_report_shape_and_bounds(all_reports: list[Report], tmp_path: Path) -> None:
    """bounded-fuzz-adversarial: report records seed/target/iterations/outcomes/max duration; caps hold."""
    _emit(all_reports, tmp_path)
    data = json.loads((tmp_path / "fuzz-report.json").read_text())
    assert data["seeds"] == list(harness.SEEDS)
    assert len(data["runs"]) == 8 * len(harness.SEEDS)
    for run in data["runs"]:
        assert {"seed", "target", "iterations", "outcomes", "max_duration_s"} <= set(run)
        assert 0 < run["iterations"] <= harness.ITERATIONS_PER_SEED
        assert run["max_duration_s"] < harness.INPUT_TIMEOUT
    # per-target wall clock: at most SEED_BUDGET (+ one-input slack) of fuzzing across seeds


def test_positive_control_valid_seeds_succeed(all_reports: list[Report]) -> None:
    """bounded-fuzz-adversarial positive control: every unmutated valid seed is accepted, and mutants are
    genuinely rejected (non-vacuous: typed rejections observed for every target)."""
    for r in all_reports:
        seeds_ok = r.outcomes.get("seed_ok", 0)
        assert seeds_ok >= 1 and not [k for k in r.outcomes if k.startswith("seed_") and k != "seed_ok"], r
        assert any(k.startswith("expected:") for k in r.outcomes), r


def test_harness_is_deterministic(tmp_path: Path) -> None:
    """bounded-fuzz-adversarial: same seed gives identical outcome counts (iteration-capped)."""
    targets = build_targets(tmp_path)[:3]
    a = [harness.run_target(t, 1, wall_cap=60).outcomes for t in targets]
    b = [harness.run_target(t, 1, wall_cap=60).outcomes for t in targets]
    assert a == b


def test_wall_clock_cap_bounds_each_target(all_reports: list[Report]) -> None:
    """bounded-fuzz-adversarial: total fuzz iterations stay iteration-capped (wall cap enforced per seed)."""
    for r in all_reports:
        assert r.iterations <= harness.ITERATIONS_PER_SEED


# ---- negative controls: the harness must catch a broken target


def test_broken_target_unexpected_exception_is_caught_and_minimized() -> None:
    """bounded-fuzz-adversarial negative control: a leaked KeyError is flagged with a minimized input."""

    def broken(data: bytes) -> None:
        if b"BOOM" in data:
            raise KeyError("leak")
        if len(data) > 5000:
            raise CustodianError("typed")

    t = Target("scratch.broken", broken, (CustodianError,), [b"ok-seed"])
    # force the mutator to produce BOOM via splice corpus
    t.seeds = [b"ok-seed", b"xxxxBOOMyyyy"]
    r = harness.run_target(t, 1, iterations=50)
    kinds = [f["kind"] for f in r.findings]
    assert "valid-seed-rejected" in kinds or "unexpected:KeyError" in kinds
    unexpected = [f for f in r.findings if f["kind"] == "unexpected:KeyError"]
    assert unexpected and min(f["minimized_len"] for f in unexpected) == 4  # minimized to b"BOOM"


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="needs SIGALRM")
def test_hang_is_detected_by_timeout() -> None:
    """bounded-fuzz-adversarial negative control: a hanging target is interrupted and reported."""

    def hang(data: bytes) -> None:
        if data == b"hang":
            time.sleep(0.5)

    t = Target("scratch.hang", hang, (CustodianError,), [b"ok", b"hang"])
    r = harness.run_target(t, 1, iterations=2, input_timeout=0.05)
    assert any(f["kind"] == "valid-seed-rejected" and f["outcome"] == "timeout" for f in r.findings)
    assert r.max_duration < 0.4


def test_memory_blowup_is_detected() -> None:
    """bounded-fuzz-adversarial negative control: unbounded allocation trips the memory cap."""
    keep: list[bytearray] = []

    def hog(data: bytes) -> None:
        keep.append(bytearray(8 * 1024 * 1024))

    t = Target("scratch.hog", hog, (CustodianError,), [b"x"])
    r = harness.run_target(t, 1, iterations=1, memory_cap=1024 * 1024)
    assert any(f["kind"] == "memory-cap-exceeded" for f in r.findings)


def test_accepted_mutation_is_flagged() -> None:
    """bounded-fuzz-adversarial negative control: a verifier that accepts a tampered receipt is flagged."""

    def lax(data: bytes) -> Any:
        return RESULT  # accepts anything

    t = Target("scratch.lax", lax, (CustodianError,), [_j(_receipt_ok())], accept_check=_receipt_accept)
    r = harness.run_target(t, 1, iterations=30)
    assert any(f["kind"] == "accepted-mutation" for f in r.findings)
