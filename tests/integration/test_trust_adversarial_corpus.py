"""TRUST gate obligation: adversarial-corpus.

Runner for the named adversarial trust corpus at
``tests/fixtures/adversarial/trust/manifest.json``. It

(a) validates the manifest schema, vocabulary and identifier uniqueness;
(b) executes every entry whose witness is a runner case: the attack must fail
    closed with exactly the manifest's expected outcome (exception type plus
    message fragment, or observed decision/route/body state), and the same
    pipeline without the adversarial step must succeed (per-entry positive
    control, so every runner class also has a positive control);
(c) proves every entry witnessed by an existing test node id still resolves
    to a collected pytest node, so the manifest cannot cite dead tests;
(d) writes a JSON report (entry id -> outcome, manifest sha256) to tmp_path and,
    when ``MAGICITE_TRUST_CORPUS_REPORT`` is set, to that path.

Fixture custody here is test-owned dependency injection (see
tests/support/custody_adapter.py). Nothing in this module qualifies a
separate-UID deployment; those rows stay UNEVALUATED (AC-TH-04/AC-TH-12).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from magicite.config import Config
from magicite.core import bundles, trust, trust_custodian_transport, writer_guard
from magicite.core.trust_custodian import CustodianError, CustodianStore
from magicite.core.trust_journal import TrustJournal
from magicite.errors import BusyError, InvalidInputError
from magicite.storage import db

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "tests/fixtures/adversarial/trust/manifest.json"
SCHEMA_ID = "magicite/trust-adversarial-corpus/1"
REPORT_SCHEMA_ID = "magicite/trust-adversarial-corpus-report/1"
ID_RE = re.compile(r"^TAC-\d{3}$")
CRITERION_RE = re.compile(r"^(AC-TH-(0[1-9]|1[0-2])|AC-S\d{2}-\d{2})$")
NODE_RE = re.compile(r"^tests/[\w/]+\.py::\w+(\[[^\]]+\])?$")
EXCEPTIONS: dict[str, type[BaseException]] = {
    "BusyError": BusyError,
    "CustodianError": CustodianError,
    "InvalidInputError": InvalidInputError,
    "TrustLedgerCorruptError": trust.TrustLedgerCorruptError,
}
REGISTRY = "registry-one"
UNREACHABLE = "custody unavailable; reconciliation required"


def _client_unreachable_message_is_product_text() -> None:
    import inspect

    source = inspect.getsource(trust_custodian_transport.CustodianClient.call)
    assert f'raise CustodianError("{UNREACHABLE}") from exc' in source, "adapter must mirror the real client"


def _load_manifest() -> tuple[dict[str, Any], str]:
    raw = MANIFEST_PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


MANIFEST, MANIFEST_SHA256 = _load_manifest()
ENTRIES: list[dict[str, Any]] = MANIFEST["entries"]
RUNNER_ENTRIES = [e for e in ENTRIES if e["witness"]["type"] == "runner"]
NODE_ENTRIES = [e for e in ENTRIES if e["witness"]["type"] == "node"]

# ===================================================================== env


@dataclass
class Env:
    """Fresh, isolated attack environment per runner case."""

    tmp: Path
    monkeypatch: pytest.MonkeyPatch
    closers: list[Callable[[], None]] = field(default_factory=list)

    # -- custodian store -------------------------------------------------
    def store(self, name: str = "custody", registry: str = REGISTRY) -> CustodianStore:
        store = CustodianStore.create(self.tmp / name)
        store.enroll(registry, trust.default_policy().to_dict(), actor="operator", reviewed=True)
        self.closers.append(store.close)
        return store

    # -- authenticated journal ------------------------------------------
    def journal(self, registry: str = REGISTRY) -> tuple[TrustJournal, CustodianStore]:
        store = self.store("custody-" + registry, registry)

        class Injected:
            def call(self, operation: str, **arguments: Any) -> Any:
                return getattr(store, operation)(registry, **arguments)

        ledger = TrustJournal(self.tmp / ("registry-" + registry), registry, Injected())
        ledger.initialize_reviewed_genesis()
        return ledger, store

    # -- domain registry with injected custody --------------------------
    def domain(self) -> tuple[Config, sqlite3.Connection, CustodianStore, Any]:
        root = self.tmp / "project"
        root.mkdir()
        cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        cfg.ensure_dirs()
        connection = db.connect(cfg.db_path)
        self.closers.append(connection.close)
        store = self.store("domain-custody", "r")

        class Adapter:
            available = True

            def call(self, operation: str, **arguments: Any) -> Any:
                if not self.available:
                    # Exactly what CustodianClient.call raises when the socket is unreachable.
                    raise CustodianError(UNREACHABLE)
                return getattr(store, operation)("r", **arguments)

        adapter = Adapter()
        original = writer_guard.resolve_custody

        def resolve(candidate: Config) -> tuple[str, Any]:
            if candidate.project_root.resolve() == root.resolve():
                return "r", adapter
            return original(candidate)

        self.monkeypatch.setattr(writer_guard, "resolve_custody", resolve)
        TrustJournal(cfg.data_dir / "trust" / "authority", "r", adapter).initialize_reviewed_genesis()
        return cfg, connection, store, adapter

    def close(self) -> None:
        for close in reversed(self.closers):
            close()


def _register_fence(store: CustodianStore, attempt: str, registry: str = REGISTRY, predecessor=None):
    return store.register_fence(
        registry,
        predecessor=predecessor or store.read_current(registry),
        attempt_id=attempt,
        holder="corpus-lease",
        local_token=1,
    )


def _decision(identity: str, action: str, *, timestamp: str = "2026-09-30T00:00:00Z", subject="subject"):
    policy = trust.default_policy()
    return trust.TrustDecision(
        decision_id=identity,
        engram_id=subject,
        content_digest="a" * 64,
        decision=action,  # type: ignore[arg-type]
        source_channel="local_authored",
        policy_id=policy.policy_id,
        policy_revision=policy.revision,
        policy_digest=policy.digest(),
        actor="operator",
        timestamp=timestamp,
    )


def _journal_commit(ledger: TrustJournal, store: CustodianStore, identity: str, action: str) -> None:
    fence = _register_fence(store, identity, ledger.registry_id)
    ledger.append(
        record_id=identity,
        kind="trust_decision",
        payload=_decision(identity, action).to_dict(),
        fence=fence,
        assert_owned=lambda: None,
    )


# ============================================================ fake socket


class FakeConn:
    def __init__(self, data: bytes = b""):
        self.data, self.pos, self.sent = data, 0, bytearray()

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


def _message_wire(message: dict[str, Any]) -> bytes:
    conn = FakeConn()
    trust_custodian_transport.send_message(conn, message)  # type: ignore[arg-type]
    return bytes(conn.sent)


def _receive_message(data: bytes) -> dict[str, Any]:
    return trust_custodian_transport.receive_message(
        FakeConn(data),
        deadline=time.monotonic() + 5,  # type: ignore[arg-type]
    )


def _receive_frame(data: bytes) -> dict[str, Any]:
    return trust_custodian_transport.receive_frame(FakeConn(data))  # type: ignore[arg-type]


def _deep(depth: int) -> bytes:
    return b'{"a":' * depth + b"1" + b"}" * depth


def _raw_message(raw: bytes, *, count: int | None = None, magic: bytes = b"MTC2") -> bytes:
    """Hand-built MTC2 envelope for payloads the honest encoder refuses to emit."""
    frag = trust_custodian_transport.FRAGMENT_BYTES
    n = count if count is not None else (len(raw) + frag - 1) // frag
    transfer = b"\x01" * 16
    out = magic + struct.pack("!II", len(raw), n) + transfer + hashlib.sha256(raw).digest()
    for index in range(n):
        chunk = raw[index * frag : (index + 1) * frag]
        out += transfer + struct.pack("!II", index, len(chunk)) + chunk
    return out


# ================================================================ receipts

_SIGNER = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
_PUB = _SIGNER.public_key().public_bytes_raw().hex()
_BIND = {
    "nonce": "nonce-fresh",
    "registry_id": REGISTRY,
    "epoch": 2,
    "operation": "read_current",
    "request_id": "request-1",
}
_RESULT = {"head_sequence": 7, "head_mac": "f" * 64}


def _receipt(**changes: Any) -> dict[str, Any]:
    return trust_custodian_transport.sign_receipt(
        _SIGNER, {**_BIND, "result": _RESULT, "error": None, **changes}
    )


def _verify(receipt: dict[str, Any], public: str = _PUB, **expect: Any) -> Any:
    return trust_custodian_transport.verify_receipt(receipt, public, **{**_BIND, **expect})


# ================================================================= bundles


def _zip(members: list[tuple[str, bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, raw, mode in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = mode << 16
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # duplicate-name UserWarning is the attack itself
                zf.writestr(info, raw)
    return buf.getvalue()


def _manifest_for(files: dict[str, bytes]) -> bytes:
    entries = tuple(
        bundles.BundleEntry(path=p, sha256=hashlib.sha256(b).hexdigest(), size=len(b))
        for p, b in sorted(files.items())
    )
    return bundles.BundleManifest(entries=entries).canonical_bytes()


def _bundle(
    files: dict[str, bytes],
    *,
    extra: list[tuple[str, bytes, int]] = (),  # type: ignore[assignment]
    manifest: bytes | None = None,
    signer: Ed25519PrivateKey | None = _SIGNER,
    archived: dict[str, bytes] | None = None,
) -> bytes:
    mbytes = manifest if manifest is not None else _manifest_for(files)
    members = [(p, b, 0o100644) for p, b in sorted((archived or files).items())]
    members += list(extra)
    members.append((bundles.MANIFEST_NAME, mbytes, 0o100644))
    if signer is not None:
        members.append((bundles.SIGNATURE_NAME, signer.sign(mbytes), 0o100644))
    return _zip(members)


def _root(key: Ed25519PrivateKey = _SIGNER, *, revoked: bool = False) -> bundles.TrustRoot:
    raw = key.public_key().public_bytes_raw()
    return bundles.TrustRoot(
        fingerprint=bundles.public_key_fingerprint(raw), public_key_bytes=raw, revoked=revoked
    )


SMALL_LIMITS = bundles.BundleLimits(max_entries=8, max_total_uncompressed=64 * 1024, max_file_bytes=16 * 1024)
GOOD_FILES = {"skill.egr.md": b"---\nname: x\n---\nbody\n", "assets/a.txt": b"alpha\n"}


def _verify_bundle(env: Env, archive: bytes, roots=None, limits=SMALL_LIMITS) -> dict[str, Any]:
    parent = env.tmp / "staging"
    result = bundles.verify_bundle(
        archive, roots=roots if roots is not None else [_root()], staging_parent=parent, limits=limits
    )
    return {"ok": result.ok, "entries": sorted(e.path for e in result.manifest.entries)}  # type: ignore[union-attr]


# =================================================================== cases

CASES: dict[str, Callable[[Env, bool], Any]] = {}


def case(name: str) -> Callable[[Callable[[Env, bool], Any]], Callable[[Env, bool], Any]]:
    def register(fn: Callable[[Env, bool], Any]) -> Callable[[Env, bool], Any]:
        assert name not in CASES, name
        CASES[name] = fn
        return fn

    return register


# ---- receipt / nonce / key / epoch / registry ---------------------------


def _receipt_case(**expect: Any) -> Callable[[Env, bool], Any]:
    def run(env: Env, attack: bool) -> Any:
        return {"result": _verify(_receipt(), **(expect if attack else {}))}

    return run


case("receipt_nonce_replay")(_receipt_case(nonce="nonce-next-request"))
case("receipt_request_id_substitution")(_receipt_case(request_id="request-2"))
case("receipt_operation_substitution")(_receipt_case(operation="commit_record"))
case("receipt_wrong_registry")(_receipt_case(registry_id="registry-two"))
case("receipt_stale_epoch")(_receipt_case(epoch=3))


@case("receipt_wrong_signing_key")
def _(env: Env, attack: bool) -> Any:
    public = Ed25519PrivateKey.from_private_bytes(b"\x09" * 32).public_key().public_bytes_raw().hex()
    return {"result": _verify(_receipt(), public if attack else _PUB)}


@case("receipt_boolean_epoch")
def _(env: Env, attack: bool) -> Any:
    # JSON true == 1 in Python; the binding must compare canonical bytes, not truthiness.
    return {"result": _verify(_receipt(epoch=True if attack else 1), epoch=1)}


@case("receipt_result_tamper")
def _(env: Env, attack: bool) -> Any:
    receipt = _receipt()
    if attack:
        receipt["payload"]["result"]["head_sequence"] = 99
    return {"result": _verify(receipt)}


@case("receipt_missing_domain_separation")
def _(env: Env, attack: bool) -> Any:
    receipt = _receipt()
    if attack:
        # Signature over the bare payload (no receipt domain tag): cross-protocol reuse.
        raw = json.dumps(receipt["payload"], sort_keys=True, separators=(",", ":")).encode()
        receipt["signature"] = _SIGNER.sign(raw).hex()
    return {"result": _verify(receipt)}


@case("receipt_nan_noncanonical")
def _(env: Env, attack: bool) -> Any:
    receipt = _receipt()
    if attack:
        receipt = json.loads(json.dumps(receipt).replace('"head_sequence": 7', '"head_sequence": NaN'))
    return {"result": _verify(receipt)}


@case("receipt_deep_json")
def _(env: Env, attack: bool) -> Any:
    receipt = _receipt()
    if attack:
        receipt["payload"]["result"] = _nested(50_000)
    return {"result": _verify(receipt)}


def _nested(depth: int) -> Any:
    value: Any = 1
    for _ in range(depth):
        value = {"a": value}
    return value


# ---- frames and fragmented messages --------------------------------------

GOOD_MESSAGE = {**_BIND, "op": "read_current", "args": {}}


def _frame_bytes(value: dict[str, Any]) -> bytes:
    conn = FakeConn()
    trust_custodian_transport.send_frame(conn, value)  # type: ignore[arg-type]
    return bytes(conn.sent)


@case("frame_oversized_header")
def _(env: Env, attack: bool) -> Any:
    good = _frame_bytes(GOOD_MESSAGE)
    data = (trust_custodian_transport.MAX_FRAME + 1).to_bytes(4, "big") + b"{}" if attack else good
    return {"frame": _receive_frame(data) == GOOD_MESSAGE}


@case("frame_zero_length")
def _(env: Env, attack: bool) -> Any:
    data = (0).to_bytes(4, "big") if attack else _frame_bytes(GOOD_MESSAGE)
    return {"frame": _receive_frame(data) == GOOD_MESSAGE}


@case("frame_truncated_body")
def _(env: Env, attack: bool) -> Any:
    good = _frame_bytes(GOOD_MESSAGE)
    return {"frame": _receive_frame(good[:-5] if attack else good) == GOOD_MESSAGE}


@case("frame_deep_json")
def _(env: Env, attack: bool) -> Any:
    if attack:
        body = _deep(100_000)
        return {"frame": _receive_frame(len(body).to_bytes(4, "big") + body)}
    return {"frame": _receive_frame(_frame_bytes(GOOD_MESSAGE)) == GOOD_MESSAGE}


@case("frame_non_object")
def _(env: Env, attack: bool) -> Any:
    if attack:
        body = b'["read_current"]'
        return {"frame": _receive_frame(len(body).to_bytes(4, "big") + body)}
    return {"frame": _receive_frame(_frame_bytes(GOOD_MESSAGE)) == GOOD_MESSAGE}


@case("message_bad_magic")
def _(env: Env, attack: bool) -> Any:
    wire = _message_wire(GOOD_MESSAGE)
    return {"message": _receive_message(b"MTC1" + wire[4:] if attack else wire) == GOOD_MESSAGE}


@case("message_logical_oversize")
def _(env: Env, attack: bool) -> Any:
    wire = _message_wire(GOOD_MESSAGE)
    if attack:
        total = trust_custodian_transport.MAX_LOGICAL + 1
        frag = trust_custodian_transport.FRAGMENT_BYTES
        wire = b"MTC2" + struct.pack("!II", total, (total + frag - 1) // frag) + wire[12:]
    return {"message": _receive_message(wire) == GOOD_MESSAGE}


@case("message_fragment_count_mismatch")
def _(env: Env, attack: bool) -> Any:
    wire = _message_wire(GOOD_MESSAGE)
    if attack:
        total = struct.unpack("!I", wire[4:8])[0]
        wire = wire[:4] + struct.pack("!II", total, 2) + wire[12:]
    return {"message": _receive_message(wire) == GOOD_MESSAGE}


def _two_fragment_wire() -> tuple[bytes, dict[str, Any]]:
    message = {**GOOD_MESSAGE, "args": {"pad": "x" * (trust_custodian_transport.FRAGMENT_BYTES + 10)}}
    return _message_wire(message), message


def _fragments(wire: bytes) -> tuple[bytes, list[bytes]]:
    header, rest, parts = wire[:60], wire[60:], []
    while rest:
        size = struct.unpack("!I", rest[20:24])[0]
        parts.append(rest[: 24 + size])
        rest = rest[24 + size :]
    return header, parts


@case("message_fragment_reorder")
def _(env: Env, attack: bool) -> Any:
    wire, message = _two_fragment_wire()
    if attack:
        header, parts = _fragments(wire)
        wire = header + parts[1] + parts[0]
    return {"message": _receive_message(wire) == message}


@case("message_fragment_transfer_splice")
def _(env: Env, attack: bool) -> Any:
    wire, message = _two_fragment_wire()
    if attack:
        # Second fragment spliced from a different (captured) transfer of the same message.
        other, _ = _two_fragment_wire()
        header, parts = _fragments(wire)
        _, foreign = _fragments(other)
        wire = header + parts[0] + foreign[1]
    return {"message": _receive_message(wire) == message}


@case("message_trailing_fragment")
def _(env: Env, attack: bool) -> Any:
    wire = _message_wire(GOOD_MESSAGE)
    return {"message": _receive_message(wire + b"\x00" if attack else wire) == GOOD_MESSAGE}


@case("message_body_tamper")
def _(env: Env, attack: bool) -> Any:
    wire = _message_wire(GOOD_MESSAGE)
    if attack:
        index = wire.index(b"read_current")
        wire = wire[:index] + b"commit_recor" + wire[index + 12 :]
    return {"message": _receive_message(wire) == GOOD_MESSAGE}


@case("message_deep_json")
def _(env: Env, attack: bool) -> Any:
    if attack:
        return {"message": _receive_message(_raw_message(_deep(100_000)))}
    return {"message": _receive_message(_raw_message(json.dumps(GOOD_MESSAGE).encode())) == GOOD_MESSAGE}


@case("message_non_object")
def _(env: Env, attack: bool) -> Any:
    raw = b'["read_current"]' if attack else json.dumps(GOOD_MESSAGE).encode()
    return {"message": _receive_message(_raw_message(raw)) == GOOD_MESSAGE}


# ---- malicious bundle author ----------------------------------------------


def _bundle_case(build: Callable[[], bytes], roots: Callable[[], list[bundles.TrustRoot]] | None = None):
    def run(env: Env, attack: bool) -> Any:
        if not attack:
            return _verify_bundle(env, _bundle(GOOD_FILES))
        return _verify_bundle(env, build(), roots() if roots else None)

    return run


case("bundle_zip_slip")(_bundle_case(lambda: _bundle(GOOD_FILES, extra=[("../escape.txt", b"x", 0o100644)])))
case("bundle_absolute_member")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("/tmp/abs.txt", b"x", 0o100644)]))
)
case("bundle_backslash_member")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("assets\\..\\..\\x.txt", b"x", 0o100644)]))
)
case("bundle_symlink_member")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("assets/link", b"/etc/passwd", 0o120777)]))
)
case("bundle_casefold_collision")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("SKILL.egr.md", b"shadow\n", 0o100644)]))
)
case("bundle_duplicate_member")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("assets/a.txt", b"evil\n", 0o100644)]))
)
case("bundle_decompression_bomb")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("assets/bomb.bin", b"\x00" * (1 << 20), 0o100644)]))
)
case("bundle_total_size_bomb")(
    _bundle_case(
        lambda: _bundle({f"assets/f{i}.bin": b"\x00" * (15 * 1024) for i in range(5)}),
    )
)
case("bundle_entry_count_flood")(_bundle_case(lambda: _bundle({f"assets/n{i}.txt": b"n" for i in range(12)})))
case("bundle_resource_tamper")(
    _bundle_case(lambda: _bundle(GOOD_FILES, archived={**GOOD_FILES, "assets/a.txt": b"omega\n"}))
)
case("bundle_unlisted_member")(
    _bundle_case(lambda: _bundle(GOOD_FILES, extra=[("assets/smuggled.sh", b"rm -rf /\n", 0o100644)]))
)
case("bundle_missing_signature")(_bundle_case(lambda: _bundle(GOOD_FILES, signer=None)))
case("bundle_untrusted_signer")(
    _bundle_case(lambda: _bundle(GOOD_FILES, signer=Ed25519PrivateKey.from_private_bytes(b"\x0b" * 32)))
)
case("bundle_revoked_root")(_bundle_case(lambda: _bundle(GOOD_FILES), roots=lambda: [_root(revoked=True)]))


@case("bundle_manifest_duplicate_key")
def _(env: Env, attack: bool) -> Any:
    if not attack:
        return _verify_bundle(env, _bundle(GOOD_FILES))
    canonical = _manifest_for(GOOD_FILES)
    # Duplicate "kind" key: last-wins parsers and first-wins parsers disagree on meaning.
    forged = canonical[:-1] + b',"kind":"magicite-bundle-manifest/1"}'
    return _verify_bundle(env, _bundle(GOOD_FILES, manifest=forged))


@case("bundle_manifest_noncanonical")
def _(env: Env, attack: bool) -> Any:
    if not attack:
        return _verify_bundle(env, _bundle(GOOD_FILES))
    pretty = json.dumps(json.loads(_manifest_for(GOOD_FILES)), indent=2, sort_keys=True).encode()
    return _verify_bundle(env, _bundle(GOOD_FILES, manifest=pretty))  # validly signed, non-canonical


# ---- custodian store: resequence / stale fence / rollback ---------------


@case("store_record_id_payload_reuse")
def _(env: Env, attack: bool) -> Any:
    store = env.store()
    fence = _register_fence(store, "a1")
    head = store.read_current(REGISTRY)
    record = store.prepare_record(
        REGISTRY,
        fence=fence,
        expected_head=head,
        record_id="rec-1",
        kind="trust_decision",
        payload=_decision("rec-1", "revoke").to_dict(),
    )
    store.commit_record(REGISTRY, fence=fence, expected_head=head, record=record)
    if attack:
        fence = _register_fence(store, "a2")
        try:
            store.prepare_record(
                REGISTRY,
                fence=fence,
                expected_head=store.read_current(REGISTRY),
                record_id="rec-1",
                kind="trust_decision",
                payload=_decision("rec-1", "admit").to_dict(),
            )
        finally:
            assert len(store.committed_records(REGISTRY)) == 2
            assert store.read_current(REGISTRY)["head_sequence"] == 2
    return {"head_sequence": store.read_current(REGISTRY)["head_sequence"]}


@case("store_commit_rolled_back_expected_head")
def _(env: Env, attack: bool) -> Any:
    store = env.store()
    fence = _register_fence(store, "a1")
    old_head = store.read_current(REGISTRY)
    first = store.prepare_record(
        REGISTRY,
        fence=fence,
        expected_head=old_head,
        record_id="revoke",
        kind="trust_decision",
        payload=_decision("revoke", "revoke").to_dict(),
    )
    store.commit_record(REGISTRY, fence=fence, expected_head=old_head, record=first)
    fence = _register_fence(store, "a2")
    head = store.read_current(REGISTRY)
    second = store.prepare_record(
        REGISTRY,
        fence=fence,
        expected_head=head,
        record_id="admit",
        kind="trust_decision",
        payload=_decision("admit", "admit").to_dict(),
    )
    try:
        store.commit_record(REGISTRY, fence=fence, expected_head=old_head if attack else head, record=second)
    finally:
        if attack:
            assert store.read_current(REGISTRY)["head_sequence"] == 2
    return {"head_sequence": store.read_current(REGISTRY)["head_sequence"]}


@case("store_paused_holder_prepares_after_new_fence")
def _(env: Env, attack: bool) -> Any:
    store = env.store()
    old = _register_fence(store, "paused-old")
    if attack:
        _register_fence(store, "newer-holder")
    head = store.read_current(REGISTRY)
    try:
        store.prepare_record(
            REGISTRY,
            fence=old,
            expected_head=head,
            record_id="late",
            kind="trust_decision",
            payload=_decision("late", "admit").to_dict(),
        )
    finally:
        if attack:
            assert store.read_current(REGISTRY)["pending_record_id"] is None
    return {"pending_record_id": store.read_current(REGISTRY)["pending_record_id"]}


# ---- authenticated journal ----------------------------------------------


@case("journal_forged_head_sequence")
def _(env: Env, attack: bool) -> Any:
    ledger, store = env.journal()
    _journal_commit(ledger, store, "admit", "admit")
    _journal_commit(ledger, store, "revoke", "revoke")
    if attack:
        head = json.loads(ledger.head_path.read_bytes())
        head["head_sequence"] += 5
        ledger.head_path.write_text(json.dumps(head))
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


@case("journal_head_deleted")
def _(env: Env, attack: bool) -> Any:
    ledger, store = env.journal()
    _journal_commit(ledger, store, "revoke", "revoke")
    if attack:
        ledger.head_path.unlink()
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


@case("journal_stale_fence_append")
def _(env: Env, attack: bool) -> Any:
    ledger, store = env.journal()
    fence = _register_fence(store, "paused-old")
    if attack:
        _register_fence(store, "newer-holder")
    before = store.read_current(REGISTRY)["head_sequence"]
    try:
        ledger.append(
            record_id="late-admit",
            kind="trust_decision",
            payload=_decision("late-admit", "admit").to_dict(),
            fence=fence,
            assert_owned=lambda: None,
        )
    finally:
        if attack:
            assert store.read_current(REGISTRY)["head_sequence"] == before
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


@case("journal_lease_lost_before_append")
def _(env: Env, attack: bool) -> Any:
    """A real writer lease is taken over by a newer fencing token before the append."""
    from magicite.storage import lease

    ledger, store = env.journal()
    fence = _register_fence(store, "holder")
    conn = db.connect(env.tmp / "lease.db")
    env.closers.append(conn.close)
    held = lease.CrossProcessLease(lock_path=env.tmp / "dream.lock", conn=conn, holder="corpus-writer")
    with held.acquire():
        if attack:
            conn.execute("UPDATE writer_lease SET fencing_token = fencing_token + 1, holder = 'newer-holder'")
            conn.commit()
        try:
            ledger.append(
                record_id="admit",
                kind="trust_decision",
                payload=_decision("admit", "admit").to_dict(),
                fence=fence,
                assert_owned=held.assert_owned,
            )
        finally:
            if attack:
                assert store.read_current(REGISTRY)["head_sequence"] == 1
                assert store.read_current(REGISTRY)["pending_record_id"] is None
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


@case("journal_precommit_materialization")
def _(env: Env, attack: bool) -> Any:
    """A prepared-but-uncommitted admit is written locally as if committed."""
    ledger, store = env.journal()
    _journal_commit(ledger, store, "revoke", "revoke")
    if attack:
        fence = _register_fence(store, "attacker")
        head = store.read_current(REGISTRY)
        record = store.prepare_record(
            REGISTRY,
            fence=fence,
            expected_head=head,
            record_id="admit",
            kind="trust_decision",
            payload=_decision("admit", "admit").to_dict(),
        )
        with ledger.journal_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        forged = json.loads(ledger.head_path.read_bytes())
        forged.update(head_sequence=record["sequence"], head_mac=record["mac"])
        ledger.head_path.write_text(json.dumps(forged))
        try:
            ledger.snapshot()
        finally:
            assert store.read_current(REGISTRY)["head_sequence"] == 2
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


@case("journal_cross_registry_transplant")
def _(env: Env, attack: bool) -> Any:
    """Whole authenticated history of another registry (admit) copied over this one (revoke)."""
    ledger, store = env.journal("registry-one")
    _journal_commit(ledger, store, "revoke", "revoke")
    if attack:
        other, other_store = env.journal("registry-two")
        _journal_commit(other, other_store, "admit", "admit")
        ledger.journal_path.write_bytes(other.journal_path.read_bytes())
        ledger.head_path.write_bytes(other.head_path.read_bytes())
    return {"latest": ledger.snapshot().latest_by_engram["subject"]["decision"]}


# ---- domain: mirrors, projections, custody availability ------------------


def _admitted(cfg: Config) -> bool:
    return trust.admission_still_valid(cfg, engram_id="subject", content_digest="a" * 64)


@case("domain_mirror_deletion")
def _(env: Env, attack: bool) -> Any:
    cfg, conn, _, _ = env.domain()
    trust.persist_decision(cfg, conn, _decision("admit", "admit"))
    if attack:
        trust.persist_decision(cfg, conn, _decision("revoke", "revoke"))
    # Original S16 exploit: every non-authoritative copy of the decision is erased.
    shutil.rmtree(trust.trust_decisions_dir(cfg), ignore_errors=True)
    conn.execute("DELETE FROM trust_decision")
    conn.commit()
    return {"admitted": _admitted(cfg), "latest": trust.latest_decision_for(cfg, "subject").decision}


@case("domain_sqlite_projection_flip")
def _(env: Env, attack: bool) -> Any:
    cfg, conn, _, _ = env.domain()
    trust.persist_decision(cfg, conn, _decision("admit", "admit"))
    if attack:
        trust.persist_decision(cfg, conn, _decision("revoke", "revoke"))
    # Projection forged to say "admit only": flip every row, drop the revoke row.
    conn.execute("UPDATE trust_decision SET decision='admit'")
    conn.execute("DELETE FROM trust_decision WHERE decision_id='revoke'")
    conn.commit()
    state = {"admitted": _admitted(cfg), "latest": trust.latest_decision_for(cfg, "subject").decision}
    trust.reload_from_mirror(cfg, conn)  # rebuild must come from authenticated history only
    rows = conn.execute("SELECT decision_id, decision FROM trust_decision ORDER BY decision_id").fetchall()
    state["rebuilt_rows"] = {r["decision_id"]: r["decision"] for r in rows}
    return state


@case("domain_hand_rollback_trust_tree")
def _(env: Env, attack: bool) -> Any:
    """Operator/attacker copies a pre-revoke trust/ tree back over the live one."""
    cfg, conn, _, _ = env.domain()
    trust.persist_decision(cfg, conn, _decision("admit", "admit"))
    saved = env.tmp / "pre-revoke-trust"
    shutil.copytree(trust.trust_dir(cfg), saved)
    trust.persist_decision(cfg, conn, _decision("revoke", "revoke"))
    if attack:
        shutil.rmtree(trust.trust_dir(cfg))
        shutil.copytree(saved, trust.trust_dir(cfg))
        assert not _admitted(cfg)
        return {"latest": trust.latest_decision_for(cfg, "subject").decision}
    return {"latest": trust.latest_decision_for(cfg, "subject").decision}


@case("domain_authority_deleted")
def _(env: Env, attack: bool) -> Any:
    cfg, conn, _, _ = env.domain()
    trust.persist_decision(cfg, conn, _decision("revoke", "revoke"))
    if attack:
        shutil.rmtree(cfg.data_dir / "trust" / "authority")
        assert not _admitted(cfg)
    return {"policy_id": trust.load_policy(cfg).policy_id}


@case("domain_custody_unavailable")
def _(env: Env, attack: bool) -> Any:
    cfg, conn, _, adapter = env.domain()
    trust.persist_decision(cfg, conn, _decision("admit", "admit"))
    if attack:
        adapter.available = False
        assert not _admitted(cfg)
    return {"admitted": _admitted(cfg), "policy_id": trust.load_policy(cfg).policy_id}


@case("domain_unenrolled_planted_mirror")
def _(env: Env, attack: bool) -> Any:
    cfg, conn, _, _ = env.domain()
    if attack:
        # A different, never-enrolled project tree whose only "authority" is a planted mirror.
        root = env.tmp / "unenrolled"
        (root / ".magicite").mkdir(parents=True)
        cfg = Config.load(root, env={"MAGICITE_EMBEDDING_PROVIDER": "hashing"})
        mirrors = trust.trust_decisions_dir(cfg)
        mirrors.mkdir(parents=True)
        (mirrors / "admit.json").write_text(json.dumps(_decision("admit", "admit").to_dict()))
        (trust.trust_policy_path(cfg)).write_text(json.dumps(trust.default_policy().to_dict()))
        assert not _admitted(cfg)
    else:
        trust.persist_decision(cfg, conn, _decision("admit", "admit"))
    return {"admitted": _admitted(cfg), "policy_id": trust.load_policy(cfg).policy_id}


@case("domain_planted_root_projection")
def _(env: Env, attack: bool) -> Any:
    """Attacker pins their own signer by writing the roots/policy projection files."""
    cfg, _, _, _ = env.domain()
    attacker = Ed25519PrivateKey.from_private_bytes(b"\x0c" * 32)
    if attack:
        root = _root(attacker)
        forged = trust.TrustPolicy(policy_id=trust.default_policy().policy_id, revision=2, roots=(root,))
        trust.trust_policy_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        trust.trust_policy_path(cfg).write_text(json.dumps(forged.to_dict()))
        trust.trust_roots_path(cfg).write_text(json.dumps(forged.to_dict()["roots"]))
    else:
        trust.pin_trust_root(cfg, public_key_bytes=attacker.public_key().public_bytes_raw())
    policy = trust.load_policy(cfg)
    return _verify_bundle(env, _bundle(GOOD_FILES, signer=attacker), roots=list(policy.roots))


@case("domain_route_body_after_revoke_with_forged_projections")
def _(env: Env, attack: bool) -> Any:
    from magicite.core import registry, router
    from magicite.embeddings.hashing_provider import get_embedder
    from magicite.mcp import bind_retrieval
    from magicite.mcp.registry import ToolContext
    from magicite.mcp.schemas import LoadSkillBodyInput

    cfg, conn, _, _ = env.domain()
    for source in (REPO / "tests/fixtures/toy-registry/engrams").glob("*.egr.md"):
        shutil.copy(source, cfg.registry_dir / source.name)
    embedder = get_embedder(dim=256)
    registry.register(cfg, conn, embedder, path=".magicite/engrams")
    for row in conn.execute("SELECT id, content_sha256 FROM engram").fetchall():
        registry.review_approve(
            cfg, conn, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
        )
    name = "proton-ge-proton-downgrade"
    row = conn.execute("SELECT id, content_sha256 FROM engram WHERE name=?", (name,)).fetchone()
    admit = trust.latest_decision_for(cfg, row["id"])
    if attack:
        registry.review_revoke(
            cfg, conn, engram_id=row["id"], expected_digest=row["content_sha256"], actor="operator"
        )
        # Forge every non-authoritative projection back to "admit".
        mirrors = trust.trust_decisions_dir(cfg)
        mirrors.mkdir(parents=True, exist_ok=True)
        (mirrors / f"{admit.decision_id}.json").write_text(json.dumps(admit.to_dict()))
        conn.execute("UPDATE trust_decision SET decision='admit' WHERE engram_id=?", (row["id"],))
        conn.commit()
    params = LoadSkillBodyInput(
        name=name,
        level="L2",
        expected_content_digest=row["content_sha256"],
        expected_policy_digest=bind_retrieval._active_policy_digest(cfg),
    )
    body = bind_retrieval.load_skill_body(ToolContext(cfg=cfg, conn=conn, embedder=embedder), params)
    routed = router.route(cfg, conn, embedder, query="rollback proton for a steam game", k=5)
    return {
        "body_status": body.status,
        "body_reason_codes": list(body.reason_codes),
        "body_disclosed": bool(body.procedure),
        "routed_subject": any(c.id == row["id"] for c in routed.candidates),
    }


@case("intake_staged_while_custody_unavailable")
def _(env: Env, attack: bool) -> Any:
    """External intake must not stage a pending record from a default/fallback policy."""
    _client_unreachable_message_is_product_text()
    cfg, conn, store, adapter = env.domain()
    before = store.read_current("r")["head_sequence"]
    adapter.available = not attack
    try:
        staged = trust.record_pending_intake(
            cfg, conn, engram_id="subject", content_digest="a" * 64, source_channel="external_file", actor="t"
        )
    finally:
        if attack:
            assert store.read_current("r")["head_sequence"] == before  # nothing appended
    return {"decision": staged.decision, "head_advanced": store.read_current("r")["head_sequence"] > before}


# ---- protected custody path: extended ACL (platform branches faked) ------


def _acl_target(env: Env) -> Path:
    target = env.tmp / "custody-profile.json"
    target.write_text("{}")
    return target


@case("acl_linux_posix_acl_xattr")
def _(env: Env, attack: bool) -> Any:
    """Linux branch on any host: POSIX ACL xattr on a protected custody path."""
    target = _acl_target(env)
    names = ["user.comment", "system.posix_acl_access"] if attack else ["user.comment", "security.selinux"]
    env.monkeypatch.setattr(trust_custodian_transport.sys, "platform", "linux")
    env.monkeypatch.setattr(trust_custodian_transport.os, "listxattr", lambda path: names, raising=False)
    trust_custodian_transport._reject_acl(target)
    return {"accepted": True}


@case("acl_darwin_extended_entry")
def _(env: Env, attack: bool) -> Any:
    """Darwin branch on any host: an extended ACL entry (write grant) on a protected custody path."""
    import ctypes

    target = _acl_target(env)
    text = b"!#acl 1\n" + (
        b"user:FFFFEEEE-DDDD-CCCC-BBBB-AAAA00000001:attacker:501:allow:write\n" if attack else b""
    )
    freed: list[int] = []

    class _Fn:
        def __init__(self, result: Any):
            self.result = result

        def __call__(self, *args: Any) -> Any:
            return self.result(*args) if callable(self.result) else self.result

    class FakeLibc:
        acl_get_fd_np = _Fn(0x1000)  # non-NULL: an extended ACL exists
        acl_to_text = _Fn(0x2000)
        acl_free = _Fn(lambda pointer: freed.append(pointer) or 0)

    env.monkeypatch.setattr(trust_custodian_transport.sys, "platform", "darwin")
    env.monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: FakeLibc())
    env.monkeypatch.setattr(ctypes, "string_at", lambda pointer: text)
    try:
        trust_custodian_transport._reject_acl(target)
    finally:
        assert sorted(freed) == [0x1000, 0x2000]  # both native buffers released on every path
    return {"accepted": True}


# ================================================================ helpers


def _matches(expected: dict[str, Any], observed: Any) -> None:
    assert isinstance(observed, dict), f"case must return a state dict, got {observed!r}"
    for key, value in expected.items():
        assert key in observed, f"state key {key!r} missing from {observed!r}"
        assert observed[key] == value, f"{key}: expected {value!r}, observed {observed[key]!r}"


def _execute(entry: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run positive control and attack in separate fresh environments; return the outcome."""
    witness, expected = entry["witness"], entry["expected"]
    fn = CASES[witness["case"]]
    with monkeypatch.context() as mp:
        control = Env((tmp / "control").resolve(), mp)
        control.tmp.mkdir()
        try:
            _matches(witness["control"], fn(control, False))
        finally:
            control.close()
    env = Env((tmp / "attack").resolve(), monkeypatch)
    env.tmp.mkdir()
    try:
        return _attack(fn, expected, env)
    finally:
        env.close()


