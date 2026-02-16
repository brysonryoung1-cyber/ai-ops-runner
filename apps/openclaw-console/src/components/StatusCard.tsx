"use client";

interface StatusCardProps {
  title: string;
  status: "pass" | "fail" | "loading" | "idle" | "warn";
  subtitle?: string;
  children?: React.ReactNode;
}

const STATUS_CONFIG = {
  pass: { dot: "bg-emerald-500", label: "PASS", labelColor: "text-emerald-400" },
  fail: { dot: "bg-red-500", label: "FAIL", labelColor: "text-red-400" },
  warn: { dot: "bg-amber-500", label: "WARN", labelColor: "text-amber-400" },
  loading: { dot: "bg-blue-400 animate-pulse-dot", label: "Running…", labelColor: "text-blue-400" },
  idle: { dot: "bg-white/30", label: "—", labelColor: "text-white/50" },
};

export default function StatusCard({
  title,
  status,
  subtitle,
  children,
}: StatusCardProps) {
  const cfg = STATUS_CONFIG[status];

  return (
    <div className="glass-surface rounded-2xl p-5">
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="text-sm font-semibold text-white/95">{title}</h3>
          {subtitle && <p className="text-xs text-white/60 mt-0.5">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
          <span className={`text-xs font-medium ${cfg.labelColor}`}>{cfg.label}</span>
        </div>
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
