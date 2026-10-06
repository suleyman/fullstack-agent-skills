import { infiniteQueryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@acme/api-types"; // generated from the API's OpenAPI schema
import { unwrap } from "@/lib/api/errors";
import type { ApiClient } from "@/lib/api/types";

export type OrganizerOrder = components["schemas"]["OrganizerOrderRead"];

export const STATUS_FILTERS = ["all", "pending", "paid", "cancelled", "expired"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

/** The URL owns the filter. Unknown values fall back to the default instead of erroring. */
export function parseStatusFilter(value: string | string[] | undefined): StatusFilter {
  return STATUS_FILTERS.find((s) => s === value) ?? "all";
}

export const eventOrderKeys = {
  all: (eventId: string) => ["organizer", "events", eventId, "orders"] as const,
  list: (eventId: string, status: StatusFilter) => [...eventOrderKeys.all(eventId), status] as const,
};

/** Shared by the server prefetch (server client) and the browser hook (BFF proxy client). */
export function eventOrdersQuery(api: ApiClient, eventId: string, status: StatusFilter) {
  return infiniteQueryOptions({
    queryKey: eventOrderKeys.list(eventId, status),
    queryFn: ({ pageParam, signal }) =>
      api
        .GET("/v1/organizer/events/{eventId}/orders", {
          params: {
            path: { eventId },
            query: { status: status === "all" ? undefined : status, limit: 50, cursor: pageParam ?? undefined },
          },
          signal,
        })
        .then(unwrap),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}

export function useCancelOrder(api: ApiClient, eventId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, reason }: { orderId: string; reason: string }) =>
      api
        .POST("/v1/organizer/orders/{orderId}/cancel", { params: { path: { orderId } }, body: { reason } })
        .then(unwrap),
    // Every status tab of this event may change. Returning the promise keeps the mutation pending
    // until the refetch lands, so the row doesn't flicker back.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: eventOrderKeys.all(eventId) }),
  });
}
