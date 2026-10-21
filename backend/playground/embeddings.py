"""Embeddings playground: see what an embedding is, and how "similar meaning" is measured.

Run from backend/:   uv run python playground/embeddings.py

Not app code. A place to experiment: change the sentences and watch the scores move.
"""

import numpy as np
from fastembed import TextEmbedding

# A small English embedding model (~67 MB, downloaded once to a cache). It runs on your CPU.
model = TextEmbedding("BAAI/bge-small-en-v1.5")

# Pretend these are paragraphs from EasyPay's policy documents.
passages = [
    "Duplicate debits are refunded in full within 3 business days.",
    "Card replacement costs PKR 500 and takes 7 days.",
    "The daily transfer limit for level 1 wallets is PKR 50,000.",
    "Report unauthorized transactions immediately; we block the card within 1 hour.",
    "Customers can reset their password from the login screen using an OTP.",
]

# And these are things customers type.
queries = [
    "I was charged twice, when do I get my money back?",
    "someone used my card without permission",
    "how much can I send per day?",
]

# passage_embed / query_embed return generators of numpy arrays; np.array stacks them into a
# matrix: one row per text, 384 columns (the embedding dimensions).
P = np.array(list(model.passage_embed(passages)))
Q = np.array(list(model.query_embed(queries)))

print(f"each text -> a vector of {P.shape[1]} numbers")
print(f"first 5 numbers of passage 0: {P[0][:5].round(3)}")
print(f"vector length (norm): {np.linalg.norm(P[0]):.3f}   <- 1.0, so cosine = dot product\n")

# Cosine similarity of every query with every passage in one matrix multiplication:
# scores[i][j] = sum over the 384 dimensions of Q[i][k] * P[j][k]
scores = Q @ P.T

for i, query in enumerate(queries):
    print(f"Q: {query}")
    ranked = np.argsort(-scores[i])  # passage indexes, best match first
    for rank, j in enumerate(ranked[:3], start=1):
        print(f"   {rank}. {scores[i][j]:.3f}  {passages[j]}")
    print()
