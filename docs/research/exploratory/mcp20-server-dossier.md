# Production MCP Server Dossier

**Protocol baseline:** Model Context Protocol (MCP) `2026-07-28`  
**Audience:** Agentic development team building a new production MCP server  
**Status:** Implementation baseline  
**Last updated:** 2026-08-14

## Executive decision

Build the new service against MCP `2026-07-28` (informally called “MCP 2.0”) using a **stateless Streamable HTTP** architecture for remote operation. Keep domain and workflow state in ordinary durable application storage; do not depend on protocol sessions, sticky routing, or a persistent connection.

Use the current official SDK for the implementation language. For a TypeScript-oriented team, start with the official TypeScript SDK and a thin HTTP adapter. Use strict schemas, OAuth/OIDC, tenant-bound authorization, idempotency for writes, multi-round-trip approval for consequential actions, and durable Tasks only for genuinely asynchronous work.

---

## 1. Goals

### Product goal

Expose a bounded set of safe, useful domain capabilities to AI hosts and agents through MCP. The server may integrate with repositories, CI/CD, deployments, ticketing, knowledge bases, operational systems, or other product APIs, but it must never become an arbitrary privileged execution surface.

### Functional requirements

- Implement MCP `2026-07-28`.
- Support remote clients via stateless Streamable HTTP.
- Support local trusted development with `stdio` where useful.
- Expose tools, resources, and prompts only where each fits its intended purpose.
- Use explicit persistent state for workflows, approvals, idempotency, provider reconciliation, and audit records.
- Authenticate remote callers with OAuth 2.0/OIDC.
- Authorize every call using tenant-, principal-, and resource-aware policy.
- Require explicit confirmation for consequential writes.
- Provide reliable async execution through Tasks where operations exceed request-time constraints.
- Emit audit-grade traces, metrics, and structured logs without secrets.

### Security requirements

- No raw shell execution tool.
- No raw SQL tool.
- No unrestricted filesystem access.
- No unrestricted URL fetch tool.
- No shared, broad provider token across tenants.
- No reliance on model reasoning for authorization or approval decisions.
- No secret material in tool arguments, responses, prompts, logs, or audit events.

### Non-goals

- Maintaining transport-level MCP sessions.
- Designing new functionality around legacy HTTP+SSE.
- Using deprecated features for greenfield work: Roots, Sampling, Logging, Dynamic Client Registration, or legacy transport patterns.
- Giving agents generic administrative or infrastructure access.

---

## 2. Architecture principles

1. **Stateless protocol, durable domain state**

   MCP calls are independent. Store workflow and business state in PostgreSQL or another durable domain store. A deployment, approval, job, or reconciliation process must remain valid after a request finishes or a process restarts.

2. **The model proposes; policy decides; code executes**

   The agent can suggest an operation. Deterministic policy, server-side authorization, and validated code determine whether the action can happen.

3. **One tool, one bounded capability**

   Prefer `deployments.create_preview` over `manage_deployments`, and `repositories.create_pull_request` over `run_git_command`.

4. **Every boundary is hostile**

   Treat tool inputs as LLM-influenced and tool output as untrusted content. Validate, constrain, sanitize, redact, and audit both directions.

5. **Least privilege by construction**

   Tool scope, OAuth scope, provider credentials, tenant access, queue identity, filesystem access, and egress all need separate restrictions.

6. **Human confirmation is explicit and parameter-bound**

   For a high-impact action, approval must identify the exact target, immutable plan, arguments, executor identity, expiration, and rollback posture.

---

## 3. Protocol baseline

| Area | Decision |
|---|---|
| MCP specification | `2026-07-28` |
| Message format | JSON-RPC 2.0 |
| Remote transport | Stateless Streamable HTTP |
| Development transport | `stdio`, only for trusted local use |
| Main endpoint | `POST /mcp` |
| Version header | `MCP-Protocol-Version: 2026-07-28` |
| Method routing | `Mcp-Method` and `Mcp-Name` headers |
| Initialization | No protocol-session dependency; support optional discovery as needed |
| Long-running work | Tasks extension, only where justified |
| Interactive confirmation | Multi Round-Trip Requests (`input_required`) |
| Rich embedded UI | MCP Apps extension, only after target-host validation |
| Compatibility | SDK negotiation/fallback if required by real target hosts |

### HTTP request example

