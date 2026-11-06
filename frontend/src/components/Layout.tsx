import {
  IconBell, IconBook2, IconBriefcase, IconBulb, IconChartLine, IconFlask, IconHome, IconLayoutDashboard,
  IconMenu2, IconRocket, IconSettings, IconTarget, IconX,
} from "@tabler/icons-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { Badge } from "@/components/wensity/badge";
import { Sidebar, SidebarFooter, SidebarHeader, SidebarItem, SidebarNav, SidebarSection } from "@/components/wensity/sidebar";
import { ToggleGroup, ToggleGroupItem } from "@/components/wensity/toggle";
import { useChrome } from "@/lib/chrome";
import { applyDensity, applyTheme, getDensity, getTheme, type Density, type Theme } from "@/lib/theme";

const ICON = { size: 17, stroke: 1.8 };

function NavItem({ to, label, icon, badge }: { to: string; label: string; icon: React.ReactNode; badge?: number }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const active = to === "/" ? pathname === "/" : pathname === to || (pathname.startsWith(`${to}/`) && !/^\/m\/\w+$/.test(to));
  return (
    <SidebarItem
      href={`/app${to === "/" ? "/" : to}`}
      active={active}
      icon={icon}
      badge={badge ? <Badge size="sm" variant="info">{badge}</Badge> : undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // new tab etc.
        e.preventDefault();
        navigate(to);
      }}
    >
      {label}
    </SidebarItem>
  );
}

