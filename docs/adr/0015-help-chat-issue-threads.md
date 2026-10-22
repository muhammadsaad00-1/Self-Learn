# 0015. Help chat and issue threads: the customer orchestration

- Status: accepted
- Date: 2026-10-23

## Context
A company that gets customer messages all day needs two different things. **Questions** ("what documents do I need for a loan?") should be answered at once from its documents. **Problems** ("my transfer didn't arrive") need investigation, an action, a status the customer can follow, and sometimes a human. One undifferentiated chat mixes both, and problems get lost in it.

## Decision
- **Two kinds of conversation.** A customer has one **help** chat and any number of **issue** threads. An issue has a status (`open → in_review / with_specialist → resolved`), a customer-facing title, and a link to its ticket.
- **Routing by code, early.** Every help message runs the normal workflow. Right after triage, the worker calls `conversations.after_triage`, and an issue thread is opened when either:
  - the category is a problem (`duplicate_charge`, `failed_payment`, `refund_request`, `unauthorized_transaction`, `card_or_account_blocked`, `account_access`), or
  - the criticality analysis says critical.

  The help chat says so at once ("I've opened a separate issue") with a link card, and the outcome lands in the issue. This is idempotent: `origin_workflow_id` is unique, so a retried workflow never opens a second issue. Customers can also open an issue directly ("Report a problem").
- **Status follows facts.** An executed action or an answer from records means resolved; an approval pending means in review; a ticket or a permanent failure means with a specialist. Resolving a ticket resolves its issue (with an optional message to the customer posted *in the issue*), reopening it reopens the issue, and a new customer message reopens a resolved issue.
- **Cheaper question path.** For anything without a tool, if the documents answered it, the resolution model call is skipped and nothing is escalated. `other` (thanks, greetings) is reply-only. Real danger in such messages is still caught by the risk analysis.
- **Short references for the model.** The resolution agent sees transactions as `T1, T2…`; code maps them back.
- **No promise without an owner.** A workflow that fails permanently (e.g. a provider outage) opens a ticket exactly once, and the customer gets its case number.
- **Model warm-up.** The worker loads the embedding model and the reranker at startup.
- **Customer token = the customer** (`sub = customer_ref`), not one conversation.

## Evidence (`make journeys`: a fresh company, its own policy document, 11 customer journeys and the team flow, real models)
- Run 1: 10/11 plus the team flow.
  - Failure: "what documents do I need for a Sahulat loan?" was triaged `kyc_document` and escalated to a ticket, although the documents had answered it.
  - The first attempt also timed out loading the reranker. Fixed by the cheaper question path and the warm-up.
- Run 2: 10/11 plus the team flow.
  - Failure: the model chose `answer_from_records` correctly but mis-copied `TXN-1004-DD6FBA-2` (it dropped a character); validation correctly refused it and escalated. Fixed by the `T1` aliases.
- Run 3 hit Groq's free-tier daily token limit (200k/day). Every workflow dead-lettered, and customers were told "a specialist will reply" with **no ticket behind it**. Fixed: failure → ticket + case number, verified for real (TCK-1014).
- Reranker: `ms-marco-MiniLM-L-12-v2` reranks 6x faster than `bge-reranker-v2-m3` (0.5 s vs 3 s), but on `make eval-rag AGENTS=1` it got 25/26 with a safety failure (it answered the out-of-scope mortgage question) and planner+rerank R@1 95%. bge got 26/26, R@1 100%. **Kept bge.** Safety beats 2.5 s.

## Consequences
- Latency stays about 15–45 s per message in Docker. The reranker on CPU in the Docker VM is the largest single cost (3 s natively, 6–15 s in the VM). Running the worker natively, or on a machine with more CPU, is the cheap fix.
- The free Groq tier allows about 15 full journey runs per day across all evals. A paid tier or a second provider behind `llm.py` removes the ceiling.
- Routing keywords and categories are code: every misroute found becomes a journey or a test.
