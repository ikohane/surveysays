import { requireAdmin } from "../../_lib/auth";
import { jsonResponse } from "../../_lib/json";
import { Env } from "../../_lib/env";

export async function onRequest(context: { request: Request; env: Env }): Promise<Response> {
  const denied = requireAdmin(context.request, context.env);
  if (denied) return denied;
  if (context.request.method !== "GET") return new Response("Method not allowed", { status: 405 });

  try {
    const r = await context.env.DB.prepare("SELECT 1 as ok").first();
    return jsonResponse({ ok: true, db: r || null }, { status: 200 });
  } catch (e: any) {
    return jsonResponse({ ok: false, error: String(e?.message || e) }, { status: 500 });
  }
}