```http
POST /mcp HTTP/1.1
Host: mcp.example.com
Content-Type: application/json
Authorization: Bearer <access-token>
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: deployments.create_preview
Idempotency-Key: 46d2c4bb-9b4c-4951-9a17-9b0fc9b0c220

{
  "jsonrpc": "2.0",
  "id": "req_01J...",
  "method": "tools/call",
  "params": {
    "name": "deployments.create_preview",
    "arguments": {
      "repository": "acme/platform-api",
      "ref": "refs/pull/482/head",
      "environment": "preview"
    }
  }
}
```

### Network routes

| Route | Purpose | Exposure |
|---|---|---|
| `POST /mcp` | Core MCP endpoint | Public behind authentication gateway |
| `GET /.well-known/oauth-protected-resource` | OAuth protected-resource metadata | Public |
| `GET /.well-known/oauth-authorization-server` | Authorization-server metadata if owned by this service | Public |
| `GET /healthz` | Liveness probe | Internal/load balancer |
| `GET /readyz` | Dependency readiness probe | Internal/load balancer |
| `GET /metrics` | Metrics scrape | Private |
| `POST /webhooks/<provider>` | Signed inbound provider events | Public, separately secured |
| `GET /admin/audit/...` | Human administration and audit | Private, separate auth plane |

Do not combine administrative APIs and MCP APIs under loose route authorization. Keep their policies, identities, and network exposure separate.

---

## 4. Reference topology

```text
AI Host
  │
  │ OAuth/OIDC token + MCP JSON-RPC request
  ▼
Edge / API Gateway
  ├─ TLS termination
  ├─ WAF and DDoS controls
  ├─ Request-size limits
  ├─ Rate limiting by tenant, principal, and tool
  └─ Host/header validation
  ▼
MCP Adapter
  ├─ Protocol validation
  ├─ JSON-RPC parsing and correlation
  ├─ Tool/resource/prompt dispatch
  └─ Output canonicalization
  ▼
Identity and Policy Layer
  ├─ Token verification
  ├─ Tenant/principal/client binding
  ├─ RBAC and ABAC
  ├─ Approval and separation-of-duties checks
  └─ Risk classification
  ▼
Domain Service Layer
  ├─ Read services
  ├─ Mutation handlers
  ├─ Provider adapters
  ├─ Idempotency enforcement
  └─ Transaction boundaries
  ▼
Persistence and Async Plane
  ├─ PostgreSQL: workflows, approvals, audit, idempotency
  ├─ Redis: cache and bounded rate state
  ├─ Queue: durable asynchronous jobs
  └─ Object storage: time-limited tenant-scoped artifacts
  ▼
External Systems
  ├─ GitHub/GitLab
  ├─ CI/CD
  ├─ Cloud provider
  ├─ Ticketing
  └─ Internal APIs
```

### Trust-zone separation

| Component | Responsibility | Privilege profile |
|---|---|---|
| Edge/API gateway | TLS, WAF, limits, routing | No provider-admin credentials |
| MCP adapter | Protocol dispatch and output normalization | No unrestricted execution authority |
| Identity/policy layer | Authentication and authorization | Reads entitlements; does not execute provider actions |
| Domain executor | Executes approved business operations | Narrow provider identities only |
| Async worker | Durable long-running work | Separate workload identity |
| Approval service | Records and validates human approvals | May authorize; cannot execute actions directly |
| Administration plane | Configuration, policy management, audit review | Separate network and strong MFA |
| Secrets service | Issues/retrieves short-lived credentials | Never exposed to model context |

---

## 5. Capability design

### When to use each MCP feature

| Capability | Use for | Do not use for |
|---|---|---|
| Tools | Explicit actions or focused queries requested by the agent | Passive documents or unbounded workflows |
| Resources | Read-only contextual data and structured retrieval | Broad secret-bearing or bulk data export |
| Prompts | Curated, user-visible templates and workflows | Hidden authorization/policy logic |
| Tasks | Durable, asynchronous, resumable operations | Simple reads and short writes |
| MCP Apps | Optional rich inline forms, results, previews | Security-critical approval unless host guarantees are verified |

### Tool naming

Use clear, stable namespaces:

```text
repositories.get_pull_request
repositories.list_changed_files
repositories.create_branch
changes.create_proposal
changes.apply_approved_patch
deployments.plan_release
deployments.request_release_approval
deployments.execute_approved_release
incidents.get_summary
incidents.create_update_draft
knowledge.search
knowledge.get_document
```

Avoid vague or overly powerful names:

```text
manage_repository
admin_action
execute_workflow
run
perform_action
run_shell
```

### Risk classes

