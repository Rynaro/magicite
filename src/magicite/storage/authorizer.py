"""G1 -- the SQLite authorizer (spec §6.2, AC-013): "writers are a place, not
a rule". This module is what turns P0 from a code-review convention into an
API error.

``install()`` registers a callback via ``sqlite3.Connection.set_authorizer``
that returns ``SQLITE_DENY`` for ``INSERT``/``UPDATE``/``DELETE``/
``DROP TABLE``/``ALTER TABLE`` on any table whose name does not start with
``eph_`` -- exactly the four statement kinds spec §6.2 names, no more (reads
are never touched: ``SQLITE_SELECT`` is not in the guarded set, so every
``SELECT`` -- including the debounce/summary reads ``core/session.py`` and
``core/signals.py`` perform -- passes through untouched).

Two connection factories, and only two (spec §2.1):

- :func:`ephemeral_connection` -- authorizer-installed, handed to the seven
  hot-path tools (``route``, ``load_skill_body``, ``signal_use``,
  ``signal_outcome``, ``session_end``, ``introspect``, ``flag_dead``). A hot
  path attempt to write ``engram``, ``edge``, or any operational table raises
  ``sqlite3.DatabaseError`` before touching a page.
- :func:`writer_connection` -- no authorizer at all. Deliberately: G1 exists
  to constrain the *hot path*, not to strangle the writer/Dream path (the
  orchestrator's explicit instruction for this milestone). ``storage.durable``'s
  own G2 assertion (``assert_single_writer()``, spec §6.2) is what constrains
  this connection -- a *logical* guard, layered on top of G1's *physical* one,
  not a replacement for it.

Both factories open a connection to the *same* on-disk database (WAL mode,
spec §2.1: "concurrent readers + hot-path writer") -- they are two handles
onto one file, never two databases.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from magicite.storage import db as db_mod

#: spec §6.2 G1, verbatim: "INSERT/UPDATE/DELETE/DROP/ALTER on any table
#: whose name does not start with eph_". SQLITE_CREATE_TABLE is deliberately
#: excluded -- migrations run once, at startup, over the writer connection
#: (before/independent of any hot-path connection existing), and guarding
#: CREATE would only add surface area without protecting anything G1 cares
#: about (a table that does not yet exist cannot hold learned state).
_GUARDED_ACTIONS: frozenset[int] = frozenset(
    {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }
)

#: The eph_* prefix is the entire authorization boundary (spec §6.2 G1): no
#: table allowlist, no per-tool carve-out -- a table either starts with
#: ``eph_`` (Tier C, hot-path-writable) or it does not (everything else,
#: durable or operational, hot-path-writes DENY unconditionally).
_ALLOWED_PREFIX = "eph_"

#: M5 security fix #3: no tool exposes ``PRAGMA``/``ATTACH`` today (a
#: hot-path handler would have to reach for raw SQL no ``bind_*.py``
#: module currently does), so this is latent, not reachable -- but it is
#: cheap and removes an otherwise-unrecoverable failure mode: a
#: ``PRAGMA user_version=0`` from a hot-path connection would desync the
#: writer connection's migration bookkeeping from the actual schema, and
#: (before the accompanying ``IF NOT EXISTS`` migration fix) the next
#: ``run_migrations()`` call would then re-run ``CREATE TABLE`` statements
#: against an already-populated schema and fail outright, permanently
#: bricking boot. ``ATTACH`` is denied for the same "no legitimate
#: hot-path use, non-zero blast radius" reasoning -- it would let a
#: hot-path statement reach an entirely different on-disk database file,
#: which is a strictly larger escape than anything the eph_ prefix check
#: was ever meant to bound. Unlike ``_GUARDED_ACTIONS`` these two are
#: denied unconditionally: there is no table name to check them against
#: (``arg1`` is the pragma/database name, not an ``eph_`` candidate), and
#: no eph_-prefixed pragma or attached database is a legitimate hot-path
#: need.
_ALWAYS_DENIED_ACTIONS: frozenset[int] = frozenset({sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_ATTACH})


def _guarded_table_name(action: int, arg1: str | None, arg2: str | None) -> str | None:
    """Which table name a given authorizer callback invocation is *about*.

    For every guarded action except ``SQLITE_ALTER_TABLE`` the table name is
    ``arg1``. ``SQLITE_ALTER_TABLE`` is the one exception in SQLite's own
    authorizer contract: ``arg1`` is the *database* name there and ``arg2``
    is the table being altered -- getting this backwards would silently
    exempt every ``ALTER TABLE`` from the guard, which is exactly the kind
    of "reads as enforcement without being enforcement" failure this
    milestone exists to rule out.
    """
    if action == sqlite3.SQLITE_ALTER_TABLE:
        return arg2
    return arg1


def _authorizer_callback(
    action: int,
    arg1: str | None,
    arg2: str | None,
    dbname: str | None,
    source: str | None,
) -> int:
    if action in _ALWAYS_DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action not in _GUARDED_ACTIONS:
        return sqlite3.SQLITE_OK
    table = _guarded_table_name(action, arg1, arg2)
    if table is not None and table.startswith(_ALLOWED_PREFIX):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def install(conn: sqlite3.Connection) -> None:
    """Install G1 on ``conn``. Idempotent: re-installing simply replaces the
    callback with an equivalent one."""
    conn.set_authorizer(_authorizer_callback)


def ephemeral_connection(db_path: str | Path, *, migrate: bool = False) -> sqlite3.Connection:
    """spec §2.1: "ephemeral_connection() (authorizer-restricted, used by the
    hot path)". ``migrate`` defaults to ``False`` -- in the running server the
    writer connection is opened first and already applies migrations
    (spec §2.6's schema bootstrap is a durable-table operation, i.e. a writer
    concern); a fresh hot-path connection just needs PRAGMAs plus the guard.
    """
    conn = db_mod.connect(db_path, migrate=migrate)
    install(conn)
    return conn


def writer_connection(db_path: str | Path, *, migrate: bool = True) -> sqlite3.Connection:
    """spec §2.1: "writer_connection() (requires a held lease)". No
    authorizer is installed here -- G2 (``storage.lease``/``storage.durable``'s
    ``assert_single_writer()``) is the guard that applies to this connection,
    not G1. Callers MUST NOT treat the mere existence of this connection
    object as license to write: every public ``storage.durable`` function
    still asserts the lease before executing a single statement.
    """
    return db_mod.connect(db_path, migrate=migrate)
