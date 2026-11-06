"""Pictures and PDFs for WhatsApp: the daily card (PNG) and the weekly report (PDF).

WhatsApp template text cannot hold tables or line breaks, but a template may carry an image or a
document header. The daily card is a table picture of decisions due, what happened, IPOs and the
portfolio; the weekly report is a one- or two-page PDF of the week. Data gathering
(``daily_sections``, ``weekly_data``) is kept apart from drawing so both can be tested.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from trademare.core.models import Market
from trademare.web.formatting import indian

log = logging.getLogger(__name__)

FONTS = [  # first one that exists; all have the rupee sign
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
INK, MUTED, LINE, BG, SHADE, BAR = "#1d2433", "#6b7280", "#e5e7eb", "#ffffff", "#f3f4f6", "#0f172a"
GREEN, RED, AMBER = "#15803d", "#b91c1c", "#b45309"
MINUS, EN_DASH = "\u2212", "\u2013"  # typographic minus and range dash
NAMES = {Market.IN: "India", Market.US: "US"}
BADGES = {Market.IN: ("INDIA · NSE · ₹", "#ea580c"), Market.US: ("US · NYSE/NASDAQ · $", "#2563eb")}
EXIT_WHY = {
    "signal": "fell out of the top ranks",
    "regime": "market turned weak",
    "time": "held too long",
    "exit": "its setup says sell",
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def whole(value: float | None, market: Market) -> str:
    """₹1,00,250 / $1,250 / minus ₹1,240, no paise or cents; a dash when unknown."""
    if value is None:
        return "—"
    sign = MINUS if value < 0 else ""
    return sign + ("₹" + indian(abs(value), 0) if market is Market.IN else f"${abs(value):,.0f}")


def price(value: float | None, market: Market) -> str:
    """Share prices: paise or cents below 1,000, whole numbers above (keeps table cells short)."""
    if value is None:
        return "—"
    decimals = 2 if abs(value) < 1000 else 0
    return ("₹" + indian(value, decimals)) if market is Market.IN else f"${value:,.{decimals}f}"


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%".replace("-", MINUS)


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


# --- what goes on the card -------------------------------------------------------------------


@dataclass
class Table:
    title: str
    columns: list[str]
    widths: list[int]  # fractions of the width, any scale
    aligns: str  # "l" or "r" per column
    rows: list[list[str]] = field(default_factory=list)
    colors: list[list[str | None]] = field(default_factory=list)
    note: str = ""
    title_color: str = INK


@dataclass
class Block:
    """One market's part of the daily card."""

    title: str
    subtitle: str
    urgent: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    portfolio: tuple[str, str, str, str] | None = None  # value, change, change colour, holdings
    market: Market | None = None


def _fill_row(text: str, market: Market) -> tuple[str, str, str, str | None]:
    """A desk fill line -> (stock, what happened, P&L, P&L colour)."""
    text = text.removesuffix(" (broker)")
    if m := re.fullmatch(r"(\S+): (LONG|SHORT) (\d+) @ ([\d,.]+)", text):
        verb = "Bought" if m[2] == "LONG" else "Sold short"
        return m[1], f"{verb} {m[3]} at {price(float(m[4].replace(',', '')), market)}", "", None
    if m := re.fullmatch(r"(\S+): closed \(([^)]*)\) @ ([\d,.]+), P&L (-?[\d,.]+)", text):
        pnl = float(m[4].replace(",", ""))
        why = "Stop hit, sold" if m[2] == "stop" else f"Sold ({m[2]})"
        what = f"{why} at {price(float(m[3].replace(',', '')), market)}"
        return m[1], what, whole(pnl, market), GREEN if pnl >= 0 else RED
    symbol, _, rest = text.partition(": ")
    return symbol, rest or text, "", None


