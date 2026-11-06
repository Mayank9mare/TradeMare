// The classic dashboard's formatters (web/formatting.py), ported so both UIs print numbers the same way.

export type Mkt = "IN" | "US" | string;
const DASH = "—";
const missing = (v: unknown): v is null | undefined => v === null || v === undefined || (typeof v === "number" && Number.isNaN(v));

/** Indian digit grouping: 12345678.9 -> "1,23,45,678.90". */
export function indian(value: number, decimals = 2): string {
  const [whole, frac] = Math.abs(value).toFixed(decimals).split(".");
  let head = whole.slice(0, -3);
  const tail = whole.slice(-3);
  const groups: string[] = [];
  while (head.length > 2) { groups.unshift(head.slice(-2)); head = head.slice(0, -2); }
  const grouped = whole.length > 3 ? [head, ...groups, tail].filter(Boolean).join(",") : whole;
  return (value < 0 ? "-" : "") + grouped + (frac ? `.${frac}` : "");
}

/** money(x, market, compact): ₹1,23,456 / ₹12.3 L / ₹1.2 Cr; $1,234.56 / $1.2M. */
export function money(value: number | null | undefined, market: Mkt, compact = true): string {
  if (missing(value)) return DASH;
  const sign = value < 0 ? "-" : "";
  const v = Math.abs(value);
  if (market === "IN") {
    if (compact && v >= 1e7) return `${sign}₹${indian(v / 1e7, v >= 1e9 ? 0 : 1)} Cr`;
    if (compact && v >= 1e5) return `${sign}₹${(v / 1e5).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} L`;
    return `${sign}₹${indian(v, v >= 1e4 ? 0 : 2)}`;
  }
  for (const [size, suffix] of [[1e12, "T"], [1e9, "B"], [1e6, "M"]] as const) {
    if (compact && v >= size) return `${sign}$${(v / size).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}${suffix}`;
  }
  return `${sign}$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function pct(value: number | null | undefined, digits = 1, signed = false): string {
  if (missing(value)) return DASH;
  const shown = Number((value * 100).toFixed(digits)) + 0;
  const text = shown.toFixed(digits);
  return signed && shown >= 0 ? `+${text}%` : `${text}%`;
}

export function num(value: number | null | undefined, digits = 1): string {
  return missing(value) ? DASH : value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function signed(value: number | null | undefined, digits = 2): string {
  return missing(value) ? DASH : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** Text colour for gains and losses; values that show as zero stay neutral. */
export function tone(value: number | null | undefined): string {
  if (missing(value) || Math.abs(value) < 5e-4) return "";
  return value > 0 ? "text-up" : "text-down";
}

const TZ: Record<string, [string, string]> = { IN: ["Asia/Kolkata", "IST"], US: ["America/New_York", "New York"] };

/** A UTC timestamp in the market's own time: "Mon 19 Oct, 15:30 IST". */
export function localTime(iso: string | null | undefined, market: Mkt): string {
  if (!iso) return DASH;
  const [tz, label] = TZ[market] ?? TZ.IN;
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}, ${get("hour")}:${get("minute")} ${label}`;
}

/** An evidence metric by kind (strategy/evidence.py): price, money, pct, times, int, number. */
export function metric(value: number | null | undefined, kind: string, market: Mkt): string {
  if (missing(value)) return DASH;
  switch (kind) {
    case "price": return money(value, market, false);
    case "money": return money(value, market);
    case "pct": return pct(value, 1, true);
    case "times": return `${value.toFixed(1)}x`;
    case "int": return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
    default: return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
}

export const daysBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
export const isoDay = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : DASH);

/** "text-up" if a result beats its benchmark, "text-down" if worse (classic `vs` filter). */
export function vs(value: number | null | undefined, other: number | null | undefined, higherBetter = true): string {
  if (value == null || other == null || Number.isNaN(value) || Number.isNaN(other) || Math.abs(value - other) < 5e-4) return "";
  return (value > other) === higherBetter ? "text-up" : "text-down";
}
