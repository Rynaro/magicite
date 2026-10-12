#!/usr/bin/env python3
"""AC-TH-05 syscall-level witness: custodian fsync happens-before the client head replace.

Three modes:

``--run --workdir DIR [--run-manifest PATH]``
    Executes the production trust append path against a real fixture custodian
    (``CustodianStore.create`` in ``DIR``, same process) and a ``TrustJournal``
    append of one ``trust_decision``. Meant to be executed under::

        strace -f -y -tt -e trace=fsync,fdatasync,rename,renameat,renameat2,write,unlink,unlinkat \\
            -o TRACE python scripts/probe_custodian_fsync_order.py --run --workdir DIR

    The custody client is an instrumented pass-through that writes short
    ``MGP:`` marker lines to a marker file around the append and around the
    ``commit_record`` custody call, so the analyzer can isolate exactly that
    transaction in the trace. No production code is patched.

``--analyze TRACE [--run-manifest PATH] [--output JSON]``
    Parses the strace output (fd paths resolved by ``-y``) and asserts that the
    custodian sqlite commit fsync/fdatasync on ``authority.sqlite`` (or its
    ``-journal``/``-wal``) inside the ``commit_record`` window COMPLETES before
    the client's rename of the ``.trust-*`` temporary onto ``head.json`` starts.
    Emits ``{verdict, events, checks, strace_version, kernel, ...}``; exits 0 on
    ``pass``, 1 on ``violated``, 2 on ``missing-events``.

``--self-test``
    Runs the parser against built-in synthetic traces (no strace required).

Qualification: same-process fixture custody on one account. This witnesses the
syscall order of the mechanism only; separate-UID deployment stays UNEVALUATED.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "magicite.fsync-order-probe/1"
MARKER_PREFIX = "MGP:"
MARK_APPEND_BEGIN = "MGP:append:begin"
MARK_APPEND_END = "MGP:append:end"
MARK_COMMIT_BEGIN = "MGP:commit:begin"
MARK_COMMIT_END = "MGP:commit:end"
SQLITE_NAMES = frozenset({"authority.sqlite", "authority.sqlite-journal", "authority.sqlite-wal"})
SYNC_CALLS = frozenset({"fsync", "fdatasync"})
RENAME_CALLS = frozenset({"rename", "renameat", "renameat2"})
UNLINK_CALLS = frozenset({"unlink", "unlinkat"})
REGISTRY = "fsync-probe-registry"

# ----------------------------------------------------------------------------------- parsing

_PREFIX = re.compile(r"^(?:\[pid\s+)?(?P<pid>\d+)?\]?\s*(?P<ts>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<rest>.*)$")
_RESUMED = re.compile(r"^<\.\.\. (?P<name>\w+) resumed>(?P<tail>.*)$")
_CALL = re.compile(r"^(?P<name>\w+)\((?P<tail>.*)$")
_UNFINISHED = " <unfinished ...>"
_RESULT = re.compile(r"\)\s+=\s+(?P<ret>-?\d+|\?)(?P<err>.*)$")
_FD = re.compile(r"^(?P<fd>-?\d+|AT_FDCWD)(?:<(?P<path>.*)>)?$")


@dataclass
class Syscall:
    name: str
    pid: str | None
    timestamp: str
    start_line: int
    end_line: int
    args: list[str]
    ret: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.ret not in {"?", ""} and not self.ret.startswith("-")


@dataclass
class Event:
    kind: str
    syscall: str
    start_line: int
    end_line: int
    timestamp: str
    pid: str | None
    path: str | None = None
    source: str | None = None
    target: str | None = None
    marker: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def split_args(text: str) -> list[str]:
    """Split strace call arguments at top-level commas (quotes, <fd paths>, brackets aware)."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "<[{(":
            depth += 1
        elif char in ">]})":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def decode_string(arg: str) -> str | None:
    """Decode a strace C-string argument (``"..."`` with optional ``...`` truncation)."""
    if arg.endswith("..."):
        arg = arg[:-3]
    if len(arg) < 2 or not (arg.startswith('"') and arg.endswith('"')):
        return None
    try:
        return bytes(arg[1:-1], "latin-1").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return arg[1:-1]


def fd_path(arg: str) -> str | None:
    match = _FD.match(arg.strip())
    if not match or match.group("path") is None:
        return None
    path = match.group("path")
    return path[: -len(" (deleted)")] if path.endswith(" (deleted)") else path


def _finish(name: str, pid: str | None, ts: str, start: int, end: int, body: str) -> Syscall | None:
    matches = list(_RESULT.finditer(body))
    if not matches:
        return None
    result = matches[-1]
    args = split_args(body[: result.start()])
    return Syscall(name, pid, ts, start, end, args, result.group("ret"), result.group("err").strip())


def parse_trace(text: str) -> list[Syscall]:
    """Parse ``strace -f -y -tt`` output, stitching ``<unfinished ...>``/``resumed`` pairs."""
    calls: list[Syscall] = []
    pending: dict[str | None, tuple[str, str, int, str]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        prefix = _PREFIX.match(line)
        if prefix is None:
            continue
        pid, ts, rest = prefix.group("pid"), prefix.group("ts"), prefix.group("rest")
        if rest.startswith(("---", "+++")):
            continue
        resumed = _RESUMED.match(rest)
        if resumed is not None:
            started = pending.pop(pid, None)
            if started is None or started[0] != resumed.group("name"):
                continue
            name, partial, start, start_ts = started
            call = _finish(name, pid, start_ts, start, number, partial + resumed.group("tail"))
            if call is not None:
                calls.append(call)
            continue
        match = _CALL.match(rest)
        if match is None:
            continue
        name, tail = match.group("name"), match.group("tail")
        if tail.endswith(_UNFINISHED):
            pending[pid] = (name, tail[: -len(_UNFINISHED)], number, ts)
            continue
        call = _finish(name, pid, ts, number, number, tail)
        if call is not None:
            calls.append(call)
    calls.sort(key=lambda c: (c.end_line, c.start_line))
    return calls


def _join(directory: str | None, name: str | None) -> str | None:
    if name is None:
        return None
    if name.startswith("/") or directory is None:
        return name
    return directory.rstrip("/") + "/" + name


def _rename_paths(call: Syscall) -> tuple[str | None, str | None]:
    if call.name == "rename" and len(call.args) >= 2:
        return decode_string(call.args[0]), decode_string(call.args[1])
    if call.name in {"renameat", "renameat2"} and len(call.args) >= 4:
        return (
            _join(fd_path(call.args[0]), decode_string(call.args[1])),
            _join(fd_path(call.args[2]), decode_string(call.args[3])),
        )
    return None, None


def _unlink_path(call: Syscall) -> str | None:
    if call.name == "unlink" and call.args:
        return decode_string(call.args[0])
    if call.name == "unlinkat" and len(call.args) >= 2:
        return _join(fd_path(call.args[0]), decode_string(call.args[1]))
    return None


def classify(calls: list[Syscall]) -> list[Event]:
    events: list[Event] = []
    for call in calls:
        if not call.ok:
            continue
        common = {
            "syscall": call.name,
            "start_line": call.start_line,
            "end_line": call.end_line,
            "timestamp": call.timestamp,
            "pid": call.pid,
        }
        if call.name == "write" and len(call.args) >= 2:
            text = decode_string(call.args[1]) or ""
            if text.startswith(MARKER_PREFIX):
                events.append(Event(kind="marker", marker=text.strip(), path=fd_path(call.args[0]), **common))
        elif call.name in SYNC_CALLS and call.args:
            path = fd_path(call.args[0])
            if path is None:
                continue
            base = os.path.basename(path)
            if base in SQLITE_NAMES:
                events.append(Event(kind="custodian-sqlite-sync", path=path, **common))
            elif base == "journal.jsonl" or base.startswith(".trust-"):
                events.append(Event(kind="client-file-sync", path=path, **common))
        elif call.name in RENAME_CALLS:
            source, target = _rename_paths(call)
            if source is None or target is None:
                continue
            if os.path.basename(source).startswith(".trust-") and os.path.basename(target) == "head.json":
                events.append(Event(kind="client-head-replace", source=source, target=target, **common))
            elif os.path.basename(source).startswith(".trust-"):
                events.append(Event(kind="client-file-replace", source=source, target=target, **common))
        elif call.name in UNLINK_CALLS:
            path = _unlink_path(call)
            if path is not None and os.path.basename(path) == "authority.sqlite-journal":
                events.append(Event(kind="custodian-journal-unlink", path=path, **common))
    return events


def _within(event: Event, begin: Event, end: Event) -> bool:
    return begin.end_line < event.start_line and event.end_line < end.start_line


def _first(events: list[Event], marker: str) -> Event | None:
    return next((e for e in events if e.kind == "marker" and e.marker == marker), None)


def _parent(path: str | None) -> str | None:
    return None if path is None else os.path.dirname(path)


def evaluate(text: str, *, custody_dir: str | None = None, registry_dir: str | None = None) -> dict[str, Any]:
    """Return the ordering verdict for one trace (pure function; no strace needed)."""
    events = classify(parse_trace(text))
    missing: list[str] = []
    violations: list[str] = []
    checks: dict[str, Any] = {}
    markers = {
        name: _first(events, name)
        for name in (MARK_APPEND_BEGIN, MARK_COMMIT_BEGIN, MARK_COMMIT_END, MARK_APPEND_END)
    }
    for name, event in markers.items():
        if event is None:
            missing.append("marker " + name)
    window: list[Event] = []
    commit_syncs: list[Event] = []
    heads: list[Event] = []
    unlinks: list[Event] = []
    if not missing:
        append_begin, append_end = markers[MARK_APPEND_BEGIN], markers[MARK_APPEND_END]
        commit_begin, commit_end = markers[MARK_COMMIT_BEGIN], markers[MARK_COMMIT_END]
        assert append_begin and append_end and commit_begin and commit_end
        if not (append_begin.end_line < commit_begin.end_line < commit_end.end_line < append_end.end_line):
            violations.append("probe markers out of order")
        window = [e for e in events if e.kind != "marker" and _within(e, append_begin, append_end)]
        commit_syncs = [
            e
            for e in window
            if e.kind == "custodian-sqlite-sync"
            and _within(e, commit_begin, commit_end)
            and (custody_dir is None or _parent(e.path) == custody_dir)
        ]
        heads = [
            e
            for e in window
            if e.kind == "client-head-replace" and (registry_dir is None or _parent(e.target) == registry_dir)
        ]
        unlinks = [
            e for e in window if e.kind == "custodian-journal-unlink" and _within(e, commit_begin, commit_end)
        ]
        if not commit_syncs:
            missing.append("custodian sqlite fsync/fdatasync inside commit_record")
        if not heads:
            missing.append("client .trust-* -> head.json rename inside append")
        elif len(heads) > 1:
            violations.append(f"expected exactly one head.json replace in append, saw {len(heads)}")
        if commit_syncs and heads:
            last_sync, head = commit_syncs[-1], heads[0]
            ordered = last_sync.end_line < head.start_line
            checks["custodian_fsync_completes_before_head_replace"] = ordered
            if not ordered:
                violations.append("head.json replaced before the custodian commit fsync completed")
            returned = commit_end.end_line < head.start_line
            checks["commit_record_returned_before_head_replace"] = returned
            if not returned:
                violations.append("head.json replaced before commit_record returned")
            names = sorted({os.path.basename(e.path or "") for e in commit_syncs})
            checks["custodian_synced_files"] = names
            if unlinks:
                # Rollback-journal mode: the journal unlink is sqlite's commit point.
                committed = unlinks[-1].end_line < head.start_line
                checks["custodian_journal_unlink_before_head_replace"] = committed
                if not committed:
                    violations.append("head.json replaced before the sqlite journal was retired")
    verdict = "missing-events" if missing else ("violated" if violations else "pass")
    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "missing": missing,
        "violations": violations,
        "checks": checks,
        "events": [asdict(e) for e in events if e.kind == "marker" or e in window],
        "custody_dir": custody_dir,
        "registry_dir": registry_dir,
    }


EXIT_CODES = {"pass": 0, "violated": 1, "missing-events": 2}

# ------------------------------------------------------------------------- synthetic traces

_C = "/tmp/probe/custody"
_R = "/tmp/probe/registry"
_M = "/tmp/probe/markers.log"


def _mark(pid: str, ts: str, text: str) -> str:
    payload = text + "\\n"
    return f'{pid} {ts} write(7<{_M}>, "{payload}", {len(text) + 1}) = {len(text) + 1}'


def _synthetic(*, violate: bool = False, drop_sync: bool = False, drop_head: bool = False) -> str:
    head = (
        f'100 10:00:00.000900 renameat(4<{_R}>, ".trust-ab12", 4<{_R}>, "head.json") = 0',
        f"100 10:00:00.000910 fsync(4<{_R}>) = 0",
    )
    commit = [
        _mark("100", "10:00:00.000500", MARK_COMMIT_BEGIN),
        f'100 10:00:00.000510 write(5<{_C}/authority.sqlite-journal>, "\\331\\325\\5\\371", 512) = 512',
        f"100 10:00:00.000520 fdatasync(5<{_C}/authority.sqlite-journal>) = 0",
        f'100 10:00:00.000530 write(3<{_C}/authority.sqlite>, "SQLite format 3", 4096) = 4096',
        f"100 10:00:00.000540 fsync(3<{_C}/authority.sqlite> <unfinished ...>",
        '101 10:00:00.000545 write(1</dev/null>, "noise", 5) = 5',
        "100 10:00:00.000550 <... fsync resumed>) = 0",
        f'100 10:00:00.000560 unlink("{_C}/authority.sqlite-journal") = 0',
        _mark("100", "10:00:00.000600", MARK_COMMIT_END),
    ]
    if drop_sync:
        commit = [line for line in commit if "sync(" not in line and "resumed" not in line]
    lines = [
        f'100 10:00:00.000000 renameat(4<{_R}>, ".trust-0000", 4<{_R}>, "head.json") = 0',
        _mark("100", "10:00:00.000100", MARK_APPEND_BEGIN),
        f"100 10:00:00.000200 fsync(5<{_C}/authority.sqlite>) = 0",
        f'100 10:00:00.000300 write(6<{_R}/journal.jsonl>, "{{\\"kind\\":\\"trust_decision\\"", 900) = 900',
        f"100 10:00:00.000310 fsync(6<{_R}/journal.jsonl>) = 0",
    ]
    if violate:
        lines += [*head, *commit]
    else:
        lines += [*commit, *(() if drop_head else head)]
    lines += [_mark("100", "10:00:00.001000", MARK_APPEND_END), "100 10:00:00.002000 +++ exited with 0 +++"]
    return "\n".join(lines) + "\n"


SYNTHETIC_TRACES = {
    "correct-order": (_synthetic(), "pass"),
    "violated-order": (_synthetic(violate=True), "violated"),
    "missing-custodian-fsync": (_synthetic(drop_sync=True), "missing-events"),
    "missing-head-replace": (_synthetic(drop_head=True), "missing-events"),
    "missing-markers": ("100 10:00:00.000000 fsync(3</x/authority.sqlite>) = 0\n", "missing-events"),
}


def self_test() -> int:
    failures = []
    for name, (text, expected) in SYNTHETIC_TRACES.items():
        verdict = evaluate(text, custody_dir=_C, registry_dir=_R)["verdict"]
        status = "ok" if verdict == expected else "FAIL"
        print(f"{status}: {name}: expected {expected}, got {verdict}")
        if verdict != expected:
            failures.append(name)
    return 1 if failures else 0


# ------------------------------------------------------------------------------- environment


def strace_version() -> str | None:
    binary = shutil.which("strace")
    if binary is None:
        return None
    proc = subprocess.run([binary, "-V"], capture_output=True, text=True, check=False)
    return (proc.stdout or proc.stderr).splitlines()[0].strip() if (proc.stdout or proc.stderr) else None


def kernel() -> dict[str, str]:
    uname = platform.uname()
    return {
        "system": uname.system,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
    }


# ---------------------------------------------------------------------------------------- run


class MarkedCustody:
    """Pass-through custody client that brackets commit_record with trace markers."""

    def __init__(self, store: Any, registry: str, marker_fd: int):
        self.store, self.registry, self.marker_fd = store, registry, marker_fd

    def mark(self, text: str) -> None:
        os.write(self.marker_fd, (text + "\n").encode())

    def call(self, operation: str, **arguments: Any) -> Any:
        if operation != "commit_record":
            return getattr(self.store, operation)(self.registry, **arguments)
        self.mark(MARK_COMMIT_BEGIN)
        try:
            return getattr(self.store, operation)(self.registry, **arguments)
        finally:
            self.mark(MARK_COMMIT_END)


def run_scenario(workdir: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    from magicite.core.trust import TrustDecision, default_policy
    from magicite.core.trust_custodian import CustodianStore
    from magicite.core.trust_journal import TrustJournal

    workdir.mkdir(parents=True, exist_ok=True)
    workdir = workdir.resolve()
    custody_dir, registry_dir = workdir / "custody", workdir / "registry"
    marker_path = workdir / "markers.log"
    marker_fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    store = CustodianStore.create(custody_dir)
    try:
        policy = default_policy()
        store.enroll(REGISTRY, policy.to_dict(), actor="probe-operator", reviewed=True)
        client = MarkedCustody(store, REGISTRY, marker_fd)
        journal = TrustJournal(registry_dir, REGISTRY, client)
        journal.initialize_reviewed_genesis()
        fence = store.register_fence(
            REGISTRY,
            predecessor=store.read_current(REGISTRY),
            attempt_id="probe-revoke",
            holder="probe-lease",
            local_token=1,
        )
        payload = TrustDecision(
            decision_id="probe-revoke",
            engram_id="probe-subject",
            content_digest="a" * 64,
            decision="revoke",
            source_channel="local_authored",
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            policy_digest=policy.digest(),
            actor="probe-operator",
            timestamp="2026-01-01T00:00:00Z",
        ).to_dict()
        client.mark(MARK_APPEND_BEGIN)
        snapshot = journal.append(
            record_id="probe-revoke",
            kind="trust_decision",
            payload=payload,
            fence=fence,
            assert_owned=lambda: None,
        )
        client.mark(MARK_APPEND_END)
        decision = snapshot.latest_by_engram["probe-subject"]["decision"]
    finally:
        store.close()
        os.close(marker_fd)
    return {
        "schema": SCHEMA + "#run",
        "custody_dir": str(custody_dir),
        "registry_dir": str(registry_dir),
        "marker_path": str(marker_path),
        "registry_id": REGISTRY,
        "acknowledged_decision": decision,
        "qualification": "same-process fixture custody; separate-UID deployment UNEVALUATED",
    }


# --------------------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="execute the traced append scenario")
    mode.add_argument("--analyze", type=Path, metavar="TRACE", help="analyze an strace output file")
    mode.add_argument("--self-test", action="store_true", help="check the parser on synthetic traces")
    parser.add_argument("--workdir", type=Path, help="scenario directory for --run (must not exist yet)")
    parser.add_argument("--run-manifest", type=Path, help="JSON written by --run, read by --analyze")
    parser.add_argument("--output", type=Path, help="JSON verdict path for --analyze")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.run:
        if args.workdir is None:
            parser.error("--run requires --workdir")
        manifest = run_scenario(args.workdir)
        if args.run_manifest is not None:
            args.run_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return 0 if manifest["acknowledged_decision"] == "revoke" else 1
    custody_dir = registry_dir = None
    if args.run_manifest is not None:
        manifest = json.loads(args.run_manifest.read_text())
        custody_dir, registry_dir = manifest["custody_dir"], manifest["registry_dir"]
    report = evaluate(
        args.analyze.read_text(errors="replace"), custody_dir=custody_dir, registry_dir=registry_dir
    )
    report["trace"] = str(args.analyze)
    report["strace_version"] = strace_version()
    report["kernel"] = kernel()
    report["qualification"] = "same-process fixture custody; separate-UID deployment UNEVALUATED"
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(text)
    print(text, end="")
    return EXIT_CODES[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