function StatusBar() {
  const { chrome } = useChrome();
  if (!chrome) return <div className="h-11" />;
  const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" });
  const time = (iso: string) => new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs sm:px-6">
      {chrome.statuses.map((s) => (
        <span key={s.market} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${s.behind ? "border-warning/50 text-warning" : "border-line text-muted-foreground"}`}>
          <span className={`size-1.5 rounded-full ${s.is_open ? "bg-good" : "bg-faint"}`} />
          <b className="text-text">{s.exchange}</b> {s.is_open ? "open" : "closed"}
          {s.behind && s.prices_through ? ` · prices from ${day(s.prices_through)}, ${s.behind} day${s.behind > 1 ? "s" : ""} behind`
            : s.next_run ? <span className="hidden md:inline"> · next update {time(s.next_run)}</span> : null}
        </span>
      ))}
      {chrome.login_needed && <span className="rounded-full border border-warning/50 px-2.5 py-1 text-warning">⚠ <b>Zerodha login needed</b></span>}
      {Object.entries(chrome.runs).filter(([, r]) => r.running).map(([code]) => (
        <span key={code} className="rounded-full border border-accent/40 px-2.5 py-1 text-accent">{code === "IN" ? "India" : "US"} updating…</span>
      ))}
      {chrome.pending_total > 0 && (
        <Link to="/signals" className="rounded-full border border-line px-2.5 py-1 text-muted-foreground no-underline hover:border-accent">
          <b className="text-accent">{chrome.pending_total}</b> signal{chrome.pending_total !== 1 ? "s" : ""} awaiting you
        </Link>
      )}
    </div>
  );
}

function Switches() {
  const [theme, setTheme] = useState<Theme>(getTheme());
  const [density, setDensity] = useState<Density>(getDensity());
  return (
    <div className="space-y-2 px-1 text-[11px] text-muted-foreground">
      <div>Theme</div>
      <ToggleGroup type="single" size="sm" className="grid w-full grid-cols-4" value={theme} onValueChange={(v) => { const t = (Array.isArray(v) ? v[0] : v) as Theme; if (t) { setTheme(t); applyTheme(t); } }}>
        {(["auto", "light", "dim", "dark"] as const).map((t) => <ToggleGroupItem key={t} value={t} className="px-0 text-[10.5px] capitalize">{t}</ToggleGroupItem>)}
      </ToggleGroup>
      <div>Density</div>
      <ToggleGroup type="single" size="sm" className="grid w-full grid-cols-2" value={density} onValueChange={(v) => { const d = (Array.isArray(v) ? v[0] : v) as Density; if (d) { setDensity(d); applyDensity(d); } }}>
        {(["comfortable", "compact"] as const).map((d) => <ToggleGroupItem key={d} value={d} className="px-1 text-[11px] capitalize">{d}</ToggleGroupItem>)}
      </ToggleGroup>
    </div>
  );
}

function Nav() {
  const { chrome } = useChrome();
  const markets = chrome?.markets ?? [];
  return (
    <SidebarNav>
      <SidebarSection>
        <NavItem to="/" label="Overview" icon={<IconHome {...ICON} />} />
        <NavItem to="/signals" label="Signals" icon={<IconBell {...ICON} />} badge={chrome?.pending_total} />
        <NavItem to="/journal" label="Trade journal" icon={<IconBook2 {...ICON} />} />
      </SidebarSection>
      {markets.map((m) => (
        <SidebarSection key={m.code} label={m.name}>
          <NavItem to={`/portfolio/${m.code}`} label="Portfolio" icon={<IconBriefcase {...ICON} />} />
          <NavItem to={`/m/${m.code}`} label={m.index} icon={<IconChartLine {...ICON} />} />
          <NavItem to={`/m/${m.code}/ideas`} label="Ideas" icon={<IconBulb {...ICON} />} />
          <NavItem to={`/setups/${m.code}`} label="Setups" icon={<IconTarget {...ICON} />} />
          {m.code === "IN" && <NavItem to="/ipos" label="IPOs" icon={<IconRocket {...ICON} />} />}
        </SidebarSection>
      ))}
      <SidebarSection label="Research">
        <NavItem to="/backtests" label="Backtests" icon={<IconFlask {...ICON} />} />
        <NavItem to="/settings" label="Settings" icon={<IconSettings {...ICON} />} />
      </SidebarSection>
    </SidebarNav>
  );
}

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2 font-semibold text-text no-underline">
      <span className="grid size-7 place-items-center rounded-lg bg-accent text-sm text-white dark:text-bg">T</span>
      TradeMare
    </Link>
  );
}

export function Layout({ children }: { children: React.ReactNode }) {
  const { chrome } = useChrome();
  const { pathname, search } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const classicHref = `${pathname}${search}`;
  return (
    <div className="flex min-h-dvh bg-bg">
      <div className="sticky top-0 hidden h-dvh shrink-0 md:block">
        <Sidebar width={236} aria-label="Main" className="h-full overflow-y-auto border-r border-line">
          <SidebarHeader><Brand /></SidebarHeader>
          <Nav />
          <SidebarFooter className="!flex-col !items-stretch gap-3">
            <Switches />
            <a href={classicHref} className="flex items-center gap-1.5 px-1 text-[12px] text-accent">
              <IconLayoutDashboard size={14} /> Classic UI for this page
            </a>
            <p className="px-1 text-[11px] text-faint">v{chrome?.version} · paper trading unless a market is validated · not financial advice</p>
          </SidebarFooter>
        </Sidebar>
      </div>
      <div className="min-w-0 flex-1">
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5 md:hidden">
          <Brand />
          <button type="button" aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)} className="grid size-11 place-items-center rounded-lg text-text">
            {menuOpen ? <IconX size={20} /> : <IconMenu2 size={20} />}
          </button>
        </header>
        {menuOpen && (
          <div className="border-b border-line bg-surface p-3 md:hidden" onClick={() => setMenuOpen(false)}>
            <Sidebar width={9999} aria-label="Main" className="w-full border-0 bg-transparent"><Nav /><SidebarFooter><Switches /></SidebarFooter></Sidebar>
          </div>
        )}
        <StatusBar />
        <main className="mx-auto max-w-6xl px-4 py-7 sm:px-6">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-faint sm:px-6">
          TradeMare {chrome?.version} · data from a local cache · not financial advice ·{" "}
          <a href={classicHref} className="text-accent">open this page in the classic UI</a>
        </footer>
      </div>
    </div>
  );
}

/** A page not moved to React yet: say so and link to the classic version. */
export function ClassicOnly() {
  const { pathname, search } = useLocation();
  return (
    <div className="rounded-card border border-line bg-surface p-6">
      <h1 className="m-0 text-xl font-semibold">Not in the new UI yet</h1>
      <p className="text-muted-foreground">This page is being moved to the new UI. It works in the classic dashboard meanwhile.</p>
      <a href={`${pathname}${search}`} className="font-semibold text-accent">Open it in the classic UI →</a>
    </div>
  );
}
