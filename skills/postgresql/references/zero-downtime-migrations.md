# Zero-downtime migrations

Playbook for changing a PostgreSQL schema under live traffic with Alembic. Assume PostgreSQL 12 or newer.

## Why DDL causes outages

Most `ALTER TABLE` forms take an `ACCESS EXCLUSIVE` lock. The lock itself is often brief. The danger is the **lock queue**: if a long-running query holds a conflicting lock, your `ALTER` waits, and every query that arrives after it waits behind the `ALTER`. A "metadata-only" change can take the site down this way. `lock_timeout` turns that into a fast, retryable migration failure.

## Alembic environment defaults

Put these in `migrations/env.py`, so every migration gets them:

```python
from sqlalchemy import text

def do_run_migrations(connection):
    # Serialize concurrent migration runs (two pipelines, a retried job).
    connection.execute(text("SELECT pg_advisory_lock(7262001)"))
    # Fail fast instead of queuing production traffic behind a blocked DDL statement.
    connection.execute(text("SET lock_timeout = '5s'"))
    connection.execute(text("SET statement_timeout = '15min'"))

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=True,
        transaction_per_migration=True,   # one bad migration doesn't roll back the ones before it
    )
    with context.begin_transaction():
        context.run_migrations()
```

Run migrations in the deploy pipeline as their own step, with the release image and the DDL role:

```
build image → run `alembic upgrade head` (one-off job) → deploy app/worker → smoke test
```

If the migration step fails, the deploy stops and the old code keeps running against the old schema. Every migration must therefore be **compatible with the code that's currently deployed**.

## Operation reference

| Operation | Risk | Safe approach |
|---|---|---|
| `ADD COLUMN` nullable, no default | Brief exclusive lock | Safe with `lock_timeout` |
| `ADD COLUMN ... DEFAULT <constant or stable expr>` | Metadata-only | Safe. `now()` counts as stable, so it's fine |
| `ADD COLUMN ... DEFAULT <volatile expr>` (`gen_random_uuid()`, `clock_timestamp()`) | **Full table rewrite** | Add nullable, backfill in batches, then set the default for new rows |
| `ALTER COLUMN ... SET NOT NULL` | Full scan under exclusive lock | `ADD CONSTRAINT ck CHECK (col IS NOT NULL) NOT VALID` → `VALIDATE CONSTRAINT ck` → `SET NOT NULL` (uses the validated check, no scan) → `DROP CONSTRAINT ck` |
| `ADD CONSTRAINT ... CHECK` | Scan under exclusive lock | `NOT VALID`, then `VALIDATE CONSTRAINT` in a separate migration |
| `ADD CONSTRAINT ... FOREIGN KEY` | Locks both tables while validating | `NOT VALID`, then `VALIDATE CONSTRAINT` |
| `ADD CONSTRAINT ... UNIQUE` | Builds an index under lock | `CREATE UNIQUE INDEX CONCURRENTLY`, then `ADD CONSTRAINT ... UNIQUE USING INDEX` |
| `CREATE INDEX` | Blocks writes for the whole build | `CREATE INDEX CONCURRENTLY` (outside a transaction) |
| `DROP INDEX` | Exclusive lock | `DROP INDEX CONCURRENTLY` |
| `ALTER COLUMN TYPE` | Usually a full rewrite | New column + dual write + backfill + switch. Exceptions: `varchar(n)` → larger `n` and `varchar` → `text` are metadata-only |
| `RENAME COLUMN` / `RENAME TABLE` | Instant, but **breaks running code** | Expand/contract (below) |
| `DROP COLUMN` / `DROP TABLE` | Instant, but **breaks code that still references it** | Remove all references (including the SQLAlchemy model) and deploy first. Drop in a later release |
| Large `UPDATE` backfill | Long transaction, row locks, bloat, replication lag | Batches with a commit per batch, run outside the schema migration |
| `ALTER TYPE ... ADD VALUE` (enum) | New value is unusable until committed | Run in `autocommit_block()`. Prefer `text` + `CHECK` |

## Concurrent index in Alembic

```python
def upgrade() -> None:
    # CONCURRENTLY can't run inside a transaction block.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_posts_author_id_created_at_id",
            "posts",
            ["author_id", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_posts_author_id_created_at_id", table_name="posts", postgresql_concurrently=True)
```

A concurrent build that fails leaves an `INVALID` index behind. Find it and drop it before retrying:

```sql
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
```

A table created in the same migration has no traffic yet, so create its indexes normally. `CONCURRENTLY` only matters for live tables.

## Adding NOT NULL to a populated column

```python
# Migration A (after the code always writes the column, and the backfill is done)
def upgrade() -> None:
    op.execute("ALTER TABLE users ADD CONSTRAINT ck_users_display_name_not_null CHECK (display_name IS NOT NULL) NOT VALID")

# Migration B
def upgrade() -> None:
    op.execute("ALTER TABLE users VALIDATE CONSTRAINT ck_users_display_name_not_null")  # SHARE UPDATE EXCLUSIVE: reads and writes continue
    op.execute("ALTER TABLE users ALTER COLUMN display_name SET NOT NULL")                 # no scan: the validated check proves it
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_display_name_not_null")
```

## Expand/contract: renaming `users.name` to `users.display_name`

| Release | Schema | Code |
|---|---|---|
| 1. Expand | Add `display_name` (nullable) | Write both columns. Read `display_name`, falling back to `name` |
| 2. Backfill | Batched job copies `name` → `display_name` where null | No change |
| 3. Switch | `display_name` → `NOT NULL` (safe recipe above) | Read and write only `display_name`. Remove `name` from the model |
| 4. Contract | Drop `name` | No change |

Each row is a separate deploy. Releases 3 and 4 must never ship together. If the API exposed the old field, keep serving it until the minimum supported mobile app version no longer reads it.

## Batched backfill

Run it as a job or management command, not inside the migration transaction:

```python
BATCH = 5_000

async def backfill_display_name(session: AsyncSession) -> None:
    cursor = UUID(int=0)                   # walk the primary key in fixed-size ranges
    while True:
        upper = await session.scalar(
            text("SELECT max(id) FROM (SELECT id FROM users WHERE id > :cursor ORDER BY id LIMIT :batch) AS b"),
            {"cursor": cursor, "batch": BATCH},
        )
        if upper is None:
            break
        await session.execute(
            text("""
                UPDATE users SET display_name = name
                WHERE id > :cursor AND id <= :upper AND display_name IS NULL
            """),
            {"cursor": cursor, "upper": upper},
        )
        await session.commit()             # short transactions: no long locks, vacuum keeps up
        cursor = upper
        await asyncio.sleep(0.05)          # throttle to protect replicas and production latency
```

Watch replication lag and p95 latency while it runs.

## Downgrades

- Write `downgrade()` for reversible steps, so you can test them in CI.
- In production, **roll forward**: ship a new migration that fixes the problem. Never run downgrades automatically.
- Migrations that drop data are irreversible. Say so in the docstring and make `downgrade()` raise.

## Pre-merge review checklist

- [ ] The migration is compatible with the currently deployed code (old code + new schema works).
- [ ] No full-table rewrite or long exclusive lock on a live table.
- [ ] Indexes on live tables use `CONCURRENTLY` inside `autocommit_block()`.
- [ ] New constraints on live tables use `NOT VALID` + `VALIDATE`.
- [ ] Backfills run in batches, outside the migration.
- [ ] Destructive changes ship one release after code stops using the column or table.
- [ ] Autogenerated operations are reviewed: no accidental drops, renames handled explicitly.
- [ ] Rehearsed on production-sized data if the table is large.
