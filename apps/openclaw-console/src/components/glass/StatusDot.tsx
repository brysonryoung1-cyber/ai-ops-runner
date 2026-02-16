"use client";

export type StatusDotVariant = "pass" | "fail" | "warn" | "loading" | "idle";

interface StatusDotProps {
  variant: StatusDotVariant;
  className?: string;
}

const VARIANT_STYLES: Record<StatusDotVariant, string> = {
  pass: "bg-emerald-500",
  fail: "bg-red-500",
  warn: "bg-amber-500",
  loading: "bg-blue-400 animate-pulse-dot",
  idle: "bg-white/30",
};

export default function StatusDot({ variant, className = "" }: StatusDotProps) {
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${VARIANT_STYLES[variant]} ${className}`}
      aria-hidden
    />
  );
}
