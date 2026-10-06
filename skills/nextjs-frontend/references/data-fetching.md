# Data fetching patterns

Reference implementations for the rules in `SKILL.md`. These patterns assume:

- API types are generated with `openapi-typescript` into a workspace package (`@acme/api-types` here). The runtime client is `openapi-fetch`.
- The web session holds the API tokens in an httpOnly cookie, read by `getSession()` in `lib/auth/session.ts`.

## 1. API clients: one for the server, one for the browser

```ts
// lib/api/types.ts
import type { Client } from "openapi-fetch";
import type { paths } from "@acme/api-types";

export type ApiClient = Client<paths>;
```

```ts
// lib/api/server.ts
import "server-only";
import createClient from "openapi-fetch";
import { redirect } from "next/navigation";
import type { paths } from "@acme/api-types";
import { getSession } from "@/lib/auth/session";
import { env } from "@/lib/env";

/** Calls the API directly from the server with the user's access token. */
export async function getServerApi() {
  const session = await getSession(); // validates the cookie, never refreshes (render can't set cookies)
  if (!session) redirect("/sign-in");
  return createClient<paths>({
    baseUrl: env.API_URL,
    headers: { Authorization: `Bearer ${session.accessToken}` },
  });
}
```

```ts
// lib/api/browser.ts
import createClient from "openapi-fetch";
import type { paths } from "@acme/api-types";

/** Same-origin BFF proxy. The proxy attaches the token, so the browser never holds it. */
export const browserApi = createClient<paths>({ baseUrl: "/api/proxy" });
```

```ts
// lib/api/errors.ts
export type Problem = {
  code: string;
  title: string;
  status: number;
  detail?: string | null;
  requestId?: string | null;
  errors?: { field: string; code: string; message: string }[];
};

export class ApiError extends Error {
  constructor(readonly status: number, readonly problem: Problem | null) {
    super(problem?.title ?? `API request failed with ${status}`);
  }
  get code() {
    return this.problem?.code ?? "unknown_error";
  }
}

/** Converts an openapi-fetch result into data or a thrown ApiError (what TanStack Query expects). */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(result.response.status, (result.error as Problem | undefined) ?? null);
  }
  return result.data as T;
}
```

## 2. QueryClient: one per request on the server, one per tab in the browser

```ts
// lib/query-client.ts
import { QueryClient, isServer } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/errors";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000, // prevents an immediate refetch of freshly hydrated data
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status < 500) return false; // 4xx won't fix itself
          return failureCount < 2;
        },
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient() {
  if (isServer) return makeQueryClient(); // never share a cache between users
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
```

```tsx
// app/providers.tsx
"use client";
import { QueryClientProvider } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";

export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
```

## 3. Query key factories and query options

The same factory serves server prefetching and client hooks. The client is a parameter because the server and the browser reach the API differently. The key leaves the client out because both return the same data.

```ts
// features/posts/api.ts
import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@acme/api-types";
import type { ApiClient } from "@/lib/api/types";
import { unwrap } from "@/lib/api/errors";

export type Post = components["schemas"]["PostRead"];

export const postKeys = {
  all: ["posts"] as const,
  lists: () => [...postKeys.all, "list"] as const,
  list: (filters: { authorId?: string }) => [...postKeys.lists(), filters] as const,
  detail: (id: string) => [...postKeys.all, "detail", id] as const,
};

export const postQueries = {
  detail: (api: ApiClient, id: string) =>
    queryOptions({
      queryKey: postKeys.detail(id),
      queryFn: ({ signal }) =>
        api.GET("/v1/posts/{postId}", { params: { path: { postId: id } }, signal }).then(unwrap),
    }),
};

export function useDeletePost(api: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.DELETE("/v1/posts/{postId}", { params: { path: { postId: id } } }).then(unwrap),
    onSuccess: (_data, id) => {
      queryClient.removeQueries({ queryKey: postKeys.detail(id) });
      return queryClient.invalidateQueries({ queryKey: postKeys.lists() }); // returning keeps the mutation pending until the refetch finishes
    },
  });
}
```

Client usage, with every state handled:

```tsx
"use client";
export function PostDetail({ id }: { id: string }) {
  const post = useQuery(postQueries.detail(browserApi, id));

  if (post.isPending) return <PostSkeleton />;
  if (post.isError) {
    if (post.error instanceof ApiError && post.error.status === 404) return <PostNotFound />;
    return <ErrorPanel error={post.error} onRetry={() => post.refetch()} />;
  }
  return <PostView post={post.data} />;
}
```

## 4. Infinite lists with cursor pagination

```ts
export function postListQuery(api: ApiClient, filters: { authorId?: string }) {
  return infiniteQueryOptions({
    queryKey: postKeys.list(filters),
    queryFn: ({ pageParam, signal }) =>
      api
        .GET("/v1/posts", { params: { query: { ...filters, cursor: pageParam ?? undefined, limit: 20 } }, signal })
        .then(unwrap),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}
```

