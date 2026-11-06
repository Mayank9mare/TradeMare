// A labelled figure in a small tile (the classic `.ratio`).
export function Ratio({ label, children, valueClass = "" }: { label: React.ReactNode; children: React.ReactNode; valueClass?: string }) {
  return (
    <div className="min-w-0 rounded-tile bg-surface px-4 py-3 shadow-card">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`font-num text-lg font-semibold tabular-nums ${valueClass}`}>{children}</div>
    </div>
  );
}

export function Ratios({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,170px),1fr))] gap-3">{children}</div>;
}
