from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.models import AuditEvent
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.pagination import decode_cursor, encode_cursor
from app.events.models import Event, EventStatus, TicketType
from app.jobs.models import Job
from app.orders.models import Order, OrderItem, OrderStatus
from app.orders.schemas import (
    BuyerSummary,
    EventSummary,
    Money,
    OrderItemRead,
    OrderListParams,
    OrderPage,
    OrderRead,
    OrganizerOrderPage,
    OrganizerOrderRead,
    ReservationCreate,
)

HOLD_DURATION = timedelta(minutes=10)

# Everything OrderRead serializes, loaded in a fixed number of queries per page (never per row).
ORDER_LOADERS = (
    selectinload(Order.event),
    selectinload(Order.items).selectinload(OrderItem.ticket_type),
)


# ---- Commands -------------------------------------------------------------------------------


async def reserve_tickets(
    session: AsyncSession, *, buyer_id: UUID, data: ReservationCreate, idempotency_key: str
) -> tuple[OrderRead, bool]:
    """Hold tickets for HOLD_DURATION. Returns (order, created).

    Safe to retry: a repeated Idempotency-Key returns the original order instead of a second hold.
    Takes buyer_id instead of a User because the rollback below expires every loaded object, and
    touching an expired attribute in async code raises MissingGreenlet.
    """
    replay = await _replay(session, buyer_id=buyer_id, idempotency_key=idempotency_key, event_id=data.event_id)
    if replay is not None:
        return replay, False

    now = datetime.now(UTC)
    event = await session.get(Event, data.event_id)
    if event is None or event.status != EventStatus.PUBLISHED:
        raise NotFound(code="event_not_found")
    if not event.sales_start_at <= now < event.sales_end_at:
        raise Conflict(code="sales_closed", detail="Ticket sales for this event are not open.")

    requested = {item.ticket_type_id: item.quantity for item in data.items}
    ticket_types = {
        tt.id: tt
        for tt in await session.scalars(
            select(TicketType).where(TicketType.event_id == event.id, TicketType.id.in_(requested))
        )
    }
    if ticket_types.keys() != requested.keys():
        raise BadRequest(code="unknown_ticket_type", detail="Every ticket type must belong to this event.")

    # One conditional UPDATE per ticket type: the check and the increment are a single atomic
    # statement, so two buyers can't both take the last ticket. Ascending id order means two
    # multi-type reservations lock rows in the same order and wait for each other instead of deadlocking.
    for ticket_type_id in sorted(requested):
        quantity = requested[ticket_type_id]
        held = await session.scalar(
            update(TicketType)
            .where(TicketType.id == ticket_type_id, TicketType.reserved + quantity <= TicketType.capacity)
            .values(reserved=TicketType.reserved + quantity)
            .returning(TicketType.id)
        )
        if held is None:
            # Nothing is committed: closing the session rolls back holds taken earlier in this loop.
            raise Conflict(code="sold_out", detail=f"Not enough '{ticket_types[ticket_type_id].name}' tickets left.")

    order = Order(
        buyer_id=buyer_id,
        event_id=event.id,
        currency=event.currency,
        total_cents=sum(ticket_types[tt_id].price_cents * qty for tt_id, qty in requested.items()),  # never from the client
        idempotency_key=idempotency_key,
        expires_at=now + HOLD_DURATION,
        items=[
            OrderItem(ticket_type_id=tt_id, quantity=qty, unit_price_cents=ticket_types[tt_id].price_cents)
            for tt_id, qty in requested.items()
        ],
    )
    session.add(order)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) != "uq_orders_buyer_id_idempotency_key":
            raise
        # A concurrent retry with the same key committed first. Undo our holds and return its order.
        await session.rollback()
        replay = await _replay(session, buyer_id=buyer_id, idempotency_key=idempotency_key, event_id=data.event_id)
        if replay is None:
            raise
        return replay, False

    session.add(
        Job(
            kind="orders.expire_hold",
            payload={"orderId": str(order.id)},
            run_at=order.expires_at,
            dedupe_key=f"orders.expire_hold:{order.id}",
        )
    )
    await session.commit()  # holds, order, items, and the expiry job commit together, or none do
    return await get_order(session, order_id=order.id, buyer_id=buyer_id), True


async def expire_hold(session: AsyncSession, *, order_id: UUID) -> None:
    """Handler for the orders.expire_hold job.

    Idempotent: paid, cancelled, or already expired orders are left alone, so a retried or
    duplicated job is harmless. FOR UPDATE serializes this with the payment webhook, which locks
    the same order row before marking it paid.
    """
    order = await session.scalar(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items)).with_for_update()
    )
    if order is None or order.status != OrderStatus.PENDING or order.expires_at > datetime.now(UTC):
        return
    order.status = OrderStatus.EXPIRED
    await _release_holds(session, order.items)
    await session.commit()


