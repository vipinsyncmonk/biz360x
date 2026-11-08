import { expect, test, type Page } from "@playwright/test";
import { signIn, watch } from "./helpers";

// A maker can record trade documents. Using Priya keeps the demo owner's sign-in codes (5 an hour) for the other files.
const MAKER = "priya@example.in";
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

/** Record an export, start its nine-month clock, bring the money home, claim RoDTEP, and record an import with its duty. */
test.describe.serial("exports and imports", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, MAKER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("starts empty, says what it does not do, and warns that there is no LUT", async () => {
    await page.goto("/trade");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Exports & imports");
    await expect(page.getByText(/Nothing here connects to ICEGATE/)).toBeVisible();
    await expect(page.getByLabel("Letter of Undertaking")).toContainText("None on record");
    await expect(page.getByText("No shipping bills yet")).toBeVisible();
  });

  test("a shipping bill is valued in rupees and the clock starts with the date of export", async () => {
    await page.getByRole("button", { name: "+ Shipping bill" }).click();
    await page.getByLabel("Shipping bill no.").fill("7712345");
    await page.getByLabel("Shipping bill date").fill(daysAgo(40));
    await page.getByLabel("Port code").fill("INMAA1");
    await page.getByLabel("FOB value (foreign)").fill("10000.00");
    await page.getByLabel("Customs exchange rate").fill("83.2150");
    await expect(page.getByText(/About ₹8,32,150\.00/)).toBeVisible();                                  // the preview; the server stores the real figure
    await page.getByRole("button", { name: "Record shipping bill" }).click();
    await expect(page.getByText("SB 7712345")).toBeVisible();
    await expect(page.getByText("USD 10000.00 · ₹8,32,150")).toBeVisible();
    await expect(page.getByText("Awaiting export", { exact: true })).toBeVisible();
    await expect(page.getByText(/Record the date of export to start the nine-month clock/)).toBeVisible();

    await page.getByRole("button", { name: "Record date of export" }).click();
    await page.getByLabel("Date of export", { exact: true }).fill(daysAgo(30));
    await page.getByRole("button", { name: "Start the clock" }).click();
    await expect(page.getByText("Proceeds due", { exact: true })).toBeVisible();
    await expect(page.getByText(/Due \d+ \w+, \d+ days left/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Record date of export" })).toHaveCount(0);        // fixed once recorded
  });

  test("money is recorded until the bill is realised", async () => {
    await page.getByRole("button", { name: "Record money received" }).click();
    await page.getByLabel("Received on").fill(daysAgo(5));
    await page.getByLabel("Amount (USD)").fill("6000.00");
    await page.getByLabel("Bank reference").fill("UTR998");
    await page.getByRole("button", { name: "Record money received" }).last().click();
    await expect(page.getByText(/USD 4000\.00 to come/)).toBeVisible();
    await expect(page.getByText(/Received: USD 6000\.00/)).toBeVisible();

    await page.getByRole("button", { name: "Record money received" }).first().click();
    await expect(page.getByLabel("Amount (USD)")).toHaveValue("4000.00");                              // suggests the balance
    await page.getByLabel("Received on").fill(daysAgo(1));
    await page.getByLabel("Amount (USD)").fill("4000.01");
    await expect(page.getByRole("alert").filter({ hasText: "Only 4000.00" })).toHaveCount(0);          // a cent of rounding is allowed
    await page.getByLabel("Amount (USD)").fill("4500");
    await expect(page.getByRole("alert").filter({ hasText: "Only 4000.00 is still to be realised" })).toBeVisible();
    await page.getByLabel("Amount (USD)").fill("4000.00");
    await page.getByRole("button", { name: "Record money received" }).last().click();
    await expect(page.getByText("Realised", { exact: true })).toBeVisible();
    await expect(page.getByText("All proceeds received")).toBeVisible();
  });

  test("RoDTEP and drawback claims are tracked", async () => {
    await page.getByRole("button", { name: "Claims and e-BRC" }).click();
    await page.getByLabel("RoDTEP claimed (₹)").fill("12000.50");
    await page.getByLabel("RoDTEP credited (₹)").fill("20000");
    await expect(page.getByRole("alert").filter({ hasText: "More RoDTEP is credited than was claimed" })).toBeVisible();
    await page.getByLabel("RoDTEP credited (₹)").fill("4000.25");
    await page.getByLabel("e-BRC reference").fill("BRC/2026/77");
    await page.getByRole("button", { name: "Save claims" }).click();
    await expect(page.getByText(/e-BRC BRC\/2026\/77/)).toBeVisible();
    await expect(page.getByText(/RoDTEP ₹12,000.50, drawback ₹0.00 claimed/)).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹8,000");                              // 12,000.50 claimed less 4,000.25 credited
  });

  test("an import records its duty, and the IGST becomes credit once the duty is paid", async () => {
    await page.getByRole("tab", { name: "Imports" }).click();
    await page.getByRole("button", { name: "+ Bill of entry" }).click();
    await page.getByLabel("Bill of entry no.").fill("5500123");
    await page.getByLabel("Bill of entry date").fill(daysAgo(10));
    await page.getByLabel("Port code").fill("INBLR4");
    await page.getByLabel("CIF value (foreign)").fill("100000");
    await page.getByLabel("Customs exchange rate").fill("11.5");
    await page.getByLabel("Basic customs duty (₹)").fill("57500");
    await page.getByLabel("Social welfare surcharge (₹)").fill("5750");
    await page.getByLabel("IGST (₹)").fill("216000");
    await page.getByRole("button", { name: "Record bill of entry" }).click();
    await expect(page.getByText("BoE 5500123")).toBeVisible();
    await expect(page.locator("section", { hasText: "Bills of entry" }).getByText("Duty ₹2,79,250")).toBeVisible();
    await expect(page.getByText("IGST credit once the duty is paid")).toBeVisible();                   // not yet: the duty is unpaid
    await page.getByRole("button", { name: "Mark duty paid" }).click();
    await page.getByLabel(/Duty on 5500123 paid on/).fill(daysAgo(8));
    await page.getByRole("button", { name: "Mark duty paid" }).last().click();
    await expect(page.getByText(/IGST credit ₹2,16,000/)).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹2,16,000");
    expect(problems()).toEqual([]);
  });

  test("the screen fits a phone", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const tab of ["Exports", "Imports"]) {
      await page.getByRole("tab", { name: tab }).click();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${tab} has no sideways scroll`).toBeLessThanOrEqual(0);
    }
    await page.screenshot({ path: "test-results/trade-phone.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("tab", { name: "Exports" }).click();
    await page.screenshot({ path: "test-results/trade-desktop.png", fullPage: true });
  });
});