| Class | Example | Default interaction |
|---|---|---|
| R0 | Public/read-only status retrieval | Allow with basic auth/rate limits |
| R1 | Tenant-scoped issue/document/repository read | Tenant and user authorization required |
| R2 | Reversible write: draft, branch, comment | Idempotency, scoped authority, audit |
| R3 | Consequential write: preview deployment, configuration change | Plan, explicit confirmation, execution |
| R4 | High-impact action: production deploy, delete, IAM change, secret rotation | Two-step approval, expiry, separation of duties |
| R5 | Arbitrary shell, SQL, unrestricted network/filesystem | Prohibited |

### Mandatory tool metadata

Each tool must have:

- Stable namespaced name
- Factual short description
- Strict input schema
- Strict output schema
- Side-effect class
- Required OAuth scope(s)
- Required role(s) and contextual policy
- Idempotency behavior
- Timeout and response-size limits
- Audit classification
- Task eligibility
- Confirmation requirement
- Provider dependency/permission profile
- Retry/failure semantics
- Owner and deprecation/versioning plan

### Input schema requirements

- Reject unknown fields.
- Use constrained enums, IDs, ranges, regexes, and maximum lengths.
- Validate after parsing and before authorization/execution.
- Canonicalize identifiers before policy evaluation.
- Resolve indirect references server-side before acting.
- Keep a schema test corpus for invalid and adversarial payloads.

Example with Zod:

```ts
const CreatePreviewDeploymentInput = z.object({
  repository: z.string()
    .regex(/^[a-z0-9][a-z0-9._-]*\/[a-z0-9][a-z0-9._-]*$/i),
  ref: z.string()
    .regex(/^(refs\/(heads|pull)\/[A-Za-z0-9._/-]+|[a-f0-9]{7,64})$/),
  environment: z.literal("preview"),
  idempotencyKey: z.string().uuid()
}).strict();
```

Reject generic raw execution contracts:

```ts
z.object({ command: z.string() });
z.object({ sql: z.string() });
z.object({ url: z.string().url() });
z.object({ path: z.string() });
```

If a legitimate feature needs a URL or path, map it through server-owned allowlists, canonicalization, target authorization, egress restrictions, response-size limits, redirect constraints, and audit logging.

---

## 6. Approval and write workflows

### Approval state machine

Use this model for high-impact actions such as production deployment:

```text
1. deployments.plan_release
   → Generates immutable plan ID, diff summary, risk flags, rollback reference.

2. deployments.request_release_approval
   → Returns input_required with complete target, revision, environment,
     rollout strategy, plan hash, impact summary, and expiration.

3. Host collects an explicit human decision.

4. deployments.execute_approved_release
   → Verifies identity, tenant, plan hash, exact arguments, target,
     approval expiry, role, policy, idempotency key, and change-window policy.

5. Server creates durable operation/task record and returns structured status.
```

### Approval binding requirements

An approval record must include:

```text
approval_id
tenant_id
requesting_principal_id
approving_principal_id
required_role/assurance
operation_name
canonical_argument_hash
immutable_plan_id
immutable_plan_hash
target_resource_ids
environment
risk_class
created_at
expires_at
decision
policy_version
```

The approved action must fail if any material field differs: target, environment, repository, revision, provider account, rollout plan, command plan, or argument hash.

### Confirmation UI requirements

The host-facing confirmation must show:

- Exact tool name
- Tenant and target system
- Target resource IDs and environment
- Exact material parameters
- Side effects and rollback posture
- Data categories that will leave the system
- Executor identity
- Approval expiration
- Immutable plan/diff reference

Never implement silent approval, implicit approval through vague user wording, or a generic “continue” approval for high-impact operations.

---

## 7. Authentication and authorization

### Authentication

Use OAuth 2.0/OIDC for remote MCP clients.

For every request:

1. Validate TLS, allowed host, method, header policy, and maximum request size.
2. Validate token signature and expiry.
3. Validate token issuer against an explicit allowlist.
4. Validate resource/audience binding to this MCP server.
5. Extract immutable `tenant_id`, `principal_id`, `client_id`, scopes, and assurance context.
6. Validate protocol headers and JSON-RPC shape.
7. Validate the tool schema.
8. Evaluate tool policy using the canonicalized arguments.
9. Log the decision without secrets.

### Client registration preference

1. Pre-register known first-party clients.
2. Support Client ID Metadata Documents where dynamic metadata is needed.
3. Avoid Dynamic Client Registration in greenfield systems unless mandatory for compatibility.

### Authorization model

