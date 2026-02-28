/**
 * Soma Last Run Resolver — single source of truth for latest Soma outcome.
 *
 * Used by: status API, autopilot_status, Overview/Runs UI, soma_fix_and_retry.
 * Always returns artifact links even when stderr is empty by reading:
 *   - RESULT.json in run dir
 *   - hostd_result.json + stdout.txt fallback
 *   - stage.json/state.json fallback
 *   - PROOF.json from run_to_done
 */

import { existsSync, readFileSync, readdirSync, mkdirSync, writeFileSync } from "fs";
import { join } from "path";
import { toCanonicalNovncUrl } from "./novnc-url";

export interface SomaLastRunResolved {
  status: "SUCCESS" | "WAITING_FOR_HUMAN" | "FAILURE" | "TIMEOUT" | "BLOCKED" | "UNKNOWN" | "running";
  error_class: string | null;
  run_id: string | null;
  artifact_dir: string | null;
  novnc_url: string | null;
  novnc_url_legacy: string | null;
  browser_gateway_url: string | null;
  instruction_line: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** Links to key artifacts (relative paths) */
  artifact_links: {
    stdout?: string;
    stderr?: string;
    result_json?: string;
    crash_json?: string;
    framebuffer?: string;
    ws_check?: string;
    timings?: string;
    proof_json?: string;
  };
}

const SOMA_ACTIONS = ["soma_run_to_done", "soma_kajabi_auto_finish"] as const;

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getRunsDir(): string {
  const root = getArtifactsRoot();
  return join(root, "runs");
}

