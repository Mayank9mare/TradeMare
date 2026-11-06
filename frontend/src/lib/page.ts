import { useCallback, useEffect, useState } from "react";

import { getJSON } from "@/lib/api";

/** A classic page's data as JSON (`<path>?format=json`): both UIs render the same context. */
export function usePage<T>(classicPath: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    const ctl = new AbortController();
    const sep = classicPath.includes("?") ? "&" : "?";
    getJSON<T>(`${classicPath}${sep}format=json`, ctl.signal).then(
      (d) => { setData(d); setError(null); },
      (e: unknown) => { if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => ctl.abort();
  }, [classicPath, version]);
  return { data, error, reload };
}
