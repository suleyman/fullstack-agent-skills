import { useQuery } from "@tanstack/react-query";
import * as Crypto from "expo-crypto";
import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { Button, ErrorState, InlineNotice, NotFoundState, QuantityStepper, ScreenSkeleton } from "@/components/ui";
import { eventQuery } from "@/features/events/api";
import { useReserveTickets } from "@/features/orders/api";
import { ApiError } from "@/lib/api/client";
import { formatMoney } from "@/lib/money";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_PER_TYPE = 10; // mirrors the API limit; the API enforces it regardless

// Route file: thin. Route params are untrusted strings (or arrays), so validate before use.
export default function CheckoutRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  if (typeof id !== "string" || !UUID.test(id)) return <NotFoundState title="Event not found" />;
  return <Checkout eventId={id} />;
}

function Checkout({ eventId }: { eventId: string }) {
  const event = useQuery(eventQuery(eventId)); // loads its own data: works from a deep link too
  const reserve = useReserveTickets();
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  // One key per checkout attempt: reused when retrying the same selection, replaced when the
  // selection changes, so a different request never replays the previous order.
  const [idempotencyKey, setIdempotencyKey] = useState(() => Crypto.randomUUID());

  if (event.isPending) return <ScreenSkeleton />;
  if (event.isError) {
    if (event.error instanceof ApiError && event.error.status === 404) return <NotFoundState title="Event not found" />;
    return <ErrorState title="Couldn't load this event" error={event.error} onRetry={() => event.refetch()} />;
  }

  const selected = event.data.ticketTypes.filter((t) => (quantities[t.id] ?? 0) > 0);
  // Display only. The server computes the authoritative total from its own prices.
  const estimatedTotal = selected.reduce((sum, t) => sum + t.price.amountMinor * quantities[t.id], 0);

  function changeQuantity(ticketTypeId: string, quantity: number) {
    setQuantities((current) => ({ ...current, [ticketTypeId]: quantity }));
    setIdempotencyKey(Crypto.randomUUID());
    reserve.reset();
  }

  function submit() {
    reserve.mutate(
      {
        body: { eventId, items: selected.map((t) => ({ ticketTypeId: t.id, quantity: quantities[t.id] })) },
        idempotencyKey,
      },
      {
        onSuccess: (order) => router.replace({ pathname: "/orders/[id]", params: { id: order.id } }),
        onError: (error) => {
          if (error instanceof ApiError && error.code === "sold_out") void event.refetch(); // show what's left
        },
      },
    );
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 16, gap: 16 }}>
      <Text accessibilityRole="header" style={{ fontSize: 22, fontWeight: "600" }}>
        {event.data.title}
      </Text>

      {event.data.ticketTypes.map((ticketType) => (
        <View key={ticketType.id} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <View style={{ gap: 2 }}>
            <Text style={{ fontWeight: "500" }}>{ticketType.name}</Text>
            <Text>{ticketType.available > 0 ? formatMoney(ticketType.price) : "Sold out"}</Text>
          </View>
          <QuantityStepper
            value={quantities[ticketType.id] ?? 0}
            max={Math.min(MAX_PER_TYPE, ticketType.available)}
            onChange={(quantity) => changeQuantity(ticketType.id, quantity)}
            accessibilityLabel={`${ticketType.name} quantity`}
          />
        </View>
      ))}

      {reserve.isError && <InlineNotice tone="error" message={reservationErrorMessage(reserve.error)} />}

      <Button
        onPress={submit}
        disabled={selected.length === 0 || reserve.isPending}
        label={
          reserve.isPending
            ? "Reserving…"
            : selected.length === 0
              ? "Choose tickets"
              : `Reserve · ${formatMoney({ amountMinor: estimatedTotal, currency: event.data.currency })}`
        }
      />
    </ScrollView>
  );
}

/** Branch on the API's stable error code, never on message text. */
function reservationErrorMessage(error: Error): string {
  if (!(error instanceof ApiError)) {
    return "Check your connection and try again. Retrying won't reserve tickets twice.";
  }
  switch (error.code) {
    case "sold_out":
      return "Some of those tickets just sold out. Availability has been updated.";
    case "sales_closed":
      return "Ticket sales for this event have closed.";
    default:
      return `Something went wrong. Reference: ${error.problem?.requestId ?? "unavailable"}`;
  }
}
