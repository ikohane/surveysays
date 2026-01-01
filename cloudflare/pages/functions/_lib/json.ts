export function jsonResponse(obj: unknown, init?: ResponseInit): Response {
  const body = JSON.stringify(obj, null, 2);
  const headers = new Headers(init?.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  return new Response(body, { ...init, headers });
}

export async function readJson(request: Request): Promise<unknown> {
  const ct = request.headers.get("content-type") || "";
  if (!ct.toLowerCase().includes("application/json")) {
    throw new Error("Expected application/json");
  }
  return await request.json();
}




