AgentFlow — Engineering Roadmap, Backlog & Learning Plan
AgentFlow — AI Business Operations PlatformTechnical Documentation
# 1. Engineering Strategy
Build in vertical slices. Every phase must produce a working increment. Do not build all infrastructure first.
# 2. Phase 0 — Repository and Engineering Foundation
- Monorepo or clearly separated app/services.
- Python project management and lint/type tooling.
- Docker Compose local stack.
- Environment configuration.
- Pre-commit hooks.
- GitHub Actions.
- Architecture Decision Record template.
- Conventional commits/release strategy.
# 3. Phase 1 — Core SaaS
- Organizations/users/roles.
- PostgreSQL schema.
- FastAPI authentication middleware.
- Tenant-scoped repositories.
- Frontend dashboard.
- Audit event foundation.
# 4. Phase 2 — First Agent
- LangChain model adapter.
- Typed structured output.
- Triage agent.
- Basic LangGraph workflow.
- Trace IDs and structured logs.
- Unit tests for graph nodes.
# 5. Phase 3 — Knowledge
- S3 document upload.
- Async ingestion.
- Parsing/chunking.
- Embeddings.
- pgvector.
- Hybrid retrieval.
- Citation objects.
- RAG evaluation dataset.
# 6. Phase 4 — Multi-Agent Workflow
- Supervisor/planner.
- Research agent.
- Knowledge agent.
- Resolution agent.
- Verifier.
- Bounded delegation and budgets.
# 7. Phase 5 — Tools and Automation
- CRM read tool.
- Ticket read/update tool.
- Email draft tool.
- Calendar tool.
- Database lookup tool.
- Tool registry.
- Tool permission model.
- MCP integration.
- EventBridge/SQS automation.
# 8. Phase 6 — Human Approval
- Approval entity.
- Approval UI.
- Policy engine.
- Workflow pause/resume.
- Expiration.
- Double authorization on execution.
- Audit events.
# 9. Phase 7 — AWS Production
- IaC.
- ECR.
- ECS/Fargate or AgentCore.
- RDS.
- S3.
- SQS/EventBridge.
- Secrets Manager/KMS.
- CloudWatch/OpenTelemetry.
- Staging and production.
# 10. Phase 8 — Evaluation and Hardening
- Golden datasets.
- Adversarial tests.
- Prompt-injection tests.
- Cross-tenant tests.
- Load tests.
- Chaos/failure tests.
- Cost regression.
- Model fallback tests.
# 11. Phase 9 — Pilot
- Choose one business workflow.
- Interview 3–5 potential users.
- Observe real workflow manually.
- Measure current time/error cost.
- Implement only the painful path.
- Pilot with synthetic or consented data.
- Measure time saved and resolution quality.
# 12. Phase 10 — Commercial MVP
- Onboarding.
- Integration setup.
- Usage dashboard.
- Plan/quotas.
- Billing.
- Documentation.
- Security page.
- Demo environment.
- Support workflow templates.
# 13. Learning Matrix
| Skill | Where learned |
| System design | Service boundaries, queues, workflow state |
| LangChain | Model/tool/retrieval abstractions |
| LangGraph | Stateful orchestration and recovery |
| RAG | Ingestion, retrieval, reranking, evaluation |
| LLM engineering | Structured output, routing, budgets |
| MCP | Tool interoperability |
| Distributed systems | SQS, events, idempotency, retries |
| AWS | IAM, ECS, RDS, S3, Bedrock, AgentCore |
| DevOps | Docker, CI/CD, IaC |
| Security | RBAC, tenant isolation, secrets, policy |
| Observability | OpenTelemetry, CloudWatch, tracing |
| Testing | Unit, integration, evaluation, load, adversarial |
| Product | Discovery, metrics, pricing, pilot |
# 14. Initial Backlog
| Priority | Task |
| P0 | Create repository and architecture ADRs |
| P0 | Create FastAPI + PostgreSQL foundation |
| P0 | Implement organization/user/role model |
| P0 | Implement workflow entity and idempotency |
| P0 | Implement first LangGraph workflow |
| P0 | Implement triage agent with structured output |
| P0 | Implement document ingestion + pgvector |
| P0 | Implement RAG citations |
| P0 | Implement research/knowledge/resolution agents |
| P0 | Implement tool registry and authorization |
| P0 | Implement policy engine |
| P0 | Implement approval workflow |
| P1 | SQS + DLQ |
| P1 | EventBridge automation |
| P1 | MCP gateway |
| P1 | OpenTelemetry tracing |
| P1 | Evaluation harness |
| P1 | AWS staging |
| P1 | Security hardening |
| P2 | Billing |
| P2 | Advanced model routing |
| P2 | Visual workflow builder |
# 15. Engineering Rules
- No direct production side effects from an LLM response.
- No untyped agent-to-agent contracts.
- No cross-tenant query without explicit tenant scope.
- No secret values in prompts or logs.
- No unbounded agent loops.
- No production deployment without evaluation results.
- No microservice split without an explicit operational reason.
- Every external side effect must be idempotent.
- Every production incident gets a reproducible test when practical.
