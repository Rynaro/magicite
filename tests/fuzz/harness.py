"""Deterministic, bounded fuzz harness (stdlib only, no hypothesis).

Bounds: fixed seed list, per-seed iteration cap, per-seed wall-clock cap,
per-input SIGALRM timeout (hang detection), per-run tracemalloc peak cap.
Invariant per input: return normally or raise ONLY a declared expected
exception type. Anything else (including timeout / memory overrun) is a
finding, recorded with a greedily minimized input.
"""

from __future__ import annotations

import json
import random
import signal
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SEEDS = (1, 2, 3, 20260930)
ITERATIONS_PER_SEED = 250
WALL_CAP_PER_SEED = 0.5  # seconds -> <= 2s per target across 4 seeds
INPUT_TIMEOUT = 2.0  # seconds, per single input (hang detector)
MEMORY_CAP = 256 * 1024 * 1024  # tracemalloc peak bytes per target run
MAX_INPUT = 256 * 1024


class FuzzTimeout(BaseException):
    """Raised by SIGALRM; BaseException so targets cannot swallow it."""


def _alarm(_signum: int, _frame: Any) -> None:
    raise FuzzTimeout()


# ---------------------------------------------------------------- mutators


def bit_flip(rng: random.Random, data: bytes) -> bytes:
    if not data:
        return data
    buf = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        buf[rng.randrange(len(buf))] ^= 1 << rng.randrange(8)
    return bytes(buf)


def truncate(rng: random.Random, data: bytes) -> bytes:
    return data[: rng.randrange(len(data) + 1)] if data else data


def splice(rng: random.Random, data: bytes, other: bytes) -> bytes:
    a = rng.randrange(len(data) + 1)
    b = rng.randrange(len(other) + 1)
    return data[:a] + other[b:]


def oversized_length(rng: random.Random, data: bytes) -> bytes:
    """Overwrite a 4-byte window with a boundary length value."""
    if len(data) < 4:
        return data
    value = rng.choice([0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 4 * 1024 * 1024, 4 * 1024 * 1024 + 1])
    off = rng.randrange(len(data) - 3)
    return data[:off] + value.to_bytes(4, "big") + data[off + 4 :]