def _unfilled_row(text: str) -> tuple[str, str]:
    if m := re.fullmatch(r"(?:Buy|Sell) \d+ (\S+) expired on .*? without filling(?:: (.*?))?\.?", text):
        why = m[2] or ""
        if "stayed above your limit" in why:
            why = "price stayed above your limit"
        elif "stayed below your limit" in why:
            why = "price stayed below your limit"
        elif "stayed below your trigger" in why:
            why = "price never rose to the buy-above level"
        return m[1], "Not filled" + (f": {why}" if why else "")
    return "", text.rstrip(".")


def deadline(signals, market: Market) -> str:
    from zoneinfo import ZoneInfo

    ends = [s.expires_at for s in signals if s.expires_at is not None]
    if not ends:
        return ""
    end = min(ends)
    if end.tzinfo is None:
        end = end.replace(tzinfo=ZoneInfo("UTC"))
    local = end.astimezone(ZoneInfo("Asia/Kolkata" if market is Market.IN else "America/New_York"))
    return f"{_day(local.date())}, {local:%-I:%M} {local:%p}".replace("AM", "am").replace("PM", "pm")


def daily_block(
    r, alert_prefixes: tuple[str, ...], heads_up: str, closes: dict[str, float] | None = None
) -> Block | None:
    """One market's daily run as tables; None when nothing happened. ``closes``: last close per stock."""
    m = r.market
    urgent = [n.split(" ", 1)[1] if " " in n else n for n in r.notes if n.startswith(alert_prefixes)]
    if r.kill_switch:
        urgent.insert(0, f"Kill switch tripped ({r.kill_reason}): new buys are blocked")
    results = [n.removeprefix(heads_up).strip().split(" ", 1)[0] for n in r.notes if n.startswith(heads_up)]
    signals = list(r.signals)
    if not (urgent or results or signals or r.orders_ended or r.fills or getattr(r, "stops_raised", None)):
        return None
    name = NAMES.get(m, m.value)
    block = Block(title=f"{name} market", subtitle=f"TradeMare · after {_day(r.session)}", market=m)
    block.urgent = urgent
    buys = [s for s in signals if s.action != "EXIT"]
    sells = [s for s in signals if s.action == "EXIT"]
    if buys:
        block.tables.append(_buy_table(buys, m, closes))
        block.tables[-1].title = f"{name} · {block.tables[-1].title}"
    if sells:
        due = deadline(sells, m)
        t = Table(
            f"{name} · SELL signals" + (f" · decide by {due}" if due else ""),
            ["Stock", "Qty", "Price now", "Sell", "Why"],
            [22, 8, 17, 18, 35],
            "lrrll",
            title_color=RED,
            note="Sell = a market order at the next session's open",
        )
        for s in sells:
            setup = (
                (s.strategy or "").removeprefix("setup:") if (s.strategy or "").startswith("setup:") else ""
            )
            now = price(closes.get(s.symbol), m) if closes else "—"
            why = EXIT_WHY.get(s.exit_reason or "", s.exit_reason or "rule")
            t.rows.append(
                [s.symbol + (f" · {setup.upper()}" if setup else ""), str(s.quantity), now, "at open", why]
            )
            t.colors.append([None, None, MUTED, RED])
        block.tables.append(t)
    happened = Table(f"{name} · since last run", ["Stock", "What happened", "P&L"], [24, 56, 20], "llr")
    for f in r.fills:
        stock, what, pnl, colour = _fill_row(f, m)
        happened.rows.append([stock, what, pnl])
        happened.colors.append([None, None, colour])
    for e in r.orders_ended:
        stock, what = _unfilled_row(e)
        happened.rows.append([stock, what, ""])
        happened.colors.append([None, MUTED])
    for sym in results:
        happened.rows.append([sym, "Results next session (you hold it)", ""])
        happened.colors.append([None, AMBER])
    if happened.rows:
        block.tables.append(happened)
    if raised := getattr(r, "stops_raised", None):
        t = Table(
            f"{name} · raise your stop-loss",
            ["Stock", "Price now", "Old stop", "New stop"],
            [31, 23, 23, 23],
            "lrrr",
            title_color=GREEN,
            note="The trailing stop moved up with the price. If you hold this stock in Zerodha, raise "
            "its GTT stop to the new level; TradeMare cannot change orders you placed in Kite",
        )
        for sym, old, new, close in raised:
            t.rows.append([sym, price(close, m), price(old, m), price(new, m)])
            t.colors.append([None, MUTED, MUTED, GREEN])
        block.tables.append(t)
    if r.equity is not None:
        change, colour = "", INK
        if r.previous_equity:
            change_pct = r.equity / r.previous_equity - 1
            change, colour = pct(change_pct), GREEN if change_pct >= 0 else RED
        block.portfolio = (whole(r.equity, m), change, colour, str(r.open_positions))
    return block


