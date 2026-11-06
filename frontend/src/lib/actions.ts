// Actions go through the classic dashboard's own form endpoints (same checks, same CSRF token).
// They answer with a redirect carrying ?msg= or ?err=; the outcome is read from the final URL.

export interface ActionResult { ok: boolean; message: string }

export async function postForm(url: string, csrf: string, fields: Record<string, string | number | boolean | string[] | undefined> = {}): Promise<ActionResult> {
  const body = new FormData();
  body.set("csrf_token", csrf);
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === "" || v === false) continue; // unticked checkboxes are not sent, as in a form
    if (Array.isArray(v)) v.forEach((x) => body.append(k, x));
    else body.set(k, v === true ? "on" : String(v));
  }
  try {
    const r = await fetch(url, { method: "POST", body });
    const final = new URL(r.url, location.origin);
    const err = final.searchParams.get("err");
    const msg = final.searchParams.get("msg");
    if (!r.ok) return { ok: false, message: `${r.status} ${r.statusText}` };
    if (err) return { ok: false, message: err };
    return { ok: true, message: msg ?? "Done" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : String(e) };
  }
}
