---
name: postgresql
description: Opinionated rules for PostgreSQL and PostGIS behind an application API, covering schema design, constraints, indexing, query optimization, transactions and concurrency, cursor pagination, zero-downtime migrations with Alembic, and geospatial queries. Use when designing tables, writing or reviewing migrations, adding indexes, reading EXPLAIN output, fixing slow queries or lock contention, handling race conditions, or implementing location and radius search.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# PostgreSQL

Design and operate the PostgreSQL database that one API service owns. The database is the **last line of defense for correctness**: it enforces invariants the application can't guarantee under concurrency. Migrations are treated as deployments.

## When to use

- Designing or changing tables, constraints, or indexes.
- Writing or reviewing an Alembic migration, especially against a table that already has traffic.
- Diagnosing a slow query, lock waits, deadlocks, connection exhaustion, or bloat.
- Implementing pagination, counters, queues, upserts, or anything with concurrent writers.
- Storing or querying locations with PostGIS.

## Core rules

1. **Enforce invariants in the database.** Uniqueness, referential integrity, non-null, value ranges, and non-overlap are `UNIQUE`, `FOREIGN KEY`, `NOT NULL`, `CHECK`, and `EXCLUDE` constraints. Application checks exist to produce friendly error messages. Constraints are what keep the data correct. "Select, then insert if missing" in application code is a race: use a unique constraint plus `ON CONFLICT`.
2. **Indexes follow queries.** Every index must name the query or constraint it serves. Add indexes from real query patterns (`pg_stat_statements`, `EXPLAIN`), not by habit.
3. **Migrations are deployments.** Each one is compatible with the code currently running, is safe under production load, and runs as a separate pipeline step, never on application startup.
4. **Keep transactions short.** Never hold one open across network calls, user think-time, or file uploads.
5. **One owner.** Only the API service's migration step changes the schema. No other service writes to its tables.

## Schema design

- **Primary keys:** `uuid` (UUIDv7 for insert locality) for anything exposed through the API. Generate it in the application, or with `uuidv7()` on PostgreSQL 18+. Use `bigint GENERATED ALWAYS AS IDENTITY` for internal tables. Never use `serial` in new code, and never expose sequential IDs publicly.
- **Time:** always `timestamptz`, never `timestamp`. Store UTC and convert at the edges.
- **Money:** `numeric(12,2)` or integer minor units (cents) with a currency column. Never floating point.
- **Strings:** `text`, with a `CHECK (char_length(col) <= n)` where the length is a real business rule.
- **Small sets of values:** `text` + `CHECK (status IN (...))` when values may change. Native `ENUM` types only for truly fixed sets, because enum values can't be removed and adding them has transaction restrictions.
- **`NOT NULL` by default.** Make a column nullable only when "unknown" or "absent" means something.
- **Foreign keys always,** with a deliberately chosen `ON DELETE`: `RESTRICT` (the default choice), `CASCADE` for owned children, `SET NULL` for optional references.
- **Name every constraint** through the SQLAlchemy naming convention (`uq_users_email`, `ck_posts_body_length`), so migrations stay deterministic and the API can map violations to error codes.
- **Case-insensitive uniqueness:** a unique index on `lower(email)`, or the `citext` type.
- **Multi-tenant data:** `tenant_id` on every tenant-owned table, as the leading column of composite indexes and unique constraints (`UNIQUE (tenant_id, slug)`).
- **`jsonb`** for genuinely schemaless data (provider payloads, user settings). Never for data you filter, join, or constrain relationally.
- **Soft delete** only when the product requires it. Then every unique constraint becomes a partial index (`WHERE deleted_at IS NULL`) and every query must filter on it. Consider an archive table instead.

## Indexing

