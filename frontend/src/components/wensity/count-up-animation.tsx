"use client";

import * as React from "react";
import {
  animate,
  cancelFrame,
  frame,
  motionValue,
  useInView,
  useReducedMotion,
  type AnimationPlaybackControlsWithThen,
  type MotionValue,
} from "framer-motion";
import { cn } from "@/lib/utils";

export type NumberTickerVariant = "count" | "roll";

export interface NumberTickerProps
  extends Omit<React.ComponentPropsWithoutRef<"span">, "children" | "prefix"> {
  /** The number to show. Changing it animates from whatever is on screen. */
  value: number;
  /** Where the first run starts. Default: `0`. */
  from?: number;
  /**
   * `roll` turns each digit straight to its new value.
   * `count` spins the digits like a counter, low digits turning most.
   * Default: `"roll"`.
   */
  variant?: NumberTickerVariant;
  /** Run length in seconds. Default: `1` for roll, `2.2` for count. */
  duration?: number;
  /** Seconds to wait before the first run. Default: `0`. */
  delay?: number;
  /** Start when scrolled into view, or as soon as it mounts. Default: `"view"`. */
  trigger?: "view" | "mount";
  /** Locale(s) passed to `Intl.NumberFormat`. Default: the browser locale. */
  locales?: string | string[];
  /** `Intl.NumberFormat` options: currency, percent, compact, decimals. */
  format?: Intl.NumberFormatOptions;
  /** Text before the number, such as `"+"`. */
  prefix?: string;
  /** Text after the number, such as `"+"` or `" users"`. */
  suffix?: string;
  /** Called when a run settles. */
  onComplete?: () => void;
}

/* ────────────────────────────────────────────────────────────────────────
 * Layout: what Intl says the target looks like, split into the parts that
 * turn (digits) and the parts that stay put (currency, %, K, separators).
 * ──────────────────────────────────────────────────────────────────────── */

interface Layout {
  lead: string;
  trail: string;
  /** Fraction digits shown. */
  fd: number;
  decimal: string;
  group: string;
  /** Raw value × scale = the number Intl prints (100 for %, 1/1000 for K). */
  scale: number;
  /** Localised digit glyphs 0-9. */
  digits: string[];
  /** Places k with a group separator right of column k, per integer length. */
  separators: (length: number) => ReadonlySet<number>;
  text: string;
}

const MAX_PLACES = 21;
const STRIP = 11; // 0-9 plus a trailing 0 so 9 turns into 0
/**
 * Extra full turns a low digit makes in count mode, so it reads as counting.
 * Kept to one: more makes digits strobe instead of roll.
 */
const MAX_TURNS = 1;
/**
 * Timed curves, not springs: a spring's tail keeps a digit creeping the last
 * few pixels long after it looks done. Both land at zero speed, exactly on
 * the digit, at the stated duration.
 */
const ROLL_EASE = [0.3, 0.5, 0.2, 1] as const;
const COUNT_EASE = [0.33, 0.2, 0.3, 1] as const;
const PRESENCE = { type: "spring", bounce: 0, visualDuration: 0.42 } as const;

/*
 * Each digit turns inside its own line, faded at the top and bottom, so a
 * digit slides out through a soft edge and the neighbours never show. The
 * line is taller than the text (taken back with negative margins) to leave
 * room for that fade, and the fade is fitted to the font's real ink so it
 * runs as long as possible without dimming a digit at rest. Horizontal
 * padding, also taken back, keeps wide glyphs and tight tracking unclipped.
 */
const LINE = 1.5;
const LINE_TRIM = `${-(LINE - 1.15) / 2}em`;
const PAD_X = "0.3em";
const FALLBACK_MASK =
  "linear-gradient(to bottom, transparent 0, #000 0.38em, #000 calc(100% - 0.3em), transparent 100%)";

let measureContext: CanvasRenderingContext2D | null | undefined;

