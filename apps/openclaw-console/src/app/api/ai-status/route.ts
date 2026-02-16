import { NextRequest, NextResponse } from "next/server";
import { execSync } from "child_process";

/**
 * GET /api/ai-status
 *
 * Returns AI provider connection status for the HQ panel.
 * Shows:
 *  - Which providers are configured (OpenAI now)
 *  - Review engine mode + last successful review time
 *  - Masked key fingerprint (NEVER raw keys)
 *
 * Protected by token auth (middleware).
 */

interface AIProvider {
  name: string;
  configured: boolean;
  fingerprint: string | null;
  status: "active" | "inactive" | "unknown";
}

interface ReviewEngine {
  mode: string;
  last_review: string | null;
  gate_status: "fail-closed" | "unknown";
}

function maskKey(key: string | null): string | null {
  if (!key || key.length < 8) return null;
  // Show prefix (up to "sk-") + last 4 chars only
  const prefix = key.startsWith("sk-") ? "sk-" : key.slice(0, 3);
  const suffix = key.slice(-4);
  return `${prefix}…${suffix}`;
}

function getOpenAIStatus(): AIProvider {
  // Check if OPENAI_API_KEY is available via any source
  // We NEVER read or expose the actual key — only check presence + mask fingerprint
  const envKey = process.env.OPENAI_API_KEY;

  if (envKey) {
    return {
      name: "OpenAI",
      configured: true,
      fingerprint: maskKey(envKey),
      status: "active",
    };
  }

  // Try checking via the key manager (no-throw)
  try {
    const result = execSync(
      "python3 ops/openai_key.py status 2>/dev/null || echo 'not configured'",
      {
        cwd: process.cwd(),
        timeout: 5000,
        encoding: "utf-8",
      }
    );
    const output = result.trim();
    if (output.includes("not configured") || output.includes("none")) {
      return { name: "OpenAI", configured: false, fingerprint: null, status: "inactive" };
    }
    // Extract masked fingerprint from status output
    const match = output.match(/sk-[^\s]+/);
    return {
      name: "OpenAI",
      configured: true,
      fingerprint: match ? match[0] : "configured",
      status: "active",
    };
  } catch {
    return { name: "OpenAI", configured: false, fingerprint: null, status: "unknown" };
  }
}

function getReviewEngineStatus(): ReviewEngine {
  // Check for last review verdict
  try {
    const result = execSync(
      "ls -1t artifacts/codex_review/*/verdict.json 2>/dev/null | head -1",
      {
        cwd: process.cwd(),
        timeout: 5000,
        encoding: "utf-8",
      }
    );
    const latestVerdict = result.trim();
    if (latestVerdict) {
      try {
        const { readFileSync } = require("fs");
        const verdict = JSON.parse(readFileSync(latestVerdict, "utf-8"));
        return {
          mode: "codex-review (OpenAI API)",
          last_review: verdict.timestamp || verdict.reviewed_at || null,
          gate_status: "fail-closed",
        };
      } catch {
        return {
          mode: "codex-review (OpenAI API)",
          last_review: null,
          gate_status: "fail-closed",
        };
      }
    }
  } catch {
    // No review artifacts yet
  }

  return {
    mode: "codex-review (OpenAI API)",
    last_review: null,
    gate_status: "fail-closed",
  };
}

function getLLMProvidersStatus(): AIProvider[] {
  // Try to get full LLM router status (includes Moonshot, Ollama)
  try {
    const result = execSync(
      `python3 -c "
import sys, json
sys.path.insert(0, '.')
try:
    from src.llm.router import get_router
    router = get_router()
    statuses = router.get_all_status()
    print(json.dumps(statuses))
except Exception as e:
    print(json.dumps([]))
"`,
      {
        cwd: process.cwd(),
        timeout: 10000,
        encoding: "utf-8",
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
      }
    );

    const statuses = JSON.parse(result.trim());
    if (Array.isArray(statuses) && statuses.length > 0) {
      return statuses.map((s: any) => ({
        name: s.name,
        configured: s.configured,
        fingerprint: s.fingerprint || null,
        status: s.status === "active" ? "active" : s.status === "disabled" ? "inactive" : "unknown",
      }));
    }
  } catch {
    // Fall through to OpenAI-only
  }
  return [];
}

export async function GET(req: NextRequest) {
  const origin = req.headers.get("origin");
  const secFetchSite = req.headers.get("sec-fetch-site");
  if (origin && !origin.includes("127.0.0.1") && !origin.includes("localhost") && secFetchSite !== "same-origin") {
    return NextResponse.json({ ok: false, error: "Forbidden" }, { status: 403 });
  }

  // Get providers from LLM router (includes all configured providers)
  let providers: AIProvider[] = getLLMProvidersStatus();
  if (providers.length === 0) {
    // Fallback: just show OpenAI status directly
    providers = [getOpenAIStatus()];
  }
  const reviewEngine = getReviewEngineStatus();

  return NextResponse.json({
    ok: true,
    providers,
    review_engine: reviewEngine,
  });
}
