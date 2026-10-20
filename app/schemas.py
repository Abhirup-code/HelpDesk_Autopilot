"""Request and response shapes."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Priority, Status, TicketType


class TicketCreate(BaseModel):
    type: TicketType
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    priority: Priority | None = None

    @field_validator("title", "description")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class StatusUpdate(BaseModel):
    status: Status


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: TicketType
    priority: Priority
    status: Status
    title: str
    description: str
    requester_id: int
    created_at: datetime
    due_at: datetime
    resolved_at: datetime | None
    sla_breached: bool
