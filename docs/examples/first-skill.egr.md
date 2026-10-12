---
{
  "spec": "engram/1.0",
  "name": "summarize-supplied-change",
  "id": "egr_51a7c001",
  "version": 1,
  "intent": {
    "does": "Summarize a supplied change, diff, or notes for a teammate.",
    "use_when": "The current conversation supplies material to summarize.",
    "not_when": "Implementing a feature, executing commands, or independently verifying code."
  },
  "risk": {
    "filesystem": "none",
    "subprocess": {
      "mode": "none",
      "tools": []
    },
    "network": {
      "mode": "none",
      "destinations": []
    },
    "secrets": "none"
  },
  "routing": {
    "positive": [
      "summarize supplied change",
      "explain this diff",
      "summarize release notes"
    ],
    "negative": [
      "implement a feature",
      "run tests"
    ],
    "body_digest": "2717d8b815ba565c620195419abfc0070672b2adf16aae9e4daa10d3ab82db4f"
  },
  "origin": {
    "channel": "authored",
    "verification_status": "pending",
    "content_hashes": {
      "body_sha256": "2717d8b815ba565c620195419abfc0070672b2adf16aae9e4daa10d3ab82db4f"
    }
  }
}
---
## Procedure
1. Read the change description, diff, or notes supplied in the current conversation. Identify the stated goal and the evidence actually provided.
2. Summarize what changed and why in plain language. Distinguish observed behavior from assumptions; do not invent test results or missing details.
3. List important limitations or open questions only when they follow from the supplied material.
4. Return a short summary suitable for a teammate who has not read the original material. The assistant writes this response; Magicite only supplies this procedure.

## Pitfalls
- Do not claim to have inspected files, run commands, or verified a change unless that evidence was supplied.
- Do not treat this procedure as permission to execute commands or publish content.

## Examples
+ "Summarize these release notes for a teammate" → this engram
+ "Explain what changed in the supplied diff" → this engram
- "Implement and test a new feature" → NOT this engram

## Provenance
- Authored as a beginner example; pending operator review and exact-content trust approval.
