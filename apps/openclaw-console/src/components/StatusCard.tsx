"use client";

interface StatusCardProps {
  title: string;
  status: "pass" | "fail" | "loading" | "idle" | "warn";
  subtitle?: string;
  children?: React.ReactNode;
}

const STATUS_CONFIG = {
  pass: {
    dot: "bg-apple-green",
    label: "PASS",
    labelColor: "text-apple-green",
  },
  fail: {
    dot: "bg-apple-red",
    label: "FAIL",
    labelColor: "text-apple-red",
  },
  warn: {
    dot: "bg-apple-orange",
    label: "WARN",
    labelColor: "text-apple-orange",
  },
  loading: {
    dot: "bg-apple-blue animate-pulse-dot",
    label: "Running…",
    labelColor: "text-apple-blue",
  },
  idle: {
    dot: "bg-apple-border",
    label: "—",
    labelColor: "text-apple-muted",
  },
};

export default function StatusCard({
  title,
  status,
  subtitle,
  children,
}: StatusCardProps) {
  const cfg = STATUS_CONFIG[status];

  return (
    <div className="bg-apple-card rounded-apple border border-apple-border shadow-apple p-5">
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="text-sm font-semibold text-apple-text">{title}</h3>
          {subtitle && (
            <p className="text-xs text-apple-muted mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
          <span className={`text-xs font-medium ${cfg.labelColor}`}>
            {cfg.label}
          </span>
        </div>
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}
