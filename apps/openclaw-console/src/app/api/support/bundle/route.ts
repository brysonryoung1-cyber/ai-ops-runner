import { NextRequest, NextResponse } from "next/server";
import { writeFileSync, mkdirSync, readFileSync, readdirSync, statSync, existsSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";
import { listRunRecords } from "@/lib/run-recorder";
import { loadProjectRegistrySafe } from "@/lib/projects";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

function getBuildSha(): string {
  try {
    return execSync("git rev-parse --short HEAD", {
      encoding: "utf-8",
      cwd: getRepoRoot(),
      timeout: 3000,
    }).trim();
  } catch {
    return "unknown";
  }
}

function redactSecrets(obj: unknown): unknown {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj === "string") {
    if (/sk-[a-zA-Z0-9_-]{20,}/.test(obj)) return "[REDACTED_OPENAI_KEY]";
    if (/ghp_|gho_|ghu_|ghs_|github_pat_/.test(obj)) return "[REDACTED_GH_TOKEN]";
    if (/AKIA[A-Z0-9]{16}/.test(obj)) return "[REDACTED_AWS_KEY]";
    if (/xox[baprs]-[a-zA-Z0-9-]{10,}/.test(obj)) return "[REDACTED_SLACK_TOKEN]";
    if (/^[0-9a-f]{32,}$/i.test(obj) && obj.length > 40) return "[REDACTED_TOKEN]";
    return obj;
  }
  if (Array.isArray(obj)) return obj.map(redactSecrets);
  if (typeof obj === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      const lower = k.toLowerCase();
      if (lower.includes("token") || lower.includes("secret") || lower.includes("key") || lower.includes("password")) {
        out[k] = v ? "[REDACTED]" : v;
      } else {
        out[k] = redactSecrets(v);
      }
    }
    return out;
  }
  return obj;
}

