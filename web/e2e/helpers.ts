import { expect, type Page } from "@playwright/test";

/** People in the demo data. The development sign-in code is 000000; sandbox portals and GST filing accept 123456. */
export const OWNER = "ananya@example.in";
export const FIRM = "rohan@example.in";
export const SIGN_IN_CODE = "000000";
export const PORTAL_CODE = "123456";

/** Sign in through the real form. The session lives in this tab, so keep using the same page for a whole file. */
export async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(email);
  await page.getByRole("button", { name: "Send code" }).click();
  await page.getByLabel("One-time code").fill(SIGN_IN_CODE);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
}

/**
 * Sign in without asking for a code: take a token straight from the API and hand the browser its refresh token. The hourly limit is 5 codes
 * for one email, and the demo people are used up by the files that sign in through the form, so a new file uses this.
 */
export async function signInQuietly(page: Page, email: string): Promise<void> {
  const r = await page.request.post("/api/v1/auth/token", { data: { grant_type: "otp", email, otp: SIGN_IN_CODE } });
  expect(r.ok()).toBeTruthy();
  const { refresh_token } = await r.json();
  await page.goto("/login");
  await page.evaluate((t) => window.sessionStorage.setItem("bzx.rt", t), refresh_token as string);
  await page.goto("/");
  await page.waitForURL((u) => u.pathname !== "/login");
}

/**
 * Collect what should never happen on any screen: an uncaught error in the page, or the server answering 500 or above.
 * Call `problems()` at the end of a test and expect it to be empty.
 */
export function watch(page: Page): () => string[] {
  const found: string[] = [];
  page.on("pageerror", (e) => found.push(`page error: ${e.message}`));
  page.on("response", (r) => { if (r.status() >= 500) found.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  return () => found.splice(0);
}

/** A signed-in token for calling the API directly, for setting up data a test needs. */
export async function apiToken(page: Page, email: string): Promise<string> {
  const r = await page.request.post("/api/v1/auth/token", { data: { grant_type: "otp", email, otp: SIGN_IN_CODE } });
  expect(r.ok()).toBeTruthy();
  return (await r.json()).access_token as string;
}

export async function apiGet<T>(page: Page, token: string, path: string): Promise<T> {
  const r = await page.request.get(`/api/v1${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok(), `${path} -> ${r.status()}`).toBeTruthy();
  return (await r.json()) as T;
}

export async function apiPost<T>(page: Page, token: string, path: string, data?: unknown): Promise<T> {
  const r = await page.request.post(`/api/v1${path}`, { headers: { Authorization: `Bearer ${token}`, "Idempotency-Key": `e2e-${Date.now()}-${Math.random()}` }, data });
  expect(r.ok(), `${path} -> ${r.status()}`).toBeTruthy();
  return (await r.json()) as T;
}

export async function apiPatch<T>(page: Page, token: string, path: string, data: unknown): Promise<T> {
  const r = await page.request.patch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${token}`, "If-Match": "*" }, data });
  expect(r.ok(), `${path} -> ${r.status()}`).toBeTruthy();
  return (await r.json()) as T;
}