def _buy_table(buys, m: Market, closes: dict[str, float] | None) -> Table:
    """BUY signals: what to buy, at most at what price, the stop-loss, the target if the strategy has one."""
    targets = any(getattr(s, "target_price", None) for s in buys)
    columns = ["Stock", "Qty", "Price now", "Buy up to", "Stop-loss", "Max loss"]
    widths = [24, 8, 17, 19, 17, 15]
    if targets:  # e.g. US signals sell at 2x the risk
        columns.insert(5, "Target")
        widths = [14, 8, 15, 16, 15, 14, 18]
    due = deadline(buys, m)
    t = Table(
        "BUY signals" + (f" · decide by {due}" if due else ""),
        columns,
        widths,
        "l" + "r" * (len(columns) - 1),
        title_color=GREEN,
    )
    notes = [f"All buys together {whole(sum(s.entry_price * s.quantity for s in buys), m)}"]
    for s in buys:
        strategy = s.strategy or ""
        setup = strategy.removeprefix("setup:") if strategy.startswith("setup:") else ""
        name = s.symbol + (f" · {setup.upper()}" if setup else "")
        soon = [w for w in (s.reasons or {}).get("events", []) if w.startswith("Results due")]
        if soon:
            name += " ⚠"
            when = re.search(r"\d{4}-\d{2}-\d{2}", soon[0])
            notes.append(f"⚠ {s.symbol} results {_day(date.fromisoformat(when[0])) if when else 'soon'}")
        risk = abs(s.entry_price - s.stop_price) * s.quantity if s.stop_price is not None else None
        t.rows.append(
            [
                name,
                str(s.quantity),
                price(closes.get(s.symbol), m) if closes else "—",
                ("above " if setup else "") + price(s.entry_price, m),
                price(s.stop_price, m),
                *([price(s.target_price, m) if s.target_price else "—"] if targets else []),
                whole(risk, m),
            ]
        )
        t.colors.append([None, None, MUTED, GREEN, RED] + ([GREEN] if targets else []))
    notes.append(
        "Price now = last close · Buy up to = your limit for the next session (lapses if not filled) · "
        "Stop-loss = sell if the price falls to it · Max loss = if the stop is hit"
    )
    if targets:
        notes.append("Target = sell when the price reaches it")
    else:
        notes.append(
            "No fixed target: the strategy holds winners and sells when the stop (raised as the price rises) "
            "is hit or the stock drops out of the top ranks"
        )
    if any((s.strategy or "").startswith("setup:") for s in buys):
        notes.append("above = a setup buy that fills only if the price rises past it")
    t.note = "   ·   ".join(notes)
    return t


