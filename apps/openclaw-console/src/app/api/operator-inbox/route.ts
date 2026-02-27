/**
 * GET /api/operator-inbox
 *
 * Deterministic summary of actionable items: human gates, degraded canaries,
 * last proof/deploy/canary. No LLM. No secrets.
 */

import { NextResponse } from "next/server";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";
import { resolveSomaLastRun } from "@/lib/soma-last-run-resolver";

export const dynamic = "force-dynamic";

function getRepoRoot(): string {
  return process.env.OPENCLAW_REPO_ROOT || process.cwd();
}

function getArtifactsRoot(): string {
  if (process.env.OPENCLAW_ARTIFACTS_ROOT) return process.env.OPENCLAW_ARTIFACTS_ROOT;
  return join(getRepoRoot(), "artifacts");
}

function toArtifactUrl(relPath: string | null): string | null {
  if (!relPath) return null;
  const clean = relPath.replace(/^artifacts\/?/, "").replace(/\/$/, "");
  return clean ? `/artifacts/${clean.split("/").map(encodeURIComponent).join("/")}` : null;
}

export async function GET() {
  const repoRoot = getRepoRoot();
  const artifactsRoot = getArtifactsRoot();
  const docsRoot = join(repoRoot, "docs");

  const waiting_for_human: {
    project_id: string;
    run_id: string;
    reason: string;
    canonical_url: string | null;
    single_instruction: string | null;
    artifacts_link: string | null;
  }[] = [];

  const degraded: {
    subsystem: string;
    run_id: string;
    failing_checks: string[];
    proof_link: string | null;
    incident_link: string | null;
  }[] = [];

  let last_proof: {
    tree_sha: string | null;
    run_id: string | null;
    proof_link: string | null;
    timestamp: string | null;
  } = { tree_sha: null, run_id: null, proof_link: null, timestamp: null };

  let last_deploy: {
    build_sha: string | null;
    deploy_time: string | null;
    version_link: string | null;
  } = { build_sha: null, deploy_time: null, version_link: null };

  let last_canary: {
    status: string | null;
    run_id: string | null;
    proof_link: string | null;
    timestamp: string | null;
  } = { status: null, run_id: null, proof_link: null, timestamp: null };

  // --- waiting_for_human: Soma WAITING_FOR_HUMAN ---
  const somaResolved = resolveSomaLastRun();
  if (somaResolved.status === "WAITING_FOR_HUMAN" && somaResolved.run_id) {
    waiting_for_human.push({
      project_id: "soma_kajabi",
      run_id: somaResolved.run_id,
      reason: somaResolved.error_class ?? "WAITING_FOR_HUMAN",
      canonical_url: somaResolved.novnc_url,
      single_instruction: somaResolved.instruction_line,
      artifacts_link: somaResolved.artifact_dir ? toArtifactUrl(somaResolved.artifact_dir) : null,
    });
  }

  // --- degraded: canary status !== PASS ---
  const canaryBase = join(artifactsRoot, "system", "canary");
  if (existsSync(canaryBase)) {
    const dirs = readdirSync(canaryBase)
      .filter((d) => existsSync(join(canaryBase, d, "result.json")))
      .sort((a, b) => b.localeCompare(a));
    if (dirs.length > 0) {
      const latest = dirs[0];
      try {
        const r = JSON.parse(readFileSync(join(canaryBase, latest, "result.json"), "utf-8"));
        const status = r.status ?? null;
        const proofRel = r.proof
          ? r.proof.replace(/^.*\/artifacts\//, "").replace(/^artifacts\//, "").replace(/^\//, "")
          : `system/canary/${latest}`;
        last_canary = {
          status,
          run_id: latest,
          proof_link: toArtifactUrl(proofRel),
          timestamp: null,
        };
        if (status && status !== "PASS") {
          const failingChecks: string[] = r.failed_invariant ? [r.failed_invariant] : [status];
          degraded.push({
            subsystem: "canary",
            run_id: latest,
            failing_checks: failingChecks,
            proof_link: last_canary.proof_link,
            incident_link: r.incident_id ? toArtifactUrl(`incidents/${r.incident_id}`) : null,
          });
        }
      } catch {
        last_canary.run_id = latest;
      }
    }
  }

  // --- last_proof ---
  const proofSummaryPath = join(docsRoot, "LAST_PROOF_SUMMARY.json");
  if (existsSync(proofSummaryPath)) {
    try {
      const s = JSON.parse(readFileSync(proofSummaryPath, "utf-8"));
      last_proof = {
        tree_sha: s.tree_sha ?? null,
        run_id: s.run_id ?? null,
        proof_link: s.proof_dir ? toArtifactUrl(s.proof_dir) : null,
        timestamp: null,
      };
    } catch {
      /* ignore */
    }
  }

  // --- last_deploy ---
  const deployBase = join(artifactsRoot, "deploy");
  if (existsSync(deployBase)) {
    const dirs = readdirSync(deployBase)
      .filter((d) => existsSync(join(deployBase, d, "deploy_result.json")))
      .sort((a, b) => b.localeCompare(a));
    if (dirs.length > 0) {
      const latest = dirs[0];
      try {
        const r = JSON.parse(readFileSync(join(deployBase, latest, "deploy_result.json"), "utf-8"));
        let deployTime: string | null = null;
        const receiptPath = join(deployBase, latest, "deploy_receipt.json");
        if (existsSync(receiptPath)) {
          const receipt = JSON.parse(readFileSync(receiptPath, "utf-8"));
          deployTime = receipt.timestamp ?? receipt.deploy_time ?? null;
        }
        last_deploy = {
          build_sha: r.git_head ?? null,
          deploy_time: deployTime,
          version_link: toArtifactUrl(`deploy/${latest}`),
        };
      } catch {
        last_deploy.version_link = toArtifactUrl(`deploy/${latest}`);
      }
    }
  }

  return NextResponse.json({
    waiting_for_human,
    degraded,
    last_proof,
    last_deploy,
    last_canary,
  });
}
