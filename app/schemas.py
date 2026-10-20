"""Request and response shapes."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import Priority, Status, TicketType
from app.policy import DEPARTMENTS, MIN_PASSWORD_LENGTH, REQUESTABLE_GROUPS

NAME_PATTERN = r"^[A-Za-z][A-Za-z '\-]{0,63}$"
USERNAME_PATTERN = r"^[a-z][a-z0-9]{1,31}$"


class NewHireDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(pattern=NAME_PATTERN)
    last_name: str = Field(pattern=NAME_PATTERN)
    department: Literal[tuple(DEPARTMENTS)]  # type: ignore[valid-type]
    start_date: date


class OffboardingDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=USERNAME_PATTERN)
    last_day: date


class AccessRequestDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: Literal[tuple(REQUESTABLE_GROUPS)]  # type: ignore[valid-type]


DETAIL_MODELS: dict[TicketType, type[BaseModel]] = {
    TicketType.NEW_HIRE: NewHireDetails,
    TicketType.OFFBOARDING: OffboardingDetails,
    TicketType.ACCESS_REQUEST: AccessRequestDetails,
}


class TicketCreate(BaseModel):
    type: TicketType
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    priority: Priority | None = None
    details: dict = Field(default_factory=dict)

    @field_validator("title", "description")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def check_details(self) -> "TicketCreate":
        model = DETAIL_MODELS.get(self.type)
        if model is None:
            if self.details:
                raise ValueError(f"{self.type.value} tickets take no details")
            return self
        self.details = model.model_validate(self.details).model_dump(mode="json")
        return self


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
    details: dict
    automation_error: str | None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reason_for_rejection(self) -> "ApprovalDecision":
        if self.decision == "reject" and not (self.reason and self.reason.strip()):
            raise ValueError("a reason is required to reject")
        return self


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor: str
    action: str
    target: str
    ticket_id: int | None
    detail: str


class PasswordResetRedeem(BaseModel):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)


class InboxMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    body: str
    created_at: datetime


class ErrorOut(BaseModel):
    detail: str
