---
name: fullstack-architect
description: Coordinates system-level decisions for production applications built with Next.js, FastAPI, PostgreSQL/PostGIS, Expo, and native iOS, and delegates layer details to the nextjs-frontend, python-fastapi, postgresql, expo-react-native, and swift-ios skills. Use when starting a new product or a feature that spans more than one layer, designing an architecture, choosing how web and mobile share an API, or deciding on authentication, authorization, API contracts, validation, caching, pagination, background jobs, file uploads, observability, migrations, deployment, environments, secrets, scaling, or security.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# Full-Stack Architect

Make the decisions that cross layer boundaries and keep them consistent across every client. Produce an architecture proposal **before** code, define contracts before UI, then implement vertically using the layer skills.

## When to use

- "Build X" requests that involve more than one of: web, admin, mobile, iOS, API, database.
- "Where should this logic live?", "How do web and mobile share this?", "How should auth work?"
- Choosing an approach for uploads, jobs, caching, pagination, realtime, deployment, or environments.
- Reviewing a design or a PR that changes API contracts, auth, the schema, or the deployment flow.

For changes contained in one layer, use that layer's skill directly.

## Skill map

| Concern | Skill |
|---|---|
| Web app and admin panel (Next.js, React, shadcn/ui, TanStack Query, Zustand) | `nextjs-frontend` |
| HTTP API, business rules, auth enforcement, jobs | `python-fastapi` |
| Schema, constraints, indexes, queries, migrations, PostGIS | `postgresql` |
| Expo app, EAS Build/Update, push, deep links, permissions | `expo-react-native` |
| Native iOS, SwiftUI, Keychain, StoreKit | `swift-ios` |

**Implementation order for a feature:** schema and migration → model → request/response schemas → service + endpoint + tests → regenerate client types → web and mobile UI with every state. Finish one vertical slice before starting the next.

## Default stack

These decisions are made. Don't re-litigate them without a concrete constraint, and record any deviation and its reason in the architecture doc.

| Layer | Default | Notes |
|---|---|---|
| Web and admin | Next.js App Router, React, TypeScript, Tailwind CSS, shadcn/ui | Frontend + thin BFF. Never touches the database |
| Mobile | Expo + Expo Router + EAS | One codebase for iOS and Android |
| Native iOS | Swift + SwiftUI | Only when Expo can't meet a stated requirement |
| Remote state | TanStack Query | Web and mobile |
| Local UI state | Zustand | Only when `useState` or the URL isn't enough |
| API | FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + Alembic | The only writer to the database |
| Database | PostgreSQL, + PostGIS when location is a feature | Managed, with PITR |
| Auth | OAuth/OIDC sign-in, short-lived JWT access + rotating refresh tokens | One identity system |
| Contracts | OpenAPI from FastAPI → generated TypeScript types, Swift Codable | Generated, never hand-copied |
| Jobs | Durable Postgres-backed queue (outbox) to start | Broker only when throughput demands |
| Files | S3-compatible object storage, presigned direct uploads, CDN | API never proxies bytes |
| Observability | Structured logs, OpenTelemetry traces, error tracking on every client | Request ID end to end |

## Workflow

1. **Extract requirements:** actors and roles, core entities, the 3–5 critical user flows, which clients exist, scale assumptions (users, peak reads/writes, data growth), regulated or sensitive data (location, payments, health, minors), and non-goals. Ask only about ambiguities that change the architecture (multi-tenant or not, who can see what). State every other assumption explicitly.
2. **Write the proposal** with [references/architecture-template.md](references/architecture-template.md): stack table, component diagram, data model, API surface, auth model, cross-cutting decisions, risks, and delivery slices.
3. **Define the contract** for the first slice: resources, request/response schemas, error codes, pagination.
4. **Implement vertically** with the layer skills, in the order above.
5. **Update the doc** whenever a decision changes. The decision log is part of the deliverable.

## System boundaries

