"use client";

interface EmptyRunsProps {
  projectFilter?: string | null;
}

export default function EmptyRuns({ projectFilter }: EmptyRunsProps) {
  return (
    <div className="glass-surface rounded-2xl p-12 text-center">
      <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-white/5 flex items-center justify-center">
        <svg
          className="w-6 h-6 text-white/40"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
      </div>
      <p className="text-sm text-white/70">
        {projectFilter
          ? `No runs found for project "${projectFilter}".`
          : "No runs recorded yet. Execute an action to see run history."}
      </p>
    </div>
  );
}
