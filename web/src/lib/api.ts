/** Typed API client generated from api/openapi.yaml (`npm run gen:api`), with sign-in state and token refresh.
 *
 *  Where tokens live: the access token is only in memory; the refresh token is in sessionStorage, so a page reload keeps the
 *  session but closing the tab ends it. Neither is in localStorage or a cookie the server reads. A refresh token that is
 *  used twice ends the whole session on the server, so refreshing is single-flight. */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "@/api/schema";
import { needsRefresh } from "./tokens";

export const API_BASE = "/api/v1";
const RT_KEY = "bzx.rt";

export type Me = components["schemas"]["Me"];
export type Obligation = components["schemas"]["Obligation"];
export type ApprovalRequest = components["schemas"]["ApprovalRequest"];

export class ApiProblem extends Error {
  constructor(public status: number, public code: string, public title: string, public detail?: string,
              public fieldErrors: Array<{ field: string; message: string }> = []) {
    super(detail ? `${title}: ${detail}` : title);
  }
}

let access: string | null = null;
let inflight: Promise<string | null> | null = null;

const storage = () => (typeof window === "undefined" ? null : window.sessionStorage);
export const hasRefreshToken = () => !!storage()?.getItem(RT_KEY);

export function setTokens(t: { access_token: string; refresh_token?: string }) {
  access = t.access_token;
  if (t.refresh_token) storage()?.setItem(RT_KEY, t.refresh_token);
}

export function clearTokens() {
  access = null;
  inflight = null;
  storage()?.removeItem(RT_KEY);
}

export async function toProblem(res: Response): Promise<ApiProblem> {
  try {
    const b = await res.json();
    return new ApiProblem(res.status, b.code ?? "error", b.title ?? res.statusText, b.detail, b.errors ?? []);
  } catch {
    return new ApiProblem(res.status, "error", res.statusText || "Request failed");
  }
}

async function postJson(path: string, body: unknown, headers: Record<string, string> = {}): Promise<Response> {
  return fetch(API_BASE + path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
}

/** Exchange the refresh token for new tokens. Only one exchange runs at a time. */
export function refresh(): Promise<string | null> {
  const rt = storage()?.getItem(RT_KEY);
  if (!rt) return Promise.resolve(null);
  inflight ??= (async () => {
    try {
      const res = await postJson("/auth/token", { grant_type: "refresh_token", refresh_token: rt });
      if (!res.ok) { clearTokens(); return null; }
      const t = await res.json();
      setTokens(t);
      return t.access_token as string;
    } catch {
      return access;                                      // offline: keep what we have, the next call will try again
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

async function ensureAccess(): Promise<string | null> {
  if (access && !needsRefresh(access)) return access;
  return (await refresh()) ?? access;
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const t = await ensureAccess();
    if (t) request.headers.set("Authorization", `Bearer ${t}`);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: API_BASE });
api.use(authMiddleware);

/** Unwrap an openapi-fetch result: the data, or a thrown ApiProblem built from the problem+json body. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || r.data === undefined) {
    const e = (r.error ?? {}) as { code?: string; title?: string; detail?: string; errors?: Array<{ field: string; message: string }> };
    throw new ApiProblem(r.response.status, e.code ?? "error", e.title ?? (r.response.statusText || "Request failed"), e.detail, e.errors ?? []);
  }
  return r.data;
}

// ---- sign-in (these calls need no bearer token, so they bypass the client)
export async function requestCode(email: string): Promise<void> {
  const res = await postJson("/auth/otp", { email });
  if (!res.ok) throw await toProblem(res);
}

export async function signInWithCode(email: string, otp: string, mfa_code?: string): Promise<void> {
  const res = await postJson("/auth/token", { grant_type: "otp", email, otp, ...(mfa_code ? { mfa_code } : {}) });
  if (!res.ok) throw await toProblem(res);
  setTokens(await res.json());
}

export async function signOut(): Promise<void> {
  const rt = storage()?.getItem(RT_KEY);
  try {
    if (rt && access) await postJson("/auth/logout", { refresh_token: rt }, { Authorization: `Bearer ${access}` });
  } finally {
    clearTokens();
  }
}

export const newIdempotencyKey = () => crypto.randomUUID();