function parseRunRecord(raw: string): Record<string, unknown> | null {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Find the latest soma_run_to_done or soma_kajabi_auto_finish run from run records.
 */
function getLatestSomaRunRecord(): { run_id: string; action: string; artifact_dir?: string; status?: string; error_class?: string; started_at?: string; finished_at?: string } | null {
  const runsDir = getRunsDir();
  if (!existsSync(runsDir)) return null;
  const dirs = readdirSync(runsDir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort()
    .reverse();
  for (const d of dirs.slice(0, 200)) {
    const runPath = join(runsDir, d, "run.json");
    if (!existsSync(runPath)) continue;
    const raw = readFileSync(runPath, "utf-8");
    const rec = parseRunRecord(raw);
    if (!rec) continue;
    const action = rec.action as string;
    if (!SOMA_ACTIONS.includes(action as (typeof SOMA_ACTIONS)[number])) continue;
    return {
      run_id: (rec.run_id as string) ?? d,
      action,
      artifact_dir: rec.artifact_dir as string | undefined,
      status: rec.status as string | undefined,
      error_class: rec.error_class as string | undefined,
      started_at: rec.started_at as string | undefined,
      finished_at: rec.finished_at as string | undefined,
    };
  }
  return null;
}

/**
 * Resolve artifact_dir from run record. For soma_run_to_done, hostd runs it and
 * artifact_dir may point to artifacts/hostd/XXX. The run record may have it.
 * Also check run_to_done PROOF dirs and hostd dirs by timestamp.
 */
function resolveArtifactDir(
  runRecord: { run_id: string; action: string; artifact_dir?: string }
): string | null {
  const repoRoot = join(getArtifactsRoot(), "..");
  if (runRecord.artifact_dir && existsSync(join(repoRoot, runRecord.artifact_dir))) {
    return runRecord.artifact_dir;
  }
  const root = getArtifactsRoot();

  // run_to_done PROOF dirs
  const runToDoneRoot = join(root, "soma_kajabi", "run_to_done");
  if (existsSync(runToDoneRoot)) {
    const dirs = readdirSync(runToDoneRoot, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort()
      .reverse();
    for (const d of dirs.slice(0, 5)) {
      if (existsSync(join(runToDoneRoot, d, "PROOF.json"))) {
        return `artifacts/soma_kajabi/run_to_done/${d}`;
      }
    }
  }

  // hostd dirs by timestamp (Console run_id format: YYYYMMDD-HHmmss-XXXX)
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(runRecord.run_id);
  if (match) {
    const prefix = `${match[1]}${match[2]}${match[3]}_${match[4]}${match[5]}${match[6]}`;
    const hostdDir = join(root, "hostd");
    if (existsSync(hostdDir)) {
      const entries = readdirSync(hostdDir, { withFileTypes: true });
      const candidates = entries
        .filter((e) => e.isDirectory() && e.name.startsWith(prefix))
        .map((e) => e.name)
        .sort()
        .reverse();
      if (candidates.length > 0) {
        return `artifacts/hostd/${candidates[0]}`;
      }
    }
  }

  return runRecord.artifact_dir ?? null;
}

/**
 * Extract status/error_class from artifact dir. Reads RESULT.json, hostd_result.json,
 * stdout.txt (last JSON line), PROOF.json.
 */
function extractFromArtifactDir(artifactDir: string): {
  status?: string;
  error_class?: string;
  novnc_url?: string;
  instruction_line?: string;
} {
  const root = getArtifactsRoot();
  const repoRoot = join(root, "..");
  const fullPath = join(repoRoot, artifactDir.startsWith("artifacts/") ? artifactDir : `artifacts/${artifactDir}`);
  if (!existsSync(fullPath)) return {};

  const result: { status?: string; error_class?: string; novnc_url?: string; instruction_line?: string } = {};

  // RESULT.json (from auto_finish)
  const resultPath = join(fullPath, "RESULT.json");
  if (existsSync(resultPath)) {
    try {
      const data = JSON.parse(readFileSync(resultPath, "utf-8"));
      if (data.status) result.status = data.status;
      if (data.error_class) result.error_class = data.error_class;
      if (data.novnc_url) result.novnc_url = data.novnc_url;
      if (data.instruction_line) result.instruction_line = data.instruction_line;
    } catch {
      /* ignore */
    }
  }

  // WAITING_FOR_HUMAN.json (auth gate artifact; fills novnc_url when RESULT has FAILURE + auth error_class)
  const wfhPath = join(fullPath, "WAITING_FOR_HUMAN.json");
  if (existsSync(wfhPath)) {
    try {
      const data = JSON.parse(readFileSync(wfhPath, "utf-8"));
      if (data.novnc_url) result.novnc_url = data.novnc_url;
      if (data.instruction_line) result.instruction_line = data.instruction_line;
    } catch {
      /* ignore */
    }
  }

  // PROOF.json (from run_to_done)
  const proofPath = join(fullPath, "PROOF.json");
  if (existsSync(proofPath)) {
    try {
      const data = JSON.parse(readFileSync(proofPath, "utf-8"));
      if (data.status) result.status = data.status;
      if (data.error_class) result.error_class = data.error_class;
      if (data.novnc_url) result.novnc_url = data.novnc_url;
      if (data.instruction_line) result.instruction_line = data.instruction_line;
      return result;
    } catch {
      /* ignore */
    }
  }

  // hostd_result.json
  const hostdResultPath = join(fullPath, "hostd_result.json");
  if (existsSync(hostdResultPath)) {
    try {
      const data = JSON.parse(readFileSync(hostdResultPath, "utf-8"));
      if (data.error_class) result.error_class = data.error_class;
    } catch {
      /* ignore */
    }
  }

  // stdout.txt last JSON line (soma_run_to_done prints JSON)
  const stdoutPath = join(fullPath, "stdout.txt");
  if (existsSync(stdoutPath)) {
    try {
      const lines = readFileSync(stdoutPath, "utf-8").trim().split("\n");
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i].trim();
        if (line.startsWith("{")) {
          const data = JSON.parse(line) as Record<string, unknown>;
          if (data.status) result.status = data.status as string;
          if (data.error_class) result.error_class = data.error_class as string;
          if (data.novnc_url) result.novnc_url = data.novnc_url as string;
          if (data.instruction_line) result.instruction_line = data.instruction_line as string;
          break;
        }
      }
    } catch {
      /* ignore */
    }
  }

  return result;
}

/**
 * Build artifact links object for the given artifact_dir.
 */
function buildArtifactLinks(artifactDir: string | null): SomaLastRunResolved["artifact_links"] {
  const links: SomaLastRunResolved["artifact_links"] = {};
  if (!artifactDir) return links;
  const root = getArtifactsRoot();
  const repoRoot = join(root, "..");
  const fullPath = join(repoRoot, artifactDir.startsWith("artifacts/") ? artifactDir : `artifacts/${artifactDir}`);
  if (!existsSync(fullPath)) return links;

  const base = artifactDir.replace(/\/$/, "");
  const rel = (p: string) => `${base}/${p}`.replace(/^artifacts\/artifacts\//, "artifacts/");
  if (existsSync(join(fullPath, "stdout.txt"))) links.stdout = rel("stdout.txt");
  if (existsSync(join(fullPath, "stderr.txt"))) links.stderr = rel("stderr.txt");
  if (existsSync(join(fullPath, "RESULT.json"))) links.result_json = rel("RESULT.json");
  if (existsSync(join(fullPath, "CRASH.json"))) links.crash_json = rel("CRASH.json");
  if (existsSync(join(fullPath, "framebuffer.png"))) links.framebuffer = rel("framebuffer.png");
  if (existsSync(join(fullPath, "ws_check.json"))) links.ws_check = rel("ws_check.json");
  if (existsSync(join(fullPath, "timings.json"))) links.timings = rel("timings.json");
  if (existsSync(join(fullPath, "PROOF.json"))) links.proof_json = rel("PROOF.json");

  return links;
}

/**
 * Write artifacts/soma_kajabi/last_run.json with resolved fields (non-secret).
 * Call when soma_run_to_done or soma_kajabi_auto_finish completes.
 */
export function writeSomaLastRunIndex(): void {
  try {
    const resolved = resolveSomaLastRun();
    const root = getArtifactsRoot();
    const outDir = join(root, "soma_kajabi");
    mkdirSync(outDir, { recursive: true });
    writeFileSync(
      join(outDir, "last_run.json"),
      JSON.stringify(resolved, null, 2),
      "utf-8"
    );
  } catch {
    /* best-effort; never throw */
  }
}

/**
 * Resolve the latest Soma run. Used by UI + autopilot.
 */
export function resolveSomaLastRun(): SomaLastRunResolved {
  const empty: SomaLastRunResolved = {
    status: "UNKNOWN",
    error_class: null,
    run_id: null,
    artifact_dir: null,
    novnc_url: null,
    novnc_url_legacy: null,
    browser_gateway_url: null,
    instruction_line: null,
    started_at: null,
    finished_at: null,
    artifact_links: {},
  };

  const runRecord = getLatestSomaRunRecord();
  if (!runRecord) return empty;

  const artifactDir = resolveArtifactDir(runRecord);
  const extracted = artifactDir ? extractFromArtifactDir(artifactDir) : {};

  let rawStatus = extracted.status ?? runRecord.status ?? "UNKNOWN";
  if (rawStatus === "success") rawStatus = "SUCCESS";
  if (rawStatus === "failure" || rawStatus === "error") rawStatus = "FAILURE";

  const AUTH_NEEDED_ERROR_CLASSES = new Set([
    "KAJABI_CLOUDFLARE_BLOCKED",
    "KAJABI_NOT_LOGGED_IN",
    "KAJABI_SESSION_EXPIRED",
    "KAJABI_CAPTURE_INTERACTIVE_FAILED",
    "SESSION_CHECK_TIMEOUT",
    "SESSION_CHECK_BROWSER_CLOSED",
    "KAJABI_INTERACTIVE_CAPTURE_ERROR",
    "KAJABI_INTERACTIVE_CAPTURE_TIMEOUT",
  ]);
  const errorClass = extracted.error_class ?? runRecord.error_class ?? null;
  if (
    (rawStatus === "FAILURE" || rawStatus === "TIMEOUT") &&
    errorClass &&
    AUTH_NEEDED_ERROR_CLASSES.has(errorClass)
  ) {
    rawStatus = "WAITING_FOR_HUMAN";
  }

  const validStatuses = ["SUCCESS", "WAITING_FOR_HUMAN", "FAILURE", "TIMEOUT", "BLOCKED", "running"] as const;
  const finalStatus = validStatuses.includes(rawStatus as (typeof validStatuses)[number])
    ? (rawStatus as SomaLastRunResolved["status"])
    : "UNKNOWN";

  const rawNovnc = extracted.novnc_url ?? null;
  const novncCanonical = rawNovnc ? toCanonicalNovncUrl(rawNovnc) ?? rawNovnc : null;

  let browserGatewayUrl: string | null = null;
  if (finalStatus === "WAITING_FOR_HUMAN" && runRecord.run_id) {
    const root = getArtifactsRoot();
    const bgDir = join(root, "browser_gateway");
    if (existsSync(bgDir)) {
      const bgDirs = readdirSync(bgDir, { withFileTypes: true })
        .filter((e) => e.isDirectory())
        .map((e) => e.name)
        .sort()
        .reverse();
      for (const d of bgDirs.slice(0, 3)) {
        const sessionPath = join(bgDir, d, "session.json");
        if (existsSync(sessionPath)) {
          try {
            const session = JSON.parse(readFileSync(sessionPath, "utf-8"));
            if (session.status === "LIVE") {
              browserGatewayUrl = `/browser/${session.session_id}`;
              break;
            }
          } catch { /* ignore */ }
        }
      }
    }
  }

  return {
    status: finalStatus,
    error_class: extracted.error_class ?? runRecord.error_class ?? null,
    run_id: runRecord.run_id,
    artifact_dir: artifactDir,
    novnc_url: novncCanonical,
    novnc_url_legacy: rawNovnc && rawNovnc !== novncCanonical ? rawNovnc : null,
    browser_gateway_url: browserGatewayUrl,
    instruction_line: extracted.instruction_line ?? null,
    started_at: runRecord.started_at ?? null,
    finished_at: runRecord.finished_at ?? null,
    artifact_links: buildArtifactLinks(artifactDir ?? null),
  };
}
