"use client";

import React from "react";

interface ErrorBoundaryState {
  hasError: boolean;
  errorMessage: string | null;
}

/**
 * Global error boundary for OpenClaw HQ.
 *
 * Catches unhandled React errors, shows a user-friendly banner,
 * and redacts any potentially sensitive info from the message.
 * Navigation links still work because they're real <a> tags.
 */
export default class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, errorMessage: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    const raw = error.message || "Unknown error";
    const redacted = raw
      .replace(/token[\s:=]+[\w-]+/gi, "[REDACTED]")
      .replace(/password[\s:=]+[^\s]+/gi, "[REDACTED]")
      .replace(/api[_-]?key[\s:=]+[\w-]+/gi, "[REDACTED]")
      .replace(/bearer\s+[\w.-]+/gi, "[REDACTED]")
      .slice(0, 200);
    return { hasError: true, errorMessage: redacted };
  }

  componentDidCatch(error: Error) {
    try {
      const raw = (error.message || "").slice(0, 200);
      const redacted = raw
        .replace(/token[\s:=]+[\w-]+/gi, "[REDACTED]")
        .replace(/password[\s:=]+[^\s]+/gi, "[REDACTED]")
        .replace(/api[_-]?key[\s:=]+[\w-]+/gi, "[REDACTED]")
        .replace(/bearer\s+[\w.-]+/gi, "[REDACTED]")
        .replace(/[\w.-]+@[\w.-]+\.\w+/g, "[REDACTED]");
      fetch("/api/ui/telemetry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event: "error",
          page: typeof window !== "undefined" ? window.location.pathname : "unknown",
          control: "ErrorBoundary",
          detail: redacted,
        }),
      }).catch(() => {});
    } catch {
      // Fire-and-forget
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-6">
          <div className="p-4 rounded-2xl glass-surface border border-red-500/20 max-w-2xl mx-auto">
            <p className="text-sm font-semibold text-red-300">Something went wrong</p>
            <p className="text-xs text-red-200/80 mt-1">
              {this.state.errorMessage}
            </p>
            <p className="text-xs text-white/60 mt-3">
              Navigation links still work. Try clicking a sidebar link to continue.
            </p>
            <button
              onClick={() => this.setState({ hasError: false, errorMessage: null })}
              className="mt-3 px-3 py-1.5 text-xs font-medium rounded-xl bg-white/10 hover:bg-white/15 text-white/90 border border-white/10"
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
