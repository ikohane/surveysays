export type Env = {
  DB: any;
  ADMIN_TOKEN?: string;
};

export function getAdminToken(env: Env): string | null {
  const t = (env.ADMIN_TOKEN || "").trim();
  return t ? t : null;
}




