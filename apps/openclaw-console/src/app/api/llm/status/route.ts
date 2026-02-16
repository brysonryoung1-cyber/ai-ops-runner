import { NextRequest, NextResponse } from "next/server";
import { execSync } from "child_process";
import { readFileSync, existsSync, readdirSync } from "fs";
import { join } from "path";

/**
 * GET /api/llm/status
 *
 * Returns LLM provider status for the HQ panel.
 * Shows:
 *  - All providers (OpenAI, Moonshot, Ollama) with enabled/disabled status
 *  - Configuration health (key present, API base parseable)
 *  - Masked key fingerprints (NEVER raw keys)
 *  - Router status (review pinned to OpenAI, fail-closed)
 *  - Config validation status
 *
 * Protected by token auth (middleware).
 */

interface LLMProviderStatus {
  name: string;
  enabled: boolean;
  configured: boolean;
  status: "active" | "disabled" | "inactive" | "error";
  fingerprint: string | null;
  api_base?: string;
  review_model?: string;
  review_fallback?: boolean;
  review_fallback_model?: string;
}

interface ProviderDoctorState {
  state: "OK" | "DEGRADED" | "DOWN";
  last_error_class: string | null;
}

interface LLMStatusResponse {
  ok: boolean;
  providers: LLMProviderStatus[];
  router: {
    review_provider: string;
    review_model: string;
    review_gate: "fail-closed";
    expensive_review_override: boolean;
    review_guard: "pass" | "fail";
  };
  config: {
    valid: boolean;
    path: string;
    error: string | null;
  };
  doctor?: {
    last_timestamp: string | null;
    providers: {
      openai?: ProviderDoctorState;
      mistral?: ProviderDoctorState;
    };
  };
}

async function getLLMStatus(): Promise<LLMStatusResponse> {
  const repoRoot = process.env.OPENCLAW_REPO_ROOT || process.cwd();
  // On VPS: fetch from runner API (has repo + secrets mount)
  const runnerUrl = process.env.RUNNER_API_URL;
  if (runnerUrl) {
    try {
      const url = `${runnerUrl.replace(/\/$/, "")}/llm/status`;
      const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
      const data = await res.json();
      if (data?.ok && Array.isArray(data.providers)) {
        const status: LLMStatusResponse = {
          ok: true,
          providers: data.providers.map((p: any) => ({
            name: p.name,
            enabled: p.enabled,
            configured: p.configured,
            status: p.status,
            fingerprint: p.fingerprint ?? null,
            api_base: p.api_base,
            review_model: p.review_model,
            review_fallback: p.review_fallback,
            review_fallback_model: p.review_fallback_model,
          })),
          router: {
            review_provider: data.router?.review_provider ?? "OpenAI",
            review_model: data.router?.review_model ?? "gpt-4o-mini",
            review_gate: "fail-closed",
            expensive_review_override: !!data.router?.expensive_review_override,
            review_guard: data.router?.review_guard ?? "pass",
          },
          config: {
            valid: data.config?.valid ?? !data.init_error,
            path: data.config?.path ?? "config/llm.json",
            error: data.config?.error ?? data.init_error ?? null,
          },
        };
        const doctorData = readLatestProviderDoctor(repoRoot);
        if (doctorData) status.doctor = doctorData;
        return status;
      }
    } catch {
      // Fall through to Python or fallback
    }
  }
  // Try to get status from the Python LLM router (local dev with repo cwd)
  try {
    const result = execSync(
      `python3 -c "
import sys, json, os
sys.path.insert(0, '.')
try:
    from src.llm.router import get_router
    from src.llm.openai_provider import CODEX_REVIEW_MODEL
    router = get_router()
    statuses = router.get_all_status()
    allow_expensive = os.environ.get('OPENCLAW_ALLOW_EXPENSIVE_REVIEW') == '1'
    review_guard_pass = not (CODEX_REVIEW_MODEL == 'gpt-4o' and not allow_expensive)
    print(json.dumps({
        'ok': True,
        'providers': statuses,
        'review_model': CODEX_REVIEW_MODEL,
        'allow_expensive_review': allow_expensive,
        'review_guard_pass': review_guard_pass,
        'init_error': router.init_error
    }))
except Exception as e:
    print(json.dumps({'ok': False, 'error': str(e)}))
"`,
      {
        cwd: repoRoot,
        timeout: 10000,
        encoding: "utf-8",
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
      }
    );

    const data = JSON.parse(result.trim());

    if (data.ok) {
      const providers: LLMProviderStatus[] = data.providers.map(
        (p: any) => ({
          name: p.name,
          enabled: p.enabled,
          configured: p.configured,
          status: p.status,
          fingerprint: p.fingerprint || null,
          api_base: p.api_base,
          review_model: p.review_model,
          review_fallback: p.review_fallback,
          review_fallback_model: p.review_fallback_model,
        })
      );

      const status: LLMStatusResponse = {
        ok: true,
        providers,
        router: {
          review_provider: "OpenAI",
          review_model: data.review_model || "gpt-4o-mini",
          review_gate: "fail-closed",
          expensive_review_override: !!data.allow_expensive_review,
          review_guard: data.review_guard_pass ? "pass" : "fail",
        },
        config: {
          valid: !data.init_error,
          path: "config/llm.json",
          error: data.init_error || null,
        },
      };
      const doctorData = readLatestProviderDoctor(repoRoot);
      if (doctorData) status.doctor = doctorData;
      return status;
    }
  } catch {
    // Fall through to manual status check
  }

  // Fallback: read config/llm.json directly and check env vars
  const status = getFallbackStatus(repoRoot);
  const doctorData = readLatestProviderDoctor(repoRoot);
  if (doctorData) status.doctor = doctorData;
  return status;
}

