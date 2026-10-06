# AgentFlow Technical Documentation

This package defines the proposed production-grade architecture for **AgentFlow**, an AI Business Operations Platform.

## Documents

1. `01_Master_Product_Technical_Specification.docx`
   - Product vision
   - Functional/non-functional requirements
   - Product scope
   - Technology baseline
   - Risks
   - Definition of Done

2. `02_System_Architecture_and_Design.docx`
   - Logical architecture
   - AWS deployment topology
   - Domain modules
   - Data model
   - API design
   - Idempotency
   - Failure handling
   - Architecture decisions

3. `03_AI_Agent_RAG_and_Evaluation_Specification.docx`
   - Multi-agent architecture
   - LangGraph state
   - LangChain responsibilities
   - RAG pipeline
   - Prompt-injection defense
   - Model routing
   - Memory
   - Evaluation framework

4. `04_AWS_Security_DevOps_and_Operations.docx`
   - AWS architecture
   - IAM/security
   - Secrets
   - Networking
   - CI/CD
   - IaC
   - OpenTelemetry
   - AgentCore integration
   - Production checklist

5. `05_Engineering_Roadmap_Backlog_and_Learning_Plan.docx`
   - Build phases
   - Learning matrix
   - Initial backlog
   - Engineering rules
   - Commercialization path

## Recommended implementation philosophy

Build a vertical slice first. Keep the core system portable, introduce AWS managed services when they solve a concrete operational problem, and treat deterministic authorization/policy/state management as separate from probabilistic LLM reasoning.

## Current reference technologies

- Python / FastAPI
- LangChain / LangGraph
- PostgreSQL / pgvector
- Redis
- S3
- SQS / EventBridge / Step Functions
- Amazon Bedrock / AgentCore
- OpenTelemetry / CloudWatch
- Docker
- Terraform or AWS CDK
- GitHub Actions

## Important design principle

**The model can propose. The system decides.**

LLMs should not be trusted as the final authority for permissions, tenant boundaries, financial limits, irreversible actions, or workflow state transitions.
