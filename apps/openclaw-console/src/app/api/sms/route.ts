import { NextRequest, NextResponse } from "next/server";
import { executeAction } from "@/lib/hostd";
import { writeAuditEntry, hashParams } from "@/lib/audit";
import { createHmac, timingSafeEqual } from "crypto";

/**
 * SMS allowlist: comma-separated phone numbers from env.
 * Fail-closed: empty or missing allowlist rejects all requests.
 */
function loadSmsAllowlist(): Set<string> {
  const raw = process.env.SMS_ALLOWLIST || "";
  const numbers = new Set<string>();
  for (const num of raw.split(",")) {
    const cleaned = num.trim().replace(/[-() ]/g, "");
    if (cleaned) {
      numbers.add(cleaned.startsWith("+") ? cleaned : `+1${cleaned}`);
    }
  }
  return numbers;
}

function normalizeSender(phone: string): string {
  const cleaned = phone.trim().replace(/[-() ]/g, "");
  return cleaned.startsWith("+") ? cleaned : `+1${cleaned}`;
}

/**
 * Validate Twilio request signature (X-Twilio-Signature header).
 * Uses HMAC-SHA1 as specified by Twilio's security model.
 * Fail-closed: rejects if auth token is missing or signature doesn't match.
 */
function validateTwilioSignature(
  req: NextRequest,
  params: Record<string, string>
): boolean {
  const authToken = process.env.TWILIO_AUTH_TOKEN;
  if (!authToken) {
    return false; // Fail-closed: no auth token configured
  }

  const signature = req.headers.get("x-twilio-signature");
  if (!signature) {
    return false; // Fail-closed: no signature provided
  }

  // Build the full URL that Twilio used to compute the signature
  const url = req.url;

  // Sort params and concatenate
  const sortedKeys = Object.keys(params).sort();
  let dataStr = url;
  for (const key of sortedKeys) {
    dataStr += key + params[key];
  }

  const computed = createHmac("sha1", authToken)
    .update(dataStr)
    .digest("base64");

  // Constant-time comparison to prevent timing attacks
  try {
    return timingSafeEqual(
      Buffer.from(computed, "utf-8"),
      Buffer.from(signature, "utf-8")
    );
  } catch {
    return false; // Length mismatch
  }
}

/** In-memory rate limiter for inbound SMS (1/min per sender). */
const inboundRateMap = new Map<string, number>();
const INBOUND_RATE_LIMIT_MS = 60_000; // 1 minute

function checkInboundRateLimit(sender: string): boolean {
  const now = Date.now();
  const last = inboundRateMap.get(sender) || 0;
  if (now - last < INBOUND_RATE_LIMIT_MS) {
    return false; // Rate limited
  }
  inboundRateMap.set(sender, now);
  return true;
}

/**
 * POST /api/sms
 * Twilio inbound webhook for SMS commands.
 *
 * Protected by:
 *  1. Token auth (middleware — X-OpenClaw-Token)
 *  2. Twilio request signature validation (X-Twilio-Signature + HMAC-SHA1)
 *  3. SMS sender allowlist (fail-closed: empty = deny all)
 *  4. Inbound rate limiting (1/min per sender)
 *  5. Audit log
 *
 * Twilio sends: From, Body, MessageSid, AccountSid
 */
