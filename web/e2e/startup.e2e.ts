import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signIn, watch } from "./helpers";

const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);

/** Build a cap table from nothing through the forms, model a round on it, grant options, and draft an investor update. */
test.describe.serial("startup track", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  const tab = (name: string) => page.getByRole("tab", { name, exact: true }).click();
  const saved = () => expect(page.getByRole("status").filter({ hasText: "Saved." })).toBeVisible();

  test("starts empty and says what to do", async () => {
    await page.goto("/startup");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("cap table");
    await expect(page.getByText("No shares are on the cap table yet")).toBeVisible();
    await expect(page.getByText("No schemes apply to this company's track.")).toBeVisible();   // the demo company is not on the startup track
  });

  test("share classes, shareholders and allotments make the cap table", async () => {
    await tab("Share class");
    await page.getByLabel("Name").fill("Equity");
    await page.getByRole("button", { name: "Add share class" }).click();
    await saved();
    await page.getByLabel("Name").fill("Series Seed CCPS");
    await page.getByLabel("Instrument").selectOption("ccps");
    await page.getByRole("button", { name: "Add share class" }).click();
    await saved();

    await tab("Shareholder");
    await page.getByLabel("Name").fill("Karthik M");
    await page.getByRole("button", { name: "Add shareholder" }).click();
    await saved();
    await page.getByLabel("Name").fill("Singapore Fund");
    await page.getByLabel("Kind").selectOption("investor");
    await page.getByLabel("Country").fill("SG");
    await page.getByRole("button", { name: "Add shareholder" }).click();
    await saved();

    await tab("Allotment");
    const record = async (holder: string, cls: string, qty: string, price: string) => {
      await page.getByLabel("Shares for").selectOption({ label: holder });
      await page.getByLabel("Class", { exact: true }).selectOption({ label: cls });
      await page.getByLabel("Shares", { exact: true }).fill(qty);
      await page.getByLabel("Issue price per share (₹)").fill(price);
      await page.getByLabel("Money received on").fill(yesterday);
      await page.getByLabel("Allotted on").fill(yesterday);
      await page.getByRole("button", { name: "Record allotment" }).click();
      await saved();
    };
    await record("Karthik M", "Equity", "8000", "10");
    await record("Singapore Fund", "Series Seed CCPS", "2000", "2000");

    const table = page.getByRole("table").first();
    await expect(table.getByRole("row", { name: /Karthik M/ })).toContainText("80.0%");
    await expect(table.getByRole("row", { name: /Singapore Fund/ })).toContainText("20.0%");
    await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
  });

  test("a round shows its steps and what it does to the cap table", async () => {
    await tab("Round");
    await page.getByLabel("Name").fill("Seed extension");
    await page.getByLabel("Instrument").selectOption({ label: "Series Seed CCPS" });
    await page.getByLabel("Target amount (₹)").fill("5000000");
    await page.getByLabel("Pre-money valuation (₹)").fill("20000000");
    await page.getByRole("button", { name: "Start round" }).click();
    await saved();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Seed extension is");
    await expect(page.getByRole("listitem", { name: /Term sheet: in progress/ })).toBeVisible();
    await expect(page.getByRole("listitem", { name: /FC-GPR on FIRMS/ })).toBeVisible();          // there is a foreign holder

    await page.getByRole("button", { name: "Move to Term sheet" }).click();
    await expect(page.getByRole("heading", { name: /Seed extension · Term sheet/ })).toBeVisible();

    await page.getByRole("button", { name: "After Seed extension" }).click();
    await expect(page.getByText("New shares: 2,500")).toBeVisible();                                // 20,000,000 / 10,000 = 2,000 a share; 5,000,000 buys 2,500
    const table = page.getByRole("table").first();
    await expect(table.getByRole("row", { name: /New investors · Seed extension/ })).toContainText("20.0%");
    await expect(table.getByRole("row", { name: /Karthik M/ })).toContainText("64.0%");
    await page.getByRole("button", { name: "Today", exact: true }).click();
    await expect(table.getByRole("row", { name: /Karthik M/ })).toContainText("80.0%");
  });

  test("an ESOP pool and a grant show vesting and join the cap table", async () => {
    await tab("ESOP pool");
    await page.getByLabel("Name").fill("ESOP 2025");
    await page.getByLabel("Approved by shareholders on").fill("2025-01-10");
    await page.getByLabel("Options in the pool").fill("2000");
    await page.getByRole("button", { name: "Add pool" }).click();
    await saved();

    // the demo company has no employees yet: add one the way payroll would
    const token = await apiToken(page, OWNER);
    const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
    await apiPost(page, token, `/entities/${eid}/employees`, { employee_code: "E-ESOP", full_name: "Meera Iyer", date_of_joining: "2024-06-01", work_state: "29" });
    await page.reload();

    await tab("Option grant");
    await page.getByLabel("Pool").selectOption({ index: 1 });
    await page.getByLabel("Employee").selectOption({ index: 1 });
    await page.getByLabel("Granted on").fill("2025-01-15");
    await page.getByLabel("Options", { exact: true }).fill("480");
    await page.getByLabel("Strike price (₹)").fill("10");
    await page.getByRole("button", { name: "Grant options" }).click();
    await saved();
    await expect(page.getByRole("heading", { name: "ESOP" })).toBeVisible();
    await expect(page.getByText("480 of 2,000 granted", { exact: true })).toBeVisible();
    await expect(page.getByRole("table").first().getByRole("row", { name: /ESOP pool · ESOP 2025/ })).toBeVisible();
  });

  test("the investor update is drafted from the books, with the founder's own note", async () => {
    await page.getByLabel("Founder note").fill("We hired two engineers this month.");
    await page.getByRole("button", { name: "Preview" }).click();
    const text = page.getByLabel("Investor update preview");
    await expect(text).toContainText("Cash in bank:");
    await expect(text).toContainText("Seed extension is");
    await expect(text).toContainText("We hired two engineers this month.");
    expect(problems()).toEqual([]);
  });

  // Reuses this tab's sign-in: a person can ask for only 5 sign-in codes an hour, and the suite already signs in as the demo owner several times.
  test("the screen fits a phone and a desktop", async () => {
    for (const [name, size] of [["desktop", { width: 1440, height: 1000 }], ["phone", { width: 375, height: 812 }]] as const) {
      await page.setViewportSize(size);
      await page.goto("/startup");
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("table").first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${name} has no sideways scroll`).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/startup-${name}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 1280, height: 720 });
  });
});
