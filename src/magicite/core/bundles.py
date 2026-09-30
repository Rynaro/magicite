"""Portable registry bundles: manifest, staging, and offline Ed25519 verify (C10 / S04).

Fail closed. No network. Archive members are validated before any bytes leave
staging. Signature authenticity is independent of local admission (C10).
"""

from __future__ import annotations

import io
import json
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from magicite.engram.digests import canonical_json_bytes, sha256_hex
from magicite.errors import InvalidInputError

BUNDLE_MANIFEST_KIND: Literal["magicite-bundle-manifest/1"] = "magicite-bundle-manifest/1"
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.sig"


@dataclass(frozen=True, slots=True)
class BundleLimits:
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_total_uncompressed: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES


@dataclass(frozen=True, slots=True)
class BundleEntry:
    path: str
    sha256: str
    size: int
    media_type: str | None = None
    schema_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
        }
        if self.media_type is not None:
            out["media_type"] = self.media_type
        if self.schema_version is not None:
            out["schema_version"] = self.schema_version
        return out


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """Canonical bundle manifest (C10). Encoding: UTF-8 JSON, sorted keys, compact."""

    kind: Literal["magicite-bundle-manifest/1"] = BUNDLE_MANIFEST_KIND
    entries: tuple[BundleEntry, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "kind": self.kind,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return sha256_hex(self.canonical_bytes())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        if data.get("kind") != BUNDLE_MANIFEST_KIND:
            raise InvalidInputError(
                f"unsupported bundle manifest kind {data.get('kind')!r}",
                details={"expected": BUNDLE_MANIFEST_KIND},
            )
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise InvalidInputError("bundle manifest entries must be a list")
        entries: list[BundleEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                raise InvalidInputError("bundle manifest entry must be an object")
            path = item.get("path")
            sha = item.get("sha256")
            size = item.get("size")
            if not isinstance(path, str) or not isinstance(sha, str) or not isinstance(size, int):
                raise InvalidInputError("bundle entry requires path:str, sha256:str, size:int")
            if size < 0:
                raise InvalidInputError(f"bundle entry size must be >= 0 for {path!r}")
            entries.append(
                BundleEntry(
                    path=path,
                    sha256=sha,
                    size=size,
                    media_type=item.get("media_type") if isinstance(item.get("media_type"), str) else None,
                    schema_version=(
                        item.get("schema_version")
                        if isinstance(item.get("schema_version"), str)
                        else None
                    ),
                )
            )
        # Canonical order: sorted by path for stable digest of rebuilt manifests.
        entries.sort(key=lambda e: e.path)
        return cls(entries=tuple(entries))

    @classmethod
    def from_canonical_bytes(cls, raw: bytes) -> BundleManifest:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidInputError(f"bundle manifest is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise InvalidInputError("bundle manifest root must be an object")
        # Reject duplicate keys by re-encoding and comparing — json.loads keeps last
        # key; C10 forbids duplicates. Detect via object_pairs_hook.
        try:
            json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except InvalidInputError:
            raise
        return cls.from_dict(data)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise InvalidInputError(f"duplicate JSON key {key!r} in bundle manifest")
        out[key] = value
    return out


@dataclass(frozen=True, slots=True)
class BundleVerifyResult:
    ok: bool
    manifest: BundleManifest | None = None
    manifest_digest: str | None = None
    signer_fingerprint: str | None = None
    staging_dir: Path | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrustRoot:
    """Pinned public key identified by SHA-256 fingerprint of raw 32-byte key."""

    fingerprint: str
    public_key_bytes: bytes
    revoked: bool = False

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self.public_key_bytes)


def public_key_fingerprint(public_key_bytes: bytes) -> str:
    if len(public_key_bytes) != 32:
        raise InvalidInputError("Ed25519 public key must be 32 bytes")
    return sha256_hex(public_key_bytes)


def verify_manifest_signature(
    *,
    manifest_bytes: bytes,
    signature: bytes,
    roots: SequenceTrustRoots,
) -> str:
    """Verify detached Ed25519 signature; return matching key fingerprint.

    ``roots`` is an iterable of :class:`TrustRoot`. Unknown / revoked keys fail.
    """
    if not roots:
        raise InvalidInputError("no local trust roots configured; refuse unsigned/unknown admission")
    for root in roots:
        if root.revoked:
            continue
        try:
            root.public_key().verify(signature, manifest_bytes)
        except (InvalidSignature, ValueError):
            continue
        return root.fingerprint
    raise InvalidInputError(
        "bundle signature does not match any non-revoked local trust root",
        details={"configured_roots": [r.fingerprint for r in roots]},
    )


# Type alias kept simple for callers without importing typing.Sequence noise at runtime.
SequenceTrustRoots = list[TrustRoot] | tuple[TrustRoot, ...]


def _assert_safe_member_path(name: str) -> PurePosixPath:
    """Reject zip-slip, absolute paths, drive letters, UNC, NUL, and non-NFC paths."""
    if not name or name.endswith("/"):
        raise InvalidInputError(f"unsafe archive member path {name!r}")
    if "\x00" in name:
        raise InvalidInputError(f"NUL in archive member path rejected: {name!r}")
    if "\\" in name:
        raise InvalidInputError(f"archive member path must use POSIX separators: {name!r}")
    if name.startswith("/") or name.startswith("\\"):
        raise InvalidInputError(f"absolute archive member path rejected: {name!r}")
    # Windows drive-letter and UNC-style names (C10 path containment).
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise InvalidInputError(f"drive-letter archive member path rejected: {name!r}")
    if name.startswith("//") or name.startswith("\\\\"):
        raise InvalidInputError(f"UNC-style archive member path rejected: {name!r}")
    # Require NFC; reject other Unicode normal forms / non-ASCII for V1.
    nfc = unicodedata.normalize("NFC", name)
    if nfc != name:
        raise InvalidInputError(f"non-NFC archive member path rejected: {name!r}")
    posix = PurePosixPath(name)
    if posix.is_absolute() or ".." in posix.parts or posix.parts[0] == "":
        raise InvalidInputError(f"path escape rejected for archive member: {name!r}")
    # ASCII-only paths in V1 (align with engram asset path rule).
    try:
        name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidInputError(f"non-ASCII archive member path rejected: {name!r}") from exc
    return posix


def extract_bundle_archive(
    archive: Path | bytes,
    *,
    dest: Path,
    limits: BundleLimits | None = None,
) -> dict[str, Path]:
    """Extract a zip bundle into ``dest`` with zip-slip / bomb guards.

    Symlinks and absolute/escape paths are rejected before write. Returns a
    mapping of archive-relative path → extracted filesystem path.
    """
    limits = limits or BundleLimits()
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    raw = archive if isinstance(archive, bytes) else Path(archive).read_bytes()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise InvalidInputError(f"bundle is not a valid zip archive: {exc}") from exc

    written: dict[str, Path] = {}
    total = 0
    seen_casefold: dict[str, str] = {}

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_entries:
            raise InvalidInputError(
                f"bundle exceeds entry limit ({len(infos)} > {limits.max_entries})"
            )
        for info in infos:
            # ZipInfo.external_attr high bits: symlink on Unix when S_IFLNK.
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # S_IFLNK
                raise InvalidInputError(f"symlink archive member rejected: {info.filename!r}")
            rel = _assert_safe_member_path(info.filename)
            rel_s = str(rel)
            folded = rel_s.casefold()
            if folded in seen_casefold and seen_casefold[folded] != rel_s:
                raise InvalidInputError(
                    f"case-folding duplicate paths rejected: {seen_casefold[folded]!r} vs {rel_s!r}"
                )
            if rel_s in written:
                raise InvalidInputError(f"duplicate archive member path: {rel_s!r}")
            seen_casefold[folded] = rel_s

            if info.file_size > limits.max_file_bytes:
                raise InvalidInputError(
                    f"archive member {rel_s!r} exceeds per-file limit "
                    f"({info.file_size} > {limits.max_file_bytes})"
                )
            total += info.file_size
            if total > limits.max_total_uncompressed:
                raise InvalidInputError(
                    f"bundle exceeds total uncompressed limit "
                    f"({total} > {limits.max_total_uncompressed})"
                )

            target = (dest / rel_s).resolve()
            try:
                target.relative_to(dest)
            except ValueError as exc:
                raise InvalidInputError(f"extracted path escapes staging: {rel_s!r}") from exc

            target.parent.mkdir(parents=True, exist_ok=True)
            # Read with an explicit remaining budget to catch zip bombs that
            # under-declare file_size.
            with zf.open(info, "r") as src, open(target, "wb") as out:
                remaining = limits.max_file_bytes
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise InvalidInputError(
                            f"archive member {rel_s!r} exceeded per-file byte budget during extract"
                        )
                    out.write(chunk)
            written[rel_s] = target

    return written


def validate_staged_against_manifest(
    staging_dir: Path,
    manifest: BundleManifest,
    *,
    limits: BundleLimits | None = None,
) -> None:
    """Assert staged files match the manifest digests/sizes exactly."""
    limits = limits or BundleLimits()
    staging_dir = staging_dir.resolve()
    declared = {e.path: e for e in manifest.entries}
    if len(declared) > limits.max_entries:
        raise InvalidInputError("manifest exceeds entry limit")

    on_disk: set[str] = set()
    for path in staging_dir.rglob("*"):
        if not path.is_file():
            if path.is_symlink():
                raise InvalidInputError(f"symlink in staging rejected: {path}")
            continue
        if path.is_symlink():
            raise InvalidInputError(f"symlink in staging rejected: {path}")
        rel = str(path.relative_to(staging_dir).as_posix())
        if rel in (MANIFEST_NAME, SIGNATURE_NAME):
            continue
        on_disk.add(rel)
        entry = declared.get(rel)
        if entry is None:
            raise InvalidInputError(f"staged file not listed in manifest: {rel!r}")
        raw = path.read_bytes()
        if len(raw) != entry.size:
            raise InvalidInputError(
                f"size mismatch for {rel!r}: staged={len(raw)} manifest={entry.size}"
            )
        if sha256_hex(raw) != entry.sha256:
            raise InvalidInputError(f"digest mismatch for staged resource {rel!r}")

    missing = set(declared) - on_disk
    if missing:
        raise InvalidInputError(f"manifest entries missing from staging: {sorted(missing)}")


def verify_bundle(
    archive: Path | bytes,
    *,
    roots: SequenceTrustRoots,
    staging_parent: Path | None = None,
    limits: BundleLimits | None = None,
    require_signature: bool = True,
) -> BundleVerifyResult:
    """Stage → load canonical manifest → verify signature → check resource digests.

    On failure raises :class:`InvalidInputError` (fail closed) unless the caller
    prefers the result object — this function raises; use try/except at the
    intake boundary.
    """
    limits = limits or BundleLimits()
    if staging_parent is not None:
        parent = Path(staging_parent)
    else:
        parent = Path(tempfile.mkdtemp(prefix="magicite-bundle-"))
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="stage-", dir=str(parent)))

    written = extract_bundle_archive(archive, dest=staging, limits=limits)
    if MANIFEST_NAME not in written:
        raise InvalidInputError("bundle missing manifest.json")

    manifest_bytes = written[MANIFEST_NAME].read_bytes()
    # Re-canonicalize: accept only if bytes equal canonical encoding of parsed form.
    manifest = BundleManifest.from_canonical_bytes(manifest_bytes)
    canonical = manifest.canonical_bytes()
    if canonical != manifest_bytes:
        raise InvalidInputError(
            "bundle manifest is not in canonical encoding "
            "(UTF-8 JSON, sorted keys, compact separators)"
        )

    signer_fp: str | None = None
    if require_signature:
        if SIGNATURE_NAME not in written:
            raise InvalidInputError("bundle missing manifest.sig (detached Ed25519 signature)")
        signature = written[SIGNATURE_NAME].read_bytes()
        signer_fp = verify_manifest_signature(
            manifest_bytes=manifest_bytes, signature=signature, roots=roots
        )

    validate_staged_against_manifest(staging, manifest, limits=limits)
    return BundleVerifyResult(
        ok=True,
        manifest=manifest,
        manifest_digest=manifest.digest(),
        signer_fingerprint=signer_fp,
        staging_dir=staging,
        reasons=(),
    )