/** A mask whose fades end exactly where the digits' ink begins. */
function inkMask(row: HTMLElement): string | null {
  if (measureContext === undefined) {
    measureContext = document.createElement("canvas").getContext("2d");
  }
  const ctx = measureContext;
  if (!ctx) return null;
  const cs = getComputedStyle(row);
  const size = parseFloat(cs.fontSize);
  const line = parseFloat(cs.lineHeight);
  if (!size || !Number.isFinite(line)) return null;
  ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const m = ctx.measureText("0123456789");
  if (!m.fontBoundingBoxAscent) return null;
  const baseline = (line - (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent)) / 2 + m.fontBoundingBoxAscent;
  const margin = size * 0.025;
  const top = Math.max(1, baseline - m.actualBoundingBoxAscent - margin);
  const bottom = Math.min(line - 1, baseline + m.actualBoundingBoxDescent + margin);
  if (bottom <= top) return null;
  // Smoothstep stops, so the fade has no visible start or end line.
  const steps = [0, 0.2, 0.4, 0.6, 0.8, 1];
  const ease = (f: number) => f * f * (3 - 2 * f);
  const stops = [
    ...steps.map((f) => `rgba(0,0,0,${ease(f).toFixed(3)}) ${(top * f).toFixed(2)}px`),
    ...steps.map((f) => `rgba(0,0,0,${ease(1 - f).toFixed(3)}) ${(bottom + (line - bottom) * f).toFixed(2)}px`),
  ];
  return `linear-gradient(to bottom, ${stops.join(", ")})`;
}

function makeFormatter(
  locales: string | string[] | undefined,
  options: Intl.NumberFormatOptions,
) {
  try {
    return new Intl.NumberFormat(locales, options);
  } catch {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  }
}

function readParts(formatter: Intl.NumberFormat, n: number) {
  let int = "";
  let frac = "";
  let lead = "";
  let trail = "";
  let decimal = ".";
  let seenNumber = false;
  for (const part of formatter.formatToParts(n)) {
    switch (part.type) {
      case "integer":
        int += part.value;
        seenNumber = true;
        break;
      case "fraction":
        frac += part.value;
        break;
      case "decimal":
        decimal = part.value;
        break;
      case "group":
      case "minusSign":
      case "plusSign":
        break;
      default:
        if (seenNumber) trail += part.value;
        else lead += part.value;
    }
  }
  return { int, frac, lead, trail, decimal };
}

function buildLayout(
  value: number,
  locales: string | string[] | undefined,
  format: Intl.NumberFormatOptions | undefined,
  prefix: string,
  suffix: string,
): Layout {
  const options = format ?? {};
  const full = makeFormatter(locales, options);
  const latn = makeFormatter(locales, { ...options, numberingSystem: "latn" });
  const abs = Number.isFinite(value) ? Math.abs(value) : 0;

  const target = readParts(latn, abs);
  const shown = Number(`${target.int || "0"}.${target.frac || "0"}`);
  let scale = 1;
  if (abs > 0 && shown > 0) {
    scale = shown / abs;
  } else {
    const one = readParts(latn, 1);
    scale = Number(`${one.int || "1"}.${one.frac || "0"}`) || 1;
  }

  const resolved = full.resolvedOptions() as Intl.ResolvedNumberFormatOptions & {
    useGrouping?: unknown;
  };
  const plain = makeFormatter(locales, {
    numberingSystem: "latn",
    maximumFractionDigits: 0,
    useGrouping: resolved.useGrouping as Intl.NumberFormatOptions["useGrouping"],
  });
  const groupPart = makeFormatter(locales, { numberingSystem: "latn" })
    .formatToParts(1234567)
    .find((part) => part.type === "group");

  const digitFormatter = makeFormatter(locales, {
    numberingSystem: resolved.numberingSystem,
    useGrouping: false,
    maximumFractionDigits: 0,
  });
  const digits = Array.from({ length: 10 }, (_, d) => digitFormatter.format(d));

  const cache = new Map<number, ReadonlySet<number>>();
  const separators = (length: number) => {
    const n = Math.min(Math.max(1, length), MAX_PLACES);
    const hit = cache.get(n);
    if (hit) return hit;
    const chunks = plain
      .formatToParts(10 ** (n - 1))
      .filter((part) => part.type === "integer")
      .map((part) => part.value.length);
    const set = new Set<number>();
    let acc = 0;
    for (let i = chunks.length - 1; i > 0; i -= 1) {
      acc += chunks[i] ?? 0;
      set.add(acc);
    }
    cache.set(n, set);
    return set;
  };

  return {
    lead: prefix + target.lead,
    trail: target.trail + suffix,
    fd: target.frac.length,
    decimal: target.decimal,
    group: groupPart?.value ?? ",",
    scale,
    digits,
    separators,
    text: `${prefix}${full.format(Number.isFinite(value) ? value : 0)}${suffix}`,
  };
}

