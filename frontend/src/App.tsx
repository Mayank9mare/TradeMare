import { Overview } from "@/pages/Overview";

export default function App() {
  return (
    <div className="min-h-dvh bg-bg pb-[env(safe-area-inset-bottom)]">
      <header className="sticky top-0 z-10 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <a href="/app/" className="flex items-center gap-2 font-semibold text-text no-underline">
            <span className="grid size-7 place-items-center rounded-lg bg-accent text-sm text-white dark:text-bg">T</span>
            TradeMare <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">new UI · beta</span>
          </a>
          <nav className="flex items-center gap-4 text-[13px]">
            <a href="/signals" className="text-accent">Signals</a>
            <a href="/journal" className="hidden text-accent sm:inline">Trade journal</a>
            <a href="/" className="text-muted">Classic dashboard</a>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-7 sm:px-6">
        <h1 className="mb-1 text-[26px] font-bold tracking-tight">Overview</h1>
        <p className="mt-0 mb-6 text-[13px] text-muted">Paper trading in 2 markets plus your Zerodha portfolio. Nothing is ordered without your approval.</p>
        <Overview />
      </main>
    </div>
  );
}
