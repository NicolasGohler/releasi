import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME = "releasi_session";
const SESSION_DURATION_MS = 7 * 24 * 60 * 60 * 1000; // 1 week

async function signToken(timestamp: number, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(String(timestamp))
  );
  const base64 = btoa(String.fromCharCode(...new Uint8Array(sig)));
  return `${timestamp}.${base64}`;
}

export async function POST(req: NextRequest) {
  const { password } = await req.json().catch(() => ({}));

  const secret = process.env.DASHBOARD_SECRET;
  const correctPassword = process.env.DASHBOARD_PASSWORD;

  if (!secret || !correctPassword) {
    return NextResponse.json({ error: "Auth not configured" }, { status: 500 });
  }

  if (password !== correctPassword) {
    return NextResponse.json({ error: "Wrong password" }, { status: 401 });
  }

  const token = await signToken(Date.now(), secret);

  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: SESSION_DURATION_MS / 1000,
    path: "/",
  });
  return res;
}
