"""Ledger module: a simulated core-banking ledger (accounts, transactions, reversals).

Stands in for the payment provider's real system so the agents' tools have something real
to read and change. In production this module becomes an adapter to that system's API; the
tool layer above it stays the same.
"""
