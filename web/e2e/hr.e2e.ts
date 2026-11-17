import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiToken, signInQuietly, watch } from "./helpers";

// The owner sets the policy and decides leave. Signs in without a code (see signInQuietly): the demo people's hourly codes are used up by other files.
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const now = new Date();
const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
const firstMonday = new Date(now.getFullYear(), now.getMonth(), 1 + ((8 - monthStart.getDay()) % 7));
const tuesday = new Date(firstMonday.getFullYear(), firstMonday.getMonth(), firstMonday.getDate() + 1);

/** Policy once, a new hire from a CTC, unpaid leave, and a payroll run that already knows about both. */
test.describe.serial("HR: policy, onboarding, leave and the payroll run", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("the company starts on the starter policy and saves its own", async () => {
    await page.goto("/payroll");
    await page.getByRole("link", { name: "HR policy" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("HR policy");
    await expect(page.getByText("Starter policy", { exact: true })).toBeVisible();
    await expect(page.getByRole("table", { name: "Leave types" })).toContainText("Earned leave");
    await page.getByLabel("Basic pay (% of CTC)").fill("140");
    await expect(page.getByLabel("Save the policy").getByRole("alert")).toContainText("Basic pay");
    await page.getByLabel("Basic pay (% of CTC)").fill("40");
    await page.getByLabel("Add an item").fill("Laptop issued");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByText(/Saved\. New salaries and leave follow this policy from today/)).toBeVisible();
    await expect(page.getByText("Starter policy", { exact: true })).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  test("a new hire is entered with a CTC and the salary is worked out from the policy", async () => {
    await page.goto("/payroll/onboard");
    await expect(page.getByText("Nobody is being onboarded")).toBeVisible();
    await page.getByRole("button", { name: "+ New hire" }).click();
    await page.getByLabel("Full name").fill("E2E Meera Nair");
    await page.getByLabel("Joining date").fill(iso(monthStart));
    await page.getByLabel("Work state code").fill("29");
    await page.getByLabel("Yearly CTC (₹)").fill("1200000");
    const salary = page.getByRole("table", { name: "Salary from the CTC" });
    await expect(salary.getByRole("row", { name: /Basic pay/ })).toContainText("₹40,000.00");         // 40% of 1,00,000 a month, from the policy just saved
    await expect(salary.getByRole("row", { name: /House rent allowance/ })).toContainText("₹16,000.00");
    await page.getByRole("button", { name: "Add new hire" }).click();
    await expect(page.getByText(/E2E Meera Nair was added as EMP-0001/)).toBeVisible();
    await expect(page.getByText(/CTC ₹12,00,000 a year/)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the checklist ticks itself from the record and onboarding finishes when the required items are done", async () => {
    const list = page.getByRole("list", { name: "Onboarding checklist for E2E Meera Nair" });
    await expect(list.getByLabel("Salary set from the CTC")).toBeChecked();
    await expect(list.getByLabel("Laptop issued")).not.toBeChecked();                                   // the item added to the policy
    const finish = page.getByRole("button", { name: "Finish onboarding E2E Meera Nair" });
    await expect(finish).toBeDisabled();
    await list.getByLabel("Offer letter signed").click();                                  // it ticks once the server has saved it
    await expect(list.getByLabel("Offer letter signed")).toBeChecked();
    await list.getByLabel("Identity and address proof collected").click();                                  // it ticks once the server has saved it
    await expect(list.getByLabel("Identity and address proof collected")).toBeChecked();
    await page.getByLabel("PAN of E2E Meera Nair").fill("ABCDE1234F");
    await page.getByRole("button", { name: "Save PAN" }).click();
    await expect(list.getByLabel("PAN on file")).toBeChecked();
    await page.getByLabel("Bank account of E2E Meera Nair").fill("501009876543");
    await page.getByLabel("IFSC").fill("HDFC0000123");
    await page.getByRole("button", { name: "Save bank account" }).click();
    await expect(page.getByText(/ready to activate/)).toBeVisible();
    await finish.click();
    await expect(page.getByText("Nobody is being onboarded")).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("unpaid leave is recorded, counted in working days and approved", async () => {
    await page.goto("/payroll/leave");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Leave");
    await page.getByRole("button", { name: "+ Record leave" }).click();
    await page.getByLabel("Kind of leave").selectOption("LOP");
    await page.getByLabel("First day").fill(iso(firstMonday));
    await page.getByLabel("Last day").fill(iso(tuesday));
    await page.getByLabel("Reason (optional)").fill("Personal");
    await page.getByRole("button", { name: "Record leave", exact: true }).click();
    await expect(page.getByText("2 days, unpaid · Personal")).toBeVisible();
    await expect(page.getByText("Waiting", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /^Approve leave of E2E Meera Nair/ }).click();
    await expect(page.getByText("Approved", { exact: true })).toBeVisible();
    await expect(page.getByText(/2 unpaid days will be deducted/)).toBeVisible();
    await page.getByRole("tab", { name: "Balances" }).click();
    const row = page.getByRole("table", { name: "Leave balances" }).getByRole("row", { name: /E2E Meera Nair/ });
    await expect(row).toContainText("2 taken");                                                         // leave without pay
    await expect(row).toContainText(/[0-9] left of [0-9]/);                                                   // paid leave untouched, pro-rated for someone who joined this month
    expect(problems()).toEqual([]);
  });

  test("a holiday is added and removed", async () => {
    await page.getByRole("tab", { name: "Holidays" }).click();
    await page.getByLabel("Holiday on").fill(`${now.getFullYear()}-12-25`);
    await page.getByLabel("Occasion").fill("Christmas");
    await page.getByRole("button", { name: "Add holiday" }).click();
    await expect(page.getByRole("list", { name: "Holiday list" })).toContainText("Christmas");
    await page.getByRole("button", { name: "Remove Christmas" }).click();
    await expect(page.getByText(/No holidays yet/)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the month's payroll run already has the new hire and the unpaid days", async () => {
    await page.goto("/payroll");
    await page.getByRole("button", { name: /^Start .+ run$/ }).click();
    const card = page.getByLabel("Before you compute");
    await expect(card).toContainText("Ready to compute");
    await expect(card).toContainText("Unpaid leave already filled in");
    await expect(card).toContainText("E2E Meera Nair 2");
    await expect(card).toContainText("Joining this month: E2E Meera Nair");
    await page.getByRole("button", { name: "Compute pay" }).click();
    await expect(page.getByRole("row", { name: /E2E Meera Nair/ })).toContainText("2 LOP");
    await expect(page.getByRole("button", { name: "Payslip of E2E Meera Nair" })).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the payslip is a PDF", async () => {
    const token = await apiToken(page, OWNER);
    const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
    const runs = await apiGet<{ data: Array<{ id: string }> }>(page, token, `/entities/${eid}/payroll-runs`);
    const slips = await apiGet<{ data: Array<{ id: string; employee_name: string }> }>(page, token, `/entities/${eid}/payroll-runs/${runs.data[0]!.id}/payslips`);
    const slip = slips.data.find((s) => s.employee_name === "E2E Meera Nair")!;
    const r = await page.request.get(`/api/v1/entities/${eid}/payslips/${slip.id}/pdf`, { headers: { Authorization: `Bearer ${token}` } });
    expect(r.status()).toBe(200);
    expect(r.headers()["content-type"]).toBe("application/pdf");
    expect((await r.body()).subarray(0, 5).toString()).toBe("%PDF-");
  });

  test("a policy document is published and an acknowledgement recorded", async () => {
    await page.goto("/payroll/policy");
    await page.getByRole("button", { name: "+ Policy document" }).click();
    await page.getByLabel("Title").fill("Leave policy");
    await page.getByLabel("About").selectOption("leave");
    await page.getByLabel("The policy", { exact: true }).fill("Earned leave is 15 days a year. Apply two working days ahead.");
    await page.getByRole("button", { name: "Publish policy" }).click();
    await expect(page.getByText(/Leave · version 1/)).toBeVisible();
    await expect(page.getByText("0 of 1 acknowledged")).toBeVisible();
    await page.getByRole("button", { name: "Open Leave policy" }).click();
    await page.getByRole("button", { name: "Record acknowledgement by E2E Meera Nair" }).click();
    await expect(page.getByText("Everyone has acknowledged this version.")).toBeVisible();
    await expect(page.getByText("1 of 1 acknowledged")).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the leave screen fits a phone", async () => {
    await page.goto("/payroll/leave");
    await expect(page.getByText("2 days, unpaid · Personal")).toBeVisible();
    await page.setViewportSize({ width: 375, height: 812 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
});
