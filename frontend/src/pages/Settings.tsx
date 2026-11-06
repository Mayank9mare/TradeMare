/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";

import { Empty } from "@/components/bits";
import { Button } from "@/components/wensity/button";
import { Card } from "@/components/wensity/card";
import { SkeletonCard } from "@/components/wensity/skeleton";
import { useToastManager } from "@/components/wensity/toast";
import { useChrome } from "@/lib/chrome";
import { usePage } from "@/lib/page";

// The classic Settings page (templates/settings.html). Saving posts the same form to /settings,
// with the same validation; broker and `validated` are not on the form, as in the classic UI.

interface Field { key: string; label: string; kind: "bool" | "choice" | "float" | "int" | "emails" | string; choices: string[]; help: string }

const show = (t: any) => (t === true ? "on" : t === false ? "off" : t == null ? "blank" : String(t));
const isOn = (v: any) => v === true || v === "on" || v === "true";

function FieldRow({ name, f, value, tested, drift, onChange }: { name: string; f: Field; value: any; tested: Record<string, any>; drift: string[]; onChange: (v: any) => void }) {
  const known = name in tested, differs = drift.includes(name);
  const label = (
    <span className="min-w-0">
      {f.label}{f.help && <small className="text-muted-foreground"> — {f.help}</small>}
      {known && <small className={`ml-1 rounded-full px-1.5 py-0.5 ${differs ? "bg-warning/15 text-warning" : "bg-good/10 text-up"}`}>{differs ? "differs from" : "as"} tested{differs && ` (${show(tested[name])})`}</small>}
    </span>
  );
  const input = "w-40 max-w-full rounded-tile border border-line bg-surface px-2.5 py-1.5 text-[13px]";
  return (
    <label className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-t border-line py-2.5 text-[13px] first:border-t-0 ${differs ? "-mx-2 rounded-tile bg-warning/5 px-2" : ""}`}>
      {label}
      {f.kind === "bool" ? <input type="checkbox" checked={isOn(value)} onChange={(e) => onChange(e.target.checked)} />
        : f.kind === "choice" ? <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className={input}>{f.choices.map((c) => <option key={c}>{c}</option>)}</select>
        : f.kind === "emails" ? <input type="text" value={Array.isArray(value) ? value.join(", ") : value ?? ""} onChange={(e) => onChange(e.target.value)} className={`${input} w-72`} />
        : <input type="text" inputMode="decimal" value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={input} />}
    </label>
  );
}

export function Settings() {
  const { data, error, reload } = usePage<Record<string, any>>("/settings");
  const { chrome } = useChrome();
  const toasts = useToastManager();
  const [values, setValues] = useState<Record<string, any> | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setValues({ ...data.values }); }, [data]);
  if (error) return <Empty>Could not load settings: {error}</Empty>;
  if (!data || !values) return <SkeletonCard />;
  const mf = data.market_fields as Field[], gf = data.global_fields as Field[], tested = data.tested ?? {}, drift = data.drift ?? [];
  const set = (k: string) => (v: any) => setValues((old) => ({ ...old!, [k]: v }));

  const save = async () => {
    if (!chrome) return;
    setBusy(true);
    setErrors([]);
    const body = new FormData();
    body.set("csrf_token", chrome.csrf_token);
    const add = (name: string, f: Field) => {
      const v = values[name];
      if (f.kind === "bool") { if (isOn(v)) body.set(name, "on"); return; }
      body.set(name, Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v));
    };
    for (const m of data.market_codes as string[]) for (const f of mf) add(`${m}.${f.key}`, f);
    for (const f of gf) add(f.key, f);
    try {
      const r = await fetch("/settings?format=json", { method: "POST", body });
      const final = new URL(r.url, location.origin);
      if (final.searchParams.get("saved") !== null) {
        const msg = final.searchParams.get("saved")!;
        setSaved(msg);
        toasts.add({ title: "Settings saved", data: { variant: "success" } });
        reload();
      } else {
        const d = await r.json().catch(() => null);
        setErrors(d?.errors ?? [`${r.status} ${r.statusText}`]);
        toasts.add({ title: "Not saved", data: { variant: "error" } });
      }
    } catch (e) {
      setErrors([e instanceof Error ? e.message : String(e)]);
    }
    setBusy(false);
  };

  return (
    <>
      <h1 className="m-0 text-[26px] font-bold tracking-tight">Settings</h1>
      {(saved ?? data.saved) && <div className="mt-3 rounded-tile border border-good/40 px-4 py-2.5 text-[13px]">Saved to <code>config/settings.local.yaml</code>: {saved ?? data.saved}. The daily job uses it from its next run; <b>restart <code>trademare serve</code></b> for the dashboard and approvals to use it.</div>}
      {errors.length > 0 && <div className="mt-3 rounded-tile border border-critical/40 px-4 py-2.5 text-[13px] text-down">Not saved:<ul className="mt-1 mb-0 list-disc pl-5">{errors.map((e) => <li key={e}>{e}</li>)}</ul></div>}
      <p className="text-[13px] text-muted-foreground">Changes are validated, then written to your git-ignored <code>config/settings.local.yaml</code>. <b className="text-text">Broker</b> and <b className="text-text">validated</b> can only be changed in the files, so live trading can't be switched on from a web form.</p>
      <p className="text-[13px] text-muted-foreground">Settings marked <span className="rounded-full bg-good/10 px-1.5 py-0.5 text-up">as tested</span> are the ones the India strategy passed its history test with. Changing them means trading a version that was never tested.</p>
      {data.untested_list?.length > 0 && <div className="mb-4 rounded-tile border border-warning/50 px-4 py-2.5 text-[13px] text-warning">These differ from the tested strategy: {data.untested_list.join("; ")}.</div>}
      <div className="grid gap-4 lg:grid-cols-2">
        {(data.market_codes as string[]).map((m) => (
          <Card key={m} className="p-5">
            <h2 className="m-0 mb-2 text-base font-semibold">{m === "IN" ? "India" : "US"} <small className="font-normal text-muted-foreground">broker {data.on_disk.markets[m].broker} · {data.on_disk.markets[m].validated ? "validated" : "not validated"}</small></h2>
            {mf.map((f) => <FieldRow key={f.key} name={`${m}.${f.key}`} f={f} value={values[`${m}.${f.key}`]} tested={tested} drift={drift} onChange={set(`${m}.${f.key}`)} />)}
          </Card>
        ))}
      </div>
      <Card className="mt-4 p-5">
        <h2 className="m-0 mb-2 text-base font-semibold">General</h2>
        {gf.map((f) => <FieldRow key={f.key} name={f.key} f={f} value={values[f.key]} tested={tested} drift={drift} onChange={set(f.key)} />)}
      </Card>
      <div className="my-4"><Button loading={busy} onClick={save}>Save settings</Button></div>
      <h2 className="mt-8 mb-1 text-lg font-semibold">Effective configuration</h2>
      <p className="mt-0 text-[13px] text-muted-foreground"><code>config/settings.yaml</code> merged with <code>settings.local.yaml</code>, as on disk now.</p>
      <pre className="overflow-x-auto rounded-card border border-line bg-surface p-4 text-xs">{data.config_yaml}</pre>
    </>
  );
}
