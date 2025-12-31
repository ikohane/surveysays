import { Env } from "./env";

export async function ensureCampaignId(env: Env, campaignKey: string): Promise<number> {
  const key = campaignKey.trim();
  if (!key) throw new Error("campaignKey required");

  const existing = await env.DB.prepare("SELECT id FROM campaigns WHERE campaign_key = ?")
    .bind(key)
    .first();
  if (existing && typeof existing.id === "number") return existing.id;

  const res = await env.DB.prepare("INSERT INTO campaigns (campaign_key) VALUES (?)").bind(key).run();
  // D1 returns meta.last_row_id
  const id = res?.meta?.last_row_id;
  if (typeof id !== "number") {
    throw new Error("Failed to create campaign");
  }
  return id;
}

export async function getInvitationByToken(env: Env, token: string): Promise<any | null> {
  const t = token.trim();
  if (!t) return null;
  const row = await env.DB.prepare(
    `SELECT
      i.*,
      c.campaign_key as campaign_key
     FROM invitations i
     JOIN campaigns c ON c.id = i.campaign_id
     WHERE i.token = ?`
  )
    .bind(t)
    .first();
  return row || null;
}

export async function markInvitationOpened(env: Env, token: string): Promise<void> {
  await env.DB.prepare(
    `UPDATE invitations
     SET opened_at = COALESCE(opened_at, strftime('%Y-%m-%d %H:%M:%f', 'now'))
     WHERE token = ?`
  )
    .bind(token)
    .run();
}


