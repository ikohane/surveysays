import { Env, getAdminToken } from "./env";

export function requireAdmin(request: Request, env: Env): Response | null {
  const expected = getAdminToken(env);
  if (!expected) {
    return new Response("Server missing ADMIN_TOKEN", { status: 500 });
  }

  const auth = request.headers.get("authorization") || "";
  const m = auth.match(/^Bearer\s+(.+)$/i);
  const got = (m?.[1] || "").trim();
  if (!got || got !== expected) {
    return new Response("Unauthorized", { status: 401 });
  }
  return null;
}




