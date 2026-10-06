/**
 * Formats integer minor units in the currency's own precision: JPY has 0 decimals, EUR 2, KWD 3.
 * Dividing by 100 everywhere is a bug waiting for the first non-2-decimal currency.
 */
export function formatMoney({ amountMinor, currency }: { amountMinor: number; currency: string }, locale?: string) {
  const format = new Intl.NumberFormat(locale, { style: "currency", currency });
  const digits = format.resolvedOptions().maximumFractionDigits ?? 2;
  return format.format(amountMinor / 10 ** digits);
}
