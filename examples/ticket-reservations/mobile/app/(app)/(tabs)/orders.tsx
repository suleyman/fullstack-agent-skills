import { FlashList } from "@shopify/flash-list";
import { useInfiniteQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, Text } from "react-native";
import { EmptyState, ErrorState, InlineRetry, ListSkeleton, StatusBadge } from "@/components/ui";
import { myOrdersQuery, type Order } from "@/features/orders/api";
import { formatMoney } from "@/lib/money";

export default function OrdersScreen() {
  const orders = useInfiniteQuery(myOrdersQuery());
  const [refreshing, setRefreshing] = useState(false); // pull-to-refresh only, not background refetches

  const items = orders.data?.pages.flatMap((page) => page.items) ?? [];

  if (orders.isPending) return <ListSkeleton />;
  if (orders.isError && items.length === 0) {
    return <ErrorState title="Couldn't load your orders" error={orders.error} onRetry={() => orders.refetch()} />;
  }

  async function onRefresh() {
    setRefreshing(true);
    try {
      await orders.refetch();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <FlashList
      data={items}
      keyExtractor={(order) => order.id}
      renderItem={({ item }) => <OrderRow order={item} />}
      onEndReached={() => {
        if (orders.hasNextPage && !orders.isFetchingNextPage) orders.fetchNextPage();
      }}
      onEndReachedThreshold={0.5}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListEmptyComponent={<EmptyState title="No orders yet" body="Tickets you reserve show up here." />}
      ListFooterComponent={
        orders.isFetchingNextPage ? (
          <ActivityIndicator style={{ padding: 16 }} />
        ) : orders.isFetchNextPageError ? (
          <InlineRetry label="Couldn't load more" onRetry={() => orders.fetchNextPage()} />
        ) : null
      }
    />
  );
}

function OrderRow({ order }: { order: Order }) {
  const ticketCount = order.items.reduce((count, item) => count + item.quantity, 0);
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => router.push({ pathname: "/orders/[id]", params: { id: order.id } })} // IDs, not objects
      style={{ padding: 16, gap: 4 }}
    >
      <Text style={{ fontWeight: "600" }}>{order.event.title}</Text>
      <Text>
        {ticketCount} {ticketCount === 1 ? "ticket" : "tickets"} · {formatMoney(order.total)}
      </Text>
      <StatusBadge label={statusLabel(order.status)} />
    </Pressable>
  );
}

/** Installed app versions outlive API changes: a status added later must render, not crash. */
function statusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "Reserved · complete payment";
    case "paid":
      return "Confirmed";
    case "expired":
      return "Reservation expired";
    case "cancelled":
      return "Cancelled";
    default:
      return "Processing";
  }
}