- **The API owns the database.** FastAPI is the only service that reads or writes Postgres, and the only place business rules and authorization live. Alembic in the API repo is the only thing that changes the schema.
- **Next.js is a frontend and a thin BFF.** It renders UI, holds the web session in an httpOnly cookie, and forwards requests to the API with a bearer token. No ORM or database connection in the web app.
- **Mobile and iOS call the API directly** with bearer tokens, using the same endpoints as the web.
- **Start with a modular monolith:** one API deployable with domain packages inside, plus workers that run the same codebase under a different entrypoint. Extract a service only for a different scaling profile (media processing), a different owner or release cadence, or a hard isolation requirement.

## API design

- REST, resource-oriented, plural nouns, `/v1` prefix, JSON.
- **camelCase on the wire,** converted once in the API's Pydantic base schema. TypeScript and Swift clients consume it as-is, with no key-transform interceptors.
- **Explicit request and response schemas per operation.** Never expose ORM models. Never accept fields the client must not control (owner, role, price, status transitions).
- **IDs:** UUIDv7 strings. Never expose sequential integers.
- **Time:** ISO 8601 UTC with offset (`2026-03-01T10:15:30.123456Z`). Dates as `YYYY-MM-DD`. Never naive datetimes.
- **Money:** integer minor units + ISO currency code.
- **Enums:** strings. Every client handles unknown values, because mobile binaries outlive enum changes.
- **Evolve additively.** Within a version, add optional fields and new endpoints only. Never rename, remove, retype, or newly require a field in place. A breaking change means a new endpoint or version, and the old one is served until the minimum supported app version no longer uses it.
- Mutations return the updated resource. Non-idempotent creates that clients may retry (orders, payments, messages from flaky networks) accept an `Idempotency-Key` header.
- **Capabilities over duplicated rules:** return flags such as `viewerCanEdit` so clients don't reimplement authorization.

## Shared contracts

- **Source of truth:** the OpenAPI document generated from FastAPI's Pydantic models, exported in CI with `app.openapi()`.
- **TypeScript:** `openapi-typescript` generates `packages/api-types`, imported by web and mobile. `openapi-fetch` (or a thin fetch wrapper) is the typed runtime client.
- **Swift:** Apple's `swift-openapi-generator`, or hand-written `Codable` models verified against captured fixtures.
- **CI fails** when committed generated types differ from regenerated ones. Breaking-change detection on the OpenAPI diff (e.g. `oasdiff`) blocks accidental contract breaks.
- Client form schemas (zod) are UX helpers derived from the contract, never its source.

## Repository layout

```
.
├── apps/
│   ├── web/                 # Next.js (nextjs-frontend)
│   ├── mobile/              # Expo (expo-react-native)
│   └── ios/                 # SwiftUI (swift-ios), only if needed
├── services/
│   └── api/                 # FastAPI + Alembic + workers (python-fastapi, postgresql)
├── packages/
│   └── api-types/           # generated from services/api OpenAPI; never edited by hand
├── infra/                   # docker-compose for local dev, IaC
└── .github/workflows/
```

Use a monorepo (pnpm workspaces for TypeScript, `uv` for Python), so an API change, its regenerated types, and the client updates land in one PR.

## Authentication

- **One identity system.** Either the API issues its own tokens (users table; Google/Apple sign-in verified server-side), or an external IdP (Auth0, Clerk, Cognito, Keycloak) issues them and the API verifies through JWKS. Never both.
- **Access token:** a JWT valid for 5–15 minutes, carrying identity only (`sub`, `sid`, `iss`, `aud`, `exp`, `iat`). The API loads the user on each request, so bans and role changes apply immediately.
- **Refresh token:** opaque, at least 256 bits of randomness, stored hashed, bound to a session/device, **rotated on every use**. Reuse of a rotated token revokes the whole session.
- **Web:** tokens live in an encrypted httpOnly, Secure, SameSite=Lax cookie (`__Host-` prefix) owned by Next.js. Browser JavaScript never sees them. Next.js server code attaches `Authorization: Bearer` when calling the API.
- **Mobile/iOS:** access token in memory, refresh token in SecureStore/Keychain (`ThisDeviceOnly`). Sign-in uses native Sign in with Apple / Google SDKs, or the system browser with PKCE. The ID token goes to the API, which verifies it and issues its own tokens.
- **Logout** revokes the server session (and its push registrations) using the refresh token.

