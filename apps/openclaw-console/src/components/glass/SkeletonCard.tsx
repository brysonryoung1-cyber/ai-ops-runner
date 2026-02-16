"use client";

export default function SkeletonCard() {
  return (
    <div className="glass-surface rounded-2xl p-5 animate-pulse">
      <div className="h-4 bg-white/10 rounded w-3/4 mb-3" />
      <div className="h-3 bg-white/10 rounded w-full mb-4" />
      <div className="h-8 bg-white/10 rounded w-1/2" />
    </div>
  );
}
