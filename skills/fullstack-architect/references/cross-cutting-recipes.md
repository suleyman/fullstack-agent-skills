# Cross-cutting recipes

Concrete designs for problems every app on this stack runs into. Adapt the names. Keep the guarantees.

## 1. Postgres-backed job queue (outbox + queue in one table)

Writing the job in the same transaction as the business change guarantees it runs exactly when the change commits. No separate outbox relay is needed at this scale.

```sql
CREATE TABLE jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    dedupe_key text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'dead')),
    run_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 10,
    locked_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_jobs_dedupe_key ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX ix_jobs_ready ON jobs (run_at) WHERE status = 'queued';
```

**Enqueue** (inside the service's transaction):

```python
session.add(Job(kind="push.comment_created", payload={"commentId": str(comment.id)},
                dedupe_key=f"push.comment_created:{comment.id}"))
await session.commit()  # the comment and its job commit together, or neither does
```

**Claim** (worker loop, each claim in its own short transaction):

```sql
UPDATE jobs
SET status = 'running', locked_at = now(), attempts = attempts + 1
WHERE id IN (
    SELECT id FROM jobs
    WHERE status = 'queued' AND run_at <= now()
    ORDER BY run_at
    LIMIT 10
    FOR UPDATE SKIP LOCKED
)
RETURNING id, kind, payload, attempts, max_attempts;
```

**Finish:**

- Success → `status = 'done'`, or delete the row. Purge done jobs on a schedule.
- Failure → if `attempts >= max_attempts`, set `status = 'dead'` and alert. Otherwise set `status = 'queued'` and `run_at = now() + backoff(attempts)` (exponential with jitter, capped).
- A reaper requeues `running` jobs whose `locked_at` is older than the job timeout (the worker crashed).

**Rules:**

- Handlers are idempotent. A job may run more than once (crash after the side effect, before marking done).
- Scheduled jobs (cron) run in the worker under `pg_try_advisory_xact_lock(<job key>)`, so only one replica runs each tick.
- When throughput outgrows this, around thousands of jobs per second, keep the table as an outbox and relay rows to a broker.

## 2. Presigned uploads

```sql
CREATE TABLE uploads (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose text NOT NULL CHECK (purpose IN ('avatar', 'post_image')),
    object_key text NOT NULL,
    content_type text NOT NULL,
    max_bytes integer NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'uploaded', 'ready', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_uploads_object_key UNIQUE (object_key)
);
```

```python
UPLOAD_RULES = {
    "avatar": {"types": {"image/jpeg", "image/png", "image/webp"}, "max_bytes": 5 * 1024 * 1024},
    "post_image": {"types": {"image/jpeg", "image/png", "image/webp", "image/heic"}, "max_bytes": 15 * 1024 * 1024},
}

async def create_upload(session, s3, *, owner: User, data: UploadCreate) -> UploadTicket:
    rules = UPLOAD_RULES[data.purpose]
    if data.content_type not in rules["types"] or data.size_bytes > rules["max_bytes"]:
        raise BadRequest(code="upload_not_allowed")

    upload_id = new_id()
    key = f"{settings.environment}/{data.purpose}/{owner.id}/{upload_id}"  # never a client filename
    session.add(Upload(id=upload_id, owner_id=owner.id, purpose=data.purpose, object_key=key,
                       content_type=data.content_type, max_bytes=rules["max_bytes"]))
    await session.commit()

    # Presigned POST enforces the size range and content type at the storage layer.
    form = s3.generate_presigned_post(
        Bucket=settings.upload_bucket,
        Key=key,
        Fields={"Content-Type": data.content_type},
        Conditions=[{"Content-Type": data.content_type}, ["content-length-range", 1, rules["max_bytes"]]],
        ExpiresIn=300,
    )
    return UploadTicket(upload_id=upload_id, url=form["url"], fields=form["fields"])
```

Presigning is local CPU work, but the boto3 client can resolve credentials over the network on first use. Create the client at startup. Then:

1. `POST /v1/uploads/{id}/complete`: the API checks ownership, `HEAD`s the object (exists, size ≤ `max_bytes`), sets `uploaded`, and enqueues `media.process_upload`.
2. The worker sniffs the real type from the magic bytes (reject on mismatch), re-encodes the image (which drops EXIF, including GPS), writes the variants (`thumb`, `medium`, `full`), and sets `ready`.
3. A lifecycle rule on the bucket deletes objects under pending prefixes after 24 h. A scheduled job marks stale `pending` rows `rejected`.

## 3. Idempotency keys

For POSTs that clients may retry over flaky networks (orders, payments, sending messages):

```sql
CREATE TABLE idempotency_keys (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key text NOT NULL,
    request_hash bytea NOT NULL,
    response_status integer,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
```

1. `INSERT ... ON CONFLICT DO NOTHING` the key with the hash of the method, path, and body.
2. If a row already existed: a different hash → 422 `idempotency_key_reused`. No stored response yet → 409 `request_in_progress`. A stored response → replay it.
3. Perform the operation and store the response **in the same transaction** as the business write.
4. Purge keys older than 24–48 h.

## 4. Minimum supported app version

```python
@public_router.get("/app-config")
async def app_config() -> AppConfig:
    return AppConfig(
        min_supported_version={"ios": "1.8.0", "android": "1.8.0"},
        latest_version={"ios": "1.9.2", "android": "1.9.2"},
        store_url={"ios": "https://apps.apple.com/app/id<id>", "android": "https://play.google.com/store/apps/details?id=<package>"},
    )
```

- Clients check it on launch and when they return to the foreground, and show a blocking screen with a store link when they're below the minimum.
- Raise the minimum only after the version adoption data shows the impact is acceptable, and before shipping an API change that old versions can't survive.
- For an emergency, the API can reject requests from an old `X-App-Version` with 426 and code `app_version_unsupported`. Clients map that code to the same blocking screen.

## 5. Error envelope

```json
{
  "type": "https://api.example.com/problems/validation_failed",
  "title": "Request validation failed",
  "status": 422,
  "code": "validation_failed",
  "detail": null,
  "requestId": "4f6c2b8e9a7d4c1e",
  "errors": [{ "field": "body", "code": "string_too_long", "message": "String should have at most 500 characters" }]
}
```

- `code` values are part of the API contract. Document them in the OpenAPI responses and never rename them.
- Clients map `errors[].field` (camelCase, matching the request body) to form fields.

## 6. Request ID propagation

```
client (generates or omits) → Next.js BFF (forwards or generates) → FastAPI (validates or generates)
  → logs, traces, job payloads (store request_id for the jobs a request enqueues)
  → response header X-Request-ID → client error report
```

One ID lets support go from a user's screenshot to the exact log lines and query.

## 7. Account deletion

1. `DELETE /v1/me` (re-authentication within the last few minutes is required): mark the user `deleting`, revoke all sessions, and enqueue `account.delete`.
2. The job deletes or anonymizes owned rows (in batches), deletes object-storage prefixes, removes push registrations, calls third-party deletion APIs (analytics, email provider), and finally deletes the user row.
3. Content other users depend on (comments in threads) is anonymized ("Deleted user") rather than deleted, if the product requires it. Write that policy down.
4. Both app stores require in-app account deletion when the app supports account creation.