On the server, use `prefetchInfiniteQuery(postListQuery(api, filters))`. Treat cursors as opaque: never build them on the client from timestamps you display.

## 5. BFF proxy Route Handler

Lets client-side TanStack Query calls reach the API without exposing tokens. It is locked to the API origin, forwards a small header allowlist, and checks `Origin` on mutations.

```ts
// app/api/proxy/[...path]/route.ts
import { getSession } from "@/lib/auth/session";
import { env } from "@/lib/env";

const API_ORIGIN = new URL(env.API_URL).origin;
const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "if-none-match", "idempotency-key"];
const DROP_RESPONSE_HEADERS = ["content-encoding", "content-length", "transfer-encoding", "connection"];

async function proxy(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;

  if (path.some((segment) => segment === "." || segment === "..")) {
    return Response.json({ code: "bad_path", title: "Invalid path", status: 400 }, { status: 400 });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    const origin = request.headers.get("origin");
    if (origin !== env.APP_ORIGIN) {
      return Response.json({ code: "bad_origin", title: "Cross-origin request rejected", status: 403 }, { status: 403 });
    }
  }

  const session = await getSession({ refresh: true }); // Route Handlers may rotate and re-set cookies
  if (!session) {
    return Response.json({ code: "unauthenticated", title: "Sign in required", status: 401 }, { status: 401 });
  }

  const upstreamUrl = new URL(`/${path.map(encodeURIComponent).join("/")}`, API_ORIGIN);
  upstreamUrl.search = new URL(request.url).search;
  if (upstreamUrl.origin !== API_ORIGIN) {
    return Response.json({ code: "bad_path", title: "Invalid path", status: 400 }, { status: 400 });
  }

  const headers = new Headers({ authorization: `Bearer ${session.accessToken}` });
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("x-request-id", request.headers.get("x-request-id") ?? crypto.randomUUID());

  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });

  // fetch() already decoded the body, so forwarding content-encoding would corrupt it.
  const responseHeaders = new Headers(upstream.headers);
  for (const name of DROP_RESPONSE_HEADERS) responseHeaders.delete(name);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
```

## 6. Server Action for a server-rendered form

Use this when the data is rendered by Server Components rather than owned by TanStack Query.

```ts
// features/settings/actions.ts
"use server";
import { z } from "zod";
import { updateTag } from "next/cache";
import { getServerApi } from "@/lib/api/server";

const Input = z.object({
  displayName: z.string().trim().min(1).max(50),
});

export type ActionState =
  | { status: "idle" }
  | { status: "ok" }
  | { status: "error"; message?: string; fieldErrors?: Record<string, string[]> };

export async function updateProfile(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = Input.safeParse({ displayName: formData.get("displayName") });
  if (!parsed.success) {
    return { status: "error", fieldErrors: z.flattenError(parsed.error).fieldErrors };
  }

  const api = await getServerApi(); // authenticates; the API authorizes
  const { error } = await api.PATCH("/v1/me", { body: parsed.data });
  if (error) return { status: "error", message: error.title };

  updateTag("viewer-profile"); // only meaningful if a "use cache" scope is tagged with it
  return { status: "ok" };
}
```

## 7. Zustand

**Client-only UI state that is not derived from server data and not user-specific** can use a module-level store:

```ts
// features/shell/store.ts
import { create } from "zustand";

type ShellState = { sidebarOpen: boolean; toggleSidebar: () => void };

export const useShellStore = create<ShellState>()((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
}));

// Subscribe narrowly:
const sidebarOpen = useShellStore((s) => s.sidebarOpen);
```

**A store seeded from server data or scoped to a page** must be created per request or mount. A module-level store is shared by every request on the server.

```tsx
// features/editor/store.tsx
"use client";
import { createContext, useContext, useState } from "react";
import { createStore, useStore, type StoreApi } from "zustand";

type EditorState = {
  selectedBlockId: string | null;
  select: (id: string | null) => void;
};

const EditorStoreContext = createContext<StoreApi<EditorState> | null>(null);

export function EditorStoreProvider({ initialBlockId, children }: { initialBlockId: string | null; children: React.ReactNode }) {
  const [store] = useState(() =>
    createStore<EditorState>()((set) => ({
      selectedBlockId: initialBlockId,
      select: (id) => set({ selectedBlockId: id }),
    })),
  );
  return <EditorStoreContext.Provider value={store}>{children}</EditorStoreContext.Provider>;
}

export function useEditorStore<T>(selector: (state: EditorState) => T): T {
  const store = useContext(EditorStoreContext);
  if (!store) throw new Error("useEditorStore must be used inside <EditorStoreProvider>");
  return useStore(store, selector);
}
```

Never put fetched entities (`posts`, `currentUser`) in Zustand. They belong to TanStack Query.
