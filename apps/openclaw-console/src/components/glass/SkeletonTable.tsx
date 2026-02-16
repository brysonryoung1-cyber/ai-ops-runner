"use client";

interface SkeletonTableProps {
  rows?: number;
  cols?: number;
}

export default function SkeletonTable({ rows = 5, cols = 4 }: SkeletonTableProps) {
  return (
    <div className="rounded-2xl overflow-hidden glass-surface">
      <div className="px-5 py-3 border-b border-white/10">
        <div className="h-4 bg-white/10 rounded w-1/3 animate-pulse" />
      </div>
      <div className="divide-y divide-white/5">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="px-5 py-4 flex gap-4">
            {Array.from({ length: cols }).map((_, j) => (
              <div
                key={j}
                className="h-4 bg-white/10 rounded animate-pulse flex-1"
                style={{ maxWidth: j === cols - 1 ? "80px" : undefined }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
