import type { Metadata } from "next";
import { readFileSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import "./globals.css";
import Shell from "@/components/Shell";
import { TokenProvider } from "@/lib/token-context";
import { TargetProvider, type TargetInfo } from "@/lib/target-context";

export const metadata: Metadata = {
  title: "OpenClaw HQ",
  description: "Private control panel for all OpenClaw projects on aiops-1",
};

/**
 * Read the active target from ~/.config/openclaw/targets.json.
 * Returns null if file is missing or invalid (graceful fallback).
 */
function getActiveTarget(): TargetInfo | null {
  try {
    const targetsPath = join(homedir(), ".config", "openclaw", "targets.json");
    const raw = readFileSync(targetsPath, "utf-8");
    const data = JSON.parse(raw);
    const active = data?.active;
    if (active && data?.targets?.[active]) {
      const t = data.targets[active];
      return { name: active, host: t.host || "" };
    }
  } catch {
    // File doesn't exist or invalid — that's fine
  }
  return null;
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Server-side: read token and active target
  const token = process.env.OPENCLAW_CONSOLE_TOKEN || "";
  const target = getActiveTarget();

  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <TokenProvider token={token}>
          <TargetProvider target={target}>
            <Shell>{children}</Shell>
          </TargetProvider>
        </TokenProvider>
      </body>
    </html>
  );
}
