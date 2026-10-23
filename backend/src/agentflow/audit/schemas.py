import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: int
    actor_user_id: uuid.UUID | None
    actor_email: str | None  # looked up at read time; None for system actions
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    previous_hash: str | None
    event_hash: str
    occurred_at: datetime


class AuditPage(BaseModel):
    events: list[AuditEventOut]
    next_before_id: int | None  # None: this is the last page


class AuditVerification(BaseModel):
    valid: bool
    verified_events: int
    first_invalid_event_id: int | None
