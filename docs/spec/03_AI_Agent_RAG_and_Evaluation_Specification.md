AgentFlow — AI, Agent Orchestration, RAG & Evaluation Specification
AgentFlow — AI Business Operations PlatformTechnical Documentation
# 1. Agent Philosophy
Use a graph of constrained capabilities rather than a free-form swarm. Every agent has a narrow responsibility, explicit inputs/outputs, tool permissions, budget, timeout, and termination condition.
# 2. Core Agents
| Agent | Purpose | Allowed tools |
| Triage Agent | Classify request and identify workflow type | Knowledge search, customer lookup |
| Planner/Supervisor | Build bounded execution plan and delegate | Agent registry, read-only context |
| Research Agent | Gather relevant customer/business context | Knowledge, CRM read |
| Knowledge Agent | Retrieve and synthesize grounded policy/product information | Retriever |
| Resolution Agent | Propose resolution | Read-only context + calculators |
| Execution Agent | Perform approved actions | Explicitly allowlisted write tools |
| Verifier Agent | Check outcome and consistency | Read-only verification tools |
| Communication Agent | Draft customer/internal messages | Templates, email draft/send depending on policy |
# 3. LangGraph State
WorkflowState- workflow_id- org_id- request- classification- plan- retrieved_context[]- customer_context- proposed_actions[]- policy_decisions[]- approvals[]- execution_results[]- verification_results[]- errors[]- token_budget- time_budget- retry_count- final_response
# 4. Graph Design
START  -> intake  -> triage  -> supervisor_plan  -> research  -> retrieve  -> resolution  -> policy_gate       -> blocked       -> approval_required -> WAIT -> resume       -> allowed  -> execute  -> verify       -> verification_failed -> recovery/escalation       -> verified  -> communicate  -> audit  -> END
# 5. Structured Outputs
- All agent-to-agent contracts use typed Pydantic schemas.
- No downstream component should parse free-form prose to determine whether to execute an action.
- LLM output is validated before graph transitions.
- Invalid structured output triggers bounded repair/retry, then escalation.
- Business-critical calculations are performed in deterministic services.
# 6. Tool Contract
ToolResult:  status: success | rejected | retryable_error | permanent_error  data: typed payload  error_code: optional  idempotency_key: string  audit_reference: stringEvery write tool must:1. Authenticate caller context.2. Authorize tenant and role.3. Validate arguments.4. Validate policy/risk.5. Enforce idempotency.6. Execute.7. Return typed result.8. Emit audit/telemetry.
# 7. RAG Architecture
Source -> fetch -> malware/content validation -> parse -> normalize -> classify -> metadata extraction -> chunk -> embedding -> lexical index + vector index -> ACL metadata -> retrieval -> metadata filtering -> hybrid ranking -> reranking -> context budget -> generation -> citation validation
# 8. Retrieval Techniques
- Hybrid retrieval: lexical/BM25-style search plus dense vector retrieval.
- Metadata filtering before ranking for tenant, document, department, region, confidentiality, and version.
- Reranking for high-value queries.
- Query rewriting only when retrieval quality needs it; keep original query for traceability.
- Context compression to fit model budgets.
- Document/version freshness tracking.
- Citation objects that point to document/version/chunk IDs.
- Abstention when evidence is insufficient.
- Separate retrieval from generation so retrieval can be evaluated independently.
# 9. Prompt Injection Defense
- Treat user input and retrieved documents as untrusted data.
- Never place retrieved text into the system instruction layer.
- Tool authorization is enforced outside the model.
- Sensitive tools require policy approval regardless of model output.
- Use content labels and provenance metadata.
- Add adversarial retrieval cases to evaluation datasets.
- Do not allow a document to redefine agent policies, permissions, or system instructions.
# 10. Model Routing
TaskRouter  classification -> fast/low-cost model  extraction    -> structured-output model  planning      -> stronger reasoning model  summarization -> fast model  vision        -> multimodal model  fallback      -> alternate provider/modelRouting inputs:- task type- complexity- latency budget- token budget- model availability- tenant policy- cost ceiling
# 11. Memory
| Memory type | Implementation |
| Workflow state | LangGraph checkpoint/state store |
| Conversation memory | PostgreSQL or AgentCore Memory |
| Long-term user/org memory | Explicit memory records with provenance and TTL |
| Knowledge | Document/vector/lexical store; not treated as conversation memory |
Memory must be deliberate. Store stable, useful facts with provenance; do not dump entire conversations into long-term memory.
# 12. Agent Budgets
- Maximum graph steps.
- Maximum tool calls per agent.
- Maximum wall-clock duration.
- Maximum token budget.
- Maximum estimated cost.
- Maximum retry count.
- Maximum delegation depth.
# 13. Evaluation Framework
| Evaluation | Example metric |
| Classification | Accuracy / macro F1 |
| RAG | Recall@k, MRR, citation precision |
| Grounded generation | Faithfulness + citation support |
| Tool selection | Correct tool rate |
| Argument generation | Schema/semantic correctness |
| Policy | False allow / false deny rate |
| Workflow | Task success rate |
| Safety | Blocked unsafe/prompt-injection cases |
| Operations | Latency, token usage, cost, retries |
# 14. Regression Dataset
- Happy-path support cases.
- Ambiguous requests.
- Missing customer data.
- Conflicting documents.
- Outdated policies.
- Prompt injection in uploaded documents.
- Unauthorized action requests.
- Duplicate requests.
- Provider failures.
- Tool timeout/partial failure.
- Approval rejection and expiration.
# 15. Evaluation Gate
PR -> unit tests -> schema tests -> retrieval tests   -> agent evaluation subset -> security/prompt-injection tests   -> cost regression check -> deploy candidate   -> staging evaluation -> production canary
