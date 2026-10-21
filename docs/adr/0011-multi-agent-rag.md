# 0011. Multi-agent RAG: hybrid retrieval, reranking, grounded answers, verification

- Status: accepted
- Date: 2026-10-20

## Context
Agents must answer from each organization's own policies (FR-008), with citations, and must not invent amounts, fees or time limits. In fintech a confident wrong answer is worse than "I don't know". Documents are tenant data and may themselves be hostile (prompt injection, spec 03 §9).

## Decision
**Ingestion (worker job, like workflows):** upload → object storage (S3 API; MinIO locally) → parse (Markdown/text/PDF, type detected from bytes) → heading-aware chunks (~180 words, 30 overlap, section path) → embed (bge-small, local) → **injection screen** (patterns + Llama Prompt Guard 2) → swap the document's chunks in one transaction. Flagged chunks are stored but **quarantined**: never retrieved.

**Retrieval (deterministic):** per query, pgvector HNSW cosine search (iterative scan, so tenant filters don't starve results) and Postgres full-text (heading weighted A, body B; OR'ed lexemes) → **weighted RRF** (vector 1.0, keyword 0.6) across retrievers and queries → **cross-encoder rerank** (bge-reranker-v2-m3 int8, multilingual) of the top 10 → top 5.

**Agents (LangGraph):** triage → **query planner** (rewrites to English policy vocabulary, splits multi-part questions, translates Roman Urdu) → retrieve → *code* abstains on no/weak evidence → **answer agent** (sources only, numbered citations, may abstain) → *code* checks citations → **verifier agent** (sees question + cited sources; checks every claim and the scope) → one retry with feedback → `answered` | `abstained` | `unverified` (human review).

**Measurement:** `make eval-rag` reports Recall@1/@5 and MRR per stage plus end-to-end safety cases; every setting above was chosen from its numbers.

## Results (eval set: 5 docs, 26 questions incl. Roman Urdu, out-of-scope, injection)
| Stage | R@1 | R@5 | MRR |
|---|---|---|---|
| vector | 91% | 100% | 0.94 |
| keyword | 82% | 95% | 0.87 |
| fused (RRF) | 86% | 100% | 0.92 |
| reranked | 86% | 100% | 0.92 |
| **planner + rerank** | **100%** | **100%** | **1.00** |

End to end: 26/26, no safety failures. Before the injection screen and the scope-aware verifier, 2 safety cases failed: a mortgage question answered from the general loan policy, and a poisoned bulletin's claim relayed.

## Consequences
- The query planner is the biggest quality lever (Roman Urdu went from rank 6-8 to rank 1). It costs one fast LLM call.
- On this clean corpus reranking doesn't beat vector search on raw questions; it's kept because it resolves ambiguous near-ties (the demo's "charged twice … transfer" case), it's multilingual, and its score gates abstention. Cost: ~1.4-2.8 s per query on CPU, acceptable in background workflows; a hosted reranker (Bedrock, Cohere) is the upgrade path.
- Rerank scores of answerable and out-of-scope questions overlap, so the abstention threshold (−8) only removes clearly irrelevant results; the answer agent and verifier handle borderline cases.
- ~4 LLM calls per question (~3 k tokens, p50 ~10-13 s with Groq free tier, mostly rate-limit pacing). `/v1/knowledge/ask` is rate-limited per user.
- Models (~700 MB) are cached in a Docker volume; the API image grew to ~530 MB because of onnxruntime.
- MinIO no longer publishes free images; local dev uses Bitnami's frozen copy, production uses AWS S3.

## Alternatives considered
- Vector-only retrieval: best R@1 on raw questions here, but misses exact tokens (fee codes, references) and has no robust relevance signal.
- Answering without a verifier: fewer calls, but the eval showed confident out-of-scope answers.
- Injection defence by prompt only: the eval showed it isn't enough; screening at ingestion means hostile text never reaches a model.
- A dedicated vector database: unnecessary at this scale; pgvector keeps transactions, RLS and joins.
