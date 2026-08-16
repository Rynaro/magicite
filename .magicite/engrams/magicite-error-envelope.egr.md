---
spec: engram/0.2
name: magicite-error-envelope
id: egr_ac77d7f3
version: 1
provenance: authored
intent:
  does: "Raise and map Magicite tool errors through the single error envelope, and set the retryable flag honestly"
  use_when: "adding an error path to a tool body, or a caller is retrying something that will never succeed on retry"
  not_when: "the failure is a duplicate call being replayed from the idempotency cache rather than a genuine error"
triggers:
  positive:
    - "how do magicite tool errors reach the mcp client"
    - "magicite error code message hint retryable envelope"
    - "which magicite errors are retryable by default"
    - "magicite raise MagiciteError from a tool body"
  negative:
    - "magicite returned a cached response for a repeated request id"
context_affinity: [magicite, mcp, errors, architecture]
plasticity:
  storage_strength: 0.0
  exposure_count: 0
  outcome:
    success: 0
    failure: 0
  excitability: 0.05
  status: nascent
needs: []
yields: [well-formed-tool-error]
composes: []
inhibits: []
provenance_journal:
  - version: 1
    timestamp: "2026-08-15T00:00:00Z"
    author: "claude-orchestrator"
    event: authored
    note: "Codebase tranche (change magicite-codebase-skill-tranche, AC-T1)"
trust:
  origin: authored
  verification_status: pending
---
## Procedure
1. Raise a subclass of the base error from `errors.py` rather than a bare exception. The MCP boundary catches that base class, plus a generic fallback, and maps it onto the envelope — so a raw exception loses its code and hint on the way out.
2. Keep the layering intact. Nothing below the `mcp` package needs to know MCP exists; a tool body raises a domain error and the boundary does the translation. Importing MCP types into `core` to build an error is a layering violation, not a shortcut.
3. Pick the code from the existing taxonomy rather than inventing one: not-found, invalid-input, lint-failed, transition-denied, approval-required, busy, quarantined, not-implemented, idempotency-key-conflict, and path-outside-project all already exist.
4. Understand the retryable default, because it is deliberately narrow: only the busy code is retryable by default. Everything else defaults to not retryable, on the principle that a caller should not retry a request that was wrong rather than unlucky.
5. Override the flag explicitly only when the situation genuinely differs from its code's default, and know that you are making a promise to the caller about whether repeating the call can help.
6. Write the hint for the caller, not for the log. The envelope carries a hint field precisely so a client can be told what to do differently, and "check for unknown or malformed fields against the tool's input schema" is more useful than restating the error.
7. Remember that a path escaping the project root has its own code. Containment is enforced rather than advisory, so a tool that writes files must expect and surface that specific failure.
8. Do not encode retry policy in the message text. Callers key on the flag and the code; prose is for humans.

## Pitfalls
- (x1) Marking an input-validation failure retryable, which invites a client into an infinite loop over a request that can never succeed unchanged.
- (x1) Raising a bare exception from a tool body, which reaches the client as a generic failure with no code, no hint, and no retry guidance.
- (x1) Inventing a new code for a situation an existing one already covers, fragmenting the taxonomy callers switch on.
- (x1) Assuming more codes are retryable than actually are. The default set contains exactly one.

## Examples
+ "my tool needs to reject a bad argument" -> the invalid-input code, not retryable, steps 3 and 4
+ "a concurrent writer blocked us" -> the busy code, which is the retryable one, step 4
- "the same request id returned the old response" -> NOT this engram (idempotency replay, not an error)

## Provenance
- v1 2026-08-15 - authored by claude-orchestrator for the codebase tranche
