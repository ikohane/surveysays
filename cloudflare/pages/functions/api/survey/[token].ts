import { getInvitationByToken, markInvitationOpened } from "../../_lib/db";
import { jsonResponse } from "../../_lib/json";
import { Env } from "../../_lib/env";

export async function onRequest(context: { params: { token: string }; env: Env }): Promise<Response> {
  const token = String(context.params?.token || "").trim();
  if (!token) return jsonResponse({ error: "token required" }, { status: 400 });

  const inv = await getInvitationByToken(context.env, token);
  if (!inv) return jsonResponse({ error: "not found" }, { status: 404 });

  await markInvitationOpened(context.env, token);

  let questionnaireJson: any = null;
  try {
    questionnaireJson = JSON.parse(String(inv.questionnaire_json || "{}"));
  } catch {
    return jsonResponse({ error: "invalid questionnaire_json in DB" }, { status: 500 });
  }

  return jsonResponse(
    {
      campaignKey: inv.campaign_key,
      token,
      questionnaireVersion: inv.questionnaire_version,
      questionnaireHash: inv.questionnaire_hash,
      questionnaireJson,
    },
    { status: 200 }
  );
}


