AgentFlow — System Architecture & Detailed Design
AgentFlow — AI Business Operations PlatformTechnical Documentation
# 1. Logical Architecture
                        Web / API Clients                              |                         API Gateway                              |                       FastAPI Application                _________/____|____\_________               /              |               \        Auth/Tenant       Workflow API      Admin API               \              |               /                \_______ PostgreSQL ________/                              |                         Event Bus                     EventBridge / SQS                              |             +----------------+----------------+             |                                 |       Agent Runtime                      Async Workers             |                                 |       LangGraph                           SQS workers             |      +------+------+------+------+      |      |      |      |      |    Triage Research Knowledge Policy Execution      |      |      |      |      |      +------+------+-+----+------+                       |                  Tool Gateway                MCP / governed APIs                       |       +---------------+----------------+       |         |         |            |      CRM      Email    Calendar      TicketsData:S3 -> document ingestion -> parsing -> chunking -> embeddings -> PostgreSQL/pgvectorAll services -> OpenTelemetry -> CloudWatch / observability backend
# 2. Deployment Topology
- CloudFront/WAF for public web traffic.
- API Gateway or ALB in front of stateless FastAPI services.
- ECS/Fargate for predictable API/worker workloads; AgentCore Runtime for agent workloads where managed isolated agent execution is advantageous.
- RDS/Aurora PostgreSQL for transactional state and pgvector retrieval.
- S3 for documents, artifacts, exports, and immutable audit archives where required.
- SQS queues for asynchronous jobs and retries; DLQs for poison messages.
- EventBridge for domain events and automation triggers.
- Step Functions for long-running infrastructure workflows and human approval orchestration where appropriate.
- CloudWatch + OpenTelemetry for metrics, traces, and logs.
# 3. Domain Modules
| Module | Responsibilities |
| Identity | Users, organizations, roles, sessions, integration identities |
| Workflow | Workflow state, graph execution, retries, checkpoints |
| Knowledge | Sources, ingestion, retrieval, citations, document ACLs |
| Agent | Agent definitions, prompts, model routing, capabilities |
| Tooling | Tool registry, schemas, permissions, execution, idempotency |
| Policy | Risk classification, allow/deny/approval rules |
| Approval | Approval requests, reviewers, expiry, resumption |
| Automation | Triggers, conditions, actions, schedules |
| Audit | Immutable event history and actor/action records |
| Evaluation | Datasets, test cases, runs, scores, regression gates |
| Billing/Usage | Quotas, token usage, workflow counts, cost attribution |
# 4. Data Model
| Entity | Important fields |
| Organization | id, name, plan, status |
| User | id, identity_provider_id, status |
| Membership | org_id, user_id, role |
| Integration | id, org_id, provider, credential_ref, status |
| KnowledgeSource | id, org_id, type, uri, ACL, status |
| Document | id, source_id, checksum, version, metadata |
| Chunk | id, document_id, content, embedding, metadata |
| Conversation | id, org_id, requester_id |
| Workflow | id, org_id, type, status, idempotency_key, started_at, ended_at |
| WorkflowStep | id, workflow_id, node, status, attempt, input_ref, output_ref |
| AgentRun | id, workflow_step_id, agent_type, model, latency, token_usage |
| ToolDefinition | id, name, version, schema, risk_level |
| ToolExecution | id, workflow_id, tool_id, idempotency_key, status |
| Policy | id, org_id, version, rules, status |
| Approval | id, workflow_id, action, risk, reviewer, status, expires_at |
| AuditEvent | id, org_id, actor, action, target, timestamp, metadata |
| EvaluationCase | id, dataset_id, input, expected, tags |
# 5. Tenant Isolation
- Every tenant-owned row carries org_id.
- Application authorization checks org membership before every data access.
- Database Row-Level Security is a defense-in-depth layer.
- Tool credentials are tenant-scoped and never exposed to model prompts.
- Retrieval queries always include tenant and document ACL filters.
- Object storage prefixes are tenant-scoped and protected by IAM conditions where practical.
- Automated tests attempt cross-tenant reads/writes on every sensitive repository.
# 6. API Design
| Method | Endpoint | Purpose |
| POST | /v1/workflows | Start workflow |
| GET | /v1/workflows/{id} | Get workflow state |
| POST | /v1/workflows/{id}/cancel | Cancel workflow |
| POST | /v1/approvals/{id}/approve | Approve action |
| POST | /v1/approvals/{id}/reject | Reject action |
| POST | /v1/knowledge/sources | Register knowledge source |
| POST | /v1/knowledge/ingestions | Start ingestion |
| GET | /v1/audit/events | Query audit trail |
| GET | /v1/usage | Usage and cost metrics |
# 7. Idempotency
Every externally visible mutation must accept an Idempotency-Key. The server stores the request fingerprint and final result. Tool side effects use a separate action idempotency key derived from workflow ID + action type + logical target.
idempotency_key = client_keyaction_key = sha256(workflow_id + tool_name + logical_target + action_version)if action_key already COMPLETED:    return previous resultif action_key IN_PROGRESS:    return current operation statusexecute side effectpersist result atomicallyreturn result
# 8. Failure Handling
- Transient provider/API errors: bounded exponential backoff with jitter.
- Permanent validation errors: fail fast with actionable error.
- Poison messages: move to DLQ after configured max attempts.
- Agent timeout: checkpoint state and resume/retry from the last safe node.
- Human approval timeout: mark expired and stop side effects.
- Tool partial failure: use compensating action where possible; otherwise escalate.
- Provider outage: switch to a compatible fallback model for eligible task classes.
# 9. Architecture Decision Records
| Decision | Choice | Reason |
| Agent framework | LangGraph + LangChain | Explicit stateful orchestration plus reusable model/tool abstractions |
| Primary DB | PostgreSQL | Transactional core, mature ecosystem, relational reporting, pgvector option |
| Async messaging | SQS | Durable decoupling, retries, DLQ |
| Domain events | EventBridge | Loose coupling and automation fan-out |
| Agent hosting | Portable; AgentCore where beneficial | Avoid platform lock-in while using managed agent capabilities |
| Observability | OpenTelemetry-first | Vendor-neutral telemetry; export to CloudWatch and other tools |
| IaC | Terraform/CDK | Reproducible environments and reviewable infrastructure |
# 10. Sequence: Sensitive Action
User request -> Workflow created -> Agent determines candidate action -> Tool request generated -> Policy Engine evaluates -> REQUIRE_APPROVAL -> Approval persisted -> User approves -> Workflow resumes -> Tool validates authorization again -> Idempotency check -> Side effect executes -> Result verified -> Audit event -> User notified