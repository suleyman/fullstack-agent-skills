import { queryOptions } from "@tanstack/react-query";
import type { components } from "@acme/api-types"; // generated from the API's OpenAPI schema
import { apiFetch } from "@/lib/api/client";

export type EventDetail = components["schemas"]["EventDetail"];

export const eventKeys = {
  all: ["events"] as const,
  detail: (id: string) => [...eventKeys.all, "detail", id] as const,
};

export function eventQuery(id: string) {
  return queryOptions({
    queryKey: eventKeys.detail(id),
    queryFn: ({ signal }) => apiFetch<EventDetail>(`/v1/events/${encodeURIComponent(id)}`, { signal }),
    staleTime: 15_000, // availability moves fast during on-sales: never trust it for long
  });
}
