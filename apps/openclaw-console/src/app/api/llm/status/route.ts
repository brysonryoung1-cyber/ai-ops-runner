import { NextRequest, NextResponse } from "next/server";
import { execSync } from "child_process";
import { readFileSync, existsSync } from "fs";
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
}

function getLLMStatus(): LLMStatusResponse {
  // Try to get status from the Python LLM router
  try {
    const repoRoot = process.cwd();
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
        })
      );

      return {
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
    }
  } catch {
    // Fall through to manual status check
  }

  // Fallback: read config/llm.json directly and check env vars
  return getFallbackStatus();
}

function getFallbackStatus(): LLMStatusResponse {
  const repoRoot = process.cwd();
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

  const status = getLLMStatus();
  return NextResponse.json(status);
}
