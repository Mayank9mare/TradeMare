"use client";

import * as React from "react";
import {
  IconChevronLeft,
  IconChevronRight,
  IconSearch,
  IconX,
} from "@tabler/icons-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableExpandTriggerCell,
  TableExpandableContent,
  TableHead,
  TableHeader,
  TableRow,
  TableSelectCell,
  type SortDirection,
  type TableDensity,
} from "@/components/wensity/table";

/** Motion tiers, the same speeds as the --primitive-motion-* tokens. */
const SPRING = {
  fast: { type: "spring", duration: 0.08, bounce: 0 },
  moderate: { type: "spring", duration: 0.16, bounce: 0 },
  slow: { type: "spring", duration: 0.24, bounce: 0 },
} as const;

export type { SortDirection, TableDensity };

/* ─── Types ─────────────────────────────────────────────────── */

export interface DataTableColumn<TData> {
  /** Unique column identifier. */
  id: string;
  /** Header label displayed in the column head. */
  header: string;
  /** Key on the row data object used for default cell rendering and filtering. */
  accessorKey?: keyof TData;
  /** Custom cell renderer. Receives the full row. */
  cell?: (info: { row: TData }) => React.ReactNode;
  /** Whether this column can be sorted. */
  sortable?: boolean;
}

export interface DataTableFilterConfig {
  /** Placeholder text for the search input. */
  placeholder?: string;
  /** Keys on the row data to search against. Defaults to all accessorKey columns. */
  searchKeys?: string[];
  /** Debounce delay in ms. Default 240. */
  debounceMs?: number;
}

export interface DataTablePaginationConfig {
  /** Rows per page. Default 10. */
  pageSize?: number;
}

export interface DataTableExpandableConfig<TData> {
  /** Render function for expanded row content. */
  renderExpanded: (row: TData) => React.ReactNode;
}

export interface DataTableSelectableConfig<TData> {
  /** Controlled selected row IDs. */
  selectedIds?: string[];
  /** Called when selection changes. */
  onSelectionChange?: (ids: string[]) => void;
  /** Label for the select-all checkbox. */
  selectAllLabel?: string;
}

export interface DataTableProps<TData extends Record<string, unknown>> {
  /** Column definitions. */
  columns: DataTableColumn<TData>[];
  /** Row data. */
  data: TData[];
  /** Unique row identifier function. Defaults to \`row.id\` as string. */
  getRowId?: (row: TData) => string;

  /* ── Feature toggles ── */

  /** Enable column sorting. When true, any column with \`sortable: true\` becomes interactive. */
  sortable?: boolean;
  /** Enable search/filter. Pass a config object or \`true\` for defaults. */
  filterable?: boolean | DataTableFilterConfig;
  /** Enable row selection with checkboxes. Pass a config object for controlled mode or \`true\` for uncontrolled. */
  selectable?: boolean | DataTableSelectableConfig<TData>;
  /** Enable pagination. Pass a config object or \`true\` for defaults (10 rows/page). */
  paginated?: boolean | DataTablePaginationConfig;
  /** Enable expandable rows. Pass a config object with \`renderExpanded\`. */
  expandable?: boolean | DataTableExpandableConfig<TData>;

  /* ── Styling ── */

  /** Row density preset. */
  density?: TableDensity;
  /** Stick the header row on scroll. */
  stickyHeader?: boolean;
  /** Optional caption below the table. */
  caption?: string;
  /** Additional class name for the root wrapper. */
  className?: string;
  /** Empty state message when no data matches. */
  emptyState?: React.ReactNode;
}

/* ─── Constants ─────────────────────────────────────────────── */

const DEFAULT_PAGE_SIZE = 10;


/* ─── Helpers ───────────────────────────────────────────────── */

function defaultGetRowId<T extends Record<string, unknown>>(row: T): string {
  if ("id" in row && (typeof row.id === "string" || typeof row.id === "number")) {
    return String(row.id);
  }
  return "";
}

function getCellValue<T extends Record<string, unknown>>(
  row: T,
  column: DataTableColumn<T>,
): string {
  if (column.accessorKey) {
    const raw = row[column.accessorKey];
    if (raw == null) return "";
    return String(raw);
  }
  return "";
}

