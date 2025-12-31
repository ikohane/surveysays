import { getInvitationByToken } from "../../_lib/db";
import { jsonResponse, readJson } from "../../_lib/json";
import { Env } from "../../_lib/env";

function validateAnswersAgainstQuestionnaire(qjson: any, answers: any): string | null {
  const blocks = Array.isArray(qjson?.blocks) ? qjson.blocks : [];
  if (!answers || typeof answers !== "object") return "answers must be an object map";

  for (const b of blocks) {
    const t = b?.type;
    const id = String(b?.id || "").trim();
    if (!id) continue;

    if (t === "singleSelect") {
      if (b?.required) {
        const v = answers[id];
        if (typeof v !== "string" || !v.trim()) return `missing required answer for ${id}`;
      }
    } else if (t === "freeText") {
      if (b?.required) {
        const v = answers[id];
        if (typeof v !== "string" || !v.trim()) return `missing required answer for ${id}`;
      }
    }
  }
  return null;
}

export async function onRequest(context: { request: Request; params: { token: string }; env: Env }): Promise<Response> {
  if (context.request.method !== "POST") return new Response("Method not allowed", { status: 405 });

  const token = String(context.params?.token || "").trim();
  if (!token) return jsonResponse({ error: "token required" }, { status: 400 });

  const inv = await getInvitationByToken(context.env, token);
  if (!inv) return jsonResponse({ error: "not found" }, { status: 404 });

  let qjson: any;
  try {
    qjson = JSON.parse(String(inv.questionnaire_json || "{}"));
  } catch {
    return jsonResponse({ error: "invalid questionnaire_json in DB" }, { status: 500 });
  }

  let body: any;
  try {
    body = await readJson(context.request);
  } catch (e: any) {
    return jsonResponse({ error: String(e?.message || e) }, { status: 400 });
  }

  const answers = body?.answers;
  const err = validateAnswersAgainstQuestionnaire(qjson, answers);
  if (err) return jsonResponse({ error: err }, { status: 400 });

  const campaignId = Number(inv.campaign_id);
  const answersJson = JSON.stringify({ answers });

  try {
    await context.env.DB.prepare(
      `INSERT INTO submissions (campaign_id, token, answers_json)
       VALUES (?, ?, ?)`
    )
      .bind(campaignId, token, answersJson)
      .run();
  } catch (e: any) {
    const msg = String(e?.message || e);
    if (msg.toLowerCase().includes("unique") || msg.toLowerCase().includes("constraint")) {
      return jsonResponse({ error: "already submitted" }, { status: 409 });
    }
    return jsonResponse({ error: msg }, { status: 500 });
  }

  return jsonResponse({ ok: true }, { status: 200 });
}


