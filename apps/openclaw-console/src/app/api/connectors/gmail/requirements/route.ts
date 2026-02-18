import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * GET /api/connectors/gmail/requirements
 *
 * Returns required redirect URIs, scopes, and expected filename for Gmail OAuth
 * (device flow). Used by Settings instructions and action error messages.
 * No auth required (no secrets in response).
 */
export async function GET() {
  return NextResponse.json({
    ok: true,
    required_redirect_uris: [
      "https://www.google.com/device",
    ],
    required_scopes: [
      "https://www.googleapis.com/auth/gmail.readonly",
    ],
    filename_expected: "gmail_client.json",
    app_type: "Desktop / Limited Input Device",
  });
}
