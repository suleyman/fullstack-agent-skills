---
name: python-fastapi
description: Opinionated rules for production Python APIs built with FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, and PostgreSQL, including JWT/OAuth authentication, authorization, error handling, pagination, background jobs, and testing. Use when creating or reviewing FastAPI endpoints, request/response schemas, services, database access, dependencies, auth, settings, workers, or API tests, or when debugging slow or failing API requests.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# Python FastAPI

Build the API that **owns business logic, authorization, and the database** for every client: Next.js web, Expo mobile, and native iOS. Stack: FastAPI, Pydantic v2, SQLAlchemy 2.0 with `AsyncSession` + asyncpg, Alembic, and PostgreSQL. Tooling: `uv`, `ruff`, and a type checker (mypy or pyright) in strict mode on `app/`.

## When to use

- Adding or changing endpoints, schemas, services, models, dependencies, middleware, or settings.
- Implementing authentication, authorization, pagination, uploads, or background work.
- Writing API tests. Debugging 4xx/5xx responses, slow endpoints, or connection pool problems.

Use `postgresql` for schema design, indexes, query plans, and migration safety. Use `fullstack-architect` for API contract and auth decisions that affect clients.

## Core rules

1. **Three layers, one direction.** The router handles HTTP: parsing, auth dependencies, status codes. The service holds business rules and the transaction. Queries use SQLAlchemy. Routers contain no queries. Services never import `Request` and never raise `HTTPException`. They raise domain errors, which a handler maps to HTTP.
2. **Explicit schemas per operation.** Separate `OrderCreate`, `OrderUpdate`, and `OrderRead` (or `OrderSummary`). Never return ORM objects without a response schema, and never accept ORM models as input. Input schemas use `extra="forbid"`. Fields the client must not control (`owner_id`, `role`, `status`, `is_verified`) don't appear in input schemas at all.
3. **One commit per unit of work, in the service.** Do all writes, then commit once with `await session.commit()`. Never commit in repositories, and **never commit in a dependency's teardown**: if that commit fails after the response is sent, the client sees success for a write that didn't happen.
4. **Async all the way, or not at all.** In an `async def` endpoint every I/O call must be awaited (asyncpg, `httpx.AsyncClient`). A blocking call such as `requests`, `boto3` network calls, `time.sleep`, or heavy CPU work stalls every request on the worker. Move it to `anyio.to_thread.run_sync`, use a `def` endpoint, or send it to a job.
5. **Authorization lives in queries.** Scope every read and write by the principal: `WHERE id = :id AND author_id = :user_id`, or by tenant. Return 404 for resources the caller can't see.
6. **Fail fast on configuration.** Settings come from `pydantic-settings` and are validated at import. A missing secret stops the process at startup, not on first use.
7. **One error format.** Every error, including validation errors and unhandled exceptions, returns the same Problem Details body with a stable `code` and the `requestId`.

## Architecture

```
api/
├── pyproject.toml              # uv, ruff, mypy/pyright, pytest
├── alembic.ini
├── migrations/versions/
├── app/
│   ├── main.py                 # create_app(): routers, middleware, error handlers, lifespan
│   ├── core/
│   │   ├── config.py           # Settings(BaseSettings)
│   │   ├── db.py               # engine, async_sessionmaker, Base, get_session
│   │   ├── security.py         # token verification, password hashing
│   │   ├── errors.py           # DomainError hierarchy + exception handlers
│   │   ├── schemas.py          # APISchema base (camelCase), Page[T]
│   │   └── pagination.py       # opaque cursor encode/decode
│   ├── auth/                   # dependencies.py (get_current_user, require_role), router.py, service.py
│   ├── orders/                 # one package per domain
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── service.py
│   │   └── router.py
│   └── worker/                 # job handlers (same codebase, different entrypoint)
└── tests/
```

- **Package by domain** (`orders/`, `users/`), not by layer (`models/`, `schemas/`, `routers/`).
- **camelCase on the wire.** All schemas inherit one `APISchema` base with `alias_generator=to_camel` and `populate_by_name=True`. FastAPI serializes by alias by default. TypeScript and Swift clients receive camelCase without client-side key transforms.
- **Protected by default.** Include routers with `dependencies=[Depends(get_current_user)]`. Public endpoints (sign-in, health, app config) live in a separate router so that exposing one is a deliberate choice.
- **Startup and shutdown use `lifespan`,** not `@app.on_event`. Create one shared `httpx.AsyncClient` with explicit timeouts there, and dispose of the engine on shutdown.
- **Migrations never run on startup.** Alembic runs as a separate deploy step (see the `postgresql` skill).

Reference code for each core module is in [references/project-skeleton.md](references/project-skeleton.md).

## Preferred patterns

