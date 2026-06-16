import { NextResponse } from "next/server";
import { COOKIE_NAME, USER_COOKIE_NAME } from "@/lib/session";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE_NAME, "", { maxAge: 0, path: "/" });
  res.cookies.set(USER_COOKIE_NAME, "", { maxAge: 0, path: "/" });
  return res;
}
