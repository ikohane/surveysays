import { requireAdmin } from "../../_lib/auth";
import { jsonResponse } from "../../_lib/json";
import { Env } from "../../_lib/env";

export async function onRequest(context: { request: Request; env: Env }): Promise<Response> {
  const denied = requireAdmin(context.request, context.env);
  if (denied) return denied;
  if (context.request.method !== "GET") return new Response("Method not allowed", { status: 405 });

  try {
    const envAny: any = context.env as any;
    const envKeys = Object.keys(envAny || {}).sort();
    if (!envAny || !envAny.DB) {
      return jsonResponse(
        {
          ok: false,
          error: "Missing D1 binding 'DB' in Pages Functions environment",
          envKeys,
        },
        { status: 500 }
      );
    }

    const r = await envAny.DB.prepare("SELECT 1 as ok").first();
    return jsonResponse({ ok: true, db: r || null, envKeys }, { status: 200 });
  } catch (e: any) {
    const envAny: any = context.env as any;
    const envKeys = Object.keys(envAny || {}).sort();
    return jsonResponse({ ok: false, error: String(e?.message || e), envKeys }, { status: 500 });
  }
}


