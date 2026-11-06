"use client";

import * as React from "react";
import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";

/** Motion tiers, the same speeds as the --primitive-motion-* tokens. */
const SPRING = {
  fast: { type: "spring", duration: 0.08, bounce: 0 },
  moderate: { type: "spring", duration: 0.16, bounce: 0 },
  slow: { type: "spring", duration: 0.24, bounce: 0 },
} as const;

/* ─── Types ─────────────────────────────────────────────────── */

export interface EmptyStateProps {
  /** Large icon or illustration displayed above the text. */
  icon?: React.ReactNode;
  /** Primary heading. */
  title: string;
  /** Supporting description beneath the title. */
  description?: string;
  /** Optional action slot. Pass a single button, a button group, or an input. */
  action?: React.ReactNode;
  /** Optional footer slot rendered below the action (links, secondary text). */
  footer?: React.ReactNode;
  /** Additional class name on the root wrapper. */
  className?: string;
}

/* ─── EmptyState ────────────────────────────────────────────── */

export function EmptyState({
  icon,
  title,
  description,
  action,
  footer,
  className,
}: EmptyStateProps) {
  const reduceMotion = useReducedMotion();

  return (
    <div data-wensity-primitive=""
      className={cn(
        "flex flex-col items-center justify-center gap-4 px-6 py-12 text-center",
        className,
      )}
    >
      {icon && (
        <motion.div
          initial={reduceMotion ? undefined : { opacity: 0, scale: 0.92 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={SPRING.moderate}
        >
          {icon}
        </motion.div>
      )}
      <div className="flex flex-col items-center gap-1.5">
        <p className="font-[family-name:var(--primitive-font-display)] text-[15px] font-semibold tracking-[-0.01em] text-[var(--foreground)]">
          {title}
        </p>
        {description && (
          <p className="max-w-[22rem] text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {description}
          </p>
        )}
      </div>
      {action && <div className="flex flex-wrap items-center justify-center gap-2">{action}</div>}
      {footer && <div>{footer}</div>}
    </div>
  );
}

/* ─── Illustrations (icon slots) ────────────────────────────── */

/** A subtle, larger icon container for the illustration variant. */
export function EmptyStateIllustration({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-surface="frame"
      className={cn(
        "flex size-16 items-center justify-center rounded-[var(--primitive-radius-surface)]",
        "text-[var(--muted-foreground)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

