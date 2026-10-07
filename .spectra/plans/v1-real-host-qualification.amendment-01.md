# Amendment01 — modern protocol adoption

Reason: actual Claude Code2.1.288 tools/list request id0 carried io.modelcontextprotocol/protocolVersion2026-07-28 and clientInfo; matched response returned magicite serverInfo and16tools. No initialize RPC occurred. Capture observes messages before forwarding and retains initialize; no evidence of capture loss.

Primary source: /private/tmp/magicite-r3-qualification/venv/lib/python3.12/site-packages/mcp/client/client.py:333-339 documents explicit modern-version mode adopting without probe/initialize; legacy mode retains initialize. Source SHA256 255517918d489b645b28a714c51e200de414113e554c2074966825fb77cd4544. Observed sanitized wire /private/tmp/magicite-v1-host/early-7csdje95/wire.jsonl, SHA256 81fba7684ba2ce3e081d3aefc03909a7988f55144e65f4ed4c8aacf73c4b3cfa.

Change: AC-HQ02 accepts observed supported modern adoption or legacy negotiation, explicitly labels the mode, and still requires actual request/response/client/server/inventory/schema proof. Configured version alone never suffices. No host-proof, tool causality, digest, isolation or privacy requirement is relaxed. No new tool success is claimed; actual model calls currently failed authentication before route/body, so those criteria remain unmet.

Parent/root requested this evidence-driven amendment before source implementation. Maker/checker observations supplied independently; original criteria hash1208306d61e14ac3e936f9bca004268850e801159ec0fcc0a6de4f012d15744b retained in mechanical amendment chain.