Flows, endpoints, tables, and edge cases are in [references/auth-flows.md](references/auth-flows.md).

## Authorization

- **Enforced in the API for every operation.** Client checks only shape the UI.
- **Model:** roles for coarse access (`user`, `moderator`, `admin`), plus ownership or relationship checks for object-level access.
- **Object-level checks live in the query** (`WHERE id = :id AND owner_id = :uid`, or by tenant). Return 404 for resources the caller can't see.
- **Admin endpoints** live under `/v1/admin/*`, on routers that require a staff role at the router level. Every admin action writes an audit record (actor, action, target, before/after, request ID) in the same transaction.
- Never trust IDs, roles, prices, or ownership sent by a client.

## Validation

| Layer | Job | Tool |
|---|---|---|
| Client | Fast feedback, fewer round trips | zod + React Hook Form; the same rules in mobile forms |
| API | Authoritative input validation and business rules | Pydantic schemas, service-layer checks |
| Database | Invariants under concurrency | `NOT NULL`, `CHECK`, `UNIQUE`, FK, `EXCLUDE` |

A rule that matters for correctness exists in the API or the database, never only in a client.

## Error handling

- One error body for every endpoint: RFC 9457 Problem Details (`application/problem+json`) with a stable `code`, a `requestId`, and field-level `errors` for validation failures.
- Clients branch on `code`, never on message text. Show the `requestId` on support-facing error screens.
- Status semantics: 400/422 = the client can fix it; 401 = re-authenticate; 403/404 = not allowed or not visible; 409 = conflict with current state; 429 = back off (`Retry-After`); 5xx = server fault.
- Clients retry only idempotent requests (or ones with an `Idempotency-Key`), only on network errors, 5xx, or 429, with exponential backoff.

## Pagination

- **Cursor (keyset) pagination by default** for anything user-generated or growing: `?limit=20&cursor=<opaque>` returns `{ "items": [...], "nextCursor": "..." | null }`. Only the server encodes or decodes cursors.
- Offset/page numbers only for bounded admin tables where jumping to page N is a real requirement. Always cap `limit` (max 100).
- No exact totals on large tables. Use `nextCursor` presence, or approximate counts.

## Caching

Each layer has one owner and a stated invalidation path. **If you can't say how an entry is invalidated, don't cache it.**

1. **CDN/HTTP:** static assets, public media, public pages. Authenticated responses are `Cache-Control: private, no-store`.
2. **Next.js server cache:** shared, non-personalized data only (`"use cache"` + tags), invalidated by tag after admin mutations.
3. **TanStack Query (web and mobile):** the primary cache for user data, with `staleTime` per resource and invalidation on mutation.
4. **Redis:** only for measured hot paths, with keys that include every scoping dimension (user, tenant), a TTL, and explicit invalidation on write.
5. **Database:** correct indexes and materialized views remove most of the need for 1–4.

## Background jobs

- Slow (more than a few hundred ms), unreliable (third-party calls), or retryable work runs in a worker: push notifications, email, media processing, outbound webhooks, search indexing, exports, account deletion.
- **Enqueue atomically with the business write:** an outbox or Postgres-backed job table written in the same transaction. Never "commit, then publish" for work that must happen.
- **Jobs are idempotent** (a dedupe key), with timeouts, retries with exponential backoff, a maximum attempt count, and a dead-letter state that alerts.
- Scheduled jobs take an advisory lock, so replicas don't double-run them.
- FastAPI `BackgroundTasks` only for best-effort work you can afford to lose.

## File uploads

1. The client asks the API for an upload: `POST /v1/uploads {purpose, contentType, sizeBytes}`. The API checks the content type against an allowlist and enforces a size limit per purpose, records a `pending` upload, and returns a short-lived **presigned** URL. The object key is generated server-side, never taken from a client filename.
2. The client uploads straight to object storage. API servers never proxy file bytes.
3. The client confirms the upload (`POST /v1/uploads/{id}/complete`), or a storage event fires. The API verifies the object exists and enqueues processing.
4. The worker checks the real type by magic bytes, **strips EXIF/GPS metadata**, generates the sizes clients need, and marks the upload `ready`.
5. Files are served from a CDN. Private files use short-lived signed GET URLs. Buckets are private, separated per environment, and lifecycle rules delete abandoned pending uploads.

