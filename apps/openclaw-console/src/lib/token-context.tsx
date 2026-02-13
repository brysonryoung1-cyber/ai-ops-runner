"use client";

import { createContext, useContext } from "react";

/**
 * Token context — provides the console auth token to client components.
 *
 * The token is read server-side (layout.tsx) from OPENCLAW_CONSOLE_TOKEN
 * and passed to this client context provider. Client hooks (useExec)
 * include it in the X-OpenClaw-Token header on all API requests.
 *
 * This is safe because the console only serves pages on 127.0.0.1.
 */
const TokenContext = createContext<string>("");

export function TokenProvider({
  token,
  children,
}: {
  token: string;
  children: React.ReactNode;
}) {
  return (
    <TokenContext.Provider value={token}>{children}</TokenContext.Provider>
  );
}

export function useToken(): string {
  return useContext(TokenContext);
}