def ipo_table(cards, today: date, horizon_days: int = 3, after_close: bool = False) -> Table | None:
    """Issues open now or opening within a few days, with demand and the past-IPO record.

    ``after_close``: bidding has ended for the day (5 pm), so issues closing today are left out.
    """
    rows, colors = [], []
    for c in cards:
        ipo = c.ipo
        if c.status == "open" and ipo.end == today and after_close:
            continue
        if c.status == "open" and ipo.end:
            when = "Closes today" if ipo.end == today else f"Open till {_day(ipo.end)}"
        elif c.status == "upcoming" and ipo.start and (ipo.start - today).days <= horizon_days:
            when = f"Opens {_day(ipo.start)}"
        else:
            continue
        band = (
            f"₹{indian(ipo.price_low, 0)}{EN_DASH}{indian(ipo.price_high, 0)}"
            if ipo.price_low and ipo.price_high and ipo.price_low != ipo.price_high
            else whole(ipo.price, Market.IN)
        )
        demand = f"{c.qib:,.1f}x" if c.qib is not None else "—"
        verdict = c.label or ""
        rows.append([ipo.symbol, when, band, whole(ipo.min_investment, Market.IN), demand, verdict])
        good = verdict.lower().startswith("favour")
        bad = verdict.lower().startswith(("unfavour", "avoid", "weak"))
        colors.append(
            [
                None,
                AMBER if ipo.end == today else None,
                None,
                None,
                None,
                GREEN if good else RED if bad else MUTED,
            ]
        )
    if not rows:
        return None
    return Table(
        "India · IPOs",
        ["Issue", "When", "Price", "Min. bid", "QIB", "Past record"],
        [17, 22, 15, 15, 12, 19],
        "llrrrl",
        rows,
        colors,
        note="Bidding closes at 5 pm · QIB = how many times institutions subscribed (final only at the "
        "close) · Past record = how earlier IPOs with similar demand listed",
    )


# --- drawing -----------------------------------------------------------------------------------