- **Composite order:** equality columns first, then the range or sort column. For `WHERE author_id = $1 ORDER BY created_at DESC, id DESC`, use `(author_id, created_at, id)`. A btree scans backwards, so you need `DESC` in the index only when sort directions are mixed.
- **Partial indexes** for hot subsets: `WHERE status = 'pending'`, `WHERE deleted_at IS NULL`.
- **Expression indexes** must match the query expression exactly (`lower(email)`).
- **`INCLUDE` columns** enable index-only scans on hot read paths.
- **Index types:** GIN for `jsonb @>`, arrays, full-text, and `pg_trgm` (`ILIKE '%term%'`). GiST for PostGIS, ranges, and exclusion constraints. BRIN for very large append-only tables correlated with insert order.
- **Foreign keys:** index the referencing column **when you filter children by parent** (`WHERE post_id = $1`) **or when parent rows are deleted or updated**. Without one, each parent delete scans the whole child table, and `ON DELETE CASCADE` multiplies that. **Don't add FK indexes blindly:** skip them when the FK is never filtered and the parent is never deleted (e.g. `created_by` on an append-only audit log), and when the FK is already the leading column of another index.
- **Unused indexes cost writes** and prevent HOT updates. Find them with `pg_stat_user_indexes.idx_scan = 0` over a representative period, and check replicas too, because statistics are per node.
- On tables with traffic, always use `CREATE INDEX CONCURRENTLY`. After a failed concurrent build, find and drop the `INVALID` index before retrying.

## Queries

- **No N+1 queries.** Join, or batch with `WHERE id = ANY($1::uuid[])`.
- **Keyset (cursor) pagination** for anything that grows:
  ```sql
  SELECT id, body, created_at FROM posts
  WHERE author_id = $1 AND (created_at, id) < ($2, $3)
  ORDER BY created_at DESC, id DESC
  LIMIT 21;  -- page size + 1 tells you whether a next page exists
  ```
  `OFFSET` reads and discards every skipped row and shifts when rows are inserted. Use it only for small, bounded admin tables where jumping to page N matters.
- **No exact `COUNT(*)`** over large tables per request. Use `hasMore` from `LIMIT n + 1`, or estimates (`pg_class.reltuples`).
- **Upserts:** `INSERT ... ON CONFLICT (cols) DO UPDATE | DO NOTHING`.
- **Bulk writes:** multi-row `INSERT`, `COPY`, or `UPDATE ... FROM (VALUES ...)`. Not a loop of single statements.
- **Keep predicates sargable:** `created_at >= $1 AND created_at < $2`, not `date(created_at) = $1`. Use `= ANY($1)` instead of huge `IN (...)` lists.

## Transactions and concurrency

- Use one transaction when several writes must succeed or fail together. Keep the default `READ COMMITTED` unless a specific anomaly requires more.
- **Lost updates:** use an atomic `UPDATE ... SET n = n + 1`, a `SELECT ... FOR UPDATE` on the row, or optimistic concurrency (`UPDATE ... WHERE id = $1 AND version = $2`, where 0 rows means 409).
- **`SERIALIZABLE`** for invariants spanning rows. Callers must retry on `40001` (serialization failure) and `40P01` (deadlock).
- **Job queues:** `SELECT ... FOR UPDATE SKIP LOCKED LIMIT n`.
- **Singleton jobs** (cron across replicas): `pg_try_advisory_xact_lock(key)`.
- **No overlaps** (bookings, shifts): `EXCLUDE USING gist (resource_id WITH =, during WITH &&)`, which needs `btree_gist`.
- **Deadlocks:** lock rows in a consistent order, such as ascending `id`.
- Set `statement_timeout` and `idle_in_transaction_session_timeout` on the application role (`ALTER ROLE app SET ...`).

## Migrations

The full playbook is in [references/zero-downtime-migrations.md](references/zero-downtime-migrations.md). The non-negotiables:

- **Run migrations as a release step** (a CI job or a one-off task using the release image) before new code rolls out. **Never run them on application startup.** Replicas race, long locks block boot, a failure crash-loops every instance, and destructive steps run before anyone reviews them.
- **Set `lock_timeout`** (e.g. `5s`) for every migration, so a blocked DDL fails fast instead of queuing every query behind it.
- **Expand, migrate, contract.** Add the new structure, dual-write, backfill in batches, switch reads, and drop the old structure in a **later release**, after no deployed code (and no supported mobile app version, through the API) depends on it.
- **No table rewrites under traffic:** add `NOT NULL`, `CHECK`, and FK constraints as `NOT VALID`, then `VALIDATE` them separately. Build indexes `CONCURRENTLY`. Change types by adding a new column.
- **Review every autogenerated migration.** Alembic renders a rename as drop + add (data loss), doesn't emit `CONCURRENTLY`, and misses some changes.

## PostGIS

Details and privacy guidance are in [references/postgis.md](references/postgis.md).

