from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.ids import new_id
from app.events.models import Event, TicketType
from app.users.models import User


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    buyer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, default=OrderStatus.PENDING, server_default=OrderStatus.PENDING)
    total_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # lazy="raise" everywhere: an accidental lazy load fails in development instead of becoming N+1.
    buyer: Mapped[User] = relationship(lazy="raise")
    event: Mapped[Event] = relationship(lazy="raise")
    items: Mapped[list["OrderItem"]] = relationship(lazy="raise", cascade="all, delete-orphan")

    # Mirrors the migration so `alembic check` stays clean.
    __table_args__ = (
        UniqueConstraint("buyer_id", "idempotency_key", name="uq_orders_buyer_id_idempotency_key"),
        CheckConstraint("status IN ('pending', 'paid', 'expired', 'cancelled')", name="status_valid"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso"),
        CheckConstraint("status <> 'cancelled' OR cancelled_reason IS NOT NULL", name="cancelled_reason_required"),
        Index("ix_orders_buyer_id_created_at", "buyer_id", "created_at", "id"),
        Index("ix_orders_event_id_created_at", "event_id", "created_at", "id"),
        Index("ix_orders_expires_at_pending", "expires_at", postgresql_where=text("status = 'pending'")),
    )
    # Load server-generated values (created_at) via RETURNING, so reading them after commit
    # doesn't trigger an implicit refresh, which fails in async code.
    __mapper_args__ = {"eager_defaults": True}


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True)
    ticket_type_id: Mapped[UUID] = mapped_column(ForeignKey("ticket_types.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[int]
    unit_price_cents: Mapped[int]  # copied at reservation time: later price edits don't change existing orders

    ticket_type: Mapped[TicketType] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 10", name="quantity_range"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
    )
