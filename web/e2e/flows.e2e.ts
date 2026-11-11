import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { FIRM, OWNER, PORTAL_CODE, apiGet, apiPost, apiToken, signIn, watch } from "./helpers";

interface Me { entities: Array<{ id: string }> }
interface Obl { id: string; status: string; rule?: { code?: string } }
interface GstReturn { id: string; return_type: string; status: string }

test.describe.serial("company flows", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("raise and issue a tax invoice", async () => {
    await page.goto("/billing/new");
    await page.getByLabel("Supplier GSTIN").selectOption({ index: 1 });
    await page.getByLabel("Customer", { exact: true }).selectOption({ index: 1 });
    const gst = page.getByLabel("Customer GSTIN");
    if ((await gst.locator("option").count()) > 1) await gst.selectOption({ index: 1 });
    await page.getByLabel("Description, item 1").fill("Calibration service");
    await page.getByLabel("HSN or SAC, item 1").fill("998313");
    await page.getByLabel("Quantity, item 1").fill("2");
    await page.getByLabel("Rate, item 1", { exact: true }).fill("12500");
    // The tax comes from the server, never from the browser.
    await expect(page.getByText("Taxable value")).toBeVisible();
    await expect(page.getByText("25,000.00").first()).toBeVisible();
    await page.getByRole("button", { name: "Issue invoice" }).click();
    await page.waitForURL(/\/billing\/[0-9a-f-]{36}/);
    await expect(page.locator("div.error[role=alert]")).toHaveCount(0);
    // The invoice can be saved as a PDF to attach to an email (the file is fetched with the session, so a plain link would not work).
    await expect(page.getByRole("button", { name: "View PDF" })).toBeVisible();
    const [saved] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download PDF" }).click()]);
    expect(saved.suggestedFilename()).toMatch(/\.pdf$/);
    expect(readFileSync((await saved.path())!).subarray(0, 5).toString()).toBe("%PDF-");
    expect(problems()).toEqual([]);
  });

  test("the e-invoicing card says where turnover stands and a year's turnover can be declared", async () => {
    await page.goto("/billing");
    const card = page.getByRole("region", { name: "E-invoicing" });
    await expect(card.getByText("Not required", { exact: true })).toBeVisible();                       // no earlier year is over ₹5 Cr
    await card.getByRole("button", { name: "Turnover by year" }).click();
    await card.getByLabel("Turnover (₹)").fill("1,20,00,000");
    await card.getByRole("button", { name: "Declare turnover" }).click();
    await expect(card.getByText(/1,20,00,000/).first()).toBeVisible();                // last year's declared figure now appears in the table
    await expect(card.getByText("Not required", { exact: true })).toBeVisible();                       // ₹1.2 Cr is far from the limit
    expect(problems()).toEqual([]);
  });

  test("file a GSTR-1 with the one-time code", async () => {
    const token = await apiToken(page, OWNER);
    const me = await apiGet<Me>(page, token, "/me");
    const eid = me.entities[0]!.id;
    const obls = (await apiGet<{ data: Obl[] }>(page, token, `/entities/${eid}/obligations?limit=200`)).data;
    const ready = obls.find((o) => o.rule?.code === "GST.GSTR1.MONTHLY" && o.status === "ready_to_file");
    expect(ready, "the demo data has a GSTR-1 approved for filing").toBeTruthy();
    await apiPost(page, token, `/entities/${eid}/obligations/${ready!.id}/prepare`);
    await expect.poll(async () => {
      const r = await apiGet<{ data: GstReturn[] }>(page, token, `/entities/${eid}/gst-returns`);
      return r.data.find((x) => x.return_type === "GSTR1")?.status ?? "none";
    }, { timeout: 30_000 }).toBe("ready");

    await page.goto("/gst");
    const row = page.getByRole("row", { name: /GSTR-1/ });
    await row.getByRole("button", { name: "File" }).click();
    await row.getByRole("button", { name: "Confirm filing" }).click();
    await row.getByLabel("6-digit code").fill("111111");        // a wrong code is refused and the return goes back to ready
    await row.getByRole("button", { name: "Submit code" }).click();
    await expect(row.getByText(/OTP is incorrect/)).toBeVisible();
    await row.getByRole("button", { name: "File" }).click();
    await row.getByRole("button", { name: "Confirm filing" }).click();
    await row.getByLabel("6-digit code").fill(PORTAL_CODE);
    await row.getByRole("button", { name: "Submit code" }).click();
    await expect(row.getByText(/ARN /)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("create and delete a data room", async () => {
    await page.goto("/documents");
    await page.getByRole("button", { name: "+ Add document" }).click();
    await page.getByLabel("File").setInputFiles({ name: "Cap table e2e.txt", mimeType: "text/plain", buffer: Buffer.from("cap table for the end-to-end test") });
    await page.getByRole("button", { name: "Upload" }).click();
    await expect(page.getByText(/Added to the vault|Already in the vault/)).toBeVisible();

    await page.goto("/documents/rooms");
    await page.getByRole("button", { name: "+ New data room" }).click();
    await page.getByLabel("Name").fill("E2E diligence");
    await page.getByLabel("Who can open it").fill("investor@fund.example\nlegal@fund.example");
    await page.getByRole("checkbox", { name: /Cap table e2e/ }).check();
    await page.getByRole("button", { name: "Create room" }).click();
    const room = page.locator("section", { hasText: "E2E diligence" });
    await expect(room.getByText("2 viewers, none has opened it")).toBeVisible();
    await expect(room.getByText("investor@fund.example")).toBeVisible();

    await room.getByRole("button", { name: "Delete" }).click();
    await room.getByRole("button", { name: "Yes, delete this room" }).click();
    await expect(page.getByText("E2E diligence")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });
});

test.describe.serial("firm flows", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signIn(page, FIRM);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("add a client company and see it in the grid", async () => {
    await page.goto("/group");
    await expect(page.getByRole("button", { name: "No one to chase" })).toBeDisabled();   // a new firm has nobody to chase yet
    await page.getByRole("link", { name: "Add client" }).click();
    await page.getByLabel("CIN, LLPIN, GSTIN or PAN").fill("U72900MH2022PTC999999");      // the sandbox registry's test company
    await page.getByRole("button", { name: "Look up" }).click();
    await expect(page.getByRole("heading", { name: "Is this your company?" })).toBeVisible();
    await page.getByRole("button", { name: "Yes, that is us" }).click();
    await page.getByRole("button", { name: "Build my graph" }).click();
    await page.getByRole("button", { name: "Back to the group view" }).click();
    await page.waitForURL("**/group");
    await expect(page.getByRole("button", { name: /Orchid Labs/ })).toBeVisible();
    expect(problems()).toEqual([]);
  });
});