def build_manifest_from_directory(root: Path) -> BundleManifest:
    """Helper for tests/packaging: build a canonical manifest from a directory tree."""
    root = root.resolve()
    entries: list[BundleEntry] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in (MANIFEST_NAME, SIGNATURE_NAME):
            continue
        raw = path.read_bytes()
        media = "text/markdown" if rel.endswith(".md") else "application/octet-stream"
        schema = "engram/1.0" if rel.endswith(".egr.md") else None
        entries.append(
            BundleEntry(
                path=rel,
                sha256=sha256_hex(raw),
                size=len(raw),
                media_type=media,
                schema_version=schema,
            )
        )
    entries.sort(key=lambda e: e.path)
    return BundleManifest(entries=tuple(entries))


def write_signed_bundle(
    *,
    source_dir: Path,
    out_path: Path,
    private_key,
) -> tuple[BundleManifest, str]:
    """Test/helper: pack ``source_dir`` into a signed zip at ``out_path``.

    ``private_key`` is an ``Ed25519PrivateKey``. Returns (manifest, fingerprint).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private_key must be Ed25519PrivateKey")

    manifest = build_manifest_from_directory(source_dir)
    manifest_bytes = manifest.canonical_bytes()
    signature = private_key.sign(manifest_bytes)
    pub = private_key.public_key().public_bytes_raw()
    fp = public_key_fingerprint(pub)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest.entries:
            zf.write(source_dir / entry.path, arcname=entry.path)
        zf.writestr(MANIFEST_NAME, manifest_bytes)
        zf.writestr(SIGNATURE_NAME, signature)
    return manifest, fp
