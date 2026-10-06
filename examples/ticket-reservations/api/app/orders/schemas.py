from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from app.core.schemas import APISchema, Page
from app.orders.models import OrderStatus

# ---- Requests -------------------------------------------------------------------------------


class ReservationItem(APISchema):
    model_config = ConfigDict(extra="forbid")

    ticket_type_id: UUID
    quantity: Annotated[int, Field(ge=1, le=10)]


class ReservationCreate(APISchema):
    # Prices, totals, status, and buyer are always computed server-side, so they aren't fields here.
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    items: Annotated[list[ReservationItem], Field(min_length=1, max_length=10)]

    @field_validator("items")
    @classmethod
    def ticket_types_are_unique(cls, items: list[ReservationItem]) -> list[ReservationItem]:
        ids = [item.ticket_type_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("each ticket type may appear only once")
        return items


class OrderListParams(APISchema):
    """Query parameters. Single-word names, so query keys need no aliasing."""

    status: OrderStatus | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: str | None = None


class CancelOrderRequest(APISchema):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


# ---- Responses ------------------------------------------------------------------------------


class Money(APISchema):
    amount_minor: int  # integer minor units (cents); never floats for money
    currency: str  # ISO 4217


class EventSummary(APISchema):
    id: UUID
    title: str
    venue_name: str
    starts_at: datetime


class OrderItemRead(APISchema):
    ticket_type_id: UUID
    ticket_type_name: str
    quantity: int
    unit_price: Money


class OrderRead(APISchema):
    id: UUID
    status: OrderStatus  # clients must tolerate values added later
    event: EventSummary
    items: list[OrderItemRead]
    total: Money
    expires_at: datetime | None  # only while pending
    created_at: datetime


class BuyerSummary(APISchema):
    id: UUID
    display_name: str  # no email: organizers see what they need, not more


class OrganizerOrderRead(OrderRead):
    buyer: BuyerSummary
    cancelled_reason: str | None


# Named subclasses give OpenAPI (and every generated client) stable, readable schema names.
class OrderPage(Page[OrderRead]):
    pass


class OrganizerOrderPage(Page[OrganizerOrderRead]):
    pass
