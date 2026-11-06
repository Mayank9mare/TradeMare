import { Badge } from "@/components/wensity/badge";

const NAMES: Record<string, string> = { risk_on: "Risk-on", neutral: "Neutral", risk_off: "Risk-off" };

/** The market regime label and score (classic regime_badge). */
export function RegimeBadge({ regime }: { regime: { label: string; score: number } | null | undefined }) {
  if (!regime) return null;
  const variant = regime.label === "risk_on" ? "success" : regime.label === "risk_off" ? "danger" : "neutral";
  return <Badge size="sm" variant={variant}>{NAMES[regime.label] ?? regime.label} {regime.score >= 0 ? "+" : ""}{regime.score.toFixed(2)}</Badge>;
}
