# Project skeleton

Reference implementations of the `app/core` modules, the app factory, and test fixtures. Copy what you need and adapt the names.

## `app/core/config.py`

```python
from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    database_url: PostgresDsn                      # postgresql+asyncpg://...
    jwt_issuer: str
    jwt_audience: str
    jwt_public_key: SecretStr                      # PEM, verifies access tokens
    jwt_private_key: SecretStr | None = None       # only on the service that issues tokens
    cors_origins: list[str] = []                   # empty when only servers call the API

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # raises ValidationError at startup if anything is missing
```

## `app/core/db.py`

```python
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import DateTime, MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)  # deterministic constraint names
    type_annotation_map = {datetime: DateTime(timezone=True)}  # Mapped[datetime] -> timestamptz


engine = create_async_engine(
    str(get_settings().database_url),
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
)
# statement_timeout / idle_in_transaction_session_timeout are set on the DB role, not here,
# so they also apply behind PgBouncer.

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session  # closing the session rolls back anything uncommitted


SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

## `app/core/ids.py`

```python
import uuid


def new_id() -> uuid.UUID:
    """Time-ordered UUIDv7: good index locality, not enumerable."""
    return uuid.uuid7()  # Python 3.14+. On older versions use the `uuid-utils` package.
```

## `app/core/schemas.py`

```python
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class APISchema(BaseModel):
    """Base for every request/response schema: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Page(APISchema, Generic[T]):
    items: list[T]
    next_cursor: str | None
```

## `app/core/pagination.py`

```python
import base64
import json
from datetime import datetime
from uuid import UUID

from app.core.errors import BadRequest


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, row_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(row_id)
    except (ValueError, TypeError) as exc:
        raise BadRequest(code="invalid_cursor", detail="The pagination cursor is invalid.") from exc
```

The cursor keeps full microsecond precision. A client that built cursors from displayed timestamps would lose precision (JavaScript `Date` keeps only milliseconds) and skip or repeat rows. That's why cursors are opaque.

## `app/core/errors.py`

```python
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class DomainError(Exception):
    status_code = 400
    code = "bad_request"
    title = "Bad request"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        if code:
            self.code = code


class BadRequest(DomainError): ...

class NotAuthenticated(DomainError):
    status_code, code, title = 401, "not_authenticated", "Authentication required"

class PermissionDenied(DomainError):
    status_code, code, title = 403, "permission_denied", "Permission denied"

class NotFound(DomainError):
    status_code, code, title = 404, "not_found", "Resource not found"

class Conflict(DomainError):
    status_code, code, title = 409, "conflict", "Conflict"


def problem(request: Request, *, status: int, code: str, title: str,
            detail: str | None = None, errors: list[dict] | None = None) -> JSONResponse:
    body = {
        "type": f"https://api.example.com/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "requestId": getattr(request.state, "request_id", None),
    }
    if errors:
        body["errors"] = errors
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return problem(request, status=exc.status_code, code=exc.code, title=exc.title, detail=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "code": err["type"], "message": err["msg"]}
            for err in exc.errors()
        ]
        return problem(request, status=422, code="validation_failed", title="Request validation failed", errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return problem(request, status=500, code="internal_error", title="Internal server error")
```

## `app/auth/dependencies.py`

```python
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.db import SessionDep
from app.core.errors import NotAuthenticated, PermissionDenied
from app.users.models import Role, User

bearer = HTTPBearer(auto_error=False)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_public_key.get_secret_value(),
        algorithms=["ES256"],                    # pinned; never trust the token's own alg
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub"]},
        leeway=30,
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: SessionDep,
) -> User:
    if credentials is None:
        raise NotAuthenticated()
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise NotAuthenticated() from exc
    user = await session.get(User, user_id)
    if user is None or user.disabled_at is not None:
        raise NotAuthenticated()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: Role):
    async def dependency(user: CurrentUser) -> User:
        if not user.role.at_least(minimum):
            raise PermissionDenied()
        return user

    return dependency
```

## `app/main.py`

```python
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request

from app.admin.router import router as admin_router
from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import register_error_handlers
from app.orders.router import organizer_router, router as orders_router
from app.public.router import router as public_router

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
    yield
    await app.state.http.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Example API",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex  # don't log arbitrary input
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    authenticated = [Depends(get_current_user)]
    app.include_router(public_router, prefix="/v1")
    app.include_router(orders_router, prefix="/v1", dependencies=authenticated)
    app.include_router(organizer_router, prefix="/v1", dependencies=authenticated)
    app.include_router(admin_router, prefix="/v1")  # router itself requires a staff role
    return app


app = create_app()
```

Bind `request_id` into your logger's context (for example `structlog.contextvars`), so every log line carries it. The `asgi-correlation-id` package is a pure-ASGI alternative to the `http` middleware above.

## Tests: `tests/conftest.py`

```python
import os

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.dependencies import get_current_user
from app.core.db import get_session
from app.main import create_app

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]  # a disposable PostGIS database


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def migrated_database() -> str:
    # Sync fixture: Alembic's async env.py calls asyncio.run(), which can't run inside the test loop.
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def engine(migrated_database: str):
    engine = create_async_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",  # service commits become savepoint releases
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()  # every test starts from a clean database


@pytest.fixture
async def client_for(session):
    """client_for(user) -> AsyncClient authenticated as `user`; client_for(None) -> anonymous."""
    clients: list[AsyncClient] = []

    async def shared_session():
        # Mirror production: get_session closes the session on errors, which rolls back a failed
        # request's partial writes. Without this, they would leak into later requests in the test.
        try:
            yield session
        except Exception:
            await session.rollback()  # rolls back to the savepoint, keeping earlier committed requests
            raise

    def make(user):
        app = create_app()
        app.dependency_overrides[get_session] = shared_session
        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: user
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()
```

Note: inside one test, every row created gets the same `now()` value (the transaction start time). Pagination tests therefore exercise the `id` tie-breaker, which is a feature, not a bug.