def _attack(fn: Callable[[Env, bool], Any], expected: dict[str, Any], env: Env) -> dict[str, Any]:
    if expected["kind"] == "raises":
        exc_type = EXCEPTIONS[expected["exception"]]
        try:
            observed = fn(env, True)
        except exc_type as exc:
            assert type(exc).__name__ == expected["exception"], (
                f"expected exactly {expected['exception']}, got {type(exc).__name__}: {exc}"
            )
            assert str(exc) == expected["message"], (
                f"message {str(exc)!r} != declared {expected['message']!r}"
            )
            return {"result": "fail-closed", "exception": type(exc).__name__, "message": str(exc)}
        raise AssertionError(f"attack was accepted: {observed!r}")
    observed = fn(env, True)
    _matches(expected["state"], observed)
    return {"result": "fail-closed", "state": {k: observed[k] for k in expected["state"]}}


_OUTCOMES: dict[str, dict[str, Any]] = {}
NODE_VERIFICATION = {"raises": "recorded-raise", "state": "state-delegated-to-node"}
COMPLETENESS_ENV = "MAGICITE_TRUST_CORPUS_REQUIRE_COMPLETE"


def _child_env(**extra: str) -> dict[str, str]:
    """Child pytest environment: hermetic options, deterministic embedder."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    env.update(MAGICITE_EMBEDDING_PROVIDER="hashing", **extra)
    return env


def _completeness_required() -> bool:
    """Evidence runs (report path set, or explicit flag) must be complete; dev subsets may skip."""
    return bool(os.environ.get("MAGICITE_TRUST_CORPUS_REPORT")) or os.environ.get(COMPLETENESS_ENV) == "1"


# ================================================================= tests


def test_manifest_schema_and_identity() -> None:
    m = MANIFEST
    assert m["schema"] == SCHEMA_ID
    for key in ("corpus_id", "version", "description", "classes", "capabilities", "entries", "not_covered"):
        assert key in m, key
    assert isinstance(m["version"], int) and m["version"] >= 1
    classes, capabilities = m["classes"], m["capabilities"]
    assert classes and capabilities
    ids = [e["id"] for e in ENTRIES]
    assert len(ids) == len(set(ids)), "duplicate entry id"
    assert ids == sorted(ids) and ids == [f"TAC-{i:03d}" for i in range(1, len(ids) + 1)], (
        "ids must be contiguous"
    )
    titles = [e["title"] for e in ENTRIES]
    assert len(titles) == len(set(titles)), "duplicate entry title"
    cases = [e["witness"]["case"] for e in RUNNER_ENTRIES]
    assert len(cases) == len(set(cases)), "runner case reused by two entries"
    nodes = [e["witness"]["node"] for e in NODE_ENTRIES]
    assert len(nodes) == len(set(nodes)), "test node reused by two entries"
    required = {"id", "class", "title", "attack", "capability", "criteria", "expected", "witness"}
    for e in ENTRIES:
        assert set(e) == required, (e["id"], set(e) ^ required)
        assert ID_RE.match(e["id"])
        assert e["class"] in classes, (e["id"], e["class"])
        assert e["capability"] in capabilities, (e["id"], e["capability"])
        assert e["criteria"] and all(CRITERION_RE.match(c) for c in e["criteria"]), e["id"]
        assert len(e["attack"]) >= 20, e["id"]
        exp = e["expected"]
        assert exp["kind"] in ("raises", "state"), e["id"]
        if exp["kind"] == "raises":
            assert exp["message"] and re.match(r"^[A-Z]\w*Error$", exp["exception"]), e["id"]
            if e["witness"]["type"] == "runner":
                assert exp["exception"] in EXCEPTIONS, e["id"]  # the runner must resolve it
        else:
            assert isinstance(exp["state"], dict) and exp["state"], e["id"]
        w = e["witness"]
        if w["type"] == "runner":
            assert set(w) == {"type", "case", "control"}, e["id"]
            assert w["case"] in CASES, (e["id"], w["case"])
            assert isinstance(w["control"], dict) and w["control"], e["id"]
        else:
            assert w["type"] == "node" and set(w) == {"type", "node", "asserts", "verification"}, e["id"]
            assert NODE_RE.match(w["node"]), (e["id"], w["node"])
            # Raises are re-observed by the recorder; state is never presented as runner-asserted.
            assert w["verification"] == NODE_VERIFICATION[exp["kind"]], e["id"]
            assert len(w["asserts"]) >= 20, e["id"]
    # Every class is exercised, and every runner case is mapped by exactly one entry.
    assert {e["class"] for e in ENTRIES} == set(classes), "class without entries (or undeclared class)"
    assert set(cases) == set(CASES), f"unmapped runner cases: {set(CASES) - set(cases)}"
    for item in m["not_covered"]:
        assert set(item) == {"class", "reason", "residual"} and item["reason"], item


@pytest.mark.parametrize("entry", RUNNER_ENTRIES, ids=[e["id"] for e in RUNNER_ENTRIES])
def test_runner_entry_fails_closed_with_positive_control(
    entry: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attack fails closed exactly as declared; the unmutated pipeline succeeds."""
    _OUTCOMES[entry["id"]] = _execute(entry, tmp_path, monkeypatch)


