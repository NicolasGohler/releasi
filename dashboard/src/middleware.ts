import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { COOKIE_NAME, verifySessionToken } from "@/lib/session";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow login page and auth API through without a session check
  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/api/auth")
  ) {
    return NextResponse.next();
  }

  const secret = process.env.DASHBOARD_SECRET;
  // If no secret is configured, skip auth (allows zero-config local dev)
  if (!secret) return NextResponse.next();

  const token = request.cookies.get(COOKIE_NAME)?.value;
  const claims = token ? await verifySessionToken(token, secret) : null;

  if (!claims) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
