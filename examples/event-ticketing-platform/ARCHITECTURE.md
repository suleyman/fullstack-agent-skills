# Acme Events — Architecture

> Example output of the `fullstack-architect` skill for the prompt:
> *"Build an event ticketing platform with a Next.js organizer dashboard, FastAPI API, PostgreSQL database, and Expo attendee app."*
>
> It follows `skills/fullstack-architect/references/architecture-template.md`. Slice 3 (ticket reservations) is implemented in [`../ticket-reservations`](../ticket-reservations). "Acme Events" is a fictional product.

**Status:** Accepted  
**Date:** 2026-10-06  
**Owners:** Backend lead, Mobile lead, Web lead

## 1. Context and goals

Independent organizers (venues, meetups, small festivals) create events, define ticket types, and sell tickets. Attendees buy tickets in the mobile app and show a QR code at the door. Door staff scan tickets with the same app, even when venue connectivity is poor.

Critical flows:

1. An organizer creates an event with ticket types and capacities, then publishes it.
2. An attendee reserves tickets and pays. **Tickets never oversell, and a retried request never double-charges.**
3. The attendee receives QR tickets in the app, plus a push reminder the day before.
4. Door staff scan tickets offline. Each ticket is admitted once, and conflicts surface when the device syncs.
5. An organizer sees sales in real time, cancels unpaid orders, and refunds paid ones.

## 2. Assumptions and non-goals

- **Scale (first 12 months):** 2k organizers, 200k MAU attendees, 30k events per year.
- **Peak load:** popular on-sales reach 500 reservation requests/s for a few minutes, concentrated on one or two ticket types.
- **Platforms:** iOS 17+, Android 10+ (Expo). The organizer dashboard is desktop-first web. Public event pages are web (SEO) and deep-link into the app.
- **Region:** EU. Card payments through Stripe. Prices in each event's currency.
- **Non-goals for v1:** assigned seating, resale or transfer marketplace, attendee checkout on the web, multi-currency events.

## 3. Stack decisions

| Layer | Choice | Why | Skill |
|---|---|---|---|
| Mobile | Expo + Expo Router, EAS Build/Update/Submit | One codebase for attendees and a door-staff scanning mode | `expo-react-native` |
| Web | Next.js App Router + shadcn/ui: organizer dashboard (TanStack Query islands) and public event pages (Server Components with `"use cache"` and tags) | One deployable for two audiences. Public pages are cached and invalidated on publish | `nextjs-frontend` |
| iOS (native) | None | No requirement Expo can't meet. Revisit for Wallet passes or Live Activities if they become core | — |
| API | FastAPI modular monolith (`auth`, `events`, `orders`, `payments`, `tickets`, `checkin`) + worker (same image) | One owner of inventory and money rules | `python-fastapi` |
| Database | PostgreSQL (managed, PITR, PgBouncer). **No PostGIS in v1** | City and venue text filters are enough. Add PostGIS when "events near me" becomes a requirement | `postgresql` |
| Payments | Stripe PaymentIntents (PaymentSheet in the app), idempotency keys, webhooks | Tickets are services used outside the app, so App Store guideline 3.1.3(e) requires a non-IAP payment method | — |
| Jobs | `jobs` table with `FOR UPDATE SKIP LOCKED` | Hold expiry, ticket issuance, and reminders must commit atomically with orders | `python-fastapi` |
| Files | S3-compatible storage + CDN, presigned uploads | Event cover images | — |
| Auth | Attendees: Sign in with Apple/Google. Organizers: Google or email magic link via the Next.js BFF. API-issued JWT + rotating refresh | One identity system for all three audiences | `fullstack-architect` |
| Rate limiting | Edge (per IP) + Redis (per user, per event) | Bot and scalper pressure during on-sales | — |
| Observability | Sentry on every client, OpenTelemetry, JSON logs, Stripe webhook lag metric | Money flows need end-to-end traceability | — |

## 4. System diagram

