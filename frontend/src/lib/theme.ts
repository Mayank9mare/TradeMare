// Theme and density, shared with the classic dashboard (same localStorage keys and attributes).
export type Theme = "auto" | "light" | "dim" | "dark";
export type Density = "comfortable" | "compact";

const read = (k: string, fallback: string) => { try { return localStorage.getItem(k) || fallback; } catch { return fallback; } };
const save = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } };

export const getTheme = () => read("tm-theme", "auto") as Theme;
export const getDensity = () => read("tm-density", "comfortable") as Density;

export function applyTheme(theme: Theme) {
  save("tm-theme", theme);
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
export function applyDensity(density: Density) {
  save("tm-density", density);
  if (density === "compact") document.documentElement.dataset.density = "compact";
  else delete document.documentElement.dataset.density;
}
