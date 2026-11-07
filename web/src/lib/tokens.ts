/** Token handling helpers. The access token is a JWT; the client only reads its `exp` to decide when to refresh. The server is
 *  the only judge of validity. */

export function decodeExp(jwt: string | null): number | null {
  if (!jwt) return null;
  try {
    const part = jwt.split(".")[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const json = typeof atob === "function" ? atob(b64) : Buffer.from(b64, "base64").toString("utf8");
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

const MARGIN_MS = 30_000;

export function needsRefresh(jwt: string | null, nowMs: number = Date.now()): boolean {
  const exp = decodeExp(jwt);
  return exp === null || exp * 1000 - nowMs < MARGIN_MS;
}