```python
# app/orders/schemas.py
class OrderCreate(APISchema):
    model_config = ConfigDict(extra="forbid")  # no price, total, status, or customer fields

    items: Annotated[list[OrderItemIn], Field(min_length=1, max_length=50)]
    note: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = None


class OrderRead(APISchema):
    id: UUID
    status: OrderStatus
    items: list[OrderItemRead]
    total: Money                    # integer minor units + currency
    created_at: datetime            # serialized as createdAt, ISO 8601 with offset


class OrderPage(Page[OrderRead]):
    """Named subclass, so OpenAPI and generated clients see `OrderPage`, not `Page_OrderRead_`."""
```

```python
# app/orders/router.py
router = APIRouter(prefix="/orders", tags=["orders"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, session: SessionDep, user: CurrentUser) -> OrderRead:
    return await service.create_order(session, customer=user, data=payload)
```

```python
# app/orders/service.py
async def create_order(session: AsyncSession, *, customer: User, data: OrderCreate) -> OrderRead:
    prices = await current_prices(session, [item.product_id for item in data.items])  # never trust client prices
    order = Order(customer_id=customer.id, note=data.note, items=build_items(data.items, prices))
    session.add(order)
    await session.flush()                     # assigns order.id inside the same transaction
    session.add(Job(kind="email.order_confirmation", payload={"orderId": str(order.id)}))  # commits atomically with the order
    await session.commit()
    return await get_order(session, order_id=order.id, customer_id=customer.id)
```

- **Dependencies:** `SessionDep = Annotated[AsyncSession, Depends(get_session)]` and `CurrentUser = Annotated[User, Depends(get_current_user)]`. Dependencies load things (session, user, the resource being acted on) and enforce auth. They don't run business logic.
- **PATCH:** use `data.model_dump(exclude_unset=True)` to tell "not sent" apart from "set to null".
- **N+1 prevention:** declare relationships with `lazy="raise"`, so implicit lazy loads fail loudly in development. Load explicitly with `selectinload` for collections and `joinedload` for many-to-one.
- **Sessions:** `async_sessionmaker(engine, expire_on_commit=False)`, so reading attributes after commit doesn't trigger implicit I/O (`MissingGreenlet`).
- **Pagination:** cursor (keyset) by default. Use `limit: Annotated[int, Query(ge=1, le=100)] = 20` and return `{items, nextCursor}` with an opaque cursor encoding the sort key. Fetch `limit + 1` rows to compute `nextCursor` without a `COUNT(*)`.
- **Constraint violations:** catch `IntegrityError`, inspect the constraint name (`uq_users_email`), and raise `Conflict(code="email_taken")`. The database enforces the rule, and the service translates it.
- **Upserts and races:** use `insert(...).on_conflict_do_update/do_nothing` (PostgreSQL dialect) instead of select-then-insert.
- **Outbound HTTP:** use the shared `httpx.AsyncClient`, explicit timeouts, and retries only for idempotent calls. Never keep a database transaction open across an external call.
- **Background work:** use FastAPI `BackgroundTasks` only for best-effort work you can afford to lose. Push notifications, emails, media processing, and webhooks go to a **durable job queue**, enqueued in the same transaction as the business write (outbox or a Postgres-backed queue). Jobs are idempotent.
- **Auth:** verify JWTs with PyJWT. Pin `algorithms`, verify `exp`, `iss`, and `aud`, and require `sub`. Hash passwords with Argon2 via `pwdlib[argon2]`. Load the user by `sub` on every request (a primary key lookup), so bans and role changes apply immediately. Token lifecycle: see the `fullstack-architect` skill.

## Patterns to avoid

| Avoid | Why / instead |
|---|---|
| Returning ORM objects or `dict`s without a response schema | Leaks columns and couples the API to the schema. Use explicit `*Read` schemas |
| One `PostSchema` with every field optional, used for create, update, and read | Ambiguous contract. Use a schema per operation |
| `session.commit()` in repositories, or several commits per request | Partial writes on failure. Commit once in the service |
| Commit in a `yield` dependency's teardown | Commit failures surface after the response is sent |
| `requests`, a sync DB driver, or `time.sleep` inside `async def` | Blocks the event loop for every request |
| `HTTPException` raised from services | Couples domain logic to HTTP. Raise domain errors |
| Offset pagination on growing tables | Slow at depth and unstable under inserts. Use cursors |
| `Base.metadata.create_all()` outside tests | Bypasses migrations. Use Alembic |
| `alembic upgrade` in `lifespan` or the container entrypoint | Races between replicas and crash loops. Use a release step |
| Bare `except Exception: pass` | Hides failures. Catch specific exceptions, or log and re-raise |
| `python-jose` and `passlib` in new code | Poorly maintained. Use PyJWT and pwdlib |
| `allow_origins=["*"]` with `allow_credentials=True` | Lets any site make credentialed calls. Use an explicit allowlist |
| Logging request bodies, tokens, or passwords | Credential and PII leaks. Log IDs and request IDs |

## Debugging

