"""Magicite error taxonomy (spec §3.1).

Every tool-facing error carries ``{code, message, hint, retryable}``. This
module is framework-free (no MCP imports, INV-1): ``magicite.mcp`` maps
:class:`MagiciteError` onto the wire-level MCP error response; callers in
``magicite.core``/``magicite.storage``/``magicite.engram`` raise these (or a
narrower subclass) without knowing anything about MCP.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """The frozen v1 error code set (spec §3.1)."""

    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    LINT_FAILED = "lint_failed"
    TRANSITION_DENIED = "transition_denied"
    APPROVAL_REQUIRED = "approval_required"
    BUSY = "busy"
    QUARANTINED = "quarantined"
    NOT_IMPLEMENTED = "not_implemented"
    IDEMPOTENCY_KEY_CONFLICT = "idempotency_key_conflict"
    PATH_OUTSIDE_PROJECT = "path_outside_project"


#: Error codes that are safe to retry unmodified (e.g. after a lease frees up).
_RETRYABLE_DEFAULTS: frozenset[ErrorCode] = frozenset({ErrorCode.BUSY})


class MagiciteError(Exception):
    """Base class for every error a Magicite tool body may raise.

    ``magicite.mcp.registry`` catches this (and only this, plus a generic
    fallback) at the tool-call boundary and maps it onto the MCP error
    envelope; nothing below the ``mcp`` package needs to know that MCP
    exists (INV-1).
    """

    code: ErrorCode = ErrorCode.INVALID_INPUT

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        hint: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or self.code
        self.message = message
        self.hint = hint
        self.retryable = self.code in _RETRYABLE_DEFAULTS if retryable is None else retryable
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }
        if self.details:
            d["details"] = self.details
        return d


class NotFoundError(MagiciteError):
    code = ErrorCode.NOT_FOUND


class InvalidInputError(MagiciteError):
    code = ErrorCode.INVALID_INPUT


class LintFailedError(MagiciteError):
    code = ErrorCode.LINT_FAILED


class TransitionDeniedError(MagiciteError):
    code = ErrorCode.TRANSITION_DENIED

    def __init__(self, message: str, unmet: list[str] | None = None, **kw: Any) -> None:
        super().__init__(message, details={"unmet": unmet or []}, **kw)
        self.unmet = unmet or []


class ApprovalRequiredError(MagiciteError):
    code = ErrorCode.APPROVAL_REQUIRED


class BusyError(MagiciteError):
    code = ErrorCode.BUSY


class QuarantinedError(MagiciteError):
    code = ErrorCode.QUARANTINED


class NotImplementedToolError(MagiciteError):
    code = ErrorCode.NOT_IMPLEMENTED


class IdempotencyKeyConflictError(MagiciteError):
    code = ErrorCode.IDEMPOTENCY_KEY_CONFLICT


class PathOutsideProjectError(MagiciteError):
    code = ErrorCode.PATH_OUTSIDE_PROJECT