function getSearchValue<T extends Record<string, unknown>>(
  row: T,
  columns: DataTableColumn<T>[],
  key: string,
): string {
  const column = columns.find(
    (c) => c.id === key || (c.accessorKey !== undefined && String(c.accessorKey) === key),
  );

  if (column) {
    return getCellValue(row, column);
  }

  const raw = row[key as keyof T];
  if (raw == null) return "";
  return String(raw);
}

function matchesSearch<T extends Record<string, unknown>>(
  row: T,
  query: string,
  columns: DataTableColumn<T>[],
  searchKeys?: string[],
): boolean {
  if (!query.trim()) return true;
  const lowerQuery = query.toLowerCase();

  const keysToSearch =
    searchKeys?.length
      ? searchKeys
      : columns
        .filter((col) => col.accessorKey)
        .map((col) => String(col.accessorKey));

  return keysToSearch.some((key) => {
    const value = getSearchValue(row, columns, key);
    return value.toLowerCase().includes(lowerQuery);
  });
}

function getPageRange(page: number, totalPages: number, siblingCount = 1) {
  const totalSlots = siblingCount * 2 + 5;
  if (totalPages <= totalSlots) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const leftSiblingIndex = Math.max(page - siblingCount, 1);
  const rightSiblingIndex = Math.min(page + siblingCount, totalPages);
  const showLeftEllipsis = leftSiblingIndex > 2;
  const showRightEllipsis = rightSiblingIndex < totalPages - 1;

  const items: (number | "ellipsis-start" | "ellipsis-end")[] = [1];

  if (showLeftEllipsis) {
    items.push("ellipsis-start");
  } else {
    for (let i = 2; i < leftSiblingIndex; i++) items.push(i);
  }

  for (let i = leftSiblingIndex; i <= rightSiblingIndex; i++) {
    if (i !== 1 && i !== totalPages) items.push(i);
  }

  if (showRightEllipsis) {
    items.push("ellipsis-end");
  } else {
    for (let i = rightSiblingIndex + 1; i < totalPages; i++) items.push(i);
  }

  if (totalPages > 1) items.push(totalPages);
  return items;
}

/* ─── Sub-components ────────────────────────────────────────── */

function SelectAllCheckbox({
  checked,
  indeterminate,
  onCheckedChange,
  ariaLabel,
}: {
  checked: boolean;
  indeterminate: boolean;
  onCheckedChange?: (checked: boolean) => void;
  ariaLabel: string;
}) {
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <label
      data-slot="table-checkbox"
      className={cn(
        "relative inline-grid size-5 cursor-pointer place-items-center rounded-[var(--primitive-radius-control-sm)] border border-[var(--border)]",
        "bg-[var(--primitive-field-face)] text-[var(--background)]",
        "transition-[background-color,border-color,color,box-shadow] duration-[var(--primitive-motion-fast)] ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none",
        "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--primitive-ring)] has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-[var(--background)]",
        (checked || indeterminate) &&
          "border-[var(--primitive-control-solid)] bg-[var(--primitive-control-solid)] text-[var(--primitive-control-solid-foreground)]",
      )}
    >
      <input
        ref={inputRef}
        type="checkbox"
        checked={checked}
        aria-label={ariaLabel}
        onChange={(event) => onCheckedChange?.(event.currentTarget.checked)}
        className="sr-only"
      />
      <svg aria-hidden="true" viewBox="0 0 16 16" className="size-3.5">
        {indeterminate ? (
          <path d="M4 8h8" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
        ) : (
          <path d="m3.6 8.2 2.8 2.8 6-6" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
        )}
      </svg>
    </label>
  );
}

