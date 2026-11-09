import { expect, test, type Page } from "@playwright/test";
import { FIRM, OWNER, signIn, watch } from "./helpers";

/** Every screen opens for a signed-in person, shows its heading, shows no error banner and never makes the server fail. */
const SCREENS: Array<[path: string, heading: RegExp]> = [
  ["/compliance", /Compliance Engine/],
  ["/billing", /Sales & Billing/],
  ["/billing/quotes", /Quotes/],
  ["/billing/receivables", /Collections/],
  ["/finance", /Finance & Books/],
  ["/gst", /GST & Tax/],
  ["/tds", /TDS & Advance tax/],
  ["/payroll", /Payroll & HR/],
  ["/payroll/employees", /Employees/],
  ["/legal", /Legal & Contracts/],
  ["/board", /Board & resolutions/],
  ["/notices", /Notice desk/],
  ["/documents", /Document vault/],
  ["/documents/rooms", /Data rooms/],
  ["/startup", /.+/],
  ["/msme", /.+/],
  ["/modules", /Four layers/],
  ["/copilot", /New conversation/],
  ["/settings", /Who can see, prepare, approve and sign/],
];

test.describe.serial("company screens", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("Today opens after sign-in", async () => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).toBeVisible();
    await expect(page.getByText("Loading…")).toHaveCount(0);
    await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  for (const [path, heading] of SCREENS) {
    test(`${path} opens`, async () => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await expect(page.locator("h1").first()).toContainText(heading);
      await expect(page.getByText(/^Loading/)).toHaveCount(0);
      await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
      expect(problems()).toEqual([]);
    });
  }

  test("an unknown address does not crash", async () => {
    const res = await page.goto("/no-such-screen");
    expect(res?.status()).toBe(404);
  });
});

test.describe.serial("firm screens", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, FIRM);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("the group view opens for a firm", async () => {
    await page.goto("/group");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("link", { name: "Add client" })).toBeVisible();
    await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });
});
