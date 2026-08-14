"""structlog configuration — stderr ONLY (AC-002).

A single stray byte on stdout corrupts the stdio MCP channel. This module
is the one place allowed to touch ``logging``/``structlog`` global state;
every other module calls :func:`get_logger` and never configures a handler
itself. Import this before anything else in ``__main__``/``mcp.app`` so the
root logger is stderr-bound before any third-party library has a chance to
attach a stdout handler.
"""

from __future__ import annotations

import logging
import sys

import structlog

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    """Idempotent. Routes stdlib ``logging`` and structlog to stderr only."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _CONFIGURED:
        configure()
    return structlog.get_logger(name)
