"""Agents: one narrow job each, typed input and output (spec 03 §1-2).

An agent is a prompt plus an output schema plus deterministic rules applied to the answer.
Agents never touch the database or call tools themselves; workflow nodes do that.
"""