Upload, job-queue, idempotency, and minimum-version recipes are in [references/cross-cutting-recipes.md](references/cross-cutting-recipes.md).

## Observability

- **Request ID:** accepted from a client (validated) or generated, returned in the `X-Request-ID` header and in error bodies, attached to every log line and to client error reports.
- **Logs:** structured JSON, one event per line, with no tokens and no unnecessary PII.
- **Traces:** OpenTelemetry for FastAPI, SQLAlchemy, and httpx. Propagate `traceparent` from Next.js server code to the API.
- **Errors:** error tracking (e.g. Sentry) on web client and server, mobile (with source maps for every EAS Update), iOS (dSYMs), API, and workers, tagged by release and environment.
- **Metrics:** rate, errors, and p50/p95/p99 duration per endpoint, queue depth and oldest-job age, DB pool saturation, top queries from `pg_stat_statements`, and for mobile, crash-free sessions per build and update.
- **Alert on symptoms** (error rate, latency objectives, queue age), not on causes like CPU.

## Migrations and schema changes

- Run migrations as a **separate pipeline step before new code deploys**, with a dedicated DDL role. Never on application startup.
- **Expand → migrate → contract.** Every migration is compatible with the code currently running. Destructive steps ship in a later release, after no deployed code uses the column and no supported mobile version depends on the API fields behind it. Details: the `postgresql` skill.

## Deployment and environments

| | Local | Staging | Production |
|---|---|---|---|
| Database | docker compose (PostGIS image, same major versions) | Separate managed DB, seeded or anonymized data | Managed DB with PITR and a pooler |
| API + worker | docker compose | Same image as prod | Container replicas, rolling deploy |
| Web | `next dev` | Preview deploys → staging API | Production deploy |
| Mobile | Dev client build | `preview` channel and build profile | `production` channel, store builds |
| Credentials | Dev OAuth clients, sandbox push | Staging-only credentials | Production-only credentials |

- **Build once, promote:** the same image moves through environments, configured by environment variables.
- **Release order for cross-layer features:** expand migration → backward-compatible API → web → mobile update or build → later contract cleanup.
- **Health checks:** liveness (process up) and readiness (database reachable). Graceful shutdown on SIGTERM.
- **Rollback paths, documented before the first release:** previous image (API), instant rollback (web), `eas update:rollback` (mobile JS), roll-forward migrations plus a tested restore (database).
- Non-production environments never touch production data or credentials.

## Secrets and configuration

- Secrets live only in the platform's secret manager or injected env. `.env` is local-only and gitignored, and `.env.example` is committed.
- **Anything shipped to a browser or device is public:** `NEXT_PUBLIC_*`, `EXPO_PUBLIC_*`, Info.plist, app config. Only public identifiers go there (API base URL, error-tracking DSN, publishable keys).
- Third-party secret keys (payments, storage, push credentials) live only in the API and workers.
- Every environment has its own credentials. Rotation is a config change, never a code change.
- Config is validated at startup (pydantic-settings, zod). A missing value stops the process.

## Mobile and web API reuse

- One API serves every client. Resources are client-agnostic.
- Web-only aggregation goes in the Next.js BFF. Add a screen-shaped endpoint for mobile only when round-trip cost is measured and significant.
- Every client sends `X-App-Version` and `X-Platform`. `GET /v1/app-config` returns `minSupportedVersion` per platform, and clients below it show a blocking update screen.
- Clients are tolerant readers: they ignore unknown fields and handle unknown enum values.

## Performance and scaling

Set budgets in the doc (e.g. p95 < 300 ms for core reads) and measure with production-like data. Scale in the order you'll actually need it:

1. Correct indexes, no N+1 queries, paginated and lean payloads, no client request waterfalls.
2. Stateless API replicas scaled horizontally, plus a connection pooler sized by pool math.
3. Slow work moved to workers, scaled by queue depth.
4. Query tuning, materialized views, denormalized counters.
5. Read replicas for admin and analytics reads (lag-aware).
6. Redis caching for measured hot paths.
7. Partitioning for very large time-series tables.
8. Service extraction for components with distinct scaling or ownership needs.

