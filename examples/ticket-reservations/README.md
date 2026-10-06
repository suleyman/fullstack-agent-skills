# Ticket reservations: a vertical slice

Slice 3 of the [Acme Events architecture](../event-ticketing-platform/ARCHITECTURE.md), implemented across every layer the skills cover. Attendees hold tickets for 10 minutes without ever overselling, and retries never create a second hold. Organizers see orders per event and can cancel unpaid ones.

These files are **excerpts from an application, not a runnable project.** They import shared modules (`app.core.*`, `@/lib/api/*`, UI components) whose reference implementations live in the skills' `references/` folders. The point is to show what code written under these skills looks like when the layers meet.

## Files

```
ticket-reservations/
├── api/
│   ├── migrations/versions/
│   │   ├── 0005_add_ticket_reservations.py       # counter column + NOT VALID check on a live table, new tables
│   │   └── 0006_validate_ticket_reservation_check.py
│   ├── app/events/models.py                      # catalog models from slice 2, with `reserved`
│   ├── app/orders/
│   │   ├── models.py                             # SQLAlchemy 2.0, lazy="raise", constraints mirrored
│   │   ├── schemas.py                            # explicit request/response schemas, Money in minor units
│   │   ├── service.py                            # conditional updates, lock ordering, idempotency, expiry
│   │   └── router.py                             # thin routes, Idempotency-Key header, 201 vs 200 replay
│   └── tests/orders/test_reservations.py         # oversell, all-or-nothing, retries, expiry, authz
├── mobile/
│   ├── src/lib/money.ts                          # currency-aware minor-unit formatting
│   ├── src/features/events/api.ts
│   ├── src/features/orders/api.ts                # retry-safe reservation mutation
│   └── app/(app)/
│       ├── events/[id]/checkout.tsx              # validated params, one idempotency key per attempt
│       └── (tabs)/orders.tsx                     # FlashList, every async state, tolerant status labels
└── web/
    ├── src/app/(dashboard)/events/[eventId]/orders/page.tsx  # Server Component prefetch, API 404 → notFound()
    └── src/features/orders/
        ├── api.ts                                            # shared queryOptions, mutation + invalidation
        └── components/event-orders-table.tsx                 # client island, shadcn/ui, URL-owned filter
```

## Rules demonstrated

| Rule | Skill | Where |
|---|---|---|
| Enforce critical invariants in the database (`reserved <= capacity`, quantity range, unique idempotency key) | postgresql | `0005`, `models.py` |
| Change a live table without rewrites or long locks: constant-default column, `NOT VALID` then `VALIDATE` in a separate migration | postgresql | `0005`, `0006` |
| Indexes follow queries, and each index names the query it serves | postgresql | `0005` comments |
| Don't index foreign keys blindly: `order_items.ticket_type_id` is deliberately unindexed, and `buyer_id` is covered by composite indexes | postgresql | `0005` |
| Partial index for a hot subset (pending holds) | postgresql | `ix_orders_expires_at_pending` |
| Prevent lost updates with an atomic conditional `UPDATE`, not read-modify-write | postgresql | `service.py` `reserve_tickets` |
| Lock rows in a consistent order to avoid deadlocks | postgresql | sorted ticket-type IDs in reserve and release |
| Multiple writes that must succeed together share one transaction (holds, order, items, expiry job) | python-fastapi, postgresql | `reserve_tickets` |
| Use a unique constraint + `IntegrityError` mapping for races instead of check-then-insert | python-fastapi, postgresql | idempotency key race in `reserve_tickets` |
| Explicit schemas. Clients can't send prices, totals, or status (`extra="forbid"`) | python-fastapi | `schemas.py`, `test_rejects_client_controlled_fields` |
| Money as integer minor units + currency, formatted with the currency's precision | fullstack-architect | `Money`, `money.ts`, `formatMoney` |
| Idempotent retries end to end: `Idempotency-Key` header, 200 on replay, retry-safe mobile mutation | fullstack-architect, expo-react-native | `router.py`, `orders/api.ts`, `checkout.tsx` |
| Durable job enqueued atomically with the write. Handler is idempotent | python-fastapi, fullstack-architect | `orders.expire_hold`, `expire_hold` |
| Object-level authorization in queries. Other users' data returns 404 | python-fastapi, fullstack-architect | `get_order`, `list_event_orders`, `cancel_order` |
| Audit record committed atomically with the organizer action | fullstack-architect | `cancel_order` |
| Avoid N+1: `lazy="raise"` + `selectinload` | python-fastapi | `models.py`, `ORDER_LOADERS` |
| Cursor pagination with an opaque cursor and `limit + 1` | python-fastapi, postgresql | `_keyset_page`, `myOrdersQuery` |
| A failed request's partial writes are rolled back, in production and in tests | python-fastapi | `test_multi_type_reservation_is_all_or_nothing` |
| Server Components by default, client island at the leaf. The API authorizes, and the page maps 404 | nextjs-frontend | `page.tsx` |
| TanStack Query for remote state, with one `queryOptions` factory for server and client | nextjs-frontend | `web/.../api.ts` |
| The URL owns filters | nextjs-frontend | `parseStatusFilter` |
| No hydration mismatch from date or money formatting | nextjs-frontend | fixed `LOCALE` and `timeZone` |
| Loading, empty, error, retry, and pagination states handled explicitly | nextjs-frontend, expo-react-native | `event-orders-table.tsx`, `orders.tsx`, `checkout.tsx` |
| Route params are untrusted and carry IDs, not objects. Screens load their own data | expo-react-native | `checkout.tsx`, `router.push({ pathname: "/orders/[id]" ... })` |
| Clients tolerate enum values added later | fullstack-architect, expo-react-native | `statusLabel`, `STATUS_LABELS[...] ?? status` |
| Generated API types, never hand-written | fullstack-architect | `@acme/api-types` imports |

## Order it was built in

1. Migrations `0005` and `0006`, plus models → `alembic upgrade head` locally, then `alembic check`.
2. Schemas → service → router → tests (red, then green) against docker-compose PostgreSQL.
3. A parallel-request concurrency test on independent connections (not shown) confirmed exactly `capacity` holds succeed.
4. Export OpenAPI → regenerate `@acme/api-types` → commit (CI fails on drift).
5. Mobile checkout and orders screens against the staging API, then the organizer orders page.
