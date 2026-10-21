"""Audit module: append-only record of who did what, in which org, and when (spec 02 §3).

Other modules call `audit.service.record()` inside their own transaction, so a change and its
audit event commit or roll back together.
"""