async def cancel_order(session: AsyncSession, *, order_id: UUID, organizer_id: UUID, reason: str) -> None:
    order = await session.scalar(
        select(Order)
        .join(Event, Event.id == Order.event_id)
        .where(Order.id == order_id, Event.organizer_id == organizer_id)  # authorization lives in the query
        .options(selectinload(Order.items))
        .with_for_update(of=Order)
    )
    if order is None:
        raise NotFound(code="order_not_found")  # another organizer's order looks exactly like a missing one
    if order.status == OrderStatus.CANCELLED:
        return  # idempotent: double clicks and retries are no-ops
    if order.status != OrderStatus.PENDING:
        raise Conflict(code="order_not_cancellable", detail="Paid orders are refunded, not cancelled.")

    order.status = OrderStatus.CANCELLED
    order.cancelled_reason = reason
    await _release_holds(session, order.items)
    session.add(
        AuditEvent(
            actor_id=organizer_id,
            action="order.cancel",
            target_type="order",
            target_id=order.id,
            data={"reason": reason},
        )
    )
    await session.commit()  # status, released holds, and the audit record commit together


async def _release_holds(session: AsyncSession, items: list[OrderItem]) -> None:
    for item in sorted(items, key=lambda i: i.ticket_type_id):  # same lock order as reserve_tickets
        await session.execute(
            update(TicketType)
            .where(TicketType.id == item.ticket_type_id)
            .values(reserved=TicketType.reserved - item.quantity)
        )


# ---- Queries --------------------------------------------------------------------------------


async def get_order(session: AsyncSession, *, order_id: UUID, buyer_id: UUID) -> OrderRead:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id, Order.buyer_id == buyer_id)  # buyers only ever see their own orders
        .options(*ORDER_LOADERS)
        .execution_options(populate_existing=True)  # refresh objects already in the session (a new order)
    )
    if order is None:
        raise NotFound(code="order_not_found")
    return OrderRead(**_order_fields(order))


async def list_my_orders(session: AsyncSession, *, buyer_id: UUID, params: OrderListParams) -> OrderPage:
    stmt = select(Order).where(Order.buyer_id == buyer_id).options(*ORDER_LOADERS)
    if params.status:
        stmt = stmt.where(Order.status == params.status)
    orders, next_cursor = await _keyset_page(session, stmt, limit=params.limit, cursor=params.cursor)
    return OrderPage(items=[OrderRead(**_order_fields(o)) for o in orders], next_cursor=next_cursor)


async def list_event_orders(
    session: AsyncSession, *, event_id: UUID, organizer_id: UUID, params: OrderListParams
) -> OrganizerOrderPage:
    owns_event = await session.scalar(
        select(Event.id).where(Event.id == event_id, Event.organizer_id == organizer_id)
    )
    if owns_event is None:
        raise NotFound(code="event_not_found")

    stmt = select(Order).where(Order.event_id == event_id).options(*ORDER_LOADERS, selectinload(Order.buyer))
    if params.status:
        stmt = stmt.where(Order.status == params.status)
    orders, next_cursor = await _keyset_page(session, stmt, limit=params.limit, cursor=params.cursor)
    return OrganizerOrderPage(
        items=[
            OrganizerOrderRead(
                **_order_fields(o),
                buyer=BuyerSummary.model_validate(o.buyer),
                cancelled_reason=o.cancelled_reason,
            )
            for o in orders
        ],
        next_cursor=next_cursor,
    )


async def stale_pending_order_ids(session: AsyncSession, *, limit: int = 500) -> list[UUID]:
    """For the orders.release_stale_holds sweep: holds whose expiry job never ran (partial index)."""
    stmt = (
        select(Order.id)
        .where(Order.status == OrderStatus.PENDING, Order.expires_at < func.now())
        .order_by(Order.expires_at)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


# ---- Helpers --------------------------------------------------------------------------------


async def _replay(
    session: AsyncSession, *, buyer_id: UUID, idempotency_key: str, event_id: UUID
) -> OrderRead | None:
    existing = (
        await session.execute(
            select(Order.id, Order.event_id).where(
                Order.buyer_id == buyer_id, Order.idempotency_key == idempotency_key
            )
        )
    ).one_or_none()
    if existing is None:
        return None
    # A complete implementation compares a hash of the whole request body
    # (see the fullstack-architect idempotency recipe). The event is the minimum check.
    if existing.event_id != event_id:
        raise BadRequest(code="idempotency_key_reused", detail="This Idempotency-Key was used for another request.")
    return await get_order(session, order_id=existing.id, buyer_id=buyer_id)


async def _keyset_page(
    session: AsyncSession, stmt: Select, *, limit: int, cursor: str | None
) -> tuple[list[Order], str | None]:
    if cursor:
        created_at, order_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Order.created_at, Order.id) < (created_at, order_id))
    stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)  # +1: is there a next page?
    rows = list(await session.scalars(stmt))
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return page, next_cursor


def _order_fields(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "status": order.status,
        "event": EventSummary.model_validate(order.event),
        "items": [
            OrderItemRead(
                ticket_type_id=item.ticket_type_id,
                ticket_type_name=item.ticket_type.name,
                quantity=item.quantity,
                unit_price=Money(amount_minor=item.unit_price_cents, currency=order.currency),
            )
            for item in sorted(order.items, key=lambda i: i.ticket_type.name)
        ],
        "total": Money(amount_minor=order.total_cents, currency=order.currency),
        "expires_at": order.expires_at if order.status == OrderStatus.PENDING else None,
        "created_at": order.created_at,
    }


def _constraint_name(exc: IntegrityError) -> str | None:
    """Name of the violated constraint, for asyncpg (via __cause__) or psycopg (via diag)."""
    cause = getattr(exc.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    if name is None:
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return name
