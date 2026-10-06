import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { eventOrdersQuery, parseStatusFilter } from "@/features/orders/api";
import { EventOrdersTable } from "@/features/orders/components/event-orders-table";
import { ApiError } from "@/lib/api/errors";
import { getServerApi } from "@/lib/api/server";
import { getQueryClient } from "@/lib/query-client";

export const metadata: Metadata = {
  title: "Orders",
  robots: { index: false, follow: false },
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Server Component: no "use client". It resolves params, auth, and the first page, then hands off
// to a client island. Authorization happens in the API; this page only maps its 404.
export default async function EventOrdersPage({
  params,
  searchParams,
}: {
  params: Promise<{ eventId: string }>;
  searchParams: Promise<{ status?: string | string[] }>;
}) {
  const [{ eventId }, { status }] = await Promise.all([params, searchParams]);
  if (!UUID.test(eventId)) notFound();
  const filter = parseStatusFilter(status);

  const api = await getServerApi(); // redirects to sign-in without a session
  const queryClient = getQueryClient();
  try {
    // fetchInfiniteQuery throws (prefetch would swallow the error), so another organizer's
    // event becomes a real 404 page instead of an empty table.
    await queryClient.fetchInfiniteQuery(eventOrdersQuery(api, eventId, filter));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error; // error.tsx renders the retry UI
  }

  return (
    <main className="mx-auto max-w-6xl space-y-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Orders</h1>
      <HydrationBoundary state={dehydrate(queryClient)}>
        <EventOrdersTable eventId={eventId} status={filter} />
      </HydrationBoundary>
    </main>
  );
}