/** Read latest provider doctor result from artifacts/doctor/providers/<run_id>/provider_status.json. Redacted only. */
function readLatestProviderDoctor(repoRoot: string): LLMStatusResponse["doctor"] | null {
  const base = join(repoRoot, "artifacts", "doctor", "providers");
  if (!existsSync(base)) return null;
  let dirs: string[];
  try {
    dirs = readdirSync(base);
  } catch {
    return null;
  }
  if (dirs.length === 0) return null;
  dirs.sort();
  const latestRunId = dirs[dirs.length - 1];
  const statusPath = join(base, latestRunId, "provider_status.json");
  if (!existsSync(statusPath)) return null;
  try {
    const raw = readFileSync(statusPath, "utf-8");
    const data = JSON.parse(raw) as {
      timestamp?: string;
      providers?: { openai?: { state: string; last_error_class: string | null }; mistral?: { state: string; last_error_class: string | null } };
    };
    const providers: NonNullable<LLMStatusResponse["doctor"]>["providers"] = {};
    if (data.providers?.openai)
      providers.openai = { state: data.providers.openai.state as "OK" | "DEGRADED" | "DOWN", last_error_class: data.providers.openai.last_error_class ?? null };
    if (data.providers?.mistral)
      providers.mistral = { state: data.providers.mistral.state as "OK" | "DEGRADED" | "DOWN", last_error_class: data.providers.mistral.last_error_class ?? null };
    return {
      last_timestamp: data.timestamp ?? null,
      providers,
    };
  } catch {
    return null;
  }
}

function getFallbackStatus(repoRoot: string): LLMStatusResponse {
  const configPath = join(repoRoot, "config", "llm.json");

  let configValid = false;
  let configError: string | null = null;
  let configData: any = null;

  if (existsSync(configPath)) {
    try {
      configData = JSON.parse(readFileSync(configPath, "utf-8"));
      configValid = true;
    } catch (e: any) {
      configError = `Config parse error: ${e.message}`;
    }
  } else {
    configError = "config/llm.json not found";
  }

  // Build provider list
  const providers: LLMProviderStatus[] = [];

  // OpenAI
  const openaiKey = process.env.OPENAI_API_KEY;
  providers.push({
    name: "OpenAI",
    enabled: true,
    configured: !!openaiKey,
    status: openaiKey ? "active" : "inactive",
    fingerprint: openaiKey ? maskKey(openaiKey) : null,
  });

  // Moonshot
  const moonshotKey = process.env.MOONSHOT_API_KEY;
  const moonshotEnabled =
    configData?.enabledProviders?.includes("moonshot") ?? false;
  providers.push({
    name: "Moonshot (Kimi)",
    enabled: moonshotEnabled,
    configured: !!moonshotKey,
    status: moonshotEnabled && moonshotKey ? "active" : "disabled",
    fingerprint: moonshotKey ? maskKey(moonshotKey) : null,
  });

  // Mistral (review fallback)
  const mistralKey = process.env.MISTRAL_API_KEY;
  const hasReviewFallback = configData?.reviewFallback?.provider === "mistral";
  providers.push({
    name: "Mistral (Codestral)",
    enabled: !!hasReviewFallback,
    configured: !!mistralKey,
    status: hasReviewFallback && mistralKey ? "active" : "disabled",
    fingerprint: mistralKey ? maskKey(mistralKey) : null,
  });

  // Ollama
  const ollamaEnabled =
    configData?.enabledProviders?.includes("ollama") ?? false;
  providers.push({
    name: "Ollama (Local)",
    enabled: ollamaEnabled,
    configured: false,
    status: "disabled",
    fingerprint: null,
  });

  const reviewModel = process.env.OPENCLAW_REVIEW_MODEL || "gpt-4o-mini";
  const allowExpensive = process.env.OPENCLAW_ALLOW_EXPENSIVE_REVIEW === "1";
  const reviewGuardPass = !(reviewModel === "gpt-4o" && !allowExpensive);

  return {
    ok: true,
    providers,
    router: {
      review_provider: "OpenAI",
      review_model: reviewModel,
      review_gate: "fail-closed",
      expensive_review_override: allowExpensive,
      review_guard: reviewGuardPass ? "pass" : "fail",
    },
    config: {
      valid: configValid,
      path: "config/llm.json",
      error: configError,
    },
  };
}

function maskKey(key: string): string | null {
  if (!key || key.length < 8) return null;
  const prefix = key.startsWith("sk-") ? "sk-" : key.slice(0, 3);
  const suffix = key.slice(-4);
  return `${prefix}…${suffix}`;
}

export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (
    origin &&
    !origin.includes("127.0.0.1") &&
    !origin.includes("localhost") &&
    secFetchSite !== "same-origin"
  ) {
    return NextResponse.json(
      { ok: false, error: "Forbidden" },
      { status: 403 }
    );
  }

  const status = await getLLMStatus();
  return NextResponse.json(status);
}
