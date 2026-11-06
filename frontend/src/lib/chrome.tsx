import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { getJSON } from "@/lib/api";

// The app shell's data (/api/chrome): status bar, badges, enabled markets and the form token
// that every action posts with. Refreshed every minute and after each action.

export interface MarketStatus {
  market: "IN" | "US"; exchange: string; is_open: boolean; next_run: string | null; tz_label: string;
  prices_through: string | null; behind: number;
}
export interface RunState { running: boolean; block: string | null; next: string | null; outcome: string }
export interface Chrome {
  runs: Record<string, RunState>;
  login_needed: boolean;
  statuses: MarketStatus[];
  pending_total: number;
  version: string;
  csrf_token: string;
  markets: { code: "IN" | "US"; name: string; index: string; validated: boolean }[];
}

const Ctx = createContext<{ chrome: Chrome | null; refresh: () => void }>({ chrome: null, refresh: () => {} });

export function ChromeProvider({ children }: { children: React.ReactNode }) {
  const [chrome, setChrome] = useState<Chrome | null>(null);
  const refresh = useCallback(() => {
    getJSON<Chrome>("/api/chrome").then(setChrome, () => {});
  }, []);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 60_000);
    return () => clearInterval(id);
  }, [refresh]);
  return <Ctx.Provider value={{ chrome, refresh }}>{children}</Ctx.Provider>;
}

export const useChrome = () => useContext(Ctx);
