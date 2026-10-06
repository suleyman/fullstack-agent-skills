"""Event catalog models (from slice 2), shown with the `reserved` column this slice adds."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id


class EventStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    organizer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    venue_name: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=EventStatus.DRAFT, server_default=EventStatus.DRAFT)
    starts_at: Mapped[datetime]
    sales_start_at: Mapped[datetime]
    sales_end_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'cancelled')", name="status_valid"),
        CheckConstraint("sales_start_at < sales_end_at", name="sales_window_valid"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso"),
        Index("ix_events_organizer_id_starts_at", "organizer_id", "starts_at"),
    )


class TicketType(Base):
    __tablename__ = "ticket_types"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int]
    capacity: Mapped[int]
    # Changed only through conditional UPDATEs in app/orders/service.py. Never read-modify-write.
    reserved: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("reserved >= 0 AND reserved <= capacity", name="reserved_within_capacity"),
        Index("ix_ticket_types_event_id", "event_id"),
    )

    @property
    def available(self) -> int:
        return self.capacity - self.reserved
