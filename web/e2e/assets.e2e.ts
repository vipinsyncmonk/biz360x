import { expect, test, type Page } from "@playwright/test";
import { apiGet, apiPost, apiToken, signInQuietly, watch } from "./helpers";

// A maker can prepare books entries. Signs in without a code (see signInQuietly): the demo people's hourly codes are used up by other files.
const MAKER = "priya@example.in";
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

/** Register a laptop, run depreciation for the months since, read the schedule, sell it. */
test.describe.serial("fixed assets", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, MAKER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("starts empty and says what it does not calculate", async () => {
    await page.goto("/assets");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fixed assets");
    await expect(page.getByText(/Income-tax depreciation and GST on a sale are not calculated here/)).toBeVisible();
    await expect(page.getByText("No assets yet")).toBeVisible();
  });

  test("an asset is registered with the usual life and residual value filled in by the server", async () => {
    await page.getByRole("button", { name: "+ Asset" }).click();
    await page.getByLabel("Asset", { exact: true }).fill("Dell laptop");
    await page.getByLabel("Acquired on").fill(daysAgo(75));
    await page.getByLabel(/^Cost/).fill("120000");
    await expect(page.getByLabel("Life (months)")).toHaveAttribute("placeholder", /36/);          // Schedule II: computers, three years
    await page.getByRole("button", { name: "Register asset" }).click();
    await expect(page.getByText("Dell laptop")).toBeVisible();
    await expect(page.getByText(/FA-0001 · Computers and laptops/)).toBeVisible();
    await expect(page.getByText("Straight line · 3 years")).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹1,20,000");
    expect(problems()).toEqual([]);
  });

  test("depreciation is previewed month by month, posted, and shows in the book value", async () => {
    await page.getByRole("button", { name: "Run depreciation" }).click();
    await page.getByRole("button", { name: "Show what will post" }).click();
    const table = page.getByRole("table", { name: "Depreciation to post" });
    await expect(table).toBeVisible();
    expect(await table.locator("tbody tr").count()).toBeGreaterThanOrEqual(2);
    await page.getByRole("button", { name: "Post depreciation" }).click();
    await expect(page.getByRole("table", { name: "Depreciation to post" })).toHaveCount(0);
    await expect(page.getByLabel("Key numbers")).not.toContainText("Depreciation so far₹0");
    await expect(page.getByText(/Depreciated ₹[1-9]/)).toBeVisible();
    await page.getByRole("button", { name: "Run depreciation" }).click();                         // a second run has nothing left to post
    await page.getByRole("button", { name: "Show what will post" }).click();
    await expect(page.getByText(/every month is already depreciated/)).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    expect(problems()).toEqual([]);
  });

  test("the schedule shows what was posted and what is still to come", async () => {
    await page.getByRole("button", { name: "Schedule for FA-0001" }).click();
    const table = page.getByRole("table", { name: "Depreciation schedule for FA-0001" });
    await expect(table.getByText("Posted").first()).toBeVisible();
    await expect(table.getByText("To come").first()).toBeVisible();
  });

  test("selling it charges the part month, moves it to the sold tab and posts the gain or loss", async () => {
    await page.getByRole("button", { name: "Sell or write off FA-0001" }).click();
    await page.getByLabel("Sold on").fill(daysAgo(0));
    await page.getByLabel(/^Received/).fill("90000");
    await page.getByRole("button", { name: "Sell or write off", exact: true }).click();
    await expect(page.getByText("No assets yet")).toBeVisible();
    await page.getByRole("tab", { name: "Sold or written off" }).click();
    await expect(page.getByText("Dell laptop")).toBeVisible();
    await expect(page.getByText(/Received ₹90,000/)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("an asset can be registered from a line of a booked purchase bill, and the line is used up", async () => {
    const token = await apiToken(page, MAKER);
    const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
    const existing = (await apiGet<{ data: Array<{ party_id: string; registration_id: string }> }>(page, token, `/entities/${eid}/purchase-bills?limit=5`)).data[0]!;
    const bill = await apiPost<{ id: string }>(page, token, `/entities/${eid}/purchase-bills`, {
      party_id: existing.party_id, registration_id: existing.registration_id, bill_no: "E2E/DESK/1", bill_date: daysAgo(20), accepted_on: daysAgo(20), itc_eligibility: "ineligible",   // GST is not claimed, so it is part of the cost
      lines: [{ description: "Standing desk", hsn_sac: "9403", qty: "1", rate: "40000.00", gst_rate: 18 }],
    });
    await apiPost(page, token, `/entities/${eid}/purchase-bills/${bill.id}/book`);

    await page.goto("/assets");
    await page.getByRole("button", { name: "+ Asset" }).click();
    await page.getByLabel("Fill in from").selectOption("bill");
    const option = page.getByLabel("Purchase bill").locator("option", { hasText: "E2E/DESK/1" });
    await expect(option).toHaveCount(1);
    await page.getByLabel("Purchase bill").selectOption((await option.getAttribute("value"))!);
    await page.getByLabel("Line", { exact: true }).selectOption({ index: 1 });
    await expect(page.getByLabel("Asset", { exact: true })).toHaveValue("Standing desk");
    await expect(page.getByLabel(/^Cost/)).toHaveValue("47200.00");                      // 40,000 and the 18% GST that is not claimed
    await expect(page.getByLabel("Acquired on")).toHaveValue(daysAgo(20));
    await page.getByLabel("Category").selectOption("furniture");
    await page.getByRole("button", { name: "Register asset" }).click();
    await expect(page.getByText(/· from a purchase bill/)).toBeVisible();
    await expect(page.getByLabel("Key numbers")).toContainText("₹47,200");

    await page.getByRole("button", { name: "+ Asset" }).click();                                  // nothing is left on that line: the bill is no longer offered
    await page.getByLabel("Fill in from").selectOption("bill");
    await expect(page.getByLabel("Purchase bill").locator("option", { hasText: "E2E/DESK/1" })).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  test("the screen fits a phone", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
});
