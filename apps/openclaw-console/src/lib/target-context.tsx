"use client";

import { createContext, useContext } from "react";

/**
 * Target context — provides the active Tailscale target info to client components.
 *
 * Read server-side from ~/.config/openclaw/targets.json in layout.tsx
 * and passed to client components via this context.
 */

export interface TargetInfo {
  name: string;
  host: string;
}

const TargetContext = createContext<TargetInfo | null>(null);

export function TargetProvider({
  target,
  children,
}: {
  target: TargetInfo | null;
  children: React.ReactNode;
}) {
  return (
    <TargetContext.Provider value={target}>{children}</TargetContext.Provider>
  );
}

export function useTarget(): TargetInfo | null {
  return useContext(TargetContext);
}
