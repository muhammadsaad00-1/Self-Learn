"""Tool layer: the only way agents' decisions touch business systems (spec 03 §6).

Every write tool: authenticate caller context -> authorize tenant/role -> validate args ->
policy -> idempotency -> execute -> typed result -> audit. The LLM never calls a tool
directly; workflow code does, after the policy engine (and, if needed, a human) allowed it.
"""