/** Every shown digit as one integer, without the decimal point. */
function scaledInt(raw: number, layout: Layout) {
  return Math.round(Math.abs(raw) * layout.scale * 10 ** layout.fd);
}

/** How many integer digits a scaled integer has. */
function lengthOf(n: number, fd: number) {
  return Math.min(MAX_PLACES, String(Math.floor(n / 10 ** fd)).length);
}

function digitAt(n: number, idx: number) {
  return Math.floor(n / 10 ** idx) % 10;
}

/** Width of the digit at a (possibly fractional) turn position. */
function glyphWidth(metrics: Metrics, turns: number) {
  const pos = wrap(turns);
  const low = Math.floor(pos);
  const w0 = metrics.digits[low % 10] ?? 0;
  const w1 = metrics.digits[(low + 1) % 10] ?? 0;
  return w0 + (w1 - w0) * (pos - low);
}

function smooth(x: number) {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
}

function wrap(turns: number) {
  return ((turns % 10) + 10) % 10;
}

/** Column `idx` (0 = lowest shown place) is part of the number. */
function placeShows(n: number, idx: number, fd: number) {
  return idx <= fd || n >= 10 ** idx;
}

interface Slot {
  presence: MotionValue<number>;
  on: boolean;
}

interface Column {
  turns: MotionValue<number>;
  /** Where the current run started and ends, in unwrapped turns. */
  start: number;
  target: number;
  /** How much of the number this column was (0-1) when the run started. */
  startShown: number;
  /** Whether it is part of the number when the run ends. */
  targetOn: boolean;
  /** How much of the number it is right now, 0 to 1. */
  shown: number;
}

interface Metrics {
  digits: number[];
  group: number;
  minus: number;
  font: number;
}

/**
 * NumberTicker
 *
 * A number that spins up like a counter (`count`) or turns each digit
 * straight to its new value (`roll`). Formatting comes from
 * `Intl.NumberFormat`, so currency, percent, compact and locale grouping
 * all work. Each column is as wide as the digit it shows, so proportional
 * fonts keep their natural spacing, and digits that appear or disappear
 * (999 to 1,000) open their width instead of jumping. The finished value is
 * always in the DOM for screen readers and crawlers.
 */
export function NumberTicker({ variant = "roll", ...props }: NumberTickerProps) {
  return <NumberTickerEngine key={variant} variant={variant} {...props} />;
}

