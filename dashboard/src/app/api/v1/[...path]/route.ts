import type { NextRequest } from "next/server";
import { COOKIE_NAME, verifySessionToken } from "@/lib/session";

// Server-side proxy from the dashboard (Vercel) to the Linauto backend.
// Keeps BACKEND_API_KEY out of the browser — the key is only ever attached
// here, on the server. Browser calls hit /api/v1/* same-origin; this handler
// forwards them upstream with the bearer token.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL || "";
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || "";
const DASHBOARD_SECRET = process.env.DASHBOARD_SECRET || "";

// Headers we never want to forward upstream (hop-by-hop or problematic).
const STRIP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-length", // Node fetch sets this itself for streamed bodies
  "authorization", // replaced with server-side key below
  "x-releasi-user-id", // identity is derived from the signed cookie, never trusted from the client
]);

// Headers to strip from the upstream response.
const STRIP_RESPONSE_HEADERS = new Set([
  "transfer-encoding",
  "connection",
  "keep-alive",
  "content-encoding", // upstream may have encoded; fetch may have decoded
]);

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  if (!BACKEND_API_KEY) {
    return new Response(
      JSON.stringify({ detail: "Dashboard misconfigured: BACKEND_API_KEY unset" }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }

  const { path } = await ctx.params;
  const upstreamUrl = `${BACKEND_URL}/api/v1/${path.join("/")}${req.nextUrl.search}`;

  const forwardHeaders = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) {
      forwardHeaders.set(key, value);
    }
  });
  forwardHeaders.set("authorization", `Bearer ${BACKEND_API_KEY}`);

  // Attribute human-driven actions: verify the signed session cookie here
  // (server-side) and forward the resolved user id to the backend so it can
  // stamp lead_events / notes. The inbound header was already stripped above,
  // so the browser can't spoof this. No valid session → no header → backend
  // attributes the action to "System".
  if (DASHBOARD_SECRET) {
    const token = req.cookies.get(COOKIE_NAME)?.value;
    const claims = token ? await verifySessionToken(token, DASHBOARD_SECRET) : null;
    if (claims?.userId) {
      forwardHeaders.set("x-releasi-user-id", claims.userId);
    }
  }

  // Forward the real client IP so backend rate limiting sees per-user keys,
  // not Vercel's egress IP.
  const existingXff = req.headers.get("x-forwarded-for");
  const clientIp = req.headers.get("x-real-ip") || "";
  if (!existingXff && clientIp) {
    forwardHeaders.set("x-forwarded-for", clientIp);
  }

  const method = req.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";

  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers: forwardHeaders,
    cache: "no-store",
    redirect: "manual",
  };
  if (hasBody) {
    init.body = req.body;
    init.duplex = "half";
  }

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch (err) {
    return new Response(
      JSON.stringify({
        detail: "Upstream fetch failed",
        error: err instanceof Error ? err.message : String(err),
      }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) {
      respHeaders.set(key, value);
    }
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
  proxy as OPTIONS,
  proxy as HEAD,
};