function SearchBar({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <div data-wensity-primitive=""
      data-surface="field"
      className={cn(
        "relative flex h-[var(--primitive-control-height-md)] w-full max-w-xs items-center gap-2",
        "rounded-[var(--primitive-radius-control)]",
        "px-3 text-sm transition-[border-color,box-shadow] duration-[var(--primitive-motion-fast)]",
        "focus-within:border-[color-mix(in_srgb,var(--foreground)_24%,transparent)]",
        "focus-within:ring-2 focus-within:ring-[var(--primitive-ring)] focus-within:ring-offset-2 focus-within:ring-offset-[var(--background)]",
      )}
    >
      <IconSearch size={15} stroke={1.75} className="shrink-0 text-[var(--muted-foreground)]" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          "w-full bg-transparent text-[var(--foreground)]",
          "placeholder:text-[var(--primitive-text-placeholder,var(--muted-foreground))]",
          "outline-none",
        )}
      />
      <AnimatePresence>
        {value ? (
          <motion.button
            type="button"
            onClick={() => onChange("")}
            initial={shouldReduceMotion ? false : { opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={shouldReduceMotion ? undefined : { opacity: 0, scale: 0.9 }}
            transition={SPRING.fast}
            whileTap={{ scale: 0.92 }}
            className={cn(
              "flex size-5 shrink-0 items-center justify-center rounded-[var(--primitive-radius-control-sm)]",
              "text-[var(--muted-foreground)] hover:bg-[var(--primitive-surface-hover)] hover:text-[var(--foreground)]",
              "transition-colors duration-[var(--primitive-motion-fast)]",
            )}
          >
            <IconX size={12} stroke={2} />
          </motion.button>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function PaginationBar({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;

  const items = getPageRange(page, totalPages);

  const navButtonClass = cn(
    "relative isolate inline-flex size-7 shrink-0 items-center justify-center rounded-[var(--primitive-radius-control-sm)]",
    "text-xs font-medium leading-none select-none touch-manipulation outline-none",
    "transition-[color,background-color] duration-[var(--primitive-motion-fast)] ease-[cubic-bezier(0.23,1,0.32,1)]",
    "text-[var(--muted-foreground)]",
    "hover:bg-[var(--primitive-surface-hover)] hover:text-[var(--foreground)]",
    "focus-visible:ring-2 focus-visible:ring-[var(--primitive-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
    "disabled:pointer-events-none disabled:opacity-35",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  );

  return (
    <nav
      data-wensity-primitive=""
      aria-label="Pagination"
      className="flex items-center gap-0.5"
    >
      <button
        type="button"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        className={navButtonClass}
        aria-label="Previous page"
      >
        <IconChevronLeft size={14} stroke={2} />
      </button>

      {items.map((item) => {
        if (item === "ellipsis-start" || item === "ellipsis-end") {
          return (
            <span
              key={item}
              className="inline-flex size-7 items-center justify-center text-[length:var(--primitive-text-hint)] text-[var(--muted-foreground)]"
              aria-hidden
            >
              …
            </span>
          );
        }

        const isActive = item === page;
        return (
          <button
            key={item}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onPageChange(item)}
            className={cn(
              navButtonClass,
              "rounded-[var(--primitive-radius-control-sm)] tabular-nums",
              isActive &&
                "bg-[var(--primitive-surface-selected)] text-[var(--foreground)]",
            )}
          >
            {item}
          </button>
        );
      })}

      <button
        type="button"
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        className={navButtonClass}
        aria-label="Next page"
      >
        <IconChevronRight size={14} stroke={2} />
      </button>
    </nav>
  );
}

/* ─── DataTable ─────────────────────────────────────────────── */

function DataTableInner<TData extends Record<string, unknown>>({
  columns,
  data,
  getRowId = defaultGetRowId,
  sortable = false,
  filterable = false,
  selectable = false,
  paginated = false,
  expandable = false,
  density = "comfortable",
  stickyHeader = false,
  caption,
  className,
  emptyState,
}: DataTableProps<TData>) {
  /* ── Resolve configs ── */

  const filterConfig: DataTableFilterConfig =
    typeof filterable === "object" ? filterable : {};
  const paginationConfig: DataTablePaginationConfig =
    typeof paginated === "object" ? paginated : {};
  const selectableConfig: DataTableSelectableConfig<TData> | null =
    typeof selectable === "object" ? selectable : null;
  const expandableConfig: DataTableExpandableConfig<TData> | null =
    typeof expandable === "object" ? expandable : null;

  const isFilterable = filterable !== false;
  const isPaginated = paginated !== false;
  const isSelectable = selectable !== false;
  const isExpandable = expandable !== false;
  const isSortable = sortable !== false;

  const pageSize = paginationConfig.pageSize ?? DEFAULT_PAGE_SIZE;

  /* ── State ── */

  const [searchQuery, setSearchQuery] = React.useState("");
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const [sortKey, setSortKey] = React.useState<string | null>(null);
  const [sortDirection, setSortDirection] = React.useState<SortDirection>(null);
  const [currentPage, setCurrentPage] = React.useState(1);
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = React.useState<string | null>(null);

  /* ── Debounce search ── */

  React.useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedQuery(searchQuery),
      filterConfig.debounceMs ?? 240,
    );
    return () => clearTimeout(timer);
  }, [searchQuery, filterConfig.debounceMs]);

  /* ── Controlled selection ── */

  const effectiveSelectedIds = selectableConfig?.selectedIds
    ? new Set(selectableConfig.selectedIds)
    : selectedIds;

  const handleSelectionChange = React.useCallback(
    (ids: Set<string>) => {
      if (selectableConfig?.onSelectionChange) {
        selectableConfig.onSelectionChange([...ids]);
      } else {
        setSelectedIds(ids);
      }
    },
    [selectableConfig],
  );

  /* ── Sorting ── */

  const toggleSort = React.useCallback(
    (key: string) => {
      if (sortKey === key) {
        setSortDirection((prev) =>
          prev === "asc" ? "desc" : prev === "desc" ? null : "asc",
        );
      } else {
        setSortKey(key);
        setSortDirection("asc");
      }
      setCurrentPage(1);
    },
    [sortKey],
  );

  /* ── Process: filter → sort → paginate ── */

  const filtered = React.useMemo(() => {
    if (!debouncedQuery.trim()) return data;
    return data.filter((row) =>
      matchesSearch(row, debouncedQuery, columns, filterConfig.searchKeys),
    );
  }, [data, debouncedQuery, columns, filterConfig.searchKeys]);

  const sorted = React.useMemo(() => {
    if (!sortKey || !sortDirection) return filtered;
    const col = columns.find((c) => c.id === sortKey);
    if (!col) return filtered;

    return [...filtered].sort((a, b) => {
      const aVal = getCellValue(a, col);
      const bVal = getCellValue(b, col);
      const cmp = aVal.localeCompare(bVal, undefined, { numeric: true });
      return sortDirection === "desc" ? -cmp : cmp;
    });
  }, [filtered, sortKey, sortDirection, columns]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);

  const paginatedData = React.useMemo(() => {
    if (!isPaginated) return sorted;
    const start = (safePage - 1) * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, isPaginated, safePage, pageSize]);

  /* ── Reset page on filter change ── */

  // A new query means a different result set, so page 2 of the old results is
  // meaningless. Reset during render rather than from an effect, which showed
  // one frame of the wrong page.
  const [lastQuery, setLastQuery] = React.useState(debouncedQuery);
  if (debouncedQuery !== lastQuery) {
    setLastQuery(debouncedQuery);
    setCurrentPage(1);
  }

  /* ── Selection helpers ── */

  const allRowIds = React.useMemo(
    () => paginatedData.map((row) => getRowId(row)).filter(Boolean),
    [paginatedData, getRowId],
  );

  const allSelected =
    allRowIds.length > 0 && allRowIds.every((id) => effectiveSelectedIds.has(id));
  const isIndeterminate =
    allRowIds.some((id) => effectiveSelectedIds.has(id)) && !allSelected;

  /* ── Total colSpan for empty/expansion cells ── */

  const totalColSpan =
    columns.length +
    (isSelectable ? 1 : 0) +
    (isExpandable && expandableConfig ? 1 : 0);

  const resolveSortDir = (colId: string): SortDirection =>
    sortKey === colId ? sortDirection : null;

  return (
    <div data-wensity-primitive="" className={cn("w-full space-y-3", className)}>
      {isFilterable ? (
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={filterConfig.placeholder}
        />
      ) : null}

      <Table density={density} stickyHeader={stickyHeader}>
        {caption ? <TableCaption>{caption}</TableCaption> : null}

        <TableHeader>
          <TableRow>
            {isSelectable ? (
              <TableHead className="w-10 px-3 text-center">
                <SelectAllCheckbox
                  checked={allSelected}
                  indeterminate={isIndeterminate}
                  onCheckedChange={(checked) => {
                    if (checked) {
                      handleSelectionChange(new Set(allRowIds));
                    } else {
                      handleSelectionChange(new Set());
                    }
                  }}
                  ariaLabel={selectableConfig?.selectAllLabel ?? "Select all rows"}
                />
              </TableHead>
            ) : null}

            {isExpandable && expandableConfig ? (
              <TableHead className="w-10 px-3 text-center" />
            ) : null}

            {columns.map((col) => {
              const canSort = isSortable && col.sortable;
              return (
                <TableHead
                  key={col.id}
                  sortable={canSort}
                  sortDirection={canSort ? resolveSortDir(col.id) : null}
                  onSort={canSort ? () => toggleSort(col.id) : undefined}
                >
                  {col.header}
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>

        <TableBody animatedSort={isSortable}>
          {paginatedData.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={totalColSpan}
                className="px-4 py-10 text-center text-sm text-[var(--muted-foreground)]"
              >
                <span className="text-[length:var(--primitive-text-hint)] text-[var(--muted-foreground)] sm:text-sm">
                  {emptyState ?? "No results found."}
                </span>
              </TableCell>
            </TableRow>
          ) : (
            paginatedData.map((row) => {
              const rowId = getRowId(row);
              const isSelected = effectiveSelectedIds.has(rowId);
              const isExpanded = expandedId === rowId;

              const handleSelectRow = (next: boolean) => {
                const nextSet = new Set(effectiveSelectedIds);
                if (next) {
                  nextSet.add(rowId);
                } else {
                  nextSet.delete(rowId);
                }
                handleSelectionChange(nextSet);
              };

              const handleExpandRow = (next: boolean) => {
                setExpandedId(next ? rowId : null);
              };

              return (
                <React.Fragment key={rowId || JSON.stringify(row)}>
                  <TableRow
                    selected={isSelectable && isSelected}
                    expanded={isExpandable && isExpanded}
                    selectable={isSelectable}
                    onSelectedChange={isSelectable ? handleSelectRow : undefined}
                    expandable={isExpandable && !!expandableConfig && !isSelectable}
                    onExpandedChange={
                      isExpandable && expandableConfig && !isSelectable
                        ? handleExpandRow
                        : undefined
                    }
                  >
                    {isSelectable ? (
                      <TableSelectCell
                        checked={isSelected}
                        onCheckedChange={(v) => handleSelectRow(v === true)}
                        ariaLabel={`Select ${getCellValue(row, columns[0])}`}
                      />
                    ) : null}

                    {isExpandable && expandableConfig ? (
                      <TableExpandTriggerCell
                        expanded={isExpanded}
                        onToggle={() => handleExpandRow(!isExpanded)}
                      />
                    ) : null}

                    {columns.map((col) => (
                      <TableCell key={col.id}>
                        {col.cell ? col.cell({ row }) : getCellValue(row, col)}
                      </TableCell>
                    ))}
                  </TableRow>

                  {isExpandable && expandableConfig ? (
                    <TableExpandableContent open={isExpanded} colSpan={totalColSpan}>
                      {expandableConfig.renderExpanded(row)}
                    </TableExpandableContent>
                  ) : null}
                </React.Fragment>
              );
            })
          )}
        </TableBody>
      </Table>

      {isPaginated && totalPages > 1 ? (
        <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
          <span>
            Showing {(safePage - 1) * pageSize + 1}&ndash;
            {Math.min(safePage * pageSize, sorted.length)} of {sorted.length}
          </span>
          <PaginationBar
            page={safePage}
            totalPages={totalPages}
            onPageChange={setCurrentPage}
          />
        </div>
      ) : null}
    </div>
  );
}

/* ─── Export ────────────────────────────────────────────────── */

export const DataTable = DataTableInner as <TData extends Record<string, unknown>>(
  props: DataTableProps<TData>,
) => React.ReactElement;