/** Redact secret patterns from raw text (stderr, logs, journals). */
function redactText(text: string): string {
  return text
    .replace(/sk-[a-zA-Z0-9_-]{20,}/g, "[REDACTED_OPENAI_KEY]")
    .replace(/xox[baprs]-[a-zA-Z0-9-]{10,}/g, "[REDACTED_SLACK_TOKEN]")
    .replace(/Bearer\s+[A-Za-z0-9._-]{20,}/gi, "Bearer [REDACTED]")
    .replace(/https?:\/\/[^/]*:[^/@\s]+@/g, "[URL_REDACTED]")
    .replace(/ghp_[a-zA-Z0-9]{36}/g, "[REDACTED_GH_TOKEN]")
    .replace(/gho_[a-zA-Z0-9]{36}/g, "[REDACTED_GH_TOKEN]")
    .replace(/ghu_[a-zA-Z0-9]{36}/g, "[REDACTED_GH_TOKEN]")
    .replace(/ghs_[a-zA-Z0-9]{36}/g, "[REDACTED_GH_TOKEN]")
    .replace(/github_pat_[a-zA-Z0-9_]{22,}/g, "[REDACTED_GH_TOKEN]")
    .replace(/AKIA[A-Z0-9]{16}/g, "[REDACTED_AWS_KEY]")
    .replace(/["']?(?:aws_)?secret[_-]?access[_-]?key["']?\s*[:=]\s*["']?[A-Za-z0-9/+=]{40}/g, "[REDACTED_AWS_SECRET]")
    .replace(/["']?(?:api[_-]?key|apikey)["']?\s*[:=]\s*["']?[a-zA-Z0-9_-]{20,}["']?/gi, "[REDACTED_API_KEY]");
}

function validateOrigin(req: NextRequest): NextResponse | null {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  const host = req.headers.get("host") ?? "";
  const port = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
  const allowed = new Set([
    `http://127.0.0.1:${port}`,
    `http://localhost:${port}`,
  ]);
  if (process.env.OPENCLAW_TAILSCALE_HOSTNAME) {
    allowed.add(`https://${process.env.OPENCLAW_TAILSCALE_HOSTNAME}`);
  }
  if (origin && allowed.has(origin)) return null;
  if (secFetchSite === "same-origin") return null;
  if (!origin && (host.startsWith("127.0.0.1") || host.startsWith("localhost"))) return null;
  return NextResponse.json({
    ok: false,
    error: "Forbidden",
    error_class: "ORIGIN_BLOCKED",
    reason: "Request origin could not be verified for support bundle endpoint.",
    origin_seen: origin ?? null,
    origin_allowed: false,
  }, { status: 403 });
}

/**
 * POST /api/support/bundle
 *
 * Generates a support bundle for debugging: ui health, DoD, failing runs,
 * docker compose ps, guard/hostd journals, config fingerprints.
 * No secrets; redacted output only.
 */
export async function POST(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  const runId = `${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14)}-${Math.random().toString(36).slice(2, 6)}`;
  const bundleDir = join(getArtifactsRoot(), "support_bundle", runId);

  try {
    mkdirSync(bundleDir, { recursive: true });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: "Failed to create support bundle directory" },
      { status: 500 }
    );
  }

  const manifest: string[] = [];

  // 0. Auth status
  try {
    const consoleToken = process.env.OPENCLAW_CONSOLE_TOKEN;
    const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
    const trustTailscale = process.env.OPENCLAW_TRUST_TAILSCALE === "1";
    let hostExecutorReachable = false;
    const hostdUrl = process.env.OPENCLAW_HOSTD_URL;
    if (hostdUrl) {
      try {
        const hres = await fetch(`${hostdUrl.replace(/\/$/, "")}/health`, {
          method: "GET",
          signal: AbortSignal.timeout(2500),
        });
        if (hres.ok) {
          const hdata = await hres.json().catch(() => ({}));
          hostExecutorReachable = hdata?.ok === true;
        }
      } catch {
        // unreachable
      }
    }
    const authStatus = {
      ok: true,
      hq_token_required: !!consoleToken && !trustTailscale,
      admin_token_loaded: typeof adminToken === "string" && adminToken.length > 0,
      host_executor_reachable: hostExecutorReachable,
      build_sha: getBuildSha(),
      trust_tailscale: trustTailscale,
      collected_at: new Date().toISOString(),
    };
    writeFileSync(join(bundleDir, "auth_status.json"), JSON.stringify(authStatus, null, 2));
    manifest.push("auth_status.json");
  } catch (e) {
    writeFileSync(join(bundleDir, "auth_status.json"), JSON.stringify({ error: String(e) }));
  }

  // 1. UI health
  try {
    const buildSha = getBuildSha();
    const artifactsRoot = getArtifactsRoot();
    let artifactsReadable = false;
    let artifactDirCount = 0;
    if (existsSync(artifactsRoot)) {
      const entries = readdirSync(artifactsRoot, { withFileTypes: true });
      artifactsReadable = true;
      artifactDirCount = entries.filter((e) => e.isDirectory()).length;
    }
    const uiHealth = {
      ok: true,
      build_sha: buildSha,
      artifacts: { root: artifactsRoot, readable: artifactsReadable, dir_count: artifactDirCount },
      server_time: new Date().toISOString(),
      node_version: process.version,
    };
    writeFileSync(join(bundleDir, "ui_health.json"), JSON.stringify(redactSecrets(uiHealth), null, 2));
    manifest.push("ui_health.json");
  } catch (e) {
    writeFileSync(join(bundleDir, "ui_health.json"), JSON.stringify({ error: String(e) }));
  }

  // 2. DoD last
  try {
    const dodBase = join(getArtifactsRoot(), "dod");
    if (existsSync(dodBase)) {
      const dirs = readdirSync(dodBase).filter((d) => {
        const p = join(dodBase, d);
        return statSync(p).isDirectory();
      });
      dirs.sort((a, b) => b.localeCompare(a));
      const latestRunId = dirs[0];
      if (latestRunId) {
        const resultPath = join(dodBase, latestRunId, "dod_result.json");
        if (existsSync(resultPath)) {
          const raw = readFileSync(resultPath, "utf-8");
          const data = JSON.parse(raw);
          writeFileSync(join(bundleDir, "dod_last.json"), JSON.stringify(redactSecrets(data), null, 2));
        } else {
          writeFileSync(join(bundleDir, "dod_last.json"), JSON.stringify({ run_id: latestRunId, artifact_dir: `artifacts/dod/${latestRunId}` }));
        }
      } else {
        writeFileSync(join(bundleDir, "dod_last.json"), JSON.stringify({ last: null }));
      }
    } else {
      writeFileSync(join(bundleDir, "dod_last.json"), JSON.stringify({ last: null, note: "dod dir not found" }));
    }
    manifest.push("dod_last.json");
  } catch (e) {
    writeFileSync(join(bundleDir, "dod_last.json"), JSON.stringify({ error: String(e) }));
  }

  // 3. Last 5 failing runs
  try {
    const runsDir = join(getArtifactsRoot(), "runs");
    const allRuns = listRunRecords(100);
    const failing = allRuns.filter((r) => r.status !== "success").slice(0, 5);
    const failsDir = join(bundleDir, "failing_runs");
    mkdirSync(failsDir, { recursive: true });
    for (const run of failing) {
      const runDir = join(runsDir, run.run_id);
      const destDir = join(failsDir, run.run_id);
      mkdirSync(destDir, { recursive: true });
      try {
        const runPath = join(runDir, "run.json");
        if (existsSync(runPath)) {
          const raw = readFileSync(runPath, "utf-8");
          const parsed = JSON.parse(raw) as Record<string, unknown>;
          writeFileSync(join(destDir, "run.json"), JSON.stringify(redactSecrets(parsed), null, 2));
        }
        const summaryPath = join(runDir, "SUMMARY.md");
        if (existsSync(summaryPath)) {
          writeFileSync(join(destDir, "SUMMARY.md"), redactText(readFileSync(summaryPath, "utf-8")));
        }
        const stderrPath = join(runDir, "stderr.txt");
        if (existsSync(stderrPath)) {
          writeFileSync(join(destDir, "stderr.txt"), redactText(readFileSync(stderrPath, "utf-8")));
        }
      } catch {
        writeFileSync(join(destDir, "run.json"), JSON.stringify(run, null, 2));
      }
    }
    manifest.push("failing_runs/");
  } catch (e) {
    writeFileSync(join(bundleDir, "failing_runs_error.txt"), String(e));
  }

  // 3b. Last 10 runs (ids/status/project_id/action/artifact_dir)
  try {
    const last10 = listRunRecords(10).map((r) => ({
      run_id: r.run_id,
      status: r.status,
      project_id: r.project_id,
      action: r.action,
      started_at: r.started_at,
      finished_at: r.finished_at,
      duration_ms: r.duration_ms,
      artifact_paths: r.artifact_paths,
    }));
    writeFileSync(join(bundleDir, "last_10_runs.json"), JSON.stringify(last10, null, 2));
    manifest.push("last_10_runs.json");
  } catch (e) {
    writeFileSync(join(bundleDir, "last_10_runs.json"), JSON.stringify({ error: String(e) }));
  }

  // 3c. Last forbidden context from server-side event log
  try {
    const forbiddenLogPath = join(getArtifactsRoot(), ".last_forbidden.json");
    if (existsSync(forbiddenLogPath)) {
      const raw = readFileSync(forbiddenLogPath, "utf-8");
      const parsed = JSON.parse(raw);
      writeFileSync(join(bundleDir, "last_forbidden.json"), JSON.stringify(redactSecrets(parsed), null, 2));
    } else {
      writeFileSync(
        join(bundleDir, "last_forbidden.json"),
        JSON.stringify({ last: null, note: "No 403 events recorded yet.", collected_at: new Date().toISOString() }, null, 2)
      );
    }
    manifest.push("last_forbidden.json");
  } catch {
    // best-effort
  }

  // 4. docker compose ps
  try {
    const repoRoot = getRepoRoot();
    const psOut = execSync("docker compose ps --format json 2>&1 || docker-compose ps 2>&1 || echo 'docker compose not available'", {
      encoding: "utf-8",
      cwd: repoRoot,
      timeout: 10000,
    });
    writeFileSync(join(bundleDir, "docker_compose_ps.txt"), redactText(psOut));
    manifest.push("docker_compose_ps.txt");
  } catch (e) {
    writeFileSync(join(bundleDir, "docker_compose_ps.txt"), `Error: ${e}`);
  }

  // 5. Guard journal (last 200 lines) — redacted
  try {
    const journalOut = execSync("journalctl -u openclaw-guard.service -n 200 --no-pager 2>&1 || echo 'journalctl failed'", {
      encoding: "utf-8",
      timeout: 5000,
    });
    writeFileSync(join(bundleDir, "guard_journal.txt"), redactText(journalOut));
    manifest.push("guard_journal.txt");
  } catch (e) {
    writeFileSync(join(bundleDir, "guard_journal.txt"), `Error: ${e}`);
  }

  // 6. Hostd journal (last 200 lines) — redacted
  try {
    const hostdOut = execSync("journalctl -u openclaw-hostd.service -n 200 --no-pager 2>&1 || echo 'journalctl hostd failed'", {
      encoding: "utf-8",
      timeout: 5000,
    });
    writeFileSync(join(bundleDir, "hostd_journal.txt"), redactText(hostdOut));
    manifest.push("hostd_journal.txt");
  } catch (e) {
    writeFileSync(join(bundleDir, "hostd_journal.txt"), `Error: ${e}`);
  }

  // 7. Config fingerprints (redacted)
  try {
    const registry = loadProjectRegistrySafe();
    const enabledProjects = registry?.projects?.filter((p) => p.enabled).map((p) => p.id) ?? [];
    const fingerprints = {
      build_sha: getBuildSha(),
      enabled_projects: enabledProjects,
      action_registry_version: 1,
      collected_at: new Date().toISOString(),
    };
    writeFileSync(join(bundleDir, "config_fingerprints.json"), JSON.stringify(fingerprints, null, 2));
    manifest.push("config_fingerprints.json");
  } catch (e) {
    writeFileSync(join(bundleDir, "config_fingerprints.json"), JSON.stringify({ error: String(e) }));
  }

  const artifactDir = `artifacts/support_bundle/${runId}`;
  const permalink = `/artifacts/support_bundle/${runId}`;

  return NextResponse.json({
    ok: true,
    run_id: runId,
    artifact_dir: artifactDir,
    permalink,
    manifest,
  });
}