function NumberTickerEngine({
  value,
  from = 0,
  variant = "roll",
  duration,
  delay = 0,
  trigger = "view",
  locales,
  format,
  prefix = "",
  suffix = "",
  onComplete,
  className,
  style,
  ...rest
}: NumberTickerProps) {
  const rootRef = React.useRef<HTMLSpanElement>(null);
  const rowRef = React.useRef<HTMLSpanElement>(null);
  const measureRef = React.useRef<HTMLSpanElement>(null);
  const inView = useInView(rootRef, { once: true, amount: 0.5 });
  const reduceMotion = useReducedMotion();
  const started = trigger === "mount" || inView;

  const safeValue = Number.isFinite(value) ? value : 0;
  const safeFrom = Number.isFinite(from) ? from : 0;
  const formatKey = JSON.stringify(format ?? null);
  const localeKey = JSON.stringify(locales ?? null);
  const layout = React.useMemo(
    () => buildLayout(safeValue, locales, format, prefix, suffix),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [safeValue, localeKey, formatKey, prefix, suffix],
  );
  // What the first paint shows. Fixed after mount, so React never rewrites
  // inline styles that the motion loop owns.
  const [first] = React.useState(() => ({
    n: scaledInt(safeFrom, layout),
    fd: layout.fd,
    negative: safeFrom < 0,
  }));

  // Columns never unmount: a shrinking number folds them to zero width.
  const needed = Math.max(
    lengthOf(scaledInt(safeFrom, layout), layout.fd),
    lengthOf(scaledInt(safeValue, layout), layout.fd),
  );
  const [maxPlaces, setMaxPlaces] = React.useState(needed);
  if (needed > maxPlaces) setMaxPlaces(needed);
  const places = Math.max(maxPlaces, needed);
  const columnCount = places + layout.fd;

  // Live copies for the motion loop, which runs outside render.
  const layoutRef = React.useRef(layout);
  const placesRef = React.useRef(places);
  const onCompleteRef = React.useRef(onComplete);
  const instantRef = React.useRef(false);
  React.useLayoutEffect(() => {
    layoutRef.current = layout;
    placesRef.current = places;
    onCompleteRef.current = onComplete;
    instantRef.current = Boolean(reduceMotion);
  });

  // The number on screen as a scaled integer: `from` until the first run.
  const shownRef = React.useRef(first.n);

  const separatorPlaces = React.useMemo(() => {
    const all = new Set<number>();
    for (let n = 1; n <= places; n += 1) {
      for (const k of layout.separators(n)) all.add(k);
    }
    return all;
  }, [layout, places]);

  /* ── Motion state, kept outside React ─────────────────────────────── */

  const metricsRef = React.useRef<Metrics | null>(null);
  const columnsRef = React.useRef(new Map<number, Column>());
  const signRef = React.useRef<Slot | null>(null);
  const paintRef = React.useRef<() => void>(() => {});
  const scheduledRef = React.useRef(false);

  // One paint per frame, however many motion values changed.
  const process = React.useCallback(() => {
    scheduledRef.current = false;
    paintRef.current();
  }, []);
  const schedule = React.useCallback(() => {
    if (scheduledRef.current) return;
    scheduledRef.current = true;
    frame.render(process);
  }, [process]);

  const makeSlot = React.useCallback(
    (on: boolean): Slot => {
      const presence = motionValue(on ? 1 : 0);
      presence.on("change", schedule);
      return { presence, on };
    },
    [schedule],
  );

  const getColumn = React.useCallback(
    (idx: number) => {
      let col = columnsRef.current.get(idx);
      if (!col) {
        const n = shownRef.current ?? 0;
        const digit = digitAt(n, idx);
        const on = placeShows(n, idx, layoutRef.current.fd);
        const turns = motionValue(digit);
        turns.on("change", schedule);
        col = {
          turns,
          start: digit,
          target: digit,
          startShown: on ? 1 : 0,
          targetOn: on,
          shown: on ? 1 : 0,
        };
        columnsRef.current.set(idx, col);
      }
      return col;
    },
    [schedule],
  );

  const toggle = React.useCallback((slot: Slot, on: boolean) => {
    if (slot.on === on) return;
    slot.on = on;
    if (instantRef.current) {
      slot.presence.stop();
      slot.presence.set(on ? 1 : 0);
    } else {
      animate(slot.presence, on ? 1 : 0, PRESENCE);
    }
  }, []);

  const paint = () => {
    const row = rowRef.current;
    const metrics = metricsRef.current;
    if (!row || !metrics) return;
    const L = layoutRef.current;

    // How much each column belongs to the number, top down. Opening and
    // closing ride on the column's own roll, so the number widens as a
    // leading digit rolls in, narrows as one rolls away to 0, and every
    // width change lands exactly when the digits do. A column is never less
    // shown than the one on its left, so no gap and no stray leading 0.
    let higher = 0;
    let length = 1;
    for (let idx = placesRef.current + L.fd - 1; idx >= 0; idx -= 1) {
      const col = getColumn(idx);
      const now = col.turns.get();
      const span = col.target - col.start;
      const moved = Math.abs(now - col.start);
      const target = col.targetOn ? 1 : 0;
      let own: number;
      if (idx <= L.fd) own = 1;
      else if (col.startShown === target) own = target;
      else if (Math.abs(span) < 1e-6) own = 0;
      else if (target === 1) own = col.startShown + (1 - col.startShown) * smooth(moved / Math.min(0.7, Math.abs(span)));
      else own = col.startShown * (1 - smooth(moved / Math.abs(span)));
      col.shown = Math.max(own, higher);
      higher = col.shown;
      if (col.shown > 0.001) length = Math.max(length, idx - L.fd + 1);
    }
    const groups = L.separators(length);

    for (const el of row.querySelectorAll<HTMLElement>("[data-ticker-col]")) {
      const col = columnsRef.current.get(Number(el.dataset.idx));
      const strip = el.firstElementChild?.nextElementSibling as HTMLElement | null;
      if (!col || !strip) continue;
      const now = col.turns.get();
      // Width travels from the start digit's to the end digit's along the
      // roll itself, ignoring the digits passing by, so the number never
      // shakes sideways mid-roll.
      const span = col.target - col.start;
      const progress = Math.abs(span) > 1e-6 ? Math.min(1, Math.max(0, (now - col.start) / span)) : 1;
      const from = glyphWidth(metrics, col.start);
      const width = from + (glyphWidth(metrics, col.target) - from) * progress;
      el.style.width = `${width * col.shown}px`;
      // Fade in only once there is room, so an opening digit never sits
      // over its neighbours.
      el.style.opacity = String(smooth((col.shown - 0.25) / 0.6));
      strip.style.transform = `translate3d(0, ${(-wrap(now) * 100) / STRIP}%, 0)`;
    }

    for (const el of row.querySelectorAll<HTMLElement>("[data-ticker-sep]")) {
      const place = Number(el.dataset.place);
      const shown = groups.has(place) ? (columnsRef.current.get(place + L.fd)?.shown ?? 0) : 0;
      el.style.width = `${metrics.group * shown}px`;
      el.style.opacity = String(smooth((shown - 0.3) / 0.7));
    }
    const signEl = row.querySelector<HTMLElement>("[data-ticker-sign]");
    if (signEl && signRef.current) {
      const p = signRef.current.presence.get();
      signEl.style.width = `${metrics.minus * p}px`;
      signEl.style.opacity = String(p);
    }
  };

  React.useLayoutEffect(() => {
    paintRef.current = paint;
  });

  /* ── Measuring: real glyph widths, so proportional digits stay put ── */

  React.useLayoutEffect(() => {
    const box = measureRef.current;
    if (!box) return;
    const read = () => {
      const widths = Array.from(box.children, (s) => s.getBoundingClientRect().width);
      const digits = widths.slice(0, 10);
      metricsRef.current = {
        digits,
        group: widths[10] ?? 0,
        minus: widths[11] ?? 0,
        font: parseFloat(getComputedStyle(box).fontSize) || 16,
      };
      const row = rowRef.current;
      const mask = row ? inkMask(row) : null;
      if (row && mask) row.style.setProperty("--ticker-mask", mask);
      paintRef.current();
    };
    read();
    const observer = new ResizeObserver(read);
    observer.observe(box);
    let alive = true;
    document.fonts?.ready.then(() => {
      if (alive) read();
    });
    return () => {
      alive = false;
      observer.disconnect();
    };
  }, [layout]);

  // New columns get state before they paint.
  React.useLayoutEffect(() => {
    for (let idx = 0; idx < columnCount; idx += 1) getColumn(idx);
    if (!signRef.current) signRef.current = makeSlot(first.negative);
    paintRef.current();
  });

  /* ── Runs ─────────────────────────────────────────────────────────── */

  const firstRunRef = React.useRef(true);
  const runLength = Math.max(0.05, duration ?? (variant === "roll" ? 1 : 2.2));

  React.useEffect(() => {
    if (!started) return;
    const wait = firstRunRef.current ? Math.max(0, delay) : 0;
    firstRunRef.current = false;

    const L = layoutRef.current;
    const n = scaledInt(safeValue, L);
    const previous = shownRef.current ?? 0;
    shownRef.current = n;
    const up = n >= previous;
    const change = Math.abs(n - previous);
    const count = placesRef.current + L.fd;

    if (signRef.current) toggle(signRef.current, safeValue < 0 && n > 0);

    const running: AnimationPlaybackControlsWithThen[] = [];
    const moving: Column[] = [];
    for (let idx = 0; idx < count; idx += 1) {
      const col = getColumn(idx);
      const digit = digitAt(n, idx);
      // Count restarts from what is on screen; roll keeps its digit path.
      const base = variant === "count" ? col.turns.get() : col.target;
      const here = wrap(base);
      let step = up ? (digit - here + 10) % 10 : -((here - digit + 10) % 10);
      if (Math.abs(step) < 1e-6 || Math.abs(Math.abs(step) - 10) < 1e-6) step = 0;
      if (variant === "count") {
        const laps = Math.min(MAX_TURNS, Math.floor(change / 10 ** idx / 10));
        step += (up ? 10 : -10) * laps;
      }
      col.start = col.turns.get();
      col.target = base + step;
      col.startShown = col.shown;
      col.targetOn = placeShows(n, idx, L.fd);

      if (reduceMotion) {
        col.turns.stop();
        col.turns.set(col.target);
        col.start = col.target;
        col.startShown = col.targetOn ? 1 : 0;
        continue;
      }
      if (Math.abs(col.turns.get() - col.target) < 1e-6) continue;
      moving.push(col);
    }

    if (variant === "count") {
      // One wave: every digit turns at the same speed, so the row reads as a
      // single motion. Digits with further to go start first (the low ones),
      // shorter trips join later, and everything lands together.
      const distance = (col: Column) => Math.abs(col.target - col.turns.get());
      const longest = Math.max(1, ...moving.map(distance));
      for (const col of moving) {
        const share = Math.max(0.4, distance(col) / longest);
        running.push(
          animate(col.turns, col.target, {
            duration: runLength * share,
            delay: wait + runLength * (1 - share),
            ease: COUNT_EASE,
          }),
        );
      }
    } else {
      for (const col of moving) {
        running.push(
          animate(col.turns, col.target, {
            duration: runLength,
            delay: wait,
            ease: ROLL_EASE,
          }),
        );
      }
    }
    schedule();

    let cancelled = false;
    const finish = () => {
      if (!cancelled) onCompleteRef.current?.();
    };
    if (running.length === 0) finish();
    else Promise.all(running.map((c) => new Promise<void>((r) => c.then(r)))).then(finish);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [started, safeValue, layout, variant, runLength, reduceMotion]);

  React.useEffect(
    () => () => {
      cancelFrame(process);
      for (const col of columnsRef.current.values()) col.turns.stop();
      signRef.current?.presence.stop();
    },
    [process],
  );

  /* ── Markup ───────────────────────────────────────────────────────── */

  const firstGroups = layout.separators(lengthOf(first.n, first.fd));

  const slots: React.ReactNode[] = [];
  for (let place = places - 1; place >= -layout.fd; place -= 1) {
    const idx = place + layout.fd;
    const on = placeShows(first.n, idx, first.fd);
    const digit = digitAt(first.n, idx);
    slots.push(
      <span
        key={`c${place}`}
        data-ticker-col=""
        data-idx={idx}
        className="relative inline-block box-content text-center"
        style={{
          paddingInline: PAD_X,
          marginInline: `-${PAD_X}`,
          maskImage: `var(--ticker-mask, ${FALLBACK_MASK})`,
          WebkitMaskImage: `var(--ticker-mask, ${FALLBACK_MASK})`,
          ...(on ? null : { width: 0, opacity: 0 }),
        }}
      >
        {/* In-flow glyph: gives the column its baseline and first width. */}
        <span className="invisible">{layout.digits[digit]}</span>
        <span
          data-ticker-strip=""
          className="absolute flex flex-col items-center will-change-transform"
          style={{
            top: 0,
            left: PAD_X,
            right: PAD_X,
            transform: `translate3d(0, ${(-digit * 100) / STRIP}%, 0)`,
          }}
        >
          {Array.from({ length: STRIP }, (_, i) => (
            <span key={i} className="block">
              {layout.digits[i % 10]}
            </span>
          ))}
        </span>
      </span>,
    );
    if (place > 0 && separatorPlaces.has(place)) {
      const sepOn = firstGroups.has(place) && on;
      slots.push(
        <span
          key={`s${place}`}
          data-ticker-sep=""
          data-place={place}
          className="inline-block whitespace-pre"
          style={sepOn ? undefined : { width: 0, opacity: 0 }}
        >
          {layout.group}
        </span>,
      );
    }
    if (place === 0 && layout.fd > 0) {
      slots.push(
        <span key="decimal" className="whitespace-pre">
          {layout.decimal}
        </span>,
      );
    }
  }

  return (
    <span
      ref={rootRef}
      className={cn("relative inline-block whitespace-nowrap", className)}
      style={style}
      {...rest}
    >
      <span className="sr-only">{layout.text}</span>
      <span
        ref={rowRef}
        aria-hidden="true"
        className="inline-flex items-baseline"
        style={{ lineHeight: LINE, marginBlock: LINE_TRIM }}
      >
        <span
          data-ticker-sign=""
          className="inline-block"
          style={first.negative ? undefined : { width: 0, opacity: 0 }}
        >
          −
        </span>
        {layout.lead ? <span className="whitespace-pre">{layout.lead}</span> : null}
        {slots}
        {layout.trail ? <span className="whitespace-pre">{layout.trail}</span> : null}
      </span>
      <span
        ref={measureRef}
        aria-hidden="true"
        className="pointer-events-none invisible absolute left-0 top-0 whitespace-pre"
      >
        {layout.digits.map((d, i) => (
          <span key={i} className="inline-block">
            {d}
          </span>
        ))}
        <span className="inline-block">{layout.group}</span>
        <span className="inline-block">−</span>
      </span>
    </span>
  );
}
