"use client";

type PillVariant = "default" | "success" | "warn" | "fail" | "info";

interface PillProps {
  children: React.ReactNode;
  variant?: PillVariant;
  className?: string;
}

const VARIANT_STYLES: Record<PillVariant, string> = {
  default: "bg-white/10 text-white/80 border-white/10",
  success: "bg-emerald-500/15 text-emerald-200 border-emerald-500/25",
  warn: "bg-amber-500/15 text-amber-200 border-amber-500/25",
  fail: "bg-red-500/15 text-red-200 border-red-500/25",
  info: "bg-blue-500/15 text-blue-200 border-blue-500/25",
};

export default function Pill({
  children,
  variant = "default",
  className = "",
}: PillProps) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-medium border ${VARIANT_STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