def test_every_runner_class_has_a_positive_control() -> None:
    by_class: dict[str, int] = {}
    for e in RUNNER_ENTRIES:
        by_class[e["class"]] = by_class.get(e["class"], 0) + bool(e["witness"]["control"])
    assert by_class and all(count >= 1 for count in by_class.values())


@pytest.fixture(scope="module")
def collected_nodes() -> set[str]:
    files = sorted({e["witness"]["node"].split("::")[0] for e in NODE_ENTRIES})
    for f in files:
        assert (REPO / f).is_file(), f"witness module missing: {f}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            *files,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        env=_child_env(),
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    return {line.strip() for line in proc.stdout.splitlines() if "::" in line}


@pytest.fixture(scope="module")
def node_observations(collected_nodes: set[str], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Execute every witness node once in a child pytest with the raises recorder."""
    log = tmp_path_factory.mktemp("corpus-nodes") / "raises.json"
    nodes = [e["witness"]["node"] for e in NODE_ENTRIES if e["witness"]["node"] in collected_nodes]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
        + ["-p", "tests.support.raises_recorder", *nodes],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=1800,
        env=_child_env(MAGICITE_RAISES_LOG=str(log)),
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-4000:]
    return json.loads(log.read_text())


@pytest.mark.parametrize("entry", NODE_ENTRIES, ids=[e["id"] for e in NODE_ENTRIES])
def test_node_witness_resolves(entry: dict[str, Any], collected_nodes: set[str]) -> None:
    node = entry["witness"]["node"]
    assert node in collected_nodes, f"{entry['id']}: dead witness {node}"


@pytest.mark.parametrize("entry", NODE_ENTRIES, ids=[e["id"] for e in NODE_ENTRIES])
def test_node_witness_observes_declared_outcome(
    entry: dict[str, Any], node_observations: dict[str, Any]
) -> None:
    """The witness node passes; for raises entries it really catches the declared type AND full message.

    State expectations are not re-observed here: they are reported as
    ``node-passed-state-delegated`` (the node's own assertions), never as runner-checked.
    """
    node, expected = entry["witness"]["node"], entry["expected"]
    outcome = node_observations["outcomes"].get(node)
    if outcome == "skipped":
        _OUTCOMES[entry["id"]] = {"result": "node-skipped", "node": node}
        pytest.skip(f"{entry['id']}: witness skipped on this platform; not counted as executed")
    assert outcome == "passed", f"{entry['id']}: witness {node} outcome {outcome!r}"
    if expected["kind"] == "state":
        _OUTCOMES[entry["id"]] = {"result": "node-passed-state-delegated", "node": node}
        return
    seen = node_observations["raises"].get(node, [])
    declared = [expected["exception"], expected["message"]]
    assert declared in seen, f"{entry['id']}: declared {declared} not observed; recorded {seen}"
    _OUTCOMES[entry["id"]] = {
        "result": "node-raise-observed",
        "node": node,
        "exception": declared[0],
        "message": declared[1],
    }


def test_emit_report(tmp_path: Path) -> None:
    """Runs last in this module: emits entry id -> outcome for the r3 evidence package.

    Skipped witnesses count as not executed. Completeness is enforced only for
    evidence runs (MAGICITE_TRUST_CORPUS_REPORT set, or
    MAGICITE_TRUST_CORPUS_REQUIRE_COMPLETE=1); dev subsets (-k, --lf,
    --deselect) still write the honest partial report and skip.
    """
    skipped = sorted(k for k, v in _OUTCOMES.items() if v["result"] == "node-skipped")
    missing = sorted({e["id"] for e in ENTRIES if e["id"] not in _OUTCOMES} | set(skipped))
    counts: dict[str, int] = {}
    for value in _OUTCOMES.values():
        counts[value["result"]] = counts.get(value["result"], 0) + 1
    report = {
        "schema": REPORT_SCHEMA_ID,
        "corpus_schema": MANIFEST["schema"],
        "corpus_version": MANIFEST["version"],
        "manifest_path": str(MANIFEST_PATH.relative_to(REPO)),
        "manifest_sha256": MANIFEST_SHA256,
        "entry_count": len(ENTRIES),
        "runner_entries": len(RUNNER_ENTRIES),
        "node_entries": len(NODE_ENTRIES),
        "complete": not missing,
        "not_executed": missing,
        "skipped": skipped,
        "result_counts": dict(sorted(counts.items())),
        "completeness_enforced": _completeness_required(),
        "outcomes": {k: _OUTCOMES[k] for k in sorted(_OUTCOMES)},
        "not_covered": [item["class"] for item in MANIFEST["not_covered"]],
        "qualification": "fixture custody only; separate-UID deployment UNEVALUATED",
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (tmp_path / "trust-adversarial-corpus-report.json").write_text(text)
    dest = os.environ.get("MAGICITE_TRUST_CORPUS_REPORT")
    if dest:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(text)
    if missing:
        if _completeness_required():
            pytest.fail(f"evidence run incomplete; not executed: {missing}")
        pytest.skip(f"partial corpus run ({len(missing)} not executed; report complete=false): {missing}")