```
                 ┌────────────────────────────── trust boundary ──────────────────────────────┐
[Attendee app] ──HTTPS + Bearer───────────────▶ [FastAPI replicas] ──▶ [PgBouncer] ──▶ [PostgreSQL]
[Door-staff mode] ── sync check-ins ──────────▶       │    ▲                               ▲
                                                      │    │ webhooks                       │ claim jobs
[Organizer browser] ──cookie──▶ [Next.js BFF] ────────┘    └──────────── [Stripe] ◀──── [Worker] ──▶ Expo Push / email
[Public browser] ──▶ [Next.js public pages ("use cache", tag per event)] ──▶ API (published events only)
[Apps / web] ◀── cover images ── [CDN] ◀── [Object storage] ◀── presigned upload (organizer)
```

## 5. Domain model

| Table | Key columns | Constraints | Indexes (→ query served) |
|---|---|---|---|
| `events` | `id`, `organizer_id`, `title`, `starts_at`, `venue_name`, `city`, `currency`, `status`, `sales_start_at`, `sales_end_at` | `status IN (...)`, `sales_start_at < sales_end_at`, ISO currency length | `(organizer_id, starts_at)` (→ dashboard list). `(city, starts_at)` WHERE published (→ public listing) |
| `ticket_types` | `id`, `event_id`, `name`, `price_cents`, `capacity`, `reserved` | `CHECK (reserved BETWEEN 0 AND capacity)`, `capacity > 0`, `price_cents >= 0` | `event_id` (→ event page, cascade) |
| `orders` | `id`, `buyer_id`, `event_id`, `status`, `total_cents`, `currency`, `idempotency_key`, `expires_at` | `UNIQUE (buyer_id, idempotency_key)`, status check, `total_cents >= 0` | `(buyer_id, created_at, id)` (→ my orders). `(event_id, created_at, id)` (→ organizer sales). `expires_at` WHERE pending (→ stale-hold reaper) |
| `order_items` | `order_id`, `ticket_type_id`, `quantity`, `unit_price_cents` | PK pair, `quantity BETWEEN 1 AND 10` | PK only (→ load an order's items) |
| `payments` | `id`, `order_id`, `stripe_payment_intent_id`, `status`, `amount_cents` | `UNIQUE (stripe_payment_intent_id)` | `order_id` |
| `tickets` | `id`, `order_id`, `ticket_type_id`, `checked_in_at` | — | `order_id` (→ my tickets). `(ticket_type_id)` WHERE `checked_in_at IS NULL` (→ door counts) |
| `checkins` | `ticket_id`, `device_id`, `scanned_at`, `synced_at`, `outcome` | outcome check | `ticket_id` (→ conflict detection) |
| `event_staff` | `event_id`, `user_id`, `role` | PK pair | PK (→ scanner authorization) |
| `webhook_events` | `provider`, `event_id`, `received_at`, `processed_at` | `PRIMARY KEY (provider, event_id)` | PK (→ deduplication) |
| `uploads`, `jobs`, `audit_events`, auth tables | See the architect references | | |

Sensitive data: buyer names and emails, payment references (no card data: Stripe holds it), device push tokens.

## 6. API surface

All paths are under `/v1`, camelCase JSON, Problem Details errors, cursor pagination (`limit` ≤ 100).

| Method | Path | Auth | Request → Response | Notes |
|---|---|---|---|---|
| GET | `/app-config` | Public | → `AppConfig` | Minimum supported versions |
| GET | `/events`, `/events/{eventId}` | Public | `city, cursor` → `EventPage` / → `EventDetail` | Published only. Availability is computed live |
| POST | `/orders` | User | `ReservationCreate` + `Idempotency-Key` → `OrderRead` (201, or 200 on replay) | Holds tickets for 10 minutes. Rate limited per user and event |
| GET | `/orders/{orderId}`, `/me/orders` | User | → `OrderRead` / `status, cursor` → `OrderPage` | Buyer-scoped |
| POST | `/orders/{orderId}/payment-intent` | User | → `PaymentIntentRead` | Stripe idempotency key = order ID |
| POST | `/webhooks/stripe` | Stripe signature | raw body → 200 | Deduplicated on the Stripe event ID |
| GET | `/me/tickets` | User | → `TicketPage` | Signed QR payloads |
| POST | `/checkins/sync` | Event staff | `CheckinBatch` → `CheckinSyncResult` | Idempotent per scan ID |
| GET/POST/PATCH | `/organizer/events`, `/organizer/events/{eventId}` | Owner | `EventCreate`/`EventUpdate` → `OrganizerEventRead` | Publish via `PATCH status` |
| GET | `/organizer/events/{eventId}/orders` | Owner | `status, cursor` → `OrganizerOrderPage` | |
| POST | `/organizer/orders/{orderId}/cancel` | Owner | `CancelOrderRequest` → 204 | Pending orders only. Idempotent, audited |
| POST | `/organizer/orders/{orderId}/refund` | Owner | `RefundRequest` → `RefundRead` | Stripe refund job, audited |

## 7. Authentication and authorization

- **Attendees** sign in with Apple or Google (native SDKs). The ID token is exchanged at `/auth/oauth/{provider}`.
- **Organizers** sign in to the web dashboard through Google or an email magic link handled by the Next.js BFF. Tokens live in the `__Host-session` cookie.
- **Roles:** every account is a `user`. Organizer access is **ownership** (`events.organizer_id`). Door-staff access is **membership** (`event_staff`). Platform staff have an `admin` role for support tooling.
- **Object rules,** all enforced in queries: buyers see only their own orders. Organizers see only orders for events they own (other organizers' events return 404). Scanners sync check-ins only for events they're staff on.
- **Audit:** organizer cancellations, refunds, publishes, and staff changes write `audit_events` in the same transaction.

## 8. Cross-cutting decisions

- **Errors:** new codes `sold_out`, `sales_closed`, `unknown_ticket_type`, `idempotency_key_reused`, `order_not_cancellable`, `payment_failed`, `ticket_already_checked_in`.
- **Inventory:** `ticket_types.reserved` is changed only by conditional `UPDATE ... WHERE reserved + :qty <= capacity`, in ascending ticket-type ID order (no deadlocks between multi-type orders). The `CHECK` constraint is the backstop.
- **Idempotency:** `POST /orders` requires an `Idempotency-Key` (unique per buyer), so a retried request returns the same order. Stripe calls use the order ID as their idempotency key. Webhooks are deduplicated by event ID.
- **Caching:** public event pages are cached with `"use cache"` + `cacheTag("event:<id>")` and revalidated when an organizer publishes or edits. Availability is never cached on the server. Clients fetch it with a 15 s `staleTime`.
- **Pagination:** keyset on `(created_at, id)` for orders and tickets, `(starts_at, id)` for event listings.
- **Jobs:** `orders.expire_hold` (scheduled at `expires_at`), `orders.release_stale_holds` (every minute, advisory lock), `tickets.issue` (after payment), `payments.reconcile` (hourly), `push.event_reminder` (24 h before start), `email.receipt`, `refunds.process`.
- **Offline check-in:** QR payloads are signed by the API (Ed25519). The scanner validates the signature offline, records scans locally, and syncs in batches. The server keeps the first scan per ticket, and later duplicates come back as `ticket_already_checked_in` conflicts for staff review.
- **Notifications:** payloads carry `{ url: "/tickets/<id>" }` and no personal data. Reminders can be turned off per event.
- **Observability:** dashboards for reservation conflict rate (`sold_out` per minute), hold-to-payment conversion, webhook processing lag, job queue age, and check-in sync backlog. Alerts on webhook lag > 2 min, payment errors > 2%, oldest queued job > 5 min.

## 9. Data lifecycle and privacy

- **Card data never touches our systems.** Stripe holds it. We store only payment intent IDs and amounts.
- **Orders and payments are financial records,** kept for the period local accounting law requires (commonly 7–10 years). Account deletion therefore **anonymizes** the buyer on orders instead of deleting the rows, and that's why `orders.buyer_id` is `ON DELETE RESTRICT`.
- **Retention:** check-in device logs are deleted after 90 days, push tokens on logout or a `DeviceNotRegistered` receipt.
- **Store disclosures:** name, email, purchase history, device identifiers. No tracking SDKs.

## 10. Environments and deployment

- **Environments:** local (docker compose: Postgres, API, worker, MinIO, Stripe CLI webhook forwarding), staging (Stripe test mode), production (live mode). Webhook signing secrets and Stripe keys are per environment.
- **Pipeline (API):** test → build image → `alembic upgrade head` as a one-off job → rolling deploy of API and worker → smoke test.
- **Web:** preview deploys per PR against staging. Production after API.
- **Mobile:** EAS profiles `development` / `preview` / `production`, `fingerprint` runtime policy, staged EAS Updates for JS fixes. Native changes (camera, Stripe SDK upgrades) go through TestFlight and Play internal testing.
- **Release order:** expand migration → API → web → mobile → contract cleanup one release later.
- **Rollback:** previous image (API and worker), previous deployment (web), `eas update:rollback` (mobile JS), roll-forward migrations with a rehearsed PITR restore.

## 11. Performance and scaling

- **Budgets:** `POST /orders` p95 < 200 ms at 500 req/s on one event. Event page TTFB < 300 ms (cached). Organizer order list p95 < 400 ms.
- **Expected first bottleneck:** **hot-row contention** on a single `ticket_types` row during a big on-sale. Every reservation updates the same row, so they serialize on its lock.
- **Responses, in order, when measured:**
  1. Keep the reservation transaction minimal (no external calls, one conditional `UPDATE` per type).
  2. Split capacity across N inventory bucket rows per ticket type, and pick a bucket at random.
  3. Add a waiting room that admits buyers at the rate the database sustains.
- **Next steps if needed:** a read replica for organizer analytics. Materialized sales summaries refreshed every minute.

## 12. Risks and open questions

| Risk / question | Impact | Mitigation / owner |
|---|---|---|
| Overselling under concurrency | Refunds, trust | DB `CHECK` + conditional updates. Concurrency test in CI. Backend lead |
| Double charge from retries | Money, support load | Idempotency keys end to end (client → API → Stripe), webhook dedupe. Backend lead |
| Webhooks lost or out of order | Paid orders without tickets | Reconciliation job against the Stripe API. Ticket issuance is idempotent. Backend lead |
| Duplicate entry with two offline scanners | Venue disputes | First sync wins, conflicts flagged in the staff UI, short sync interval when online. Mobile lead |
| Bots during on-sales | Unfair access | Per-user and per-event limits, a 10-ticket cap per order, a waiting room in v2. Product |
| Open: let organizers set hold duration? | Conversion vs inventory lockup | Fixed 10 minutes in v1. Revisit with data |

## 13. Delivery plan

1. **Foundation:** auth, `/me`, `/app-config` + minimum-version gate, CI with generated types and a drift check.
2. **Event catalog:** organizer event editor, ticket types, publish flow, public event pages with tag-based caching.
3. **Ticket reservations:** see [`../ticket-reservations`](../ticket-reservations).
4. **Payments and tickets:** PaymentSheet, webhooks, ticket issuance, signed QR codes, receipts.
5. **Door check-in:** scanner mode, offline queue, sync, and conflict review.
6. **Operations:** cancellations, refunds, reminders, reconciliation, load test at 2× peak, store submission.

## Decision log

| Date | Decision | Alternatives considered | Reason |
|---|---|---|---|
| 2026-10-06 | Counter + conditional update for inventory | One row per ticket seat with `SKIP LOCKED` | General admission doesn't need per-seat rows. Revisit for assigned seating |
| 2026-10-06 | 10-minute holds released by a scheduled job | Holds checked lazily at read time | Availability stays a simple column. The reaper covers missed jobs |
| 2026-10-06 | Stripe instead of in-app purchase | IAP / Play Billing | Tickets are services used outside the app (guideline 3.1.3(e)). Lower fees |
| 2026-10-06 | No PostGIS in v1 | PostGIS radius search | No location feature in v1 scope. One less extension to operate |
