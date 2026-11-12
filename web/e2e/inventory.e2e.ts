import { expect, test, type Page } from "@playwright/test";
import { signInQuietly, watch } from "./helpers";

// A maker can add items and adjust stock. Signs in without a code (see signInQuietly): the demo people's hourly codes are used up by other files.
const MAKER = "priya@example.in";
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

/** Add an item, record its opening stock, count it, write some off, read the movements and the valuation. */
test.describe.serial("inventory", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, MAKER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("is in the sidebar, starts empty and says what it does not do", async () => {
    await page.goto("/");
    await page.getByRole("link", { name: "Inventory" }).first().click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Inventory");
    await expect(page.getByText(/valued at weighted average cost/)).toBeVisible();
    await expect(page.getByText(/FIFO and reorder alerts are not here yet/)).toBeVisible();
    await expect(page.getByText("No items are kept in stock yet")).toBeVisible();
    await expect(page.getByRole("button", { name: "Adjust stock" })).toBeDisabled();
  });

  test("an item is added and kept in stock", async () => {
    await page.getByRole("button", { name: "+ Item" }).click();
    await page.getByLabel("Item code (SKU)").fill("E2E-VALVE");
    await page.getByLabel("Name", { exact: true }).fill("Brass valve");
    await page.getByLabel("HSN code").fill("99");
    await expect(page.getByLabel("Add an item").getByRole("alert")).toContainText("HSN");
    await page.getByLabel("HSN code").fill("8481");
    await page.getByRole("button", { name: "Add item" }).click();
    await expect(page.getByText("Brass valve")).toBeVisible();
    await expect(page.getByText("E2E-VALVE · HSN 8481")).toBeVisible();
    await expect(page.getByText("0 NOS on hand")).toBeVisible();
    await expect(page.getByText("Out of stock", { exact: true }).last()).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("opening stock comes in at its cost", async () => {
    await page.getByRole("button", { name: "Opening stock" }).click();
    const form = page.getByLabel("Opening stock", { exact: true });
    await expect(form.getByLabel("What happened")).toHaveValue("opening");
    await form.getByLabel("Date", { exact: true }).fill(daysAgo(3));
    await form.getByLabel(/^Quantity/).fill("20");
    await form.getByLabel(/^Cost of one unit/).fill("115.50");
    await form.getByRole("button", { name: "Save adjustment" }).click();
    await expect(page.getByText("20 NOS on hand")).toBeVisible();
    await expect(page.getByText("Average cost ₹115.50")).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹2,310");
    await expect(page.getByText("In stock", { exact: true })).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("a count books the difference and a write-off of too much is refused", async () => {
    await page.getByRole("button", { name: "Adjust E2E-VALVE" }).click();
    await page.getByLabel("Date", { exact: true }).fill(daysAgo(1));
    await page.getByLabel(/^Quantity counted/).fill("18");
    await expect(page.getByText(/2 NOS fewer than the books/)).toBeVisible();
    await page.getByLabel("Reason").fill("Monthly count");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(page.getByText("18 NOS on hand")).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹2,079");

    await page.getByRole("button", { name: "Adjust E2E-VALVE" }).click();
    await page.getByLabel("What happened").selectOption("write_off");
    await page.getByLabel("Date", { exact: true }).fill(daysAgo(0));
    await page.getByLabel(/^Quantity/).fill("500");
    await page.getByLabel("Reason").fill("Too many");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(page.getByText(/Not enough stock/)).toBeVisible();                          // 409 insufficient_stock, shown in the form
    await page.getByLabel(/^Quantity/).fill("3");
    await page.getByLabel("Reason").fill("Damaged in transit");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(page.getByText("15 NOS on hand")).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the movements show each change with its journal", async () => {
    await page.getByRole("button", { name: "Movements for E2E-VALVE" }).click();
    const table = page.getByRole("table", { name: "Stock movements for E2E-VALVE" });
    await expect(table.locator("tbody tr")).toHaveCount(3);
    await expect(table.getByText("Write-off: Damaged in transit")).toBeVisible();
    await expect(table.getByText("Stock count: Monthly count")).toBeVisible();
    await expect(table.getByText("Opening stock")).toBeVisible();
    await expect(table.getByText("+20")).toBeVisible();
    await expect(table.getByText("−3")).toBeVisible();
    await expect(table.getByText(/^JE-\d+$/).first()).toBeVisible();
  });

  test("the valuation as of a date adds up and goes back in time", async () => {
    await page.getByRole("tab", { name: "Valuation" }).click();
    const table = page.getByRole("table", { name: "Stock valuation by item" });
    await expect(table.getByText("Brass valve")).toBeVisible();
    await expect(table.locator("tfoot")).toContainText("₹1,732.50");                           // 15 at 115.50
    await page.getByLabel("As of").fill(daysAgo(2));
    await expect(table.locator("tfoot")).toContainText("₹2,310.00");                           // before the count: 20 at 115.50
    await page.getByLabel("As of").fill(daysAgo(30));
    await expect(page.getByText(/There was no stock on/)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("a maker cannot switch on negative stock", async () => {
    await page.getByLabel("Allow stock to go below zero").click();
    await expect(page.getByLabel("Inventory settings").getByRole("alert")).toBeVisible();     // 403: only an owner or approver may
    await expect(page.getByLabel("Allow stock to go below zero")).not.toBeChecked();
  });

  test("the screen fits a phone", async () => {
    await page.getByRole("tab", { name: "Stock on hand" }).click();
    await page.setViewportSize({ width: 375, height: 812 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
});