Use scopes as a coarse boundary and policy as the fine-grained decision layer:

```text
allow(tool, principal, tenant, resource, arguments, environment, risk)
  = valid token
  AND valid audience/resource binding
  AND principal belongs to tenant
  AND granted scope covers tool capability
  AND RBAC/ABAC permits the target resource
  AND provider entitlement permits the operation
  AND risk policy permits the action
  AND confirmation requirement is satisfied
  AND quota and rate limits allow execution
```

Recommended policy attributes:

```text
tenant_id
principal_id
client_id
authentication_assurance
tool_name
resource_owner
repository_id
project_id
environment
operation_class
change_window
approval_id
network_origin
risk_score
```

### Prevent confused deputy behavior

Never turn a user request into an action using broad server authority without reproducing user-level policy checks.

Use one of these patterns:

- Delegated user credentials, bound to the correct tenant/user.
- A server identity with a deterministic policy layer that enforces the user’s exact entitlements before using a downscoped provider credential.

Do not blindly forward inbound bearer tokens. Do not use a globally shared all-powerful provider token for all tenants.

---

## 8. Persistence, idempotency, and Tasks

### Persistent entities

```text
tenants
principals
oauth_clients
tool_entitlements
approval_requests
approval_decisions
operation_plans
operations
idempotency_keys
provider_connections
provider_credentials_metadata
audit_events
tool_catalog_versions
policy_versions
jobs
job_events
```

### Idempotency

All mutating tools must require an `idempotencyKey` or use an authenticated request header captured by the server.

Suggested idempotency record:

```text
tenant_id
principal_id
tool_name
idempotency_key
canonical_argument_hash
status
response_reference
created_at
expires_at
```

Rules:

- Same tenant, principal, tool, key, and canonical arguments: replay stored result.
- Same key with different arguments: return conflict.
- Write the idempotency record before any irreversible provider call.
- On timeout, reconcile with the provider before retrying.
- Never assume a failed HTTP response means the downstream action did not happen.

### Tasks extension

Use Tasks only for durable asynchronous work:

- Large exports
- Repository-wide indexing
- Builds
- Provisioning
- Deployment rollout
- Multi-step migrations
- Bulk operations
- Long-running provider operations

A Task needs:

- Durable task ID
- Tenant and principal ownership
- States: `queued`, `running`, `input_required`, `succeeded`, `failed`, `cancelled`, `expired`
- Status retrieval
- Structured event timeline
- Best-effort, documented cancellation
- Bounded retries and dead-letter path
- Per-tenant quotas and concurrency limits
- Resume/recovery behavior after worker restart
- Time-limited artifact references

Tasks do not weaken approval requirements. Re-evaluate authorization and approval validity at action execution time.

---

## 9. Data handling and output safety

### Data classification

| Class | Examples | MCP behavior |
|---|---|---|
| Public | Public docs, public repositories | Normal retrieval |
| Internal | Operational docs, internal tickets | Tenant authorization required |
| Confidential | Source code, customer records, incident data | Narrow retrieval, redaction, audit |
| Restricted | Secrets, keys, credentials, session tokens | Never return to model context |
| Regulated | Personal, financial, or health data | Explicit policy, minimization, redaction, retention controls |

### Output pipeline

Tool output is untrusted until processed:

```text
Provider result
→ validate against output schema
→ enforce response size limit
→ scan/redact secrets
→ extract structured fields
→ escape/remove active markup
→ tag instruction-like content where relevant
→ attach provenance metadata
→ return MCP response
```

Prefer structured results over raw documents:

```json
{
  "repository": "acme/platform-api",
  "pullRequest": 482,
  "status": "open",
  "changedFiles": 12,
  "additions": 302,
  "deletions": 46,
  "summary": "..."
}
```

### Secret management

- Store secrets only in a vault/KMS/secret manager.
- Use workload identities and short-lived provider credentials.
- Redact before logs, audit persistence, and MCP output.
- Deny retrieval of `.env`, private keys, cloud metadata, browser profiles, credential files, and CI variables by default.
- Rotate credentials after suspected exposure.
- Do not include sensitive values in tool descriptions, schemas, prompts, examples, traces, or errors.

---

## 10. Security control matrix

