// Money and percent formatting matching the classic dashboard: Indian digit grouping for rupees.

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(value: number | null | undefined, currency: "INR" | "USD", exact = false): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (currency === "USD") return usd.format(value);
  return (exact || Math.abs(value) < 10_000 ? inr2 : inr).format(value);
}

export function pct(value: number | null | undefined, digits = 1, signed = true): string {
  if (value == null || Number.isNaN(value)) return "—";
  const n = (value * 100).toFixed(digits);
  return `${signed && value > 0 ? "+" : ""}${n}%`.replace("-", "−");
}

export function tone(value: number | null | undefined): "text-up" | "text-down" | "text-muted" {
  if (!value) return "text-muted";
  return value > 0 ? "text-up" : "text-down";
}

export function shortDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}