export async function POST(req: NextRequest) {
  const startTime = Date.now();

  // Parse Twilio webhook payload (URL-encoded form data)
  let from_number: string;
  let body: string;
  let messageSid: string;
  let rawParams: Record<string, string> = {};

  const contentType = req.headers.get("content-type") || "";

  if (contentType.includes("application/x-www-form-urlencoded")) {
    const formData = await req.formData();
    from_number = formData.get("From")?.toString() || "";
    body = formData.get("Body")?.toString() || "";
    messageSid = formData.get("MessageSid")?.toString() || "";
    // Collect all form params for signature validation
    formData.forEach((value, key) => {
      rawParams[key] = value.toString();
    });
  } else if (contentType.includes("application/json")) {
    const json = await req.json();
    from_number = json.From || json.from || "";
    body = json.Body || json.body || "";
    messageSid = json.MessageSid || json.message_sid || "";
    rawParams = json;
  } else {
    return NextResponse.json(
      { ok: false, error: "Unsupported content type" },
      { status: 400 }
    );
  }

  if (!from_number || !body) {
    return NextResponse.json(
      { ok: false, error: "Missing From or Body" },
      { status: 400 }
    );
  }

  // --- Gate 1: Twilio signature validation (fail-closed) ---
  if (!validateTwilioSignature(req, rawParams)) {
    const actor = `sms:${from_number.slice(-4)}`;
    writeAuditEntry({
      timestamp: new Date().toISOString(),
      actor,
      action_name: "sms_signature_rejected",
      params_hash: hashParams({ from: from_number.slice(-4) }),
      exit_code: null,
      duration_ms: Date.now() - startTime,
      error: "Invalid or missing Twilio signature",
    });
    return NextResponse.json(
      { ok: false, error: "Forbidden: invalid request signature" },
      { status: 403 }
    );
  }

  // --- Gate 2: Sender allowlist (fail-closed) ---
  const allowlist = loadSmsAllowlist();
  const normalizedSender = normalizeSender(from_number);
  if (allowlist.size === 0 || !allowlist.has(normalizedSender)) {
    const actor = `sms:${from_number.slice(-4)}`;
    writeAuditEntry({
      timestamp: new Date().toISOString(),
      actor,
      action_name: "sms_allowlist_rejected",
      params_hash: hashParams({ from: from_number.slice(-4) }),
      exit_code: null,
      duration_ms: Date.now() - startTime,
      error: "Sender not in SMS allowlist",
    });
    return NextResponse.json(
      { ok: false, error: "Forbidden: sender not allowed" },
      { status: 403 }
    );
  }

  // --- Gate 3: Inbound rate limiting (1/min per sender) ---
  if (!checkInboundRateLimit(normalizedSender)) {
    const twiml = `<?xml version="1.0" encoding="UTF-8"?><Response><Message>Rate limited. Try again in 1 minute.</Message></Response>`;
    return new NextResponse(twiml, {
      headers: { "Content-Type": "text/xml" },
      status: 429,
    });
  }

  // Audit log entry
  const actor = `sms:${from_number.slice(-4)}`;
  writeAuditEntry({
    timestamp: new Date().toISOString(),
    actor,
    action_name: "sms_inbound",
    params_hash: hashParams({ from: from_number.slice(-4), body, messageSid }),
    exit_code: null,
    duration_ms: 0,
    error: undefined,
  });

  // Route the SMS command via Host Executor (hostd)
  const command = body.trim().toUpperCase().replace(/\s+/g, "_");

  // Map SMS commands to console actions
  // LAST_ERRORS uses a dedicated remote command to show recent errors
  const SMS_COMMAND_MAP: Record<string, string> = {
    STATUS: "soma_status",
    RUN_SNAPSHOT: "soma_snapshot_home",
    RUN_HARVEST: "soma_harvest",
    RUN_MIRROR: "soma_mirror",
    LAST_ERRORS: "soma_last_errors",
  };

  const action = SMS_COMMAND_MAP[command];
  if (!action) {
    const twiml = `<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escapeXml(`Unknown command: ${body.trim()}. Available: STATUS, RUN_SNAPSHOT, RUN_HARVEST, RUN_MIRROR, LAST_ERRORS`)}</Message></Response>`;
    return new NextResponse(twiml, {
      headers: { "Content-Type": "text/xml" },
    });
  }

  try {
    const result = await executeAction(action);

    const duration_ms = Date.now() - startTime;
    writeAuditEntry({
      timestamp: new Date().toISOString(),
      actor,
      action_name: `sms_exec_${command}`,
      params_hash: hashParams({ command, action }),
      exit_code: result.exitCode,
      duration_ms,
      error: result.error || undefined,
    });

    // Return TwiML with result summary
    const summary = result.ok
      ? result.stdout.slice(0, 1500).replace(/\x1b\[[0-9;]*m/g, "")
      : `FAILED: ${result.error || result.stderr.slice(0, 500)}`;

    const twiml = `<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escapeXml(summary)}</Message></Response>`;
    return new NextResponse(twiml, {
      headers: { "Content-Type": "text/xml" },
    });
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    const twiml = `<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escapeXml(`Internal error: ${errorMsg.slice(0, 200)}`)}</Message></Response>`;
    return new NextResponse(twiml, {
      headers: { "Content-Type": "text/xml" },
      status: 500,
    });
  }
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}
