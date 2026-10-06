"""Ticket reservations: no overselling, all-or-nothing holds, idempotent retries, expiry, authorization.

Fixtures `session` and `client_for` come from tests/conftest.py (see the python-fastapi skill's
project skeleton). `make_user` and `make_event` are small helpers in tests/factories.py that insert
rows. Tests run against real PostgreSQL, never SQLite.

These tests run every request on one connection, so they prove the logic, not the row locking.
A separate suite (tests/concurrency/, not shown) fires 50 parallel reservations for the last 10
tickets over independent connections against a committed database, and asserts exactly 10 succeed.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, update

from app.orders import service
from app.orders.models import Order
from tests.factories import make_event, make_user

pytestmark = pytest.mark.anyio


async def reserve(client: AsyncClient, event, items, *, key: str | None = None):
    return await client.post(
        "/v1/orders",
        json={
            "eventId": str(event.id),
            "items": [{"ticketTypeId": str(tt.id), "quantity": qty} for tt, qty in items],
        },
        headers={"Idempotency-Key": key or uuid4().hex},
    )


async def reserved_count(session, ticket_type) -> int:
    await session.refresh(ticket_type)
    return ticket_type.reserved


async def test_reserves_tickets_with_server_computed_total(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )
    buyer = client_for(await make_user(session))

    response = await reserve(buyer, event, [(general, 2)])

    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "pending"
    assert order["total"] == {"amountMinor": 5_000, "currency": event.currency}
    assert order["expiresAt"] is not None
    assert await reserved_count(session, general) == 2


async def test_never_oversells(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 2)]
    )

    first = await reserve(client_for(await make_user(session)), event, [(general, 2)])
    second = await reserve(client_for(await make_user(session)), event, [(general, 1)])

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "sold_out"
    assert await reserved_count(session, general) == 2


async def test_multi_type_reservation_is_all_or_nothing(session, client_for):
    event, (general, vip) = await make_event(
        session,
        organizer=await make_user(session),
        ticket_types=[("General", 2_500, 10), ("VIP", 9_000, 1)],
    )
    await reserve(client_for(await make_user(session)), event, [(vip, 1)])  # VIP is now sold out

    response = await reserve(client_for(await make_user(session)), event, [(general, 2), (vip, 1)])

    assert response.status_code == 409
    # Whichever row was locked first, the failed request leaves no General hold behind.
    assert await reserved_count(session, general) == 0


async def test_retry_with_same_idempotency_key_returns_same_order(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )
    buyer = client_for(await make_user(session))
    key = uuid4().hex

    first = await reserve(buyer, event, [(general, 2)], key=key)
    retry = await reserve(buyer, event, [(general, 2)], key=key)  # e.g. the first response was lost

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["id"] == first.json()["id"]
    assert await reserved_count(session, general) == 2  # held once, not twice


async def test_expired_hold_releases_tickets_exactly_once(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )
    buyer = client_for(await make_user(session))
    order_id = UUID((await reserve(buyer, event, [(general, 3)])).json()["id"])
    await session.execute(
        update(Order).where(Order.id == order_id).values(expires_at=func.now() - timedelta(minutes=1))
    )

    await service.expire_hold(session, order_id=order_id)
    await service.expire_hold(session, order_id=order_id)  # a duplicated job must be harmless

    assert await reserved_count(session, general) == 0
    assert (await buyer.get(f"/v1/orders/{order_id}")).json()["status"] == "expired"


async def test_organizer_cancel_releases_holds_and_is_idempotent(session, client_for):
    organizer_user = await make_user(session)
    event, (general,) = await make_event(session, organizer=organizer_user, ticket_types=[("General", 2_500, 5)])
    order_id = (await reserve(client_for(await make_user(session)), event, [(general, 2)])).json()["id"]
    organizer = client_for(organizer_user)

    for _ in range(2):  # a double click must not fail or release twice
        response = await organizer.post(f"/v1/organizer/orders/{order_id}/cancel", json={"reason": "Duplicate booking"})
        assert response.status_code == 204

    assert await reserved_count(session, general) == 0
    page = (await organizer.get(f"/v1/organizer/events/{event.id}/orders", params={"status": "cancelled"})).json()
    assert [o["id"] for o in page["items"]] == [order_id]
    assert page["items"][0]["cancelledReason"] == "Duplicate booking"


async def test_rejects_ticket_types_from_another_event(session, client_for):
    organizer = await make_user(session)
    event_a, _ = await make_event(session, organizer=organizer, ticket_types=[("General", 2_500, 5)])
    _, (other_general,) = await make_event(session, organizer=organizer, ticket_types=[("General", 2_500, 5)])

    response = await reserve(client_for(await make_user(session)), event_a, [(other_general, 1)])

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_ticket_type"


async def test_rejects_duplicate_ticket_types(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )

    response = await reserve(client_for(await make_user(session)), event, [(general, 1), (general, 1)])

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "items"


async def test_rejects_client_controlled_fields(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )

    response = await client_for(await make_user(session)).post(
        "/v1/orders",
        json={"eventId": str(event.id), "items": [{"ticketTypeId": str(general.id), "quantity": 1}], "totalCents": 1},
        headers={"Idempotency-Key": uuid4().hex},
    )

    assert response.status_code == 422
    assert any(error["field"] == "totalCents" for error in response.json()["errors"])


async def test_requires_idempotency_key(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )

    response = await client_for(await make_user(session)).post(
        "/v1/orders", json={"eventId": str(event.id), "items": [{"ticketTypeId": str(general.id), "quantity": 1}]}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "idempotency-key"


async def test_buyers_cannot_read_each_others_orders(session, client_for):
    event, (general,) = await make_event(
        session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)]
    )
    order_id = (await reserve(client_for(await make_user(session)), event, [(general, 1)])).json()["id"]

    response = await client_for(await make_user(session)).get(f"/v1/orders/{order_id}")

    assert response.status_code == 404
    assert response.json()["code"] == "order_not_found"


async def test_organizers_only_see_their_own_events(session, client_for):
    event, _ = await make_event(session, organizer=await make_user(session), ticket_types=[("General", 2_500, 5)])

    response = await client_for(await make_user(session)).get(f"/v1/organizer/events/{event.id}/orders")

    assert response.status_code == 404
    assert response.json()["code"] == "event_not_found"


async def test_requires_authentication(client_for):
    response = await client_for(None).get("/v1/me/orders")

    assert response.status_code == 401