| Threat | Required controls |
|---|---|
| Prompt injection in tool output | Structured output, sanitization, provenance, no model-directed instructions from returned text |
| Tool poisoning/rug pull | Reviewed tool catalog, schema hash pinning, release signing, change alerts, compatibility tests |
| Cross-server shadowing | Namespace isolation, explicit trust configuration, no implicit delegation between servers |
| Confused deputy | User/tenant authorization on every call, downscoped credentials, audience checks |
| SSRF | URL allowlists, egress proxy, reserved-IP denylist, DNS rebinding defense, redirect controls |
| Command injection | No raw shell tools; fixed templates; argument arrays; sandboxed executor |
| Path traversal | Server-controlled roots; canonicalization; allowlisted paths; symlink-aware handling |
| Credential theft | Vault, workload identity, short-lived credentials, no secret output |
| Data exfiltration | Egress controls, DLP/redaction, explicit confirmation, quotas, audit |
| Replay/double execution | Idempotency, provider reconciliation, durable operation records |
| Supply-chain compromise | Lockfiles, SBOM, signed artifacts, dependency scans, provenance, review |
| Local server escape | Container sandbox, restricted mounts, non-root user, restricted network |
| Denial of service/cost abuse | Size limits, timeouts, quotas, rate limits, queue backpressure, per-tool concurrency |

### Local development hardening

For local `stdio` servers:

- Use an unprivileged user.
- Mount only the required workspace.
- Disable network access by default.
- Do not expose SSH keys, Docker socket, browser profiles, cloud credentials, Kubernetes configuration, password stores, or host home directory.
- Use separate dev and production credentials.
- Prefer ephemeral containers or equivalent isolation for untrusted third-party MCP servers.

---

## 11. Observability and audit

### Correlation fields

Every operation should connect these identifiers:

```text
trace_id
request_id
jsonrpc_id
mcp_method
mcp_name
tenant_id
principal_id
client_id
tool_name
operation_id
approval_id
task_id
provider_request_id
policy_version
tool_catalog_version
```

### Audit event example

```json
{
  "eventType": "mcp.tool.authorized",
  "occurredAt": "2026-08-14T21:30:11Z",
  "traceId": "...",
  "tenantId": "tenant_...",
  "principalId": "user_...",
  "clientId": "client_...",
  "tool": "deployments.execute_approved_release",
  "argumentHash": "sha256:...",
  "target": {
    "environment": "production",
    "repositoryId": "repo_...",
    "revision": "..."
  },
  "decision": "allow",
  "policyVersion": "2026-08-14.3",
  "approvalId": "approval_...",
  "result": "started",
  "secretRedactions": 0
}
```

### Required dashboards

- Tool calls by tenant, principal, client, and risk class
- Authorization denials by reason
- Approval requested, approved, rejected, expired
- Task latency, backlog, failures, cancellation
- External provider failure/rate-limit behavior
- Output redaction and secret-detection events
- Egress denials and SSRF attempts
- Suspicious privilege escalation attempts
- Per-tenant cost and resource consumption

Never store bearer tokens, authorization headers, plaintext secrets, unredacted PII, or raw provider payloads in audit records.

---

## 12. Testing strategy

### Protocol conformance

- Test all supported MCP methods and protocol-version behavior.
- Reject unsupported versions cleanly.
- Test JSON-RPC error behavior.
- Test header/body method and tool-name consistency.
- Maintain golden request/response fixtures.
- Verify against at least two actual target hosts.
- Test retry and reconnect behavior with multiple load-balanced server instances.

### Tool contract tests

For each tool, cover:

- Valid input
- Missing required field
- Unknown field
- Invalid enum/range/regex
- Oversized input
- Cross-tenant target
- Unauthorized principal
- Expired or mismatched approval
- Duplicate idempotency key
- Same key with changed arguments
- Provider timeout
- Provider partial success
- Retry after process restart
- Output containing secrets, markup, or instruction injection
- Maximum output size

### Security tests

- Prompt-injection corpus in resources and tool outputs
- Tool-description/schema mutation detection
- SSRF corpus: loopback, private, link-local, cloud metadata, redirects, encoded IPs, DNS rebinding
- Path traversal corpus: encoded separators, symlinks, null bytes, Windows/POSIX forms
- Command injection corpus
- OAuth issuer confusion, audience confusion, token substitution, expiry/revocation
- Tenant isolation tests
- Idempotency/replay tests
- Dependency and image scanning
- Red-team simulation for tool catalog or provider adapter compromise

### Resilience tests

- Burst traffic for discovery and common tools
- Queue saturation under per-tenant quotas
- Worker failure during a provider action
- Database failover
- Provider outage/rate limiting
- Deployment rollout while traffic is active
- Cache eviction
- WAF/API-gateway behavior with MCP headers