class Canvas:
    """A tall white page that grows downwards; cropped at the end."""

    def __init__(self, width: int, margin: int, scale: float = 1.0) -> None:
        self.w, self.m, self.k = width, margin, scale
        self.im = Image.new("RGB", (width, 6000), BG)
        self.d = ImageDraw.Draw(self.im)
        self.y = 0

    def f(self, size: int) -> ImageFont.FreeTypeFont:
        return _font(round(size * self.k))

    def px(self, n: float) -> int:
        return round(n * self.k)

    def text(self, x: int, s: str, size: int, color: str = INK, dy: int = 0) -> None:
        self.d.text((x, self.y + self.px(dy)), s, fill=color, font=self.f(size))

    def fit(self, s: str, size: int, width: int) -> str:
        font = self.f(size)
        if self.d.textlength(s, font=font) <= width:
            return s
        while s and self.d.textlength(s + "…", font=font) > width:
            s = s[:-1]
        return s + "…"

    def wrap(self, s: str, size: int, width: int) -> list[str]:
        font, lines, line = self.f(size), [], ""
        for word in s.split(" "):
            trial = f"{line} {word}".strip()
            if self.d.textlength(trial, font=font) > width and line:
                lines.append(line)
                line = word
            else:
                line = trial
        return [*lines, line] if line else lines

    def header(self, title: str, subtitle: str, market: Market | None = None) -> None:
        h = self.px(130)
        self.d.rectangle([0, self.y, self.w, self.y + h], fill=BAR)
        self.text(self.m, title, 44, "white", dy=24)
        self.text(self.m, subtitle, 26, "#cbd5e1", dy=80)
        if market in BADGES:  # a coloured market badge on the right
            label, colour = BADGES[market]
            font = self.f(26)
            bw = self.d.textlength(label, font=font) + self.px(36)
            x1 = self.w - self.m
            top = self.y + self.px(40)
            self.d.rounded_rectangle([x1 - bw, top, x1, top + self.px(50)], self.px(25), fill=colour)
            self.d.text((x1 - bw + self.px(18), top + self.px(10)), label, fill="white", font=font)
        self.y += h + self.px(30)

    def heading(self, s: str, color: str = INK, size: int = 34) -> None:
        self.text(self.m, s, size, color)
        self.y += self.px(size * 1.7)

    def urgent(self, items: list[str]) -> None:
        width = self.w - 2 * self.m
        lines = [line for item in items for line in self.wrap("• " + item, 28, width - self.px(40))]
        h = self.px(70) + self.px(42) * len(lines)
        self.d.rounded_rectangle([self.m, self.y, self.w - self.m, self.y + h], self.px(16), fill="#fee2e2")
        self.text(self.m + self.px(20), "Urgent", 30, RED, dy=14)
        for i, line in enumerate(lines):
            self.text(self.m + self.px(20), line, 28, RED, dy=60 + 42 * i)
        self.y += h + self.px(30)

    def table(self, t: Table, size: int = 30) -> None:
        self.heading(t.title, t.title_color)
        width = self.w - 2 * self.m
        total = sum(t.widths)
        cols = [round(width * w / total) for w in t.widths]
        rh, pad = self.px(size * 1.95), self.px(18)
        self.d.rectangle([self.m, self.y, self.w - self.m, self.y + rh], fill=SHADE)
        self._row(t.columns, cols, t.aligns, [MUTED] * len(cols), size - 4, rh, pad)
        self.y += rh
        for i, row in enumerate(t.rows):
            colors = t.colors[i] if i < len(t.colors) else []
            row_colors = [(colors[j] if j < len(colors) and colors[j] else INK) for j in range(len(cols))]
            self._row(row, cols, t.aligns, row_colors, size, rh, pad)
            self.y += rh
            self.d.line([self.m, self.y, self.w - self.m, self.y], fill=LINE, width=2)
        if t.note:
            for line in self.wrap(t.note, size - 7, width):
                self.text(self.m, line, size - 7, MUTED, dy=12)
                self.y += self.px((size - 7) * 1.45)
            self.y += self.px(12)
        self.y += self.px(36)

    def _row(self, cells, cols, aligns, colors, size, rh, pad) -> None:
        x = self.m
        font = self.f(size)
        for cell, cw, a, color in zip(cells, cols, aligns, colors, strict=False):
            s = self.fit(cell, size, cw - 2 * pad)
            tx = x + pad if a == "l" else x + cw - pad - self.d.textlength(s, font=font)
            self.d.text((tx, self.y + (rh - self.px(size * 1.2)) // 2), s, fill=color, font=font)
            x += cw

    def stat_boxes(self, items: list[tuple[str, str, str]], size: int = 38) -> None:
        width = self.w - 2 * self.m
        gap = self.px(20)
        bw = (width - gap * (len(items) - 1)) // len(items)
        h = self.px(130)
        for i, (label, value, color) in enumerate(items):
            x = self.m + i * (bw + gap)
            self.d.rounded_rectangle([x, self.y, x + bw, self.y + h], self.px(16), fill=SHADE)
            self.d.text((x + self.px(22), self.y + self.px(20)), label, fill=MUTED, font=self.f(24))
            self.d.text((x + self.px(22), self.y + self.px(60)), value, fill=color, font=self.f(size))
        self.y += h + self.px(40)

    def bullets(self, items: list[str], size: int = 26) -> None:
        width = self.w - 2 * self.m - self.px(30)
        for item in items:
            for i, line in enumerate(self.wrap(item, size, width)):
                self.text(self.m + self.px(10), ("• " if i == 0 else "   ") + line, size)
                self.y += self.px(size * 1.6)
        self.y += self.px(20)

    def png(self) -> bytes:
        out = io.BytesIO()
        self.im.crop((0, 0, self.w, self.y + self.px(10))).save(out, "PNG", optimize=True)
        return out.getvalue()


def render_daily(blocks: list[Block], ipos: Table | None) -> bytes:
    c = Canvas(1080, 40)
    for b in blocks:
        c.header(b.title, b.subtitle, b.market)
        if b.urgent:
            c.urgent(b.urgent)
        for t in b.tables:
            c.table(t)
        if b.portfolio:
            value, change, colour, holdings = b.portfolio
            c.stat_boxes(
                [
                    ("Paper portfolio", value, INK),
                    ("Today", change or "—", colour),
                    ("Holdings", holdings, INK),
                ]
            )
    if ipos:
        if not blocks:
            c.header("India IPOs", f"TradeMare · {_day(date.today())}", Market.IN)
        c.table(ipos, size=26)
    return c.png()


def daily_caption(blocks: list[Block], ipos: Table | None, reports) -> str:
    """The template's one-line summary: '{market}: 3 decisions due Mon 2 Nov 3:30 pm, 2 trades done'."""
    parts = []
    for r in reports:
        bits = []
        if r.signals:
            n = len(r.signals)
            due = deadline(r.signals, r.market)
            bits.append(f"{n} decision{'s' if n != 1 else ''} due" + (f" {due}" if due else ""))
        if r.fills:
            bits.append(f"{len(r.fills)} trade{'s' if len(r.fills) != 1 else ''} done")
        if r.orders_ended:
            bits.append(f"{len(r.orders_ended)} not filled")
        if raised := getattr(r, "stops_raised", None):
            bits.append(f"raise {len(raised)} stop{'s' if len(raised) != 1 else ''}")
        urgent = sum(1 for n in r.notes if n.startswith(("ALERT", "WARNING"))) + int(r.kill_switch)
        if urgent:
            bits.insert(0, f"{urgent} urgent")
        if bits:
            parts.append(f"{NAMES.get(r.market, r.market.value)}: " + ", ".join(bits))
    if ipos:
        parts.append(f"{len(ipos.rows)} IPO{'s' if len(ipos.rows) != 1 else ''} open or opening soon")
    return "; ".join(parts) or "nothing new"


# --- weekly report -------------------------------------------------------------------------------


@dataclass
class Weekly:
    market: Market
    week_end: date
    equity: float | None = None
    week_change: float | None = None
    benchmark_change: float | None = None
    since_start: float | None = None
    holdings: Table | None = None
    trades: Table | None = None
    ipos: Table | None = None
    coming: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.week_change is not None:
            bits.append(f"account {pct(self.week_change)}")
        if self.benchmark_change is not None:
            bits.append(
                f"{'Nifty 50' if self.market is Market.IN else 'S&P 500'} {pct(self.benchmark_change)}"
            )
        n = len(self.trades.rows) if self.trades else 0
        bits.append(f"{n} trade{'s' if n != 1 else ''}")
        return ", ".join(bits)


def _close_on_or_before(bars, day: date) -> float | None:
    if bars is None or bars.empty:
        return None
    upto = bars[bars.index.date <= day] if hasattr(bars.index, "date") else bars
    return float(upto["close"].iloc[-1]) if len(upto) else None


def weekly_data(config, db, store, events, market: Market, week_end: date, ipo_cards=None) -> Weekly:
    from sqlalchemy import select

    from trademare.storage.db import EquityRow, OrderRow, PositionRow, SignalRow

    m = market.value
    week_start = week_end - timedelta(days=6)
    w = Weekly(market=market, week_end=week_end)
    with db.session() as s:
        eq = list(s.scalars(select(EquityRow).where(EquityRow.market == m).order_by(EquityRow.day)))
        now = [e for e in eq if e.day <= week_end]
        before = [e for e in eq if e.day < week_start]
        if now:
            w.equity = now[-1].equity
            base = before[-1] if before else now[0]
            w.week_change = now[-1].equity / base.equity - 1 if base.equity else None
            w.since_start = now[-1].equity / eq[0].equity - 1 if eq[0].equity else None
            bench = config.markets[market].benchmark
            if bench:
                bars = store.read_bars(market, bench)
                a, b = _close_on_or_before(bars, base.day), _close_on_or_before(bars, now[-1].day)
                w.benchmark_change = b / a - 1 if a and b else None
        holdings = Table(
            "Holdings",
            ["Stock", "Qty", "Bought", "Now", "Return", "Stop", "To stop", "Results"],
            [20, 8, 13, 13, 12, 13, 10, 11],
            "lrrrrrrr",
        )
        coming = []
        for p in s.scalars(select(PositionRow).where(PositionRow.market == m, PositionRow.status == "open")):
            last = p.last_price or p.entry_price
            ret = (last / p.entry_price - 1) * (1 if p.direction == "LONG" else -1)
            to_stop = abs(last - p.stop_price) / last if p.stop_price else None
            ev = events.cached(market, p.symbol) if events else None
            res = ev.next_results if ev and ev.next_results and ev.next_results >= week_end else None
            close_call = res is not None and (res - week_end).days <= 10
            name = p.symbol + (
                f" · {p.strategy.removeprefix('setup:').upper()}"
                if (p.strategy or "").startswith("setup:")
                else ""
            )
            holdings.rows.append(
                [
                    name,
                    str(p.quantity),
                    price(p.entry_price, market),
                    price(last, market),
                    pct(ret),
                    price(p.stop_price, market),
                    "—" if to_stop is None else f"{to_stop:.1%}",
                    f"{res.day} {res:%b}" if res else "—",
                ]
            )
            holdings.colors.append(
                [
                    None,
                    None,
                    None,
                    None,
                    GREEN if ret >= 0 else RED,
                    None,
                    AMBER if to_stop is not None and to_stop < 0.05 else None,
                    AMBER if close_call else None,
                ]
            )
            if close_call:
                gap = (
                    f"; stop is {to_stop:.0%} away and results can gap past it" if to_stop is not None else ""
                )
                coming.append(f"{p.symbol} results {_day(res)}{gap}")
        w.holdings = holdings if holdings.rows else None
        trades = Table("Trades this week", ["Day", "Stock", "What", "P&L"], [16, 22, 42, 20], "lllr")
        events_list = []
        for p in s.scalars(
            select(PositionRow).where(
                PositionRow.market == m,
                PositionRow.entry_date >= week_start,
                PositionRow.entry_date <= week_end,
            )
        ):
            events_list.append(
                (p.entry_date, p.symbol, f"Bought {p.quantity} at {price(p.entry_price, market)}", "", None)
            )
        for p in s.scalars(
            select(PositionRow).where(
                PositionRow.market == m,
                PositionRow.exit_date >= week_start,
                PositionRow.exit_date <= week_end,
            )
        ):
            why = (
                "stop hit"
                if p.exit_reason == "stop"
                else EXIT_WHY.get(p.exit_reason or "", p.exit_reason or "")
            )
            pnl = p.realized_pnl
            events_list.append(
                (
                    p.exit_date,
                    p.symbol,
                    f"Sold at {price(p.exit_price, market)} ({why})",
                    whole(pnl, market),
                    None if pnl is None else GREEN if pnl >= 0 else RED,
                )
            )
        for o in s.scalars(
            select(OrderRow).where(
                OrderRow.market == m,
                OrderRow.status == "EXPIRED",
                OrderRow.valid_until >= week_start,
                OrderRow.valid_until <= week_end,
            )
        ):
            events_list.append((o.valid_until, o.symbol, "Order not filled", "", None))
        for day, sym, what, pnl, colour in sorted(events_list, key=lambda e: e[0]):
            trades.rows.append([f"{day:%a} {day.day}", sym, what, pnl])
            trades.colors.append([None, None, MUTED if what.startswith("Order not") else None, colour])
        w.trades = trades if trades.rows else None
        pending = list(
            s.scalars(select(SignalRow).where(SignalRow.market == m, SignalRow.status == "PENDING"))
        )
        if pending:
            due = deadline(pending, market)
            coming.append(
                f"{len(pending)} signal{'s' if len(pending) != 1 else ''} waiting for you"
                + (f", decide by {due}" if due else "")
            )
    w.coming = coming
    if ipo_cards is not None and market is Market.IN:
        w.ipos = weekly_ipo_table(ipo_cards, week_end)
    return w


def last_closes(store, market: Market, symbols) -> dict[str, float]:
    """Latest cached close for each symbol (skips any without prices)."""
    out = {}
    for sym in set(symbols):
        try:
            bars = store.read_bars(market, sym)
        except Exception:
            bars = None
        if bars is not None and not bars.empty:
            out[sym] = float(bars["close"].iloc[-1])
    return out


def weekly_ipo_table(cards, week_end: date) -> Table | None:
    """Listed this week (with the listing-day gain), open now, and opening in the next week."""
    t = Table("IPOs", ["Issue", "Status", "Price", "QIB", "Listing / record"], [20, 28, 14, 12, 26], "llrrl")
    week_start = week_end - timedelta(days=6)
    for c in cards:
        ipo = c.ipo
        demand = f"{c.qib:,.1f}x" if c.qib is not None else "—"
        if c.status == "listed" and ipo.listing_date and week_start <= ipo.listing_date <= week_end:
            gain = c.listing_gain
            t.rows.append(
                [
                    ipo.symbol,
                    f"Listed {_day(ipo.listing_date)}",
                    whole(ipo.price, Market.IN),
                    demand,
                    f"{pct(gain)} on day 1" if gain is not None else "—",
                ]
            )
            t.colors.append([None, None, None, None, None if gain is None else GREEN if gain >= 0 else RED])
        elif c.status == "open" and ipo.end:
            t.rows.append(
                [
                    ipo.symbol,
                    f"Open till {_day(ipo.end)}",
                    whole(ipo.price, Market.IN),
                    demand,
                    c.label or "—",
                ]
            )
            t.colors.append([])
        elif c.status == "upcoming" and ipo.start and (ipo.start - week_end).days <= 9:
            t.rows.append([ipo.symbol, f"Opens {_day(ipo.start)}", whole(ipo.price, Market.IN), "—", "—"])
            t.colors.append([])
        elif c.status == "closed" and ipo.listing_date:
            t.rows.append(
                [
                    ipo.symbol,
                    f"Lists {_day(ipo.listing_date)}",
                    whole(ipo.price, Market.IN),
                    demand,
                    c.label or "—",
                ]
            )
            t.colors.append([])
    return t if t.rows else None


def render_weekly(reports: list[Weekly]) -> bytes:
    """A4 pages at 150 dpi, one section per market; a long section continues on the next page."""
    pages = []
    for w in reports:
        c = Canvas(1240, 70)
        c.y = c.px(60)
        c.text(c.m, f"TradeMare weekly report · {NAMES.get(w.market, w.market.value)}", 46)
        c.y += c.px(66)
        c.text(c.m, f"Week ending {_day(w.week_end)} {w.week_end.year} · paper account", 26, MUTED)
        c.y += c.px(70)
        bench = "Nifty 50" if w.market is Market.IN else "S&P 500"
        c.stat_boxes(
            [
                ("Portfolio", whole(w.equity, w.market), INK),
                ("This week", pct(w.week_change), GREEN if (w.week_change or 0) >= 0 else RED),
                (bench, pct(w.benchmark_change), INK),
                ("Since start", pct(w.since_start), GREEN if (w.since_start or 0) >= 0 else RED),
            ],
            size=36,
        )
        for t in (w.holdings, w.trades, w.ipos):
            if t:
                c.table(t, size=26)
        if not (w.holdings or w.trades):
            c.text(c.m, "No holdings and no trades this week.", 26, MUTED)
            c.y += c.px(60)
        if w.coming:
            c.heading("Coming up", size=32)
            c.bullets(w.coming)
        page_h = 1754
        img = c.im.crop((0, 0, c.w, max(c.y + c.px(40), page_h)))
        for top in range(0, img.height, page_h):  # split a long section into A4 pages
            page = Image.new("RGB", (c.w, page_h), BG)
            page.paste(img.crop((0, top, c.w, min(top + page_h, img.height))), (0, 0))
            pages.append(page)
            if top + page_h >= c.y + c.px(40):
                break
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return out.getvalue()
