import { useState } from "react";

// A table whose columns sort on click (like the classic `table.sortable`). Each column says how
// to render a cell and what value to sort by.
export interface Col<T> { key: string; head: React.ReactNode; cell: (r: T) => React.ReactNode; sort?: (r: T) => number | string | null; num?: boolean }

export function SortTable<T>({ rows, cols, minWidth = 720, rowKey }: { rows: T[]; cols: Col<T>[]; minWidth?: number; rowKey: (r: T) => string }) {
  const [by, setBy] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const col = cols.find((c) => c.key === by?.key);
  const sorted = col?.sort
    ? [...rows].sort((a, b) => {
        const x = col.sort!(a), y = col.sort!(b);
        if (x === y) return 0;
        if (x === null) return 1;
        if (y === null) return -1;
        return (x < y ? -1 : 1) * by!.dir;
      })
    : rows;
  return (
    <div className="overflow-x-auto rounded-card border border-line bg-surface">
      <table className="w-full text-[13px]" style={{ minWidth }}>
        <thead className="text-left text-xs text-muted-foreground"><tr>
          {cols.map((c) => (
            <th key={c.key} className={`px-3.5 py-2.5 font-medium ${c.num ? "text-right" : ""}`} aria-sort={by?.key === c.key ? (by.dir === 1 ? "ascending" : "descending") : undefined}>
              {c.sort ? (
                <button type="button" className="inline-flex items-center gap-1 hover:text-text" onClick={() => setBy((b) => ({ key: c.key, dir: b?.key === c.key && b.dir === 1 ? -1 : 1 }))}>
                  {c.head}<span aria-hidden className="text-faint">{by?.key === c.key ? (by.dir === 1 ? "▲" : "▼") : "↕"}</span>
                </button>
              ) : c.head}
            </th>
          ))}
        </tr></thead>
        <tbody>{sorted.map((r) => (
          <tr key={rowKey(r)} className="border-t border-line hover:bg-row-hover">
            {cols.map((c) => <td key={c.key} className={`px-3.5 py-2.5 align-top ${c.num ? "text-right tabular-nums" : ""}`}>{c.cell(r)}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
