"use client";

import StatusDot from "./StatusDot";
import type { StatusDotVariant } from "./StatusDot";

interface MetricTileProps {
  label: string;
  value: string | React.ReactNode;
  status?: StatusDotVariant;
  subtitle?: string;
  className?: string;
}

export default function MetricTile({
  label,
  value,
  status,
  subtitle,
  className = "",
}: MetricTileProps) {
  return (
    <div className={`p-4 rounded-xl bg-white/5 border border-white/5 ${className}`}>
      <p className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1">
        {label}
      </p>
      <div className="flex items-center gap-2">
        {status && <StatusDot variant={status} />}
        <div>
          <p className="text-sm font-medium text-white/95">{value}</p>
          {subtitle && (
            <p className="text-xs text-white/50 mt-0.5">{subtitle}</p>
          )}
        </div>
      </div>
    </div>
  );
}
