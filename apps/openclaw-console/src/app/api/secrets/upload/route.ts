import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "crypto";

const ALLOWED_FILENAMES = new Set(["gmail_client.json"]);
const MAX_SIZE_BYTES = 131072;

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
  return NextResponse.json(
    { ok: false, error_class: "FORBIDDEN", error: "Origin could not be verified." },
    { status: 403 }
  );
}

/** Require OPENCLAW_ADMIN_TOKEN via X-OpenClaw-Admin-Token or X-OpenClaw-Token. Returns null if ok, else NextResponse. */
function requireAdminToken(req: NextRequest): NextResponse | null {
  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN;
  if (!adminToken || typeof adminToken !== "string") {
    return NextResponse.json(
      { ok: false, error_class: "ADMIN_NOT_CONFIGURED", error: "Admin token not configured." },
      { status: 503 }
    );
  }
  const provided =
    req.headers.get("X-OpenClaw-Admin-Token") ?? req.headers.get("X-OpenClaw-Token") ?? "";
  if (provided.length < 8) {
    return NextResponse.json(
      { ok: false, error_class: "FORBIDDEN", error: "Admin token required." },
      { status: 403 }
    );
  }
  try {
    const a = Buffer.from(provided, "utf8");
    const b = Buffer.from(adminToken, "utf8");
    if (a.length !== b.length || !timingSafeEqual(a, b)) {
      return NextResponse.json(
        { ok: false, error_class: "FORBIDDEN", error: "Admin token required." },
        { status: 403 }
      );
    }
  } catch {
    return NextResponse.json(
      { ok: false, error_class: "FORBIDDEN", error: "Admin token required." },
      { status: 403 }
    );
  }
  return null;
}

/**
 * POST /api/secrets/upload
 *
 * Accepts multipart/form-data with file "file". Validates filename allowlist,
 * size (<=128KB), and JSON content. Forwards to hostd POST /secrets/upload.
 * Admin-gated: X-OpenClaw-Admin-Token required.
 */
export async function POST(req: NextRequest) {
  const originError = validateOrigin(req);
  if (originError) return originError;

  const adminError = requireAdminToken(req);
  if (adminError) return adminError;

  const hostdUrl = process.env.OPENCLAW_HOSTD_URL;
  if (!hostdUrl || !hostdUrl.startsWith("http")) {
    return NextResponse.json(
      { ok: false, error_class: "HOSTD_UNREACHABLE", error: "Host Executor URL not configured." },
      { status: 502 }
    );
  }

  let formData: FormData;
  try {
    formData = await req.formData();
  } catch {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_BODY", error: "Invalid multipart body." },
      { status: 400 }
    );
  }

  const file = formData.get("file");
  if (!file || !(file instanceof File)) {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_BODY", error: "Missing file field." },
      { status: 400 }
    );
  }

  const filename = (file.name || "").replace(/^.*[\\/]/, "");
  if (!ALLOWED_FILENAMES.has(filename)) {
    return NextResponse.json(
      { ok: false, error_class: "FILENAME_NOT_ALLOWLISTED", error: "Filename not allowlisted." },
      { status: 403 }
    );
  }

  if (file.size > MAX_SIZE_BYTES) {
    return NextResponse.json(
      { ok: false, error_class: "FILE_TOO_LARGE", error: `File exceeds ${MAX_SIZE_BYTES} bytes.` },
      { status: 400 }
    );
  }

  let bytes: ArrayBuffer;
  try {
    bytes = await file.arrayBuffer();
  } catch {
    return NextResponse.json(
      { ok: false, error_class: "READ_FAILED", error: "Failed to read file." },
      { status: 400 }
    );
  }

  const decoder = new TextDecoder("utf-8", { fatal: false });
  const text = decoder.decode(bytes);
  try {
    JSON.parse(text);
  } catch {
    return NextResponse.json(
      { ok: false, error_class: "INVALID_JSON", error: "Content is not valid JSON." },
      { status: 400 }
    );
  }

  const base64 = Buffer.from(bytes).toString("base64");
  const adminToken = process.env.OPENCLAW_ADMIN_TOKEN!;

  try {
    const res = await fetch(`${hostdUrl.replace(/\/$/, "")}/secrets/upload`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-OpenClaw-Admin-Token": adminToken,
      },
      body: JSON.stringify({ filename, content: base64 }),
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        {
          ok: false,
          error_class: (data as { error_class?: string }).error_class ?? "UPLOAD_FAILED",
          error: (data as { error?: string }).error ?? `HTTP ${res.status}`,
        },
        { status: res.status >= 400 && res.status < 600 ? res.status : 502 }
      );
    }
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { ok: false, error_class: "HOSTD_UNREACHABLE", error: message },
      { status: 502 }
    );
  }
}