---

## 13. Delivery plan

### Phase 0: Architecture decisions

Deliver:

- ADR selecting MCP `2026-07-28`
- Target-host compatibility list
- Domain boundary and initial tool inventory
- Data classification matrix
- Threat model
- OAuth identity model
- Policy/approval/queue/vault/telemetry build-vs-buy choices

Exit criteria:

- Security and platform owners approve the risk taxonomy.
- Every proposed tool has an owner, risk class, required scopes, data classification, and rollback position.

### Phase 1: Safe vertical slice

Build:

- Stateless `POST /mcp`
- One tenant-scoped R1 read tool
- One R2 reversible write tool
- OAuth/OIDC validation
- Tenant binding
- Policy middleware
- Idempotency records
- PostgreSQL audit events
- OpenTelemetry instrumentation
- CI with linting, types, tests, and container/dependency scans

Exit criteria:

- Works with two intended hosts.
- Automated tests prove tenant isolation.
- Schemas reject unknown fields.
- Every test call has an end-to-end audit trail.

### Phase 2: Consequential operations

Build:

- Approval records and human-facing confirmation flow
- Multi Round-Trip Request handling
- Immutable plans and argument-hash binding
- Approval expiry
- Separation-of-duties policy
- Provider adapters with downscoped credentials
- External-action reconciliation

Exit criteria:

- R3/R4 actions cannot execute without a valid parameter-bound approval.
- Approvals cannot be reused for changed targets or arguments.
- Timeout handling reconciles downstream side effects.

### Phase 3: Async production operations

Build:

- Tasks extension support where host compatibility warrants it
- Queue workers, retries, cancellation, job events
- Quotas and concurrency controls
- SLOs, dashboards, alerts, runbooks
- Backup, recovery, and incident response procedures

Exit criteria:

- Long jobs survive worker restart.
- Shared infrastructure is protected by quotas.
- On-call engineers can trace a tool call through provider execution.

### Phase 4: Ecosystem hardening

Build:

- Tool-catalog hash pinning and change detection
- Signed artifact, SBOM, and provenance pipeline
- Continuous dependency/security scanning
- Isolation gateway if aggregating third-party MCP servers
- Host compatibility harness
- Periodic permission review and penetration test program

---

## 14. Definition of done

The server is production-ready only when all of the following are true:

- MCP `2026-07-28` is implemented over stateless Streamable HTTP.
- No feature relies on sticky routing, protocol sessions, or a persistent connection.
- All tools are narrow, typed, documented, versioned, and tested.
- Every write is idempotent and auditable.
- Every consequential action has a parameter-bound approval requirement.
- Every request is authenticated, audience-bound, tenant-bound, authorized, rate-limited, and logged.
- Provider credentials are scoped, isolated, rotated, and never disclosed to model context.
- Inputs and outputs are validated, constrained, and sanitized.
- Sensitive data is minimized and redacted.
- Long-running work has durable ownership, status, retries, cancellation semantics, and recovery behavior.
- Dashboards, alerts, runbooks, backups, and incident procedures exist and have been tested.
- The server has been validated against every host it claims to support.
- Tool and schema changes are reviewed, versioned, and detectable.

---

## 15. Initial implementation stack

| Layer | Recommended choice |
|---|---|
| MCP SDK | Official TypeScript SDK for a TypeScript-first team |
| HTTP adapter | Hono, Fastify, or a thin native adapter |
| Validation | Zod with strict schemas |
| Primary database | PostgreSQL |
| Queue | Managed durable queue or a workflow engine for complex orchestration |
| Cache/rate limits | Redis or managed edge rate limiting |
| Policy | Central ABAC policy engine or rigorously tested in-service policy module |
| Authentication | OAuth 2.0/OIDC, PKCE, pre-registered clients first |
| Secrets | Cloud KMS + vault/secret manager + workload identity |
| Telemetry | OpenTelemetry + Prometheus/Grafana + structured audit store |
| Deployment | Stateless containers, Kubernetes, Cloud Run, ECS, or validated edge runtime |
| Infrastructure as code | Terraform/OpenTofu and CI/GitOps deployment controls |

---

## 16. Source material

- MCP Specification `2026-07-28`: <https://modelcontextprotocol.io/specification/2026-07-28>
- Official MCP SDK directory: <https://modelcontextprotocol.io/docs/2026-07-28/sdk>
- OWASP MCP Security Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html>
- Cloudflare overview of the MCP v2/stateless model: <https://blog.cloudflare.com/mcp-v2/>

