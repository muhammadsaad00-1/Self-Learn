"""Policy module: deterministic rules that decide whether a proposed action may run.

Plain code, no model: allow, require human approval, or block. Every rule is unit-tested and
every decision lists the rules that produced it, so an auditor can see exactly why.
"""
