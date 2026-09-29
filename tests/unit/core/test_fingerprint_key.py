"""Local keyed HMAC fingerprint key provider (C6 / S00 provisional)."""

from __future__ import annotations

import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from magicite.config import Config
from magicite.core import fingerprint_key as fk


@pytest.fixture(autouse=True)
def _clear_key_override():
    fk.set_fingerprint_key_override(None)
    yield
    fk.set_fingerprint_key_override(None)


def test_same_query_same_fingerprint_with_same_key(cfg) -> None:
    key = fk.load_or_create_fingerprint_key(cfg)
    a = fk.query_fingerprint("hello world", key=key)
    b = fk.query_fingerprint("hello world", key=key)
    assert a == b
    assert len(a) == 64


def test_different_key_different_fingerprint(cfg) -> None:
    key_a = bytes(range(fk.KEY_BYTES))
    key_b = bytes(range(fk.KEY_BYTES - 1, -1, -1))
    assert fk.query_fingerprint("hello", key=key_a) != fk.query_fingerprint("hello", key=key_b)


def test_key_file_created_with_0600_permissions(cfg) -> None:
    cfg.ensure_dirs()
    path = fk.fingerprint_key_path(cfg)
    assert not path.exists()
    key = fk.load_or_create_fingerprint_key(cfg)
    assert path.is_file()
    assert len(key) == fk.KEY_BYTES
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
    # Second call reuses the same key bytes.
    assert fk.load_or_create_fingerprint_key(cfg) == key


def test_override_bypasses_disk_and_is_deterministic(cfg) -> None:
    override = os.urandom(fk.KEY_BYTES)
    fk.set_fingerprint_key_override(override)
    assert fk.load_or_create_fingerprint_key(cfg) == override
    assert not fk.fingerprint_key_path(cfg).exists()
    assert fk.query_fingerprint("q", key=override) == fk.query_fingerprint("q", key=override)


def test_fingerprint_is_not_unsalted_sha256() -> None:
    key = os.urandom(fk.KEY_BYTES)
    query = "low entropy"
    fp = fk.query_fingerprint(query, key=key)
    assert fp != hashlib.sha256(query.encode("utf-8")).hexdigest()


def test_corrupt_key_file_fails_closed(cfg) -> None:
    cfg.ensure_dirs()
    path = fk.fingerprint_key_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 8)
    with pytest.raises(ValueError, match="length 8"):
        fk.load_or_create_fingerprint_key(cfg)


def _mp_load_key(
    project_root: str,
    data_dir_name: str,
    ready,
    start,
    results,
) -> None:
    """Child entry: signal ready, wait for start, then first-create."""
    from magicite.core import fingerprint_key as child_fk

    try:
        child_fk.set_fingerprint_key_override(None)
        cfg = Config(project_root=Path(project_root), data_dir_name=data_dir_name)
        ready.put(os.getpid())
        assert start.wait(timeout=30)
        key = child_fk.load_or_create_fingerprint_key(cfg)
        results.put(("ok", key))
    except BaseException as exc:  # noqa: BLE001 — surface in parent
        results.put(("err", repr(exc)))


def test_concurrent_first_create_yields_one_key(tmp_path: Path) -> None:
    """Stress concurrent first-create across processes with a shared start gate.

    Runs many independent rounds so a create-then-write race would surface as
    a short-read ValueError or divergent keys. Atomic temp+link publish must
    keep every round on a single complete key.
    """
    ctx = get_context("spawn")
    workers = 8
    rounds = 25

    for round_idx in range(rounds):
        round_root = tmp_path / f"round-{round_idx}"
        data_dir = round_root / ".magicite"
        (data_dir / "runtime").mkdir(parents=True)
        ready = ctx.Queue()
        start = ctx.Event()
        result_q = ctx.Queue()

        processes = [
            ctx.Process(
                target=_mp_load_key,
                args=(str(round_root), ".magicite", ready, start, result_q),
            )
            for _ in range(workers)
        ]
        for process in processes:
            process.start()
        try:
            for _ in range(workers):
                ready.get(timeout=30)
            start.set()
            outcomes = [result_q.get(timeout=30) for _ in range(workers)]
        finally:
            for process in processes:
                process.join(timeout=30)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

        errors = [payload for status, payload in outcomes if status == "err"]
        assert not errors, f"round {round_idx} errors: {errors}"
        keys = [payload for status, payload in outcomes if status == "ok"]
        assert len(keys) == workers
        assert len(set(keys)) == 1, f"round {round_idx} produced divergent keys"
        published = data_dir / "runtime" / "fingerprint.key"
        assert published.read_bytes() == keys[0]
        assert len(keys[0]) == fk.KEY_BYTES
        shutil.rmtree(round_root)


def test_concurrent_first_create_threaded_stress(cfg, tmp_path: Path) -> None:
    """Same-process thread fan-out; many rounds against fresh directories."""
    for round_idx in range(25):
        round_root = tmp_path / f"thr-{round_idx}"
        round_cfg = Config(project_root=round_root, data_dir_name=".magicite")
        round_cfg.ensure_dirs()
        path = fk.fingerprint_key_path(round_cfg)
        assert not path.exists()

        with ThreadPoolExecutor(max_workers=16) as pool:
            keys = list(
                pool.map(
                    lambda _i, c=round_cfg: fk.load_or_create_fingerprint_key(c),
                    range(32),
                )
            )
        assert len(set(keys)) == 1
        assert path.read_bytes() == keys[0]
        shutil.rmtree(round_root)
