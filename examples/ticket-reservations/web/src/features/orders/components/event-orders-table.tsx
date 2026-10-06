"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { ErrorPanel } from "@/components/error-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { browserApi } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/errors";
import { eventOrdersQuery, STATUS_FILTERS, useCancelOrder, type OrganizerOrder, type StatusFilter } from "../api";

// Fixed locale and time zone: the server render and the browser render must produce identical
// strings, or hydration fails.
const LOCALE = "en-GB";
const dateTime = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });

function formatMoney({ amountMinor, currency }: { amountMinor: number; currency: string }) {
  const format = new Intl.NumberFormat(LOCALE, { style: "currency", currency });
  return format.format(amountMinor / 10 ** (format.resolvedOptions().maximumFractionDigits ?? 2));
}

const STATUS_LABELS: Record<string, string> = {
  all: "All",
  pending: "Pending",
  paid: "Paid",
  cancelled: "Cancelled",
  expired: "Expired",
};

export function EventOrdersTable({ eventId, status }: { eventId: string; status: StatusFilter }) {
  const orders = useInfiniteQuery(eventOrdersQuery(browserApi, eventId, status)); // hydrated from the server
  const rows = orders.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-4">
      <nav className="flex gap-2" aria-label="Filter by status">
        {STATUS_FILTERS.map((s) => (
          <Button key={s} asChild variant={s === status ? "default" : "outline"} size="sm">
            <Link href={`?status=${s}`} aria-current={s === status ? "page" : undefined}>
              {STATUS_LABELS[s]}
            </Link>
          </Button>
        ))}
      </nav>

      {orders.isPending ? (
        <TableSkeleton />
      ) : orders.isError ? (
        <ErrorPanel error={orders.error} onRetry={() => orders.refetch()} />
      ) : rows.length === 0 ? (
        <p className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          {status === "all" ? "No orders yet." : `No ${STATUS_LABELS[status].toLowerCase()} orders.`}
        </p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Buyer</TableHead>
                <TableHead>Tickets</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created (UTC)</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((order) => (
                <TableRow key={order.id}>
                  <TableCell>{order.buyer.displayName}</TableCell>
                  <TableCell>{order.items.map((i) => `${i.quantity} × ${i.ticketTypeName}`).join(", ")}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(order.total)}</TableCell>
                  <TableCell>
                    {/* Unknown statuses from a newer API render as-is instead of breaking the table. */}
                    <Badge variant={order.status === "paid" ? "default" : "secondary"}>
                      {STATUS_LABELS[order.status] ?? order.status}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <time dateTime={order.createdAt}>{dateTime.format(new Date(order.createdAt))}</time>
                  </TableCell>
                  <TableCell className="text-right">
                    {order.status === "pending" && <CancelOrderDialog eventId={eventId} order={order} />}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {orders.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => orders.fetchNextPage()} disabled={orders.isFetchingNextPage}>
                {orders.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function CancelOrderDialog({ eventId, order }: { eventId: string; order: OrganizerOrder }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const cancelOrder = useCancelOrder(browserApi, eventId);
  const trimmed = reason.trim();

  function submit() {
    cancelOrder.mutate(
      { orderId: order.id, reason: trimmed },
      {
        onSuccess: () => {
          setOpen(false);
          setReason("");
          toast.success("Order cancelled. The tickets are available again.");
        },
        onError: (error) => {
          // Branch on the stable error code. Show the request ID so support can find the logs.
          if (error instanceof ApiError && error.code === "order_not_cancellable") {
            toast.error("This order was paid in the meantime. Refund it instead.");
            return;
          }
          const requestId = error instanceof ApiError ? error.problem?.requestId : undefined;
          toast.error("Couldn't cancel the order", { description: requestId ? `Request ID: ${requestId}` : undefined });
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Cancel
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel this order?</DialogTitle>
          <DialogDescription>
            The held tickets are released for sale immediately. The reason is stored in the audit log.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (required)"
          maxLength={500}
          aria-label="Reason for cancelling"
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Keep order
          </Button>
          <Button variant="destructive" onClick={submit} disabled={trimmed.length === 0 || cancelOrder.isPending}>
            {cancelOrder.isPending ? "Cancelling…" : "Cancel order"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading orders">
      {Array.from({ length: 6 }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}
