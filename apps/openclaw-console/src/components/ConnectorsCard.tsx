"use client";

/** Result from run route or legacy exec (for status display). */
export type ConnectorResult = {
  ok: boolean;
  stdout?: string;
  result_summary?: { kajabi?: string; gmail?: string } | Record<string, unknown>;
  run_id?: string;
  artifact_dir?: string;
  error_class?: string;
  message?: string;
  next_steps?: { instruction?: string; verification_url?: string | null; user_code?: string | null };
};

export function parseConnectorsStatus(
  stdout: string | undefined,
  resultSummary?: ConnectorResult["result_summary"]
): {
  kajabi: "connected" | "not_connected" | "unknown";
  gmail: "connected" | "not_connected" | "unknown";
} {
  if (resultSummary && typeof resultSummary === "object" && "kajabi" in resultSummary) {
    const d = resultSummary as { kajabi?: string; gmail?: string };
    return {
      kajabi: d.kajabi === "connected" ? "connected" : "not_connected",
      gmail: d.gmail === "connected" ? "connected" : "not_connected",
    };
  }
  if (!stdout?.trim()) return { kajabi: "unknown", gmail: "unknown" };
  try {
    const d = JSON.parse(stdout.trim());
    return {
      kajabi: d.kajabi === "connected" ? "connected" : "not_connected",
      gmail: d.gmail === "connected" ? "connected" : "not_connected",
    };
  } catch {
    return { kajabi: "unknown", gmail: "unknown" };
  }
}

interface ConnectorsCardProps {
  /** Status result (soma_connectors_status) for kajabi/gmail badges */
  result?: ConnectorResult;
  /** Which action is currently loading (enables per-button loading state) */
  loadingAction: string | null;
  onExec: (action: string) => void;
  /** Next steps from bootstrap start to show (instruction, verification_url, user_code) */
  nextStepsBootstrap?: ConnectorResult["next_steps"];
  /** Next steps from gmail connect start */
  nextStepsGmail?: ConnectorResult["next_steps"];
  variant?: "glass" | "apple";
}

const CONNECTOR_ACTIONS: { key: string; label: string; loadingLabel: string; primary?: boolean }[] = [
  { key: "soma_connectors_status", label: "Check Connectors", loadingLabel: "Checking…" },
  { key: "soma_kajabi_bootstrap_start", label: "Kajabi Bootstrap", loadingLabel: "Starting…", primary: true },
  { key: "soma_kajabi_gmail_connect_start", label: "Gmail Connect", loadingLabel: "Starting…", primary: true },
  { key: "soma_connectors_status", label: "Refresh status", loadingLabel: "Refreshing…" },
  { key: "soma_kajabi_bootstrap_finalize", label: "Kajabi Finalize", loadingLabel: "Finalizing…" },
  { key: "soma_kajabi_gmail_connect_finalize", label: "Gmail Finalize", loadingLabel: "Finalizing…" },
];

export default function ConnectorsCard({
  result,
  loadingAction,
  onExec,
  nextStepsBootstrap,
  nextStepsGmail,
  variant = "apple",
}: ConnectorsCardProps) {
  const status = parseConnectorsStatus(result?.stdout, result?.result_summary);

  const cardClass =
    variant === "glass"
      ? "mb-6 p-4 rounded-2xl glass-surface border border-white/10"
      : "mb-8 p-4 rounded-apple bg-apple-card border border-apple-border";
  const titleClass =
    variant === "glass"
      ? "text-sm font-semibold text-white/95 mb-3"
      : "text-sm font-semibold text-apple-text mb-3";
  const labelClass = variant === "glass" ? "text-sm text-white/70" : "text-sm text-apple-muted";
  const btnBase =
    variant === "glass"
      ? "px-3 py-1.5 text-xs font-medium rounded hover:opacity-90 disabled:opacity-50"
      : "px-3 py-1.5 text-xs font-medium rounded hover:bg-gray-200 disabled:opacity-50";
  const statusLoading = loadingAction === "soma_connectors_status";

  return (
    <div className={cardClass}>
      <h3 className={titleClass}>Connectors</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="flex items-center justify-between">
          <span className={labelClass}>Kajabi</span>
          <span
            className={`text-xs font-medium px-2 py-1 rounded ${
              status.kajabi === "connected"
                ? "bg-green-100 text-green-700"
                : status.kajabi === "not_connected"
                  ? "bg-amber-100 text-amber-700"
                  : "bg-gray-100 text-gray-600"
            }`}
          >
            {statusLoading ? "Checking…" : status.kajabi === "connected" ? "Connected" : status.kajabi === "not_connected" ? "Not connected" : "—"}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className={labelClass}>Gmail</span>
          <span
            className={`text-xs font-medium px-2 py-1 rounded ${
              status.gmail === "connected"
                ? "bg-green-100 text-green-700"
                : status.gmail === "not_connected"
                  ? "bg-amber-100 text-amber-700"
                  : "bg-gray-100 text-gray-600"
            }`}
          >
            {statusLoading ? "Checking…" : status.gmail === "connected" ? "Connected" : status.gmail === "not_connected" ? "Not connected" : "—"}
          </span>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {CONNECTOR_ACTIONS.map(({ key, label, loadingLabel, primary }, i) => (
          <button
            key={`${key}-${i}`}
            type="button"
            onClick={() => onExec(key)}
            disabled={!!loadingAction}
            className={`${btnBase} ${primary ? (variant === "glass" ? "bg-blue-500/30 text-blue-200" : "bg-blue-50 text-blue-700") : variant === "glass" ? "bg-white/10 text-white/90" : "bg-gray-100"}`}
          >
            {loadingAction === key ? loadingLabel : label}
          </button>
        ))}
      </div>
      {(nextStepsBootstrap?.instruction || nextStepsBootstrap?.verification_url || nextStepsBootstrap?.user_code) && (
        <div className={`mt-3 p-2 rounded text-xs ${variant === "glass" ? "bg-white/5 text-white/80" : "bg-gray-50 text-gray-700"}`}>
          <p className="font-medium mb-1">Kajabi next steps</p>
          {nextStepsBootstrap.instruction && <p>{nextStepsBootstrap.instruction}</p>}
          {nextStepsBootstrap.verification_url && <p className="mt-1 break-all">URL: {nextStepsBootstrap.verification_url}</p>}
          {nextStepsBootstrap.user_code && <p>Code: {nextStepsBootstrap.user_code}</p>}
        </div>
      )}
      {(nextStepsGmail?.instruction || nextStepsGmail?.verification_url || nextStepsGmail?.user_code) && (
        <div className={`mt-3 p-2 rounded text-xs ${variant === "glass" ? "bg-white/5 text-white/80" : "bg-gray-50 text-gray-700"}`}>
          <p className="font-medium mb-1">Gmail next steps</p>
          {nextStepsGmail.instruction && <p>{nextStepsGmail.instruction}</p>}
          {nextStepsGmail.verification_url && <p className="mt-1 break-all">URL: {nextStepsGmail.verification_url}</p>}
          {nextStepsGmail.user_code && <p>Code: {nextStepsGmail.user_code}</p>}
        </div>
      )}
    </div>
  );
}
