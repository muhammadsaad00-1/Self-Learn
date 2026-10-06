# 0003. Local embeddings first, Bedrock later

- Status: accepted
- Date: 2026-10-06

## Context
Groq does not offer an embeddings API. RAG (Phase 3) needs embeddings for pgvector.

## Decision
Use a local embedding model via `fastembed` (ONNX, no PyTorch) behind an `Embedder` interface. Store the model name and dimension alongside each vector. Switch or add Bedrock Titan embeddings in Phase 7.

## Consequences
- Zero cost and no key needed. Embedding runs on CPU inside the worker.
- Changing models changes vector dimension and space, so a re-embed job is required. Because every chunk records its `embedding_model`, mixed-model queries can be detected and prevented.

## Alternatives considered
- Hosted free tiers (Jina/Voyage/Cohere): another key, plus rate limits.
- sentence-transformers: pulls in PyTorch, which means a much larger image.
