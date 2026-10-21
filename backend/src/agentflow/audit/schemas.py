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
    occurred_at: datetime


class AuditPage(BaseModel):
    events: list[AuditEventOut]
    next_before_id: int | None  # None: this is the last page
