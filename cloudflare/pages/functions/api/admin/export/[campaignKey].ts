import { requireAdmin } from "../../../_lib/auth";
import { jsonResponse } from "../../../_lib/json";
import { Env } from "../../../_lib/env";

export async function onRequest(context: { request: Request; params: { campaignKey: string }; env: Env }): Promise<Response> {
  const denied = requireAdmin(context.request, context.env);
  if (denied) return denied;
  if (context.request.method !== "GET") return new Response("Method not allowed", { status: 405 });

  const campaignKey = String(context.params?.campaignKey || "").trim();
  if (!campaignKey) return jsonResponse({ error: "campaignKey required" }, { status: 400 });

  const campaign = await context.env.DB.prepare("SELECT id, campaign_key FROM campaigns WHERE campaign_key = ?")
    .bind(campaignKey)
    .first();
  if (!campaign) return jsonResponse({ error: "campaign not found" }, { status: 404 });

  const submissions = await context.env.DB.prepare(
    `SELECT token, submitted_at, answers_json
     FROM submissions
     WHERE campaign_id = ?
     ORDER BY submitted_at DESC`
  )
    .bind(Number(campaign.id))
    .all();

  return jsonResponse(
    {
      campaignKey,
      submissions: submissions?.results || [],
    },
    { status: 200 }
  );
}




