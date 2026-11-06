// Types and fetchers for the dashboard's JSON API (src/trademare/web/api_routes.py).

export type Tone = "" | "up" | "down" | "warn";

export interface Tile { label: string; value: string; sub: string; href: string; tone: Tone }
export interface Upcoming { day: string; text: string; href: string; kind: "results" | "ipo" | "deadline" | "" }
export interface Point { t: string; v: number }
export interface Idea { symbol: string; sector: string | null; entry: number; stop: number; score: number }

export interface Zerodha {
  value: number; cost: number; pnl: number; ret: number | null; count: number; as_of: string; symbols: string[];
}

export interface MarketSummary {
  code: "IN" | "US";
  name: string;
  currency: "INR" | "USD";
  equity: number;
  previous_equity: number | null;
  cash: number;
  peak: number;
  positions: number;
  max_positions: number;
  kill_switch: boolean;
  kill_reason: string | null;
  last_processed: string | null;
  pending: number;
  mood: "risk_on" | "neutral" | "risk_off" | null;
  index_name: string;
  equity_line: Point[];
  index_line: Point[];
  scan_date: string | null;
  ideas: Idea[];
}

export interface Order { status: string; text: string; symbol: string; market: string }

export interface Overview {
  today: string;
  pending_total: number;
  tiles: Tile[];
  upcoming: Upcoming[];
  zerodha: Zerodha | null;
  markets: MarketSummary[];
  orders: Order[];
}

export async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}