- **`MissingGreenlet` / "greenlet_spawn has not been called":** an implicit lazy load or expired attribute was touched in async code. Add `selectinload`/`joinedload`, keep `expire_on_commit=False`, or `await session.refresh(obj, ["relationship"])`.
- **422 you didn't expect:** read `errors[].field`. Common causes are a camelCase/snake_case alias mismatch, a scalar parameter that FastAPI treats as a query parameter when you meant the body, or `extra="forbid"` rejecting a field the client sends.
- **Slow endpoint:** count queries per request (SQLAlchemy event listener or `echo=True` locally) to find N+1, then `EXPLAIN (ANALYZE, BUFFERS)` the slowest query (see the `postgresql` skill).
- **Latency spikes across unrelated endpoints under load:** something is blocking the event loop. Run with `PYTHONASYNCIODEBUG=1` to log slow callbacks, and grep for sync clients in `async def`.
- **`QueuePool limit ... reached` / connection timeouts:** sessions are leaking, or transactions are held open across slow awaits (external HTTP, file uploads). Check `pg_stat_activity` for `idle in transaction`.
- **"prepared statement does not exist" behind PgBouncer:** transaction pooling conflicts with asyncpg's statement cache. Configure the cache as described in the SQLAlchemy asyncpg docs, or enable PgBouncer ≥ 1.21 `max_prepared_statements`.
- Include the request ID in every log line, so you can trace one request from client to database.

## Performance

- **Pool math:** (`pool_size` + `max_overflow`) × processes × replicas must stay below Postgres `max_connections` minus headroom. Add PgBouncer before raising `max_connections`.
- In containers, run one Uvicorn process per container and scale replicas. Use `--workers` on bare VMs.
- List endpoints select only the columns they serialize. Cap page size. Never return unbounded collections.
- Set `statement_timeout` and `idle_in_transaction_session_timeout` on the application database role, so a runaway query can't hold a connection indefinitely.
- Add Redis caching only after measurement shows a hot, expensive, read-mostly query, with keys that include every scoping dimension (user, tenant).

## Security

- **Object-level authorization** on every resource endpoint, with a test that requests another user's resource and gets 404 or 403.
- **Mass assignment:** `extra="forbid"` on input. Ownership and roles come from the principal, never from the body.
- **JWT:** pin algorithms (never accept `none` or follow the token's `alg` blindly) and verify `aud`/`iss`. Use RS256/ES256 with JWKS when more than one service verifies tokens.
- **Secrets:** compare tokens with `secrets.compare_digest`. Store refresh tokens hashed (SHA-256 is fine for high-entropy tokens) and passwords with Argon2.
- **SQL:** bound parameters only. `text()` gets `:params`. Never build SQL with f-strings.
- **Rate limits** on sign-in, token refresh, password reset, OTP, uploads, and content creation, at the edge or with a shared store.
- **Docs exposure:** disable `docs_url`/`openapi_url` in production for private APIs. Generate `openapi.json` in CI from `app.openapi()` instead.
- **Webhooks in:** verify signatures, and deduplicate on the provider's event ID.
- Audit dependencies with `pip-audit` in CI.

## Testing

- Use pytest with the anyio plugin (or pytest-asyncio) and `httpx.AsyncClient(transport=ASGITransport(app=app))`.
- **Use real PostgreSQL** with the same major version and extensions (PostGIS image) via docker compose or testcontainers. Never SQLite, which differs in types, constraints, locking, and has no PostGIS.
- Build the schema by running Alembic migrations once per test session, which tests the migrations too. Run each test inside a transaction that is rolled back (`join_transaction_mode="create_savepoint"`).
- Override auth with `app.dependency_overrides[get_current_user]`.
- Cover per endpoint: the happy path, validation failure (422 with the field), unauthenticated (401), another user's resource (404/403), constraint conflict (409), and pagination boundaries (empty page, last page, invalid cursor).
- In CI: `alembic upgrade head` on an empty database, `alembic check` (no model/migration drift), `ruff check`, `ruff format --check`, and the type checker.

## Production-readiness checklist

- [ ] Every endpoint has explicit request and response schemas. The generated OpenAPI is reviewed.
- [ ] Routers are protected by default. Object-level authorization is tested for every resource.
- [ ] No blocking I/O in `async def`. All outbound HTTP has timeouts.
- [ ] One commit per unit of work in the service, with no commit in dependency teardown.
- [ ] Relationships use `lazy="raise"`. List endpoints eager-load what they serialize.
- [ ] Growing lists use cursor pagination with a capped limit.
- [ ] One Problem Details error format, including 422 and 500. No stack traces in responses.
- [ ] Structured JSON logs with `requestId`. No tokens, passwords, or unnecessary PII.
- [ ] `/health/live` (process up) and `/health/ready` (database reachable). Neither runs migrations.
- [ ] Settings are validated at startup. Secrets come from the environment or a secret manager.
- [ ] Migrations run as a separate release step. `alembic check` is clean.
- [ ] Durable jobs for must-happen side effects, with retries, idempotency, and dead-letter alerting.
- [ ] Rate limits on auth and write-heavy endpoints.
- [ ] Graceful shutdown: lifespan closes HTTP clients and disposes of the engine.
- [ ] Tests run against PostgreSQL + PostGIS in CI.
