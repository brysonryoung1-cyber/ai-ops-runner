/**
 * GET /api/projects/[projectId]/status
 *
 * Soma Status: build_sha, last soma_run_to_done run, stage, Mirror PASS/FAIL, artifact links.
 * Uses Soma Last Run Resolver for consistent artifact links (even when stderr empty).
 * Only implemented for projectId=soma_kajabi.
 */

import { NextRequest, NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";
import { getRunsForProject, getLastRunForProject } from "@/lib/run-recorder";
import { getLockInfo } from "@/lib/action-lock";
import { resolveSomaLastRun } from "@/lib/soma-last-run-resolver";

export const runtime = "nodejs";

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  const repo = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  return join(repo, "artifacts");
}

function getBuildSha(): string {
  if (process.env.OPENCLAW_BUILD_SHA) return process.env.OPENCLAW_BUILD_SHA;
  try {
    const cwd = process.env.OPENCLAW_REPO_ROOT || process.cwd();
    return execSync("git rev-parse --short HEAD", {
      encoding: "utf-8",
      cwd,
      timeout: 3000,
    }).trim();
  } catch {
    return "unknown";
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ projectId: string }> }
) {
  const { projectId } = await params;
  if (projectId !== "soma_kajabi") {
    return NextResponse.json(
      { ok: false, error: "Status only available for soma_kajabi" },
      { status: 404 }
    );
  }

  const artifactsRoot = getArtifactsRoot();
  const buildSha = getBuildSha();

  // Last soma_run_to_done run
  // Use Soma Last Run Resolver for canonical status + artifact links
  const resolved = resolveSomaLastRun();
  let lastRunId: string | null = resolved.run_id;
  let lastStatus: string | null = resolved.status;
  let stage: string | null = null;
  let mirrorPass: boolean | null = null;
  let exceptionsCount: number | null = null;
  let acceptancePath: string | null = null;
  let proofPath: string | null = null;
  let artifactDir: string | null = resolved.artifact_dir;
  const novncUrl: string | null = resolved.novnc_url;
  const instructionLine: string | null = resolved.instruction_line;
  const artifactLinks = resolved.artifact_links;
  const errorClass: string | null = resolved.error_class;

  const runs = getRunsForProject("soma_kajabi", 100);
  const lastAutoFinish = runs.find((r) => r.action === "soma_kajabi_auto_finish");

  // Resolve stage from auto_finish artifact (lock or last run)
  const lockInfo = getLockInfo("soma_kajabi_auto_finish");
  if (lockInfo?.artifact_dir) {
    artifactDir = lockInfo.artifact_dir;
  }
  if (artifactDir) {
    const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
    const stagePathFromRoot = join(repoRoot, artifactDir);
    if (existsSync(stagePathFromRoot)) {
      const stageJson = join(stagePathFromRoot, "stage.json");
      const stateJson = join(stagePathFromRoot, "state.json");
      if (existsSync(stageJson)) {
        try {
          const data = JSON.parse(readFileSync(stageJson, "utf-8"));
          stage = data.stage ?? null;
        } catch {
          /* ignore */
        }
      }
      if (!stage && existsSync(stateJson)) {
        try {
          const data = JSON.parse(readFileSync(stateJson, "utf-8"));
          stage = data.stage ?? null;
        } catch {
          /* ignore */
        }
      }

      const resultPath = join(stagePathFromRoot, "RESULT.json");
      if (existsSync(resultPath)) {
        try {
          const result = JSON.parse(readFileSync(resultPath, "utf-8"));
          lastStatus = result.status ?? lastStatus;
        } catch {
          /* ignore */
        }
      }
    }
  }

  // Run-to-done PROOF artifact
  const runToDoneRoot = join(artifactsRoot, "soma_kajabi", "run_to_done");
  if (existsSync(runToDoneRoot)) {
    const dirs = readdirSync(runToDoneRoot, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort()
      .reverse();
    for (const d of dirs.slice(0, 5)) {
      const proofJson = join(runToDoneRoot, d, "PROOF.json");
      const proofMd = join(runToDoneRoot, d, "PROOF.md");
      if (existsSync(proofJson)) {
        try {
          const proof = JSON.parse(readFileSync(proofJson, "utf-8"));
          if (!lastRunId) lastRunId = proof.run_id ?? d;
          if (mirrorPass === null) mirrorPass = proof.get("mirror_pass");
          if (exceptionsCount === null) exceptionsCount = proof.get("exceptions_count");
          if (!acceptancePath) acceptancePath = proof.get("acceptance_path");
          proofPath = `artifacts/soma_kajabi/run_to_done/${d}/PROOF.md`;
          break;
        } catch {
          /* ignore */
        }
      }
    }
  }

  // Acceptance dir from last auto_finish
  const acceptRoot = join(artifactsRoot, "soma_kajabi", "acceptance");
  if (!acceptancePath && existsSync(acceptRoot)) {
    const dirs = readdirSync(acceptRoot, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort()
      .reverse();
    if (dirs.length > 0) {
      const d = dirs[0];
      acceptancePath = `artifacts/soma_kajabi/acceptance/${d}`;
      const mirrorReport = join(acceptRoot, d, "mirror_report.json");
      if (existsSync(mirrorReport)) {
        try {
          const mr = JSON.parse(readFileSync(mirrorReport, "utf-8"));
          const excs = mr.exceptions ?? [];
          exceptionsCount = excs.length;
          mirrorPass = exceptionsCount === 0;
        } catch {
          /* ignore */
        }
      }
    }
  }

  return NextResponse.json({
    ok: true,
    build_sha: buildSha,
    last_run_id: lastRunId,
    last_status: lastStatus,
    stage,
    mirror_pass: mirrorPass,
    exceptions_count: exceptionsCount,
    artifact_dir: artifactDir,
    acceptance_path: acceptancePath,
    proof_path: proofPath,
    novnc_url: novncUrl,
    instruction_line: instructionLine,
    artifact_links: artifactLinks,
    error_class: errorClass,
  });
}
