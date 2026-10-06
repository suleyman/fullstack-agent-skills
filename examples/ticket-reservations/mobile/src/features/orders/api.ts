import { infiniteQueryOptions, queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@acme/api-types"; // generated from the API's OpenAPI schema
import { eventKeys } from "@/features/events/api";
import { ApiError, apiFetch } from "@/lib/api/client";

export type Order = components["schemas"]["OrderRead"];
type OrderPage = components["schemas"]["OrderPage"];
type ReservationCreate = components["schemas"]["ReservationCreate"];

export const orderKeys = {
  all: ["orders"] as const,
  mine: () => [...orderKeys.all, "mine"] as const,
  detail: (id: string) => [...orderKeys.all, "detail", id] as const,
};

export function myOrdersQuery() {
  return infiniteQueryOptions({
    queryKey: orderKeys.mine(),
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: "20" });
      if (pageParam) params.set("cursor", pageParam); // opaque: never constructed on the client
      return apiFetch<OrderPage>(`/v1/me/orders?${params}`, { signal });
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}

export function orderQuery(id: string) {
  return queryOptions({
    queryKey: orderKeys.detail(id),
    queryFn: ({ signal }) => apiFetch<Order>(`/v1/orders/${encodeURIComponent(id)}`, { signal }),
  });
}

export function useReserveTickets() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: ReservationCreate; idempotencyKey: string }) =>
      apiFetch<Order>("/v1/orders", {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    // The app-wide default is "never retry writes". This write is the exception: the server
    // deduplicates on the Idempotency-Key, so retrying after a lost response can't hold tickets twice.
    retry: (failureCount, error) => failureCount < 2 && !(error instanceof ApiError && error.status < 500),
    onSuccess: (order) => {
      queryClient.setQueryData(orderKeys.detail(order.id), order); // the order screen renders instantly
      void queryClient.invalidateQueries({ queryKey: orderKeys.mine() });
      void queryClient.invalidateQueries({ queryKey: eventKeys.detail(order.event.id) }); // availability changed
    },
  });
}
