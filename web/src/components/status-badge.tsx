"use client";

import { cn } from "@/lib/utils";

type BadgeVariant =
  | "completed"
  | "running"
  | "failed"
  | "timeout"
  | "info"
  | "warning"
  | "error"
  | "critical"
  | "pending";

const variantStyles: Record<BadgeVariant, string> = {
  completed: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25",
  running: "bg-blue-500/15 text-blue-600 border-blue-500/25",
  failed: "bg-red-500/15 text-red-600 border-red-500/25",
  timeout: "bg-amber-500/15 text-amber-600 border-amber-500/25",
  info: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25",
  warning: "bg-amber-500/15 text-amber-600 border-amber-500/25",
  error: "bg-red-500/15 text-red-600 border-red-500/25",
  critical: "bg-red-500/15 text-red-600 border-red-500/30",
  pending: "bg-zinc-500/15 text-zinc-600 border-zinc-500/25",
};

const dotStyles: Record<BadgeVariant, string> = {
  completed: "bg-emerald-400",
  running: "bg-blue-400 animate-pulse",
  failed: "bg-red-400",
  timeout: "bg-amber-400",
  info: "bg-emerald-400",
  warning: "bg-amber-400",
  error: "bg-red-400",
  critical: "bg-red-300",
  pending: "bg-zinc-400",
};

export function StatusBadge({
  status,
  className,
  dot = false,
}: {
  status: string;
  className?: string;
  dot?: boolean;
}) {
  const normalized = (status ?? "").toLowerCase();
  const variant = (normalized as BadgeVariant) in variantStyles
    ? (normalized as BadgeVariant)
    : "pending";
  const label = status ?? "unknown";

  if (dot) {
    return (
      <span
        className={cn("inline-block h-2 w-2 rounded-full", dotStyles[variant], className)}
        title={label}
      />
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium border",
        variantStyles[variant],
        className
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", dotStyles[variant])} />
      {label}
    </span>
  );
}
