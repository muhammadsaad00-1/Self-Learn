AgentFlow — AWS, Security, DevOps & Operations Runbook
AgentFlow — AI Business Operations PlatformTechnical Documentation
# 1. AWS Reference Architecture
| Concern | AWS service |
| DNS/CDN/WAF | Route 53 + CloudFront + WAF |
| API | API Gateway or ALB |
| Containers | ECS/Fargate |
| Agent runtime | Amazon Bedrock AgentCore Runtime where appropriate |
| Models | Amazon Bedrock; provider abstraction for external models |
| Database | RDS/Aurora PostgreSQL |
| Files | S3 |
| Queue | SQS + DLQ |
| Events | EventBridge |
| Long workflows | Step Functions |
| Authentication | Cognito / enterprise IdP |
| Secrets | Secrets Manager |
| Keys | KMS |
| Observability | CloudWatch + OpenTelemetry |
| Container registry | ECR |
| IaC | Terraform or AWS CDK |
# 2. Environment Strategy
- Local: Docker Compose with PostgreSQL, Redis, API, worker, and mock integrations.
- Development: isolated AWS account/environment.
- Staging: production-like topology and evaluation suite.
- Production: separate account, restricted IAM, backups, alarms, deployment approvals.
- Never share production credentials with local development.
# 3. IAM Principles
- One role per workload where practical.
- Least privilege; no wildcard permissions unless explicitly justified.
- Separate read and write roles.
- Use short-lived credentials and workload identity.
- Use AgentCore Identity for governed third-party credentials where applicable.
- Do not put API keys in prompts, code, container images, or logs.
# 4. Agent Security Boundary
                 LLM                  |          proposed tool call                  |          Tool Authorization Layer          /        |          \       tenant     role       policy          \        |          /               Tool Gateway                    |              actual API call
# 5. Secrets
- Secrets Manager for third-party credentials.
- KMS encryption.
- Rotation where supported.
- Reference secrets by identifier, never by plaintext in application configuration.
- Redact secrets and tokens from logs.
# 6. Network Security
- Private subnets for database and internal services.
- Restrict outbound access where feasible.
- Security groups with explicit service-to-service rules.
- VPC endpoints for AWS services where justified.
- WAF rules for public APIs.
- Rate limiting and abuse protection.
# 7. Data Protection
- TLS for all network communication.
- Encryption at rest using AWS-managed or customer-managed KMS keys as appropriate.
- PII classification and minimization.
- Configurable retention policies.
- Tenant-scoped access controls for documents and embeddings.
- Audit access to sensitive data.
# 8. CI/CD
Pull Request -> formatting/lint -> type checking -> unit tests -> integration tests -> database migration validation -> security/dependency scan -> agent evaluation subset -> build container -> push ECR -> deploy staging -> smoke tests -> evaluation gate -> production approval -> canary/rolling deployment -> post-deploy checks
# 9. Infrastructure as Code
- All AWS resources created through Terraform or CDK.
- No undocumented production console changes.
- Remote state protected and locked.
- Environment-specific variables stored securely.
- Infrastructure changes reviewed through pull requests.
# 10. Observability
Use OpenTelemetry as the application-level telemetry standard. OpenTelemetry defines traces, metrics, logs, and baggage, while AWS AgentCore can emit OpenTelemetry-compatible telemetry and expose agent execution metrics through CloudWatch.
| Signal | Important fields |
| Trace | trace_id, workflow_id, org_id, agent, model, tool, status |
| Span | latency, input/output references, token usage, provider |
| Metric | workflow success, latency, tool failures, cost |
| Log | structured JSON, severity, correlation IDs |
# 11. Alerts
- API 5xx rate.
- Queue age.
- DLQ depth.
- Agent timeout rate.
- Model/provider error rate.
- Tool failure rate.
- Policy rejection spikes.
- Cost per workflow above threshold.
- Unexpected cross-tenant authorization failures.
- Database CPU/storage/connections.
# 12. Backup and Recovery
- Automated PostgreSQL backups and point-in-time recovery.
- S3 versioning for important artifacts.
- Documented RPO/RTO.
- Periodic restore drills.
- Infrastructure can be recreated from IaC.
- Do not consider backups successful until restoration is tested.
# 13. Production Security Checklist
- Tenant isolation tests passing.
- Least-privilege IAM reviewed.
- Secrets not present in repository/image/logs.
- WAF/rate limits enabled.
- Audit trail enabled.
- Approval workflows tested.
- Prompt-injection suite passing.
- Dependency/container scanning enabled.
- Backups verified.
- Incident response procedure documented.
# 14. AgentCore Integration Strategy
AgentCore is an optional managed layer, not the application architecture itself. Use Runtime when managed agent execution/isolation is beneficial; Memory for appropriate persistent agent memory; Gateway for governed tool/API exposure; Identity for outbound credentials; and Observability for managed agent telemetry. Current AWS documentation states that Runtime supports custom/open-source frameworks including LangGraph and LangChain-based workloads and multiple model providers.
# 15. Current AWS Operational Notes
Current AgentCore documentation recommends distributed tracing, consistent session IDs, custom attributes, resource monitoring, and alarms. AWS also documents AgentCore Identity for securely managing outbound credentials and recommends least-privilege execution roles.