from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from app.auth.dependencies import CurrentUser
from app.core.db import SessionDep
from app.orders import service
from app.orders.schemas import (
    CancelOrderRequest,
    OrderListParams,
    OrderPage,
    OrderRead,
    OrganizerOrderPage,
    ReservationCreate,
)

# Both routers are included with dependencies=[Depends(get_current_user)] in create_app():
# protected by default.
router = APIRouter(tags=["orders"])

# No role check here on purpose: any user can organize events. Access is scoped to events the
# caller owns, inside each query, so another organizer's data returns 404.
organizer_router = APIRouter(prefix="/organizer", tags=["organizer"])

IdempotencyKey = Annotated[str, Header(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9-]+$")]


@router.post(
    "/orders",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": OrderRead, "description": "Replay of an earlier request with the same Idempotency-Key"}},
)
async def reserve_tickets(
    payload: ReservationCreate,
    idempotency_key: IdempotencyKey,  # the "Idempotency-Key" header; required
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> OrderRead:
    # Rate limited at the edge per user and per event (bots during on-sales).
    order, created = await service.reserve_tickets(
        session, buyer_id=user.id, data=payload, idempotency_key=idempotency_key
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return order


@router.get("/orders/{order_id}")
async def get_order(order_id: UUID, session: SessionDep, user: CurrentUser) -> OrderRead:
    return await service.get_order(session, order_id=order_id, buyer_id=user.id)


@router.get("/me/orders")
async def list_my_orders(
    params: Annotated[OrderListParams, Query()], session: SessionDep, user: CurrentUser
) -> OrderPage:
    return await service.list_my_orders(session, buyer_id=user.id, params=params)


@organizer_router.get("/events/{event_id}/orders")
async def list_event_orders(
    event_id: UUID, params: Annotated[OrderListParams, Query()], session: SessionDep, user: CurrentUser
) -> OrganizerOrderPage:
    return await service.list_event_orders(session, event_id=event_id, organizer_id=user.id, params=params)


@organizer_router.post("/orders/{order_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_order(order_id: UUID, payload: CancelOrderRequest, session: SessionDep, user: CurrentUser) -> None:
    await service.cancel_order(session, order_id=order_id, organizer_id=user.id, reason=payload.reason)