- Use `geography(Point, 4326)` for "within N meters" on global data, and `geometry` with a projected SRID for heavy spatial analysis in a bounded region.
- **Longitude comes first:** `ST_MakePoint(lng, lat)`. A swapped pair is the most common geospatial bug.
- **Radius search:** `ST_DWithin(location, $point::geography, $meters)`, which uses the GiST index. Never `ST_Distance(...) < $r` in `WHERE`, which can't use the index.
- **Nearest N:** `ORDER BY location <-> $point LIMIT n`.
- Never return other users' precise coordinates. Snap them to a grid and bucket distances.

## Debugging

- **`EXPLAIN (ANALYZE, BUFFERS)`.** Look for a Seq Scan on a large table with a selective filter, estimated vs actual rows off by 10× or more (run `ANALYZE`, or add `CREATE STATISTICS` for correlated columns), a large "Rows Removed by Filter", sorts spilling to disk, or a nested loop with a large outer side. `ANALYZE` executes the statement, so wrap writes in `BEGIN; ... ROLLBACK;`.
- **Top offenders:** `pg_stat_statements` ordered by `total_exec_time`. Fix the queries that dominate total time, not the slowest one-off.
- **Locks:** join `pg_stat_activity` with `pg_blocking_pids(pid)`. Look for long `idle in transaction` sessions.
- **Index not used:** type mismatch (`uuid` vs `text` parameter), a function wrapped around the column, low selectivity, a `LIKE 'prefix%'` without `text_pattern_ops` under a non-C collation, or stale statistics.
- **Bloat or vacuum lag:** `pg_stat_user_tables.n_dead_tup` and `last_autovacuum`. Tune autovacuum per table for high-churn tables.
- Query plans on a 100-row development database tell you nothing. Reproduce with production-like volume and distribution.

## Performance

- Put a pooler (PgBouncer in transaction mode) in front once replicas × pool size approaches a few hundred connections. Each Postgres connection is a process.
- Use materialized views (`REFRESH MATERIALIZED VIEW CONCURRENTLY`, which needs a unique index) or denormalized counters for expensive aggregates read often.
- Read replicas suit analytics and admin reads. Anything that needs read-your-writes stays on the primary.
- Partition only very large time-series tables with retention needs (drop partitions instead of `DELETE`). It's not a default optimization.

## Security

- **Separate roles:** a migration role that owns the schema (DDL), and an application role with DML on its tables only. No superuser, no `CREATE` on the schema. Apps never connect as `postgres`.
- **TLS** to the database (`sslmode=verify-full` where the provider supports it).
- **Parameterized queries only.**
- Row-level security is optional defense-in-depth for multi-tenant data (`SET LOCAL app.tenant_id` per transaction, which is pooler-safe only with `SET LOCAL`).
- **Backups:** point-in-time recovery enabled and a restore drill on a schedule. A backup you've never restored doesn't count.
- Never copy production data into development or staging without anonymization.

## Testing

- Test against the same major version and extensions as production. Never SQLite.
- In CI: migrate an empty database to head. For reversible migrations, run downgrade one step and upgrade again.
- Assert that constraints reject bad data (unique, check, FK, exclusion) and that the API maps each violation to the right error.
- Rehearse risky migrations on an anonymized production-sized copy and measure lock and run times.
- Check plans of critical queries on seeded data with realistic cardinality.

## Production-readiness checklist

- [ ] Every table has a primary key, `NOT NULL` by default, `timestamptz`, and named constraints.
- [ ] Business invariants are constraints, not only application checks.
- [ ] Every index maps to a query or constraint. No duplicate or redundant indexes.
- [ ] Hot queries are `EXPLAIN ANALYZE`d at realistic volume. No N+1.
- [ ] Growing lists use keyset pagination with a capped page size.
- [ ] Migrations set `lock_timeout`, use `CONCURRENTLY` and `NOT VALID` on live tables, and follow expand/contract.
- [ ] Migrations run as a release step with a dedicated DDL role, not at startup.
- [ ] The application role is least-privilege, with `statement_timeout` and `idle_in_transaction_session_timeout` set.
- [ ] `pg_stat_statements` and slow query logging (`log_min_duration_statement`) are on.
- [ ] Backups use PITR, and a restore drill has been completed.
- [ ] PostGIS: GiST index, lon/lat order verified by a test, `ST_DWithin` for radius queries, coarse coordinates in responses.
