import { Route, Routes } from "react-router";

import { GlossaryProvider } from "@/components/bits";
import { Backtest, Backtests } from "@/pages/Backtests";
import { Ipos } from "@/pages/Ipos";
import { Settings } from "@/pages/Settings";
import { Setups } from "@/pages/Setups";
import { ClassicOnly, Layout } from "@/components/Layout";
import { ToastList, ToastProvider } from "@/components/wensity/toast";
import { ChromeProvider } from "@/lib/chrome";
import { Ideas } from "@/pages/Ideas";
import { Journal } from "@/pages/Journal";
import { Market } from "@/pages/Market";
import { Overview } from "@/pages/Overview";
import { Portfolio } from "@/pages/Portfolio";
import { Tracking } from "@/pages/Tracking";
import { Signals } from "@/pages/Signals";
import { Stock } from "@/pages/Stock";

// Each route mirrors a classic page's address (/app/signals <-> /signals); pages not moved yet
// say so and link to the classic one.
export default function App() {
  return (
    <ChromeProvider>
      <GlossaryProvider>
      <ToastProvider>
        <Layout>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/journal" element={<Journal />} />
            <Route path="/portfolio/:code" element={<Portfolio />} />
            <Route path="/portfolio/:code/tracking" element={<Tracking />} />
            <Route path="/m/:code" element={<Market />} />
            <Route path="/m/:code/ideas" element={<Ideas />} />
            <Route path="/m/:code/:symbol" element={<Stock />} />
            <Route path="/setups/:code" element={<Setups />} />
            <Route path="/ipos" element={<Ipos />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/backtests/:id" element={<Backtest />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<ClassicOnly />} />
          </Routes>
        </Layout>
        <ToastList placement="bottom-right" />
      </ToastProvider>
      </GlossaryProvider>
    </ChromeProvider>
  );
}

function OverviewPage() {
  return (
    <>
      <h1 className="mb-1 text-[26px] font-bold tracking-tight">Overview</h1>
      <p className="mt-0 mb-2 text-[13px] text-muted-foreground">Paper trading in 2 markets plus your Zerodha portfolio. Nothing is ordered without your approval.</p>
      <details className="mb-6 text-[13px]">
        <summary className="cursor-pointer text-accent">How this works</summary>
        <ol className="mt-2 space-y-1 pl-5 text-muted-foreground">
          <li><b className="text-text">Every evening</b>, after each market closes, the system downloads prices and company data and ranks the stocks.</li>
          <li>It proposes trades as <b className="text-text">signals</b>: what to buy or sell, how many shares, and a <b className="text-text">stop-loss</b>, the price at which it would sell to cap the loss.</li>
          <li><b className="text-text">You decide.</b> Open Signals, read the explanation, then approve or reject. Nothing is bought without you.</li>
          <li>Approved trades fill at the next market open and appear in your Trade journal with their profit or loss.</li>
          <li>Until a strategy is proven on history it was never tuned on, everything is <b className="text-text">paper trading</b>: real prices, pretend money.</li>
        </ol>
      </details>
      <Overview />
    </>
  );
}
