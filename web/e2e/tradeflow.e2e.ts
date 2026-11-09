import { expect, test, type Page } from "@playwright/test";
import { signIn, watch } from "./helpers";

// Runs after trade.e2e.ts, which expects no LUT and an empty list. Priya (a maker) can record all of this.
const MAKER = "priya@example.in";
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

/** Record the LUT, add a customer abroad, raise an invoice that becomes an export invoice, and complete the draft shipping bill it opens. */
test.describe.serial("the export flow", () => {
  let page: Page;
  let problems: () => string[];
  let invoiceUrl = "";

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, MAKER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("record the Letter of Undertaking", async () => {
    await page.goto("/trade");
    await expect(page.getByLabel("Letter of Undertaking", { exact: true })).toContainText("None on record");
    await page.getByRole("button", { name: "Record your LUT" }).click();
    await page.getByLabel("LUT reference (ARN)").fill("AD2904260000777");
    await page.getByRole("button", { name: "Record LUT", exact: true }).click();
    await expect(page.getByLabel("Letter of Undertaking", { exact: true })).toContainText("Valid");
  });

  test("an invoice to a customer abroad becomes an export invoice under LUT", async () => {
    await page.goto("/billing/new");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("New tax invoice");
    await page.getByRole("button", { name: "+ New customer" }).click();
    await page.getByLabel("Name", { exact: true }).fill("Fjord Imports Inc");
    await page.getByLabel("Country").selectOption("US");
    await expect(page.getByLabel("Invoiced in")).toHaveValue("USD");
    await page.getByRole("button", { name: "Add customer" }).click();

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("New export invoice");               // the new customer is picked, and the country decides
    await page.getByLabel("Supplier GSTIN").selectOption({ index: 1 });
    await page.getByLabel("Description, item 1").fill("Control valves");
    await page.getByLabel("HSN or SAC, item 1").fill("8481");
    await page.getByLabel("Quantity, item 1").fill("10");
    await page.getByLabel("Rate, item 1", { exact: true }).fill("8300");
    await page.getByLabel(/Exchange rate/).fill("83");
    await expect(page.getByText("Supplied under LUT.")).toBeVisible();
    await expect(page.getByText("A draft shipping bill opens when you issue this.")).toBeVisible();
    await expect(page.getByText("USD 1,000.00").first()).toBeVisible();                                    // what the customer pays: ₹83,000 at 83
    await page.getByRole("button", { name: "Issue invoice" }).click();
    await page.waitForURL(/\/billing\/[0-9a-f-]{36}/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("EXP/");
    invoiceUrl = page.url();
    await expect(page.getByText("Supplied under LUT: no GST charged.")).toBeVisible();
    await expect(page.getByText("Customer pays (USD)")).toBeVisible();
  });

  test("the draft shipping bill is completed with what customs issued", async () => {
    await page.goto("/trade");
    await expect(page.getByText("Shipping bill to complete")).toBeVisible();
    await expect(page.getByText(/Opened from export invoice EXP\//)).toBeVisible();
    await page.getByRole("button", { name: "Add the customs details" }).click();
    await page.getByLabel("Shipping bill no.").fill("7790001");
    await page.getByLabel("Shipping bill date").fill(daysAgo(1));
    await page.getByLabel("Port code").fill("INMAA1");
    await page.getByLabel(/Customs rate/).fill("83.4000");
    await page.getByRole("button", { name: "File shipping bill" }).click();
    await expect(page.getByText("SB 7790001")).toBeVisible();
    await expect(page.getByText("USD 1000.00 · ₹83,400")).toBeVisible();                                  // the customs rate, not the invoice's
    await expect(page.getByText("Shipping bill to complete")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  test("a credit note on the export invoice credits the customer in dollars with no tax", async () => {
    await page.goto(invoiceUrl);
    await page.getByRole("link", { name: "Issue credit note" }).click();
    await page.getByLabel("Quantity to credit, item 1").fill("2");
    await expect(page.getByText("Customer is credited (USD)")).toBeVisible();
    await expect(page.getByText("-USD 200.00")).toBeVisible();                                             // ₹16,600 at 83
    await expect(page.getByText("Credit note on an export invoice.")).toBeVisible();
    await page.getByRole("button", { name: "Issue credit note" }).click();
    await page.waitForURL(/\/billing\/[0-9a-f-]{36}$/);
    await expect(page.getByText("Customer is credited (USD)")).toBeVisible();
    await expect(page.getByLabel("Export", { exact: true })).toContainText("Supplied under LUT");
    expect(problems()).toEqual([]);
  });

  test("money from the customer abroad settles the invoice and shows the exchange difference", async () => {
    await page.goto("/trade");
    const row = page.locator("div").filter({ hasText: "SB 7790001" }).filter({ has: page.getByRole("button", { name: "Record money received" }) }).last();
    await row.getByRole("button", { name: "Record money received" }).click();
    await page.getByLabel("Received on").fill(daysAgo(0));
    await page.getByLabel("Amount (USD)").fill("800.00");
    await page.getByLabel("Rupees the bank credited (₹)").fill("66100.00");
    await page.getByLabel("Bank charges deducted (₹)").fill("100.00");
    await page.getByRole("button", { name: "Record money received" }).last().click();
    await expect(page.getByText(/USD 800\.00 on .* · exchange loss ₹200\.00/)).toBeVisible();      // ₹66,200 gross against ₹66,400 booked at 83
    await page.goto(invoiceUrl);
    await expect(page.getByText("Balance due")).toBeVisible();
    await expect(page.getByText("₹0.00").first()).toBeVisible();
    expect(problems()).toEqual([]);
  });
});
