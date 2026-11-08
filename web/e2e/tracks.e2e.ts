import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPatch, apiPost, apiToken, signIn, watch } from "./helpers";

const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);

/** The startup and MSME tracks. One file, one signed-in tab: a person can ask for only 5 sign-in codes an hour. */
test.describe.serial("startup and MSME tracks", () => {
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

  test("MSME: a company with no Udyam registration is asked for it, and registering turns the track on", async () => {
    const token = await apiToken(page, OWNER);
    const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
    await apiPatch(page, token, `/entities/${eid}`, { track: "msme" });
    await page.goto("/msme");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("No buyer is past the day payment was due.");
    await expect(page.getByText("Not registered")).toBeVisible();
    await page.getByLabel("Udyam registration number").fill("udyam-ka-03-0054321");
    await page.getByLabel("Class on the certificate").selectOption("small");
    await page.getByRole("button", { name: "Save registration" }).click();
    await expect(page.getByText("Small enterprise").first()).toBeVisible();
    await expect(page.locator("section", { hasText: "Schemes & credit" }).getByText("Eligible", { exact: true }).first()).toBeVisible();   // the MSME schemes now apply and are checked against the new class
    await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
  });

  test("MSME: late buyers show the interest owed, and a claim is drafted and filed", async () => {
    // two unpaid invoices to one customer, issued 100 and 60 days ago on 30-day terms
    const token = await apiToken(page, OWNER);
    const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
    const regs = (await apiGet<{ data: Array<{ id: string }> }>(page, token, `/entities/${eid}/registrations?kind=gstin`)).data;
    const parties = (await apiGet<{ data: Array<{ id: string; gstins: Array<{ id: string }> }> }>(page, token, `/entities/${eid}/parties?limit=50`)).data;
    const party = parties.find((p) => p.gstins.length > 0)!;
    const day = (ago: number) => new Date(Date.now() - ago * 86_400_000).toISOString().slice(0, 10);
    for (const ago of [100, 60]) {
      const doc = await apiPost<{ id: string }>(page, token, `/entities/${eid}/sales-documents`, {
        doc_type: "tax_invoice", doc_date: day(ago), registration_id: regs[0]!.id, party_id: party.id, party_gst_id: party.gstins[0]!.id,
        lines: [{ description: "Pressure transmitter", hsn_sac: "902620", qty: "10", rate: "1000.00", gst_rate: 18 }],
      });
      await apiPost(page, token, `/entities/${eid}/sales-documents/${doc.id}/issue`, {});
    }
    await expect.poll(async () => (await apiGet<{ totals: { invoices: number } }>(page, token, `/entities/${eid}/msme/late-buyers`)).totals.invoices, { timeout: 30_000 }).toBe(2);

    await page.goto("/msme");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("is with 1 buyer past the due day. Here is how to get it back.");
    await expect(page.getByText(/Interest is three times the RBI bank rate/)).toBeVisible();
    const row = page.getByRole("row", { name: new RegExp(`2 invoices`) });
    await expect(row).toContainText(/\d+ days/);
    await row.getByRole("button", { name: "Start claim" }).click();
    const claims = page.locator("section", { hasText: "Claims for delayed payment" });
    await expect(claims.getByText("Draft", { exact: true })).toBeVisible();
    await claims.getByRole("button", { name: "File on MSME Samadhaan" }).click();
    await claims.getByRole("button", { name: "Yes, file it" }).click();
    await expect(claims.getByText(/Case SAM\//)).toBeVisible();                                 // a background job filed it and recorded the case number
    await expect(claims.getByText("Filed", { exact: true })).toBeVisible();
  });

  test("MSME: a TReDS upload moves forward, and the Udyam figures show how close the limits are", async () => {
    const treds = page.locator("section", { hasText: "TReDS invoices" });
    await treds.getByRole("button", { name: "+ Record an upload" }).click();
    await page.getByLabel("Invoice").selectOption({ index: 0 });
    await page.getByLabel("Platform").selectOption("RXIL");
    await page.getByRole("button", { name: "Record upload" }).click();
    await expect(treds.getByText("Uploaded", { exact: true })).toBeVisible();
    await treds.getByRole("button", { name: "Record progress" }).click();
    await treds.getByLabel("Now").selectOption({ label: "Buyer accepted" });
    await treds.getByRole("button", { name: "Save" }).click();
    await expect(treds.getByText("Buyer accepted", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Update figures" }).click();
    await page.getByLabel("Investment in plant and machinery (₹)").fill("50000000");
    await page.getByLabel("Annual turnover (₹)").fill("500000000");
    await page.getByRole("button", { name: "Save figures" }).click();
    await expect(page.getByText("₹50.0 Cr of ₹100.0 Cr")).toBeVisible();                           // 50 crore turnover against the small-enterprise limit
    await expect(page.getByText("₹5.0 Cr of ₹25.0 Cr")).toBeVisible();
    expect(problems()).toEqual([]);
  });

  // Reuses this tab's sign-in: a person can ask for only 5 sign-in codes an hour, and the suite already signs in as the demo owner several times.
  test("the screens fit a phone and a desktop", async () => {
    for (const [name, size] of [["desktop", { width: 1440, height: 1000 }], ["phone", { width: 375, height: 812 }]] as const) {
      for (const path of ["/startup", "/msme"]) {
        await page.setViewportSize(size);
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await expect(page.getByRole("table").first()).toBeVisible();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${path} on a ${name} has no sideways scroll`).toBeLessThanOrEqual(0);
        await page.screenshot({ path: `test-results${path.replace("/", "-")}-${name}.png`, fullPage: true });
      }
    }
    await page.setViewportSize({ width: 1280, height: 720 });
  });
});
