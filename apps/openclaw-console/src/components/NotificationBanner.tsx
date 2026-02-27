"use client";

import { useState, useEffect } from "react";

interface Banner {
  type: string;
  created_at: string;
  novnc_url?: string;
  instruction?: string;
  failed_invariant?: string;
  proof_paths?: string[];
  message?: string;
}

export default function NotificationBanner() {
  const [banner, setBanner] = useState<Banner | null>(null);

  useEffect(() => {
    fetch("/api/notifications/banner")
      .then((r) => r.json())
      .then((d) => {
        if (d?.banner) setBanner(d.banner);
        else setBanner(null);
      })
      .catch(() => setBanner(null));
  }, []);

  if (!banner) return null;

  if (banner.type === "WAITING_FOR_HUMAN") {
    return (
      <div
        data-testid="notification-banner-waiting"
        className="bg-amber-500/15 border-b border-amber-500/30 px-4 py-3"
      >
        <p className="text-sm text-amber-200 font-medium">Human action required</p>
        {banner.instruction && (
          <p className="text-xs text-amber-100/90 mt-1">{banner.instruction}</p>
        )}
        {banner.novnc_url && (
          <a
            href={banner.novnc_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block mt-2 text-xs font-medium text-amber-300 hover:text-amber-200 underline"
          >
            Open noVNC →
          </a>
        )}
      </div>
    );
  }

  if (banner.type === "CANARY_DEGRADED") {
    return (
      <div
        data-testid="notification-banner-canary"
        className="bg-red-500/15 border-b border-red-500/30 px-4 py-3"
      >
        <p className="text-sm text-red-200 font-medium">Canary degraded</p>
        {banner.failed_invariant && (
          <p className="text-xs text-red-100/90 mt-1">
            Failing: {banner.failed_invariant}
          </p>
        )}
        {banner.proof_paths && banner.proof_paths.length > 0 && (
          <p className="text-xs text-red-100/80 mt-1">
            Proof: {banner.proof_paths[0]}
          </p>
        )}
      </div>
    );
  }

  return null;
}
