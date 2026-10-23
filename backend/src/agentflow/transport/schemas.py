import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from agentflow.transport.models import TripStatus


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trip_ref: str
    origin: str
    destination: str
    departs_at: datetime
    arrives_at: datetime | None
    fare: Decimal
    currency: str
    seats_total: int | None
    seats_available: int
    status: TripStatus
    vehicle: str | None
    notes: str | None
