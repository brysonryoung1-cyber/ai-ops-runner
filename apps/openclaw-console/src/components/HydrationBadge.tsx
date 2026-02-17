"use client";

import { useEffect, useState } from "react";

/**
 * Hydration / JS health indicator.
 *
 * Renders "Client: Pending" on SSR. After hydration + useEffect,
 * flips to "Client: Active". If it never flips (~2s timeout), a
 * degraded-mode banner appears. Navigation still works via <a> tags.
 */
export default function HydrationBadge() {
  const [hydrated, setHydrated] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) return;
    const timer = setTimeout(() => {
      setTimedOut(true);
    }, 2000);
    return () => clearTimeout(timer);
  }, [hydrated]);

  return (
    <>
      <span
        data-testid="hydration-badge"
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-medium border ${
          hydrated
            ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/20"
            : "bg-amber-500/15 text-amber-300 border-amber-500/20"
        }`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            hydrated ? "bg-emerald-400" : "bg-amber-400 animate-pulse"
          }`}
        />
        {hydrated ? "Client: Active" : "Client: Pending"}
      </span>
      {timedOut && !hydrated && (
        <div
          role="alert"
          className="fixed top-0 left-0 right-0 z-50 bg-amber-600/95 text-white text-xs text-center py-2 px-4 backdrop-blur-sm"
        >
          UI not fully active (client JS failed). Navigation links still work; actions may be limited.
        </div>
      )}
    </>
  );
}
