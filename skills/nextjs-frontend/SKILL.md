---
name: nextjs-frontend
description: Opinionated rules for production Next.js App Router frontends built with React, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, and Zustand, talking to a separate backend API. Use when creating or reviewing pages, layouts, Server and Client Components, Server Actions, Route Handlers, proxy/middleware, data fetching, caching, mutations, forms, or client state, and when deciding where frontend logic and state should live.
license: MIT
metadata:
  author: suleyman
  version: "1.0.0"
---

# Next.js Frontend

Build web apps and admin panels with the Next.js App Router as the **presentation layer and a thin BFF** in front of a backend API, such as FastAPI. The API owns business rules, authorization, and the database. Next.js renders UI, holds the web session cookie, and forwards requests.

Targets Next.js 15–16, React 19, Tailwind CSS v4, TanStack Query v5, and Zustand v5. Read `package.json` before using version-specific APIs (see [Version notes](#version-notes)).

## When to use

- Adding or changing routes, layouts, pages, `loading.tsx`, `error.tsx`, Route Handlers, Server Actions, or `proxy.ts`.
- Fetching, caching, or mutating remote data. Building forms, tables, and dashboards.
- Deciding where a piece of state should live.
- Reviewing a frontend PR.

Hand off to `fullstack-architect` for auth design, API contracts, and system boundaries. Hand off to `python-fastapi` when the fix belongs in the API.

## Core rules

1. **Server Components by default.** Add `"use client"` only for state, effects, event handlers, browser APIs, or client-only libraries. Put the boundary at the leaves: a page stays a Server Component and renders `<LikeButton>` as a client island.
2. **Every piece of state has exactly one owner.**

   | State | Owner |
   |---|---|
   | Server data rendered once, not refetched on the client | Server Component `await` |
   | Server data that is refetched, paginated, or mutated in the browser | TanStack Query |
   | Filters, sort, pagination, tabs, selected IDs | URL (`searchParams`) |
   | Form input | React Hook Form or `useActionState` |
   | Ephemeral component UI | `useState` |
   | Client UI state shared across distant components (sidebar, wizard step, editor selection) | Zustand |

   **Never copy server data into Zustand or `useState`.** Read it from the query and derive it with `select` or plain computation.
3. **Components render. They don't decide.** Business rules (permissions, state transitions, pricing) live in the API. Frontend-only logic (formatting, view-model mapping) lives in pure functions in `features/*/lib/` and gets unit tests. If a component contains `if (user.role === "admin" && post.status !== "locked")`, the API should return `viewerCanEdit` instead.
4. **The API is the authority.** Client validation is for UX. Hiding a button is not authorization.
5. **Design all async states.** Every query renders loading, empty, error-with-retry, and success. Every mutation renders pending and failure. "It shows a spinner forever" counts as a bug.
6. **Types come from the API contract.** Use types generated from the backend's OpenAPI schema (`openapi-typescript`). Never hand-write a duplicate `type Post = {...}`.

## Architecture

```
src/
├── app/                        # routing only: page, layout, loading, error, not-found, route
│   ├── (auth)/sign-in/page.tsx
│   ├── (app)/                  # authenticated area
│   │   ├── layout.tsx
│   │   └── posts/{page,loading,error}.tsx
│   └── api/                    # Route Handlers: BFF proxy, auth callbacks, webhooks only
├── features/
│   └── posts/
│       ├── api.ts              # query key factory, queryOptions, mutation hooks
│       ├── components/
│       ├── lib/                # pure logic, unit tested
│       └── store.ts            # Zustand, only if justified
├── components/
│   ├── ui/                     # shadcn/ui primitives (generated, owned, kept generic)
│   └── ...                     # shared composites
├── lib/
│   ├── api/                    # generated types, server client, browser client, errors
│   ├── auth/                   # session helpers (server-only)
│   ├── query-client.ts
│   └── env.ts                  # zod-validated env
└── proxy.ts                    # optimistic redirects only (middleware.ts before Next 16)
```

- `app/` files stay thin: read `params`/`searchParams`, call the data layer, compose feature components.
- Features never import another feature's internals. Shared code moves to `components/` or `lib/`.
- Add `import "server-only"` to every module that reads secrets or cookies, or calls the API with user credentials.
- Validate env at startup with zod in `lib/env.ts`. Only `NEXT_PUBLIC_*` variables reach the browser, so never put a secret there.
- Keep feature logic out of `components/ui/*`. Wrap primitives in feature components. Review CLI diffs when you update shadcn components.
- Tailwind v4: design tokens live in CSS `@theme`. Build conditional classes with `cn()` (clsx + tailwind-merge) and variants with `cva`. **Never build class names dynamically** (`` `bg-${color}-500` ``). Tailwind only generates classes it finds as complete strings.

## Data fetching and mutations

**Read-only server data:** `await` it in the Server Component. Run independent requests in parallel with `Promise.all` and wrap slow sections in `<Suspense>` so they stream.

**Interactive data:** prefetch on the server and hydrate into TanStack Query. Server and client share one `queryOptions` factory, so the cache key stays the same:

```tsx
// app/(app)/posts/[id]/page.tsx (Server Component)
export default async function PostPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const api = await getServerApi();                 // server-only, forwards the session token
  const queryClient = getQueryClient();
  await queryClient.prefetchQuery(postQueries.detail(api, id));
  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <PostDetail id={id} />                        {/* client: useQuery(postQueries.detail(browserApi, id)) */}
    </HydrationBoundary>
  );
}
```

- Never fetch in `useEffect`. Use TanStack Query or a Server Component.
- Build query keys with a per-feature factory and include every variable the `queryFn` reads.
- Set a non-zero default `staleTime` (e.g. 30s) so hydrated data is not refetched immediately.
- Pass the query's `signal` to `fetch` so cancelled queries cancel their requests.
- After a mutation, `invalidateQueries` with a key prefix from the factory. Use optimistic updates only when you define the rollback (`onError`) and the failure UX.
- **Each mutation follows the owner of its data.** For data TanStack Query owns, use `useMutation` and invalidate. For data only Server Components render, use a Server Action and revalidate. Don't mutate TanStack-owned data through a Server Action unless you also invalidate the query.
- Server Actions are public POST endpoints. Every action authenticates and validates its input with zod, then calls the API. An action never contains business logic: if mobile needs the same operation, it must be an API endpoint.
- Route Handlers exist for the BFF proxy, OAuth callbacks, cookie-setting auth endpoints, and webhooks. Don't build a second API in them.

Full patterns are in [references/data-fetching.md](references/data-fetching.md): QueryClient setup, key factories, server/browser API clients, the BFF proxy, Server Actions, and Zustand stores that are safe for SSR.

## Caching (Next.js 16 with `cacheComponents`)

- Caching is opt-in with `"use cache"`, `cacheLife`, and `cacheTag`. Cache only **shared, non-personalized** data. Anything derived from cookies, headers, or the session stays uncached.
- After a mutation in a Server Action, call `updateTag(tag)` for read-your-writes, or `revalidateTag(tag, "max")` for stale-while-revalidate. The single-argument `revalidateTag` is deprecated.
- When data looks stale, check the caches in this order: TanStack Query (devtools) → `"use cache"` entries → the client router cache → the CDN.

## Authentication on the web

- The API's tokens live in an **httpOnly, Secure, SameSite=Lax** cookie, set only by server code (Route Handler, Server Action, or `proxy.ts`). Never use `localStorage`. Browser JavaScript never sees a token.
- Server Components cannot set cookies. Refresh expiring tokens in `proxy.ts` or a Route Handler, not during render.
- `proxy.ts` is for **optimistic** redirects only, such as "no session cookie, so go to sign-in". Re-check the session wherever protected data is read (the server API client), and rely on the API to authorize. Layouts don't re-render on every navigation, so a check in a layout alone does not protect child pages.
- Client-side queries reach the API through a same-origin BFF proxy Route Handler that attaches the bearer token. The API therefore sees the same `Authorization: Bearer` from web, mobile, and iOS.

## Patterns to avoid

| Avoid | Do instead |
|---|---|
| `"use client"` at the top of a page or layout | Client components at the leaves, receiving server data as props |
| `useEffect` + `fetch` + `useState` for remote data | TanStack Query or a Server Component `await` |
| Copying query results into Zustand or `useState` | Read from the query, derive with `select` |
| A module-level Zustand store initialized from per-user server data | Per-request store through a context provider (see references) |
| Permission logic duplicated from the backend | API-provided capability flags (`viewerCanEdit`) |
| Hand-written API response types | Types generated from OpenAPI |
| Passing whole API objects to Client Components | Pass only the fields the component renders, because props are serialized into the HTML |
| Secrets in `NEXT_PUBLIC_*` | Server-only env read in `server-only` modules |
| `` `text-${size}` `` class strings | Static variant maps or `cva` |
| `router.refresh()` to update client-fetched data | `queryClient.invalidateQueries` |
| Array index as `key` in mutable lists | Stable IDs |
| Relative time ("3 min ago") rendered on the server | Fixed-format timestamps on the server, relative time after mount |
| Barrel files re-exporting a whole feature | Direct imports |
| `useMemo`/`useCallback` everywhere by default | Measure first, or enable the React Compiler |

## Debugging

- **Hydration mismatch:** look for `Date.now()`, `Math.random()`, `typeof window` branches, locale- or time-zone-dependent formatting, and invalid HTML nesting (`<div>` inside `<p>`). Pin locale and `timeZone` in `Intl` formatters. Use `suppressHydrationWarning` only on intentionally different leaf text.
- **"Functions cannot be passed to Client Components":** a non-serializable prop crossed the boundary. Pass data, or a Server Action.
- **Query doesn't refetch after a mutation:** the invalidation key doesn't prefix-match. Use the key factory on both sides.
- **Duplicate requests after hydration:** `staleTime` is 0, or the server and client keys differ (different params or rounding).
- **Prerender error for `cookies()`/`headers()`:** dynamic APIs were called inside a cached or static scope. Move them out, or wrap the dynamic part in `<Suspense>`.
- **Tools:** TanStack Query Devtools, React DevTools Profiler, the route table printed by `next build`, and a bundle analyzer for unexpected client weight.

## Performance

- Keep client bundles small. Load heavy client-only widgets (charts, editors, maps) with `next/dynamic` from inside a Client Component.
- Avoid request waterfalls: start independent fetches together and stream slow regions with Suspense.
- Use `next/image` with explicit `sizes` and `images.remotePatterns`, and preload the LCP image. Load fonts with `next/font`.
- Paginate on the server. Virtualize client lists over a few hundred rows (TanStack Virtual).
- Zustand: subscribe with narrow selectors. Use `useShallow` when selecting several fields.
- Track Core Web Vitals (LCP, INP, CLS) in production, not only in Lighthouse.

## Security

- Server Actions and Route Handlers are public endpoints: authenticate, validate, and rate-limit them like API routes.
- Never pass tokens, secrets, or unneeded PII as props. Anything passed to a Client Component ends up in the page source.
- Avoid `dangerouslySetInnerHTML`. If it's unavoidable, sanitize with DOMPurify.
- Validate redirect targets (`?next=`): accept relative paths only, to prevent open redirects.
- The BFF proxy forwards only to the configured API origin. Never build an upstream URL from user input.
- Send security headers: CSP, HSTS, `X-Content-Type-Options`, and `frame-ancestors`. Add `robots: noindex` on admin apps.

## Testing

- **Unit (Vitest):** pure functions in `features/*/lib`, plus key factories and mappers.
- **Component (React Testing Library + MSW):** render loading, empty, error, and success for every data-driven component. Assert that the retry button calls the API again.
- **E2E (Playwright):** sign-in, the core create/edit flows, permission-denied paths, and any payments, run against a seeded API. Test async Server Components here, because unit runners support them poorly.
- **CI:** `tsc --noEmit`, plus ESLint or Biome run explicitly (`next build` no longer lints in Next 16), plus a check that regenerated API types match the committed ones.

## Production-readiness checklist

- [ ] Every data-fetching segment has `loading.tsx` or Suspense, plus `error.tsx`. Missing resources call `notFound()`.
- [ ] Every list and query has empty and error-with-retry states. Every mutation shows pending and failure.
- [ ] No page- or layout-level `"use client"` without a written reason.
- [ ] No secrets in `NEXT_PUBLIC_*`. Env is validated at startup.
- [ ] Server Actions and Route Handlers authenticate and validate input.
- [ ] Auth is not enforced only in `proxy.ts` or layouts. The API authorizes every request.
- [ ] Tokens are only in httpOnly cookies. The BFF proxy is locked to the API origin.
- [ ] API types are generated, and CI fails on drift.
- [ ] Security headers are set, `images.remotePatterns` is restricted, and admin apps are `noindex`.
- [ ] Errors are reported from client and server with source maps that are not publicly served.
- [ ] Keyboard navigation, focus management in dialogs, and form labels are verified.
- [ ] Type check, lint, unit, and E2E tests pass in CI.

## Version notes

- **Next.js 16:** `middleware.ts` is renamed to `proxy.ts` (exported function `proxy`, Node.js runtime). Sync access to `params`, `searchParams`, `cookies()`, and `headers()` is removed, so always `await` them. Turbopack is the default bundler. `next lint` is removed. `revalidateTag` requires a `cacheLife` profile. `updateTag` and `refresh` are Server Action-only.
- **Next.js 15:** request APIs are async (sync access is deprecated) and `fetch` is uncached by default. `"use cache"` requires an experimental flag, so check `next.config` before using it.
