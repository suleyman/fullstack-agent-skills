# Architecture proposal template

Use this structure for every new product or cross-layer feature. Keep it short enough that people read it, and specific enough that someone else could implement it. Replace the guidance in italics. Delete sections that genuinely don't apply, and say why in one line.

---

# <Product or feature> — Architecture

**Status:** Draft | Accepted | Superseded by <link>  
**Date:** YYYY-MM-DD  
**Owners:** <names or roles>

## 1. Context and goals

*What problem this solves and for whom. The 3–5 critical user flows, as one line each ("An attendee reserves two tickets and sees them in the app seconds after paying").*

## 2. Assumptions and non-goals

*Scale (MAU, peak requests/s, writes/s, data growth per month), supported platforms and OS versions, regions, compliance constraints. Name what's explicitly out of scope for this version.*

## 3. Stack decisions

| Layer | Choice | Why (constraint it satisfies) | Skill |
|---|---|---|---|
| Web/admin | | | `nextjs-frontend` |
| Mobile | | | `expo-react-native` |
| iOS (native) | *only if needed, with a reason* | | `swift-ios` |
| API | | | `python-fastapi` |
| Database | | | `postgresql` |
| Jobs | | | `python-fastapi` |
| Files | | | |
| Auth | | | |
| Hosting | | | |

*Any deviation from the default stack names the constraint that forces it.*

## 4. System diagram

*Plain text or Mermaid. Show clients, BFF, API, workers, database, object storage/CDN, third parties (push, email, payments, IdP), and which component talks to which. Mark trust boundaries.*

```
[Expo app] ──HTTPS/Bearer──▶ [FastAPI] ──▶ [PostgreSQL]
[Next.js admin] ─(BFF)─────▶     │   └──▶ [jobs table] ◀── [worker] ──▶ Expo Push / email
                                 └──▶ presign ──▶ [object storage] ──▶ [CDN]
```

## 5. Domain model

*Tables with key columns, constraints, and the indexes that serve named queries. Mark sensitive columns.*

| Table | Key columns | Constraints | Indexes (→ query served) |
|---|---|---|---|
| | | | |

## 6. API surface

*Endpoints grouped by resource. For each: method + path, auth requirement (public / user / role), request and response schema names, pagination style. Note idempotency keys and rate limits where relevant.*

| Method | Path | Auth | Request → Response | Notes |
|---|---|---|---|---|
| | | | | |

## 7. Authentication and authorization

*Identity source. Token lifecycle per client (web cookie via BFF, mobile SecureStore/Keychain). Roles. Object-level rules per resource ("buyers see only their own orders; organizers see orders for events they own"). Admin audit logging.*

## 8. Cross-cutting decisions

- **Errors:** *Problem Details codes introduced by this design.*
- **Validation:** *rules that need database constraints.*
- **Caching:** *what is cached, where, and how each entry is invalidated.*
- **Pagination:** *cursor key per list.*
- **Background jobs:** *job kinds, dedupe keys, retry policy.*
- **Uploads:** *purposes, allowed types, size limits, processing.*
- **Notifications:** *triggers, payload (route + id), opt-out.*
- **Observability:** *key metrics, alerts, dashboards.*

## 9. Data lifecycle and privacy

*PII inventory, sensitive data (payments, health, location), retention periods, account deletion behavior across the database, storage, analytics, and push. Store privacy labels this implies.*

## 10. Environments and deployment

*Environments and their isolation. Pipeline steps (build → migrate → deploy → smoke test). Release order across layers. Rollback per component. Mobile: build profiles, channels, minimum supported version policy.*

## 11. Performance and scaling

*Budgets (e.g. p95 per critical endpoint). Expected load. The first bottleneck you expect, and the planned response when it arrives.*

## 12. Risks and open questions

| Risk / question | Impact | Mitigation / owner |
|---|---|---|
| | | |

## 13. Delivery plan

*Vertical slices in order. Each one is shippable and covers DB → API → generated types → UI → tests.*

1. Slice 1: auth, `/me`, app config, minimum-version gate
2. Slice 2: …

## Decision log

| Date | Decision | Alternatives considered | Reason |
|---|---|---|---|
| | | | |
