"use client";

import * as React from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useRender } from "@base-ui/react/use-render";
import { cn } from "@/lib/utils";

export type MarkerVariant = "dot" | "pulse" | "numbered" | "icon" | "status";
export type MarkerTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "brand";
export type MarkerSize = "sm" | "md" | "lg";

export interface MarkerProps
  extends React.HTMLAttributes<HTMLSpanElement> {
  /** Visual variant of the marker. */
  variant?: MarkerVariant;
  /** Semantic color tone. */
  tone?: MarkerTone;
  /** Size of the marker. */
  size?: MarkerSize;
  /** Number to display (only used with `variant="numbered"`). */
  number?: number;
  /** Icon to display (only used with `variant="icon"`). */
  icon?: React.ReactNode;
  /** Status label text (only used with `variant="status"`). */
  label?: React.ReactNode;
  asChild?: boolean;
}

const sizeMap: Record<MarkerSize, string> = {
  sm: "size-4",
  md: "size-5",
  lg: "size-6",
};

const dotInnerSize: Record<MarkerSize, string> = {
  sm: "size-1.5",
  md: "size-2",
  lg: "size-2.5",
};

const numberSize: Record<MarkerSize, string> = {
  sm: "text-[length:var(--primitive-text-hint)]",
  md: "text-[length:var(--primitive-text-hint)]",
  lg: "text-xs",
};

const iconPadding: Record<MarkerSize, string> = {
  sm: "p-0.5",
  md: "p-1",
  lg: "p-1",
};

const labelSize: Record<MarkerSize, string> = {
  sm: "text-[11px]",
  md: "text-xs",
  lg: "text-[13px]",
};

const tones: Record<MarkerTone, { bg: string; ring: string; text: string }> = {
  neutral: {
    bg: "bg-[var(--muted-foreground)]",
    ring: "bg-[var(--muted-foreground)]",
    text: "text-[var(--muted-foreground)]",
  },
  success: {
    bg: "bg-[var(--primitive-success)]",
    ring: "bg-[var(--primitive-success)]",
    text: "text-[var(--primitive-success)]",
  },
  warning: {
    bg: "bg-[var(--primitive-warning)]",
    ring: "bg-[var(--primitive-warning)]",
    text: "text-[var(--primitive-warning)]",
  },
  danger: {
    bg: "bg-[var(--primitive-destructive)]",
    ring: "bg-[var(--primitive-destructive)]",
    text: "text-[var(--primitive-destructive)]",
  },
  info: {
    bg: "bg-[var(--primitive-info)]",
    ring: "bg-[var(--primitive-info)]",
    text: "text-[var(--primitive-info)]",
  },
  brand: {
    bg: "bg-[var(--primitive-control-solid)]",
    ring: "bg-[var(--primitive-control-solid)]",
    text: "text-[var(--foreground)]",
  },
};

const baseDot = cn(
  "relative inline-flex shrink-0 items-center justify-center rounded-full",
);

const markerShadow =
  "shadow-[0_0_0_1px_color-mix(in_srgb,var(--foreground)_8%,transparent)] dark:shadow-[0_0_0_1px_color-mix(in_srgb,var(--foreground)_12%,transparent)]";

/**
 * Marker — Highlights positions, points, and statuses visually.
 *
 * Five variants:
 * - `dot` – Minimal circular marker for simple indicators
 * - `pulse` – Animated marker for live or active states
 * - `numbered` – Displays ordered positions using numeric markers
 * - `icon` – Uses contextual icons inside markers
 * - `status` – Represents states using semantic indicators with an optional label
 */
export const Marker = React.forwardRef<HTMLSpanElement, MarkerProps>(
  (
    {
      variant = "dot",
      tone = "neutral",
      size = "md",
      number,
      icon,
      label,
      asChild,
      className,
      children,
      ...props
    },
    ref,
  ) => {
    const shouldReduceMotion = useReducedMotion();
    const { bg, ring, text } = tones[tone];

    let composedClassName: string;
    let body: React.ReactNode;

    if (variant === "status") {
      composedClassName = cn(
        "inline-flex items-center gap-1.5 text-[var(--foreground)]",
        labelSize[size],
        className,
      );
      body = (
        <>
          <span className={cn(baseDot, sizeMap[size], bg, markerShadow)} />
          {label ? (
            <span className={cn("leading-none font-medium", text)}>{label}</span>
          ) : null}
        </>
      );
    } else if (variant === "numbered") {
      composedClassName = cn(
        baseDot,
        sizeMap[size],
        bg,
        "text-[var(--primitive-control-solid-foreground)]",
        markerShadow,
        numberSize[size],
        "font-semibold tabular-nums leading-none",
        className,
      );
      body = number ?? 1;
    } else if (variant === "icon") {
      composedClassName = cn(
        baseDot,
        sizeMap[size],
        iconPadding[size],
        bg,
        "text-[var(--primitive-control-solid-foreground)]",
        markerShadow,
        "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg]:size-full",
        className,
      );
      body = icon;
    } else if (variant === "pulse") {
      composedClassName = cn(
        "relative inline-flex shrink-0 items-center justify-center",
        sizeMap[size],
        className,
      );
      body = (
        <>
          {!shouldReduceMotion ? (
            <motion.span
              className={cn("absolute inset-0 rounded-full", ring)}
              animate={{
                scale: [1, 1.7, 1],
                opacity: [0.5, 0, 0.5],
              }}
              transition={{
                duration: 1.6,
                repeat: Infinity,
                ease: "easeInOut",
                repeatDelay: 0.2,
              }}
            />
          ) : null}
          <span
            className={cn(
              "relative z-10 rounded-full",
              dotInnerSize[size],
              bg,
              markerShadow,
            )}
          />
        </>
      );
    } else {
      composedClassName = cn(baseDot, sizeMap[size], bg, markerShadow, className);
      body = null;
    }

    const asChildElement =
      asChild && React.isValidElement(children) ? children : null;

    const asChildRendered = useRender({
      enabled: Boolean(asChildElement),
      ref: ref as React.Ref<HTMLElement>,
      render: asChildElement ?? undefined,
      props: {
        "data-slot": "marker",
        "data-variant": variant,
        "data-tone": tone,
        "data-size": size,
        className: composedClassName,
        children: body,
        ...props,
      },
    });

    if (asChildRendered) return asChildRendered;

    return (
      <span data-wensity-primitive=""
        ref={ref}
        data-slot="marker"
        data-variant={variant}
        data-tone={tone}
        data-size={size}
        className={composedClassName}
        {...props}
      >
        {body}
      </span>
    );
  },
);

Marker.displayName = "Marker";
