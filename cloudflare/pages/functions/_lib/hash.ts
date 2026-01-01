export async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  // @ts-ignore - crypto.subtle available in Workers runtime
  const digest = await crypto.subtle.digest("SHA-256", data);
  const bytes = Array.from(new Uint8Array(digest));
  return bytes.map((b) => b.toString(16).padStart(2, "0")).join("");
}




