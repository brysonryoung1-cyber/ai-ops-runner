"use client";

export default function EmptyArtifacts() {
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
            d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5"
          />
        </svg>
      </div>
      <p className="text-sm text-white/70">No artifact directories found.</p>
    </div>
  );
}
