import { NextRequest, NextResponse } from "next/server";
import {
  COOKIE_NAME,
  USER_COOKIE_NAME,
  SESSION_DURATION_MS,
  signSessionToken,
} from "@/lib/session";

// Validates handle+password against the backend, then issues a signed session
// cookie carrying the resolved user_id. A second, unsigned ``releasi_user``
// cookie carries display metadata for the header — it's read-only-UI and
// carries no authority (the signed cookie is the only thing the middleware
// trusts).

const BACKEND_URL = process.env.BACKEND_URL || "http://REDACTED:8000";
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || "";

interface BackendUser {
  id: string;
  handle: string;
  display_name: string | null;
  is_superadmin: boolean;
  is_active: boolean;
}

export async function POST(req: NextRequest) {
  const { handle, password } = await req.json().catch(() => ({}));
  if (!handle || !password) {
    return NextResponse.json({ error: "Handle and password required" }, { status: 400 });
  }

  const secret = process.env.DASHBOARD_SECRET;
  if (!secret) {
    return NextResponse.json({ error: "Auth not configured" }, { status: 500 });
  }
  if (!BACKEND_API_KEY) {
    return NextResponse.json({ error: "Backend key not configured" }, { status: 500 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${BACKEND_API_KEY}`,
      },
      body: JSON.stringify({ handle, password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }

  if (upstream.status === 503) {
    const body = await upstream.json().catch(() => ({}));
    return NextResponse.json(
      { error: body.detail || "No users provisioned. Bootstrap via CLI on the server." },
      { status: 503 },
    );
  }
  if (!upstream.ok) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  const data = (await upstream.json()) as { user: BackendUser };
  const user = data.user;

  const token = await signSessionToken(user.id, secret);

  const res = NextResponse.json({
    ok: true,
    user: {
      id: user.id,
      handle: user.handle,
      display_name: user.display_name,
      is_superadmin: user.is_superadmin,
    },
  });
  res.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: SESSION_DURATION_MS / 1000,
    path: "/",
  });
  res.cookies.set(
    USER_COOKIE_NAME,
    JSON.stringify({
      id: user.id,
      handle: user.handle,
      display_name: user.display_name,
      is_superadmin: user.is_superadmin,
    }),
    {
      httpOnly: false,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: SESSION_DURATION_MS / 1000,
      path: "/",
    },
  );
  return res;
}
