# 0002. Groq as initial LLM provider behind a provider abstraction

- Status: accepted
- Date: 2026-10-06

## Context
We need chat/structured-output models from Phase 2 onward. Groq offers a free tier with fast open-weight models. The spec asks for provider/model substitution (01 §8) and later Bedrock routing (03 §10).

## Decision
- Groq is the first chat-model provider, accessed through LangChain's chat-model interface.
- All agent code depends on our own model-router interface (task type to model), never on a vendor SDK.
- The Anthropic API is **not** used anywhere in this project.
- API keys are supplied by the project owner and read from the environment or Secrets Manager. They are never committed.

## Consequences
- Free development. Groq rate limits mean evals must be throttled and cached.
- Adding Bedrock (Phase 7) is a new adapter plus routing config, not a refactor.
- Structured-output support varies by model, so validation and repair stays in our code (03 §5).

## Alternatives considered
- Bedrock first: needs an AWS account and costs money from day one.
- Local LLM (Ollama): free, but too slow on a laptop for agent loops and evals.