def invalid_utf8(rng: random.Random, data: bytes) -> bytes:
    bad = rng.choice([b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf8\x88\x80\x80\x80", b"\x80"])
    off = rng.randrange(len(data) + 1)
    return data[:off] + bad + data[off:]


def deep_nesting(rng: random.Random, data: bytes) -> bytes:
    depth = rng.choice([10, 100, 1000, 5000, 100000])
    kind = rng.choice([("[", "]"), ('{"a":', "}")])
    return (kind[0] * depth + "1" + kind[1] * depth).encode()


def nan_inf(rng: random.Random, data: bytes) -> bytes:
    token = rng.choice([b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-0"])
    return data.replace(rng.choice([b"1", b"true", b'"a"', b"null"]), token, 1) if data else token


def duplicate_key(rng: random.Random, data: bytes) -> bytes:
    if data.startswith(b"{") and len(data) > 2:
        return b'{"schema":"x","payload":1,"epoch":0,' + data[1:]
    return data


def non_canonical(rng: random.Random, data: bytes) -> bytes:
    return rng.choice([b" ", b"\n", b"\t", b"\xef\xbb\xbf"]) + data.replace(b",", b" , ", 1)


BYTE_MUTATORS = (
    bit_flip,
    truncate,
    oversized_length,
    invalid_utf8,
    deep_nesting,
    nan_inf,
    duplicate_key,
    non_canonical,
)


def mutate(rng: random.Random, data: bytes, corpus: list[bytes]) -> bytes:
    for _ in range(rng.randint(1, 3)):
        choice = rng.randrange(len(BYTE_MUTATORS) + 1)
        if choice == len(BYTE_MUTATORS):
            data = splice(rng, data, rng.choice(corpus))
        else:
            data = BYTE_MUTATORS[choice](rng, data)
        data = data[:MAX_INPUT]
    return data


JUNK: tuple[Any, ...] = (
    None, True, False, 0, -1, 2**70, 1.5, "", "a" * 300, "\u0000", "x", [], {}, [None], {"k": 1}, [[[]]],
)


def structural(rng: random.Random, value: Any) -> Any:
    """Replace one random node of a JSON tree with junk (type confusion)."""
    if isinstance(value, dict) and value:
        key = rng.choice(sorted(value))
        out = dict(value)
        if rng.random() < 0.3:
            del out[key]
        elif rng.random() < 0.5:
            out[key] = structural(rng, value[key])
        else:
            out[key] = rng.choice(JUNK)
        return out
    if isinstance(value, list) and value:
        i = rng.randrange(len(value))
        out_l = list(value)
        out_l[i] = rng.choice(JUNK) if rng.random() < 0.5 else structural(rng, value[i])
        return out_l
    return rng.choice(JUNK)


# ---------------------------------------------------------------- driver


@dataclass
class Target:
    name: str
    func: Callable[[bytes], Any]
    expected: tuple[type[BaseException], ...]
    seeds: list[bytes]
    # Optional: bytes -> bool "was this specific input legitimately accepted"
    accept_check: Callable[[bytes, Any], str | None] | None = None
    structural_json: bool = False


@dataclass
class Report:
    seed: int
    target: str
    iterations: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    max_duration: float = 0.0
    findings: list[dict[str, Any]] = field(default_factory=list)
    peak_memory: int = 0

    def count(self, key: str) -> None:
        self.outcomes[key] = self.outcomes.get(key, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "target": self.target,
            "iterations": self.iterations,
            "outcomes": dict(sorted(self.outcomes.items())),
            "max_duration_s": round(self.max_duration, 6),
            "peak_memory_bytes": self.peak_memory,
            "findings": self.findings,
        }


def _run_one(
    target: Target, data: bytes, timeout: float
) -> tuple[str, str | None, float, Any]:
    """Return (outcome, detail, duration, result). Outcome: ok|expected:<T>|timeout|unexpected:<T>."""
    use_alarm = hasattr(signal, "setitimer")
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, timeout)
    start = time.perf_counter()
    result: Any = None
    try:
        result = target.func(data)
        outcome, detail = "ok", None
    except FuzzTimeout:
        outcome, detail = "timeout", f"exceeded {timeout}s"
    except target.expected as exc:
        outcome, detail = f"expected:{type(exc).__name__}", None
    except Exception as exc:
        outcome, detail = f"unexpected:{type(exc).__name__}", repr(exc)[:300]
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
    return outcome, detail, time.perf_counter() - start, result


def minimize(target: Target, data: bytes, outcome: str, timeout: float) -> bytes:
    """Greedy chunk-removal minimizer preserving the same failing outcome."""
    chunk = max(len(data) // 2, 1)
    budget = 300  # max re-executions; keeps worst-case minimization bounded
    while chunk >= 1 and len(data) > 1 and budget > 0:
        i = 0
        while i < len(data) and budget > 0:
            budget -= 1
            cand = data[:i] + data[i + chunk :]
            got, _, _, _ = _run_one(target, cand, timeout)
            if got == outcome and cand:
                data = cand
            else:
                i += chunk
        chunk //= 2
    return data


def run_target(
    target: Target,
    seed: int,
    *,
    iterations: int = ITERATIONS_PER_SEED,
    wall_cap: float = WALL_CAP_PER_SEED,
    input_timeout: float = INPUT_TIMEOUT,
    memory_cap: int = MEMORY_CAP,
) -> Report:
    rng = random.Random(f"{seed}:{target.name}")
    report = Report(seed=seed, target=target.name)
    started = time.monotonic()
    spent_minimizing = 0.0
    minimized_outcomes: set[str] = set()
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        # Iteration 0..len(seeds)-1 are the unmutated valid seeds (positive control).
        for i in range(iterations):
            if i > 0 and time.monotonic() - started - spent_minimizing > wall_cap:
                report.count("wall_cap_stop")
                break
            if i < len(target.seeds):
                data = target.seeds[i]
                kind = "seed"
            else:
                base = rng.choice(target.seeds)
                if target.structural_json and rng.random() < 0.5:
                    try:
                        tree = structural(rng, json.loads(base))
                        data = json.dumps(tree).encode()
                    except (ValueError, RecursionError):
                        data = base
                else:
                    data = mutate(rng, base, target.seeds)
                kind = "mutant"
            outcome, detail, dur, result = _run_one(target, data, input_timeout)
            report.iterations += 1
            report.max_duration = max(report.max_duration, dur)
            if kind == "seed":
                report.count("seed_" + outcome)
                if outcome != "ok":
                    report.findings.append(
                        {"kind": "valid-seed-rejected", "outcome": outcome, "detail": detail}
                    )
                continue
            report.count(outcome)
            bad = None
            if outcome == "timeout" or outcome.startswith("unexpected:"):
                bad = outcome
            elif outcome == "ok" and target.accept_check is not None:
                verdict = target.accept_check(data, result)
                if verdict is not None:
                    bad = "accepted-mutation"
                    detail = verdict
            if bad is not None:
                minimal = data
                if bad != "accepted-mutation" and outcome not in minimized_outcomes:
                    # Minimize only the first occurrence per outcome (bounded; excluded from the wall cap).
                    minimized_outcomes.add(outcome)
                    t0 = time.monotonic()
                    minimal = minimize(target, data, outcome, input_timeout)
                    spent_minimizing += time.monotonic() - t0
                if len(report.findings) >= 8:
                    continue
                report.findings.append(
                    {
                        "kind": bad,
                        "detail": detail,
                        "input_len": len(data),
                        "minimized_len": len(minimal),
                        "minimized_hex": minimal[:512].hex(),
                    }
                )
        _, peak = tracemalloc.get_traced_memory()
        report.peak_memory = peak
        if peak > memory_cap:
            report.findings.append({"kind": "memory-cap-exceeded", "detail": f"peak={peak} cap={memory_cap}"})
    finally:
        if not was_tracing:
            tracemalloc.stop()
    return report


def run_all(targets: list[Target], seeds: tuple[int, ...] = SEEDS, **kw: Any) -> list[Report]:
    return [run_target(t, s, **kw) for t in targets for s in seeds]


def dump(reports: list[Report]) -> str:
    return json.dumps(
        {"harness": "magicite-bounded-fuzz/1", "seeds": list(SEEDS), "runs": [r.as_dict() for r in reports]},
        indent=2,
        sort_keys=True,
    )