Don't start at step 8.

## Security baseline

- **OWASP API Top 10:** object-level authorization in queries, short-lived tokens with rotation, explicit schemas against mass assignment, rate limits and size limits against resource exhaustion, no server-side fetches of user-supplied URLs without an allowlist (SSRF), and strict CORS with docs disabled in production.
- **Rate limits** per IP and per user on sign-in, token refresh, OTP, password reset, uploads, and content creation.
- TLS everywhere, HSTS on the web, private buckets, least-privilege database roles.
- Dependency scanning (Renovate or Dependabot, `pip-audit`, `npm audit`) and secret scanning in CI.
- **Privacy:** collect the minimum. Location is coarse by default. Retention limits are written down. Account deletion removes or anonymizes data across the database, object storage, analytics, and push registrations.

## Worked example

**Prompt:** "Build an event ticketing platform with a Next.js organizer dashboard, FastAPI API, PostgreSQL database, and Expo attendee app."

| Layer | Decision |
|---|---|
| Mobile | Expo + Expo Router, EAS Build/Update. One app for attendees, plus a door-staff scanning mode. No native iOS app: no requirement Expo can't meet |
| Web | Next.js + shadcn/ui: organizer dashboard (TanStack Query client islands) and public event pages (Server Components, `"use cache"` tagged per event, revalidated on publish) |
| API | FastAPI modular monolith (`auth`, `events`, `orders`, `payments`, `tickets`, `checkin`) + worker |
| Database | PostgreSQL. **No PostGIS:** v1 has no location feature, and city filters are enough. Add it when "events near me" becomes a requirement |
| Remote state | TanStack Query. Ticket availability gets a short `staleTime` and is never cached on the server |
| Local UI state | Zustand only for the scanner session (selected event, offline queue status) |
| Auth | Attendees: Sign in with Apple/Google → API-issued JWT + rotating refresh tokens in SecureStore. Organizers: httpOnly cookie session via the Next.js BFF. Door staff: per-event membership checked in queries |
| Contracts | Pydantic schemas → OpenAPI → `packages/api-types` for mobile and web |
| Inventory | `CHECK (reserved <= capacity)` + one conditional `UPDATE` per ticket type, in ascending ID order. Holds expire through a scheduled job |
| Payments | Stripe with idempotency keys, webhooks deduplicated by event ID, tickets issued by a job after payment. Not in-app purchase: tickets are services used outside the app (App Store guideline 3.1.3(e)) |
| Idempotency | `POST /v1/orders` requires an `Idempotency-Key` (unique per buyer), so mobile retries can't double-book |
| Offline | QR payloads signed by the API and verified offline by scanners. Check-ins sync in batches, and the server resolves duplicates |

**Key risks:** hot-row lock contention on popular on-sales (split capacity across rows or add a waiting room when measured), double charges (idempotency from client to Stripe), duplicate entry with offline scanners.

## Production-readiness checklist

- [ ] An architecture doc records decisions, assumptions, and a decision log.
- [ ] Only the API reads and writes the database. Migrations run as a pre-deploy step.
- [ ] OpenAPI-generated client types, with CI drift and breaking-change checks.
- [ ] Auth: short-lived access tokens, rotating refresh tokens with reuse detection, server-side revocation, platform-appropriate token storage.
- [ ] Object-level authorization is tested for every resource endpoint. Admin actions are audit-logged.
- [ ] Problem Details errors with request IDs from client to database logs.
- [ ] Cursor pagination with capped limits on growing lists.
- [ ] Durable, idempotent jobs with retries and dead-letter alerting.
- [ ] Presigned uploads, private buckets, EXIF stripped.
- [ ] No secrets in repos or client bundles. Per-environment credentials.
- [ ] Logs, traces, and error tracking on every client and service. Alerts on SLO symptoms.
- [ ] Rate limits and request size limits.
- [ ] Minimum supported app version enforced. API changes are additive.
- [ ] Account deletion and privacy disclosures match actual data flows.
- [ ] Rollback is documented and rehearsed for API, web, mobile, and database.
