import { requireAdmin } from "../../_lib/auth";
import { ensureCampaignId } from "../../_lib/db";
import { sha256Hex } from "../../_lib/hash";
import { jsonResponse, readJson } from "../../_lib/json";
import { Env } from "../../_lib/env";

function base64Url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  // @ts-ignore - btoa available in Workers runtime
  const b64 = btoa(s);
  return b64.replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function generateToken(): string {
  const bytes = new Uint8Array(24);
  // @ts-ignore - crypto.getRandomValues available in Workers runtime
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

export async function onRequest(context: { request: Request; env: Env }): Promise<Response> {
  try {
    const denied = requireAdmin(context.request, context.env);
    if (denied) return denied;
    if (context.request.method !== "POST") return new Response("Method not allowed", { status: 405 });

    const envAny: any = context.env as any;
    const envKeys = Object.keys(envAny || {}).sort();
    if (!envAny || !envAny.DB) {
      return jsonResponse(
        { error: "Missing D1 binding 'DB' in Pages Functions environment", envKeys },
        { status: 500 }
      );
    }

    let body: any;
    try {
      body = await readJson(context.request);
    } catch (e: any) {
      return jsonResponse({ error: String(e?.message || e) }, { status: 400 });
    }

    const campaignKey = String(body?.campaignKey || "").trim();
    const invitations = body?.invitations;
    if (!campaignKey) return jsonResponse({ error: "campaignKey required" }, { status: 400 });
    if (!Array.isArray(invitations)) return jsonResponse({ error: "invitations must be an array" }, { status: 400 });

    const campaignId = await ensureCampaignId(context.env, campaignKey);

    const out: Array<{ email: string; token: string }> = [];
    for (let i = 0; i < invitations.length; i++) {
      const inv = invitations[i] || {};
      const email = String(inv.email || "").trim().toLowerCase();
      const questionnaireVersion = inv.questionnaireVersion;
      const questionnaireJson = inv.questionnaireJson;
      const metadata = inv.metadata || {};

      if (!email) return jsonResponse({ error: `invitations[${i}].email required` }, { status: 400 });
      if (!questionnaireJson || typeof questionnaireJson !== "object") {
        return jsonResponse({ error: `invitations[${i}].questionnaireJson required` }, { status: 400 });
      }

      const strata = metadata?.recipientStrata || {};
      const firstName = String(strata?.firstname || "").trim();
      const lastName = String(strata?.lastname || "").trim();

      const qjsonText = JSON.stringify(questionnaireJson);
      const qhash =
        (typeof metadata?.questionnaireHash === "string" && metadata.questionnaireHash.trim()) ||
        (await sha256Hex(qjsonText));

      let token = String(inv.token || "").trim();
      if (!token) token = generateToken();

      // Insert or replace on token. If token collides and was generated, retry a few times.
      let attempts = 0;
      while (true) {
        attempts++;
        try {
          await context.env.DB.prepare(
            `INSERT INTO invitations (
              campaign_id, token, email, first_name, last_name,
              questionnaire_version, questionnaire_hash, questionnaire_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
              campaign_id=excluded.campaign_id,
              email=excluded.email,
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              questionnaire_version=excluded.questionnaire_version,
              questionnaire_hash=excluded.questionnaire_hash,
              questionnaire_json=excluded.questionnaire_json,
              metadata_json=excluded.metadata_json`
          )
            .bind(
              campaignId,
              token,
              email,
              firstName || null,
              lastName || null,
              Number.isFinite(questionnaireVersion) ? Number(questionnaireVersion) : null,
              qhash,
              qjsonText,
              JSON.stringify(metadata || {})
            )
            .run();
          break;
        } catch (e: any) {
          if (!inv.token && attempts < 5) {
            token = generateToken();
            continue;
          }
          return jsonResponse(
            { error: `DB error inserting invitation[${i}]: ${String(e?.message || e)}` },
            { status: 500 }
          );
        }
      }

      out.push({ email, token });
    }

    return jsonResponse({ campaignKey, invitations: out }, { status: 200 });
  } catch (e: any) {
    return jsonResponse(
      {
        error: String(e?.message || e),
        stack: String(e?.stack || ""),
      },
      { status: 500 }
    );
  }
}


