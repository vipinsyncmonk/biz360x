import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signInQuietly, watch } from "./helpers";

// Everyone signs in without a code (see signInQuietly): the hourly codes are used up by other files.
const MEERA = "e2e-tax-meera@example.in";
const NAME = "E2E Tax Meera";

/** An employee declares a regime and a deduction, HR verifies it, and the employee sees it verified. */
test.describe.serial("tax declarations: the employee declares, HR verifies", () => {
  let page: Page;                 // the owner, acting as HR
  let theirs: Page;               // the employee
  let problems: () => string[];
  let theirProblems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
    const token = await apiToken(page, OWNER);
    const me = await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me");
    const eid = me.entities[0]!.id;
    const made = await apiPost<{ employee: { id: string } }>(page, token, `/entities/${eid}/employees/onboard`, { full_name: NAME, employee_code: "TAX1", date_of_joining: "2026-01-05", work_state: "29",
      ctc_annual: "1500000.00", designation: "Analyst", email: MEERA });
    await apiPost(page, token, `/entities/${eid}/employees/${made.employee.id}/self-service`, {});
  });
  test.afterAll(async () => { await theirs?.context().close(); await page.context().close(); });

  test("the employee accepts, chooses the old regime and sends a deduction to HR", async ({ browser, baseURL }) => {
    theirs = await (await browser.newContext({ baseURL })).newPage();
    theirProblems = watch(theirs);
    await signInQuietly(theirs, MEERA);
    await theirs.getByLabel("Your employee page").getByRole("button", { name: /^Open my employee page at / }).click();
    await theirs.waitForURL("**/self");
    const panel = theirs.getByLabel("My tax declaration");
    await expect(panel).toContainText("Not started");
    await panel.getByLabel(/^Tax regime for /).selectOption("old");
    await panel.getByRole("button", { name: "Add a deduction" }).click();
    await expect(panel.getByRole("button", { name: "Send to HR" })).toBeDisabled();                  // a line with nothing in it
    await panel.getByLabel("What it is").fill("PPF");
    await panel.getByLabel("Amount for the year (₹)").fill("100000");
    await panel.getByLabel("Proof (policy or receipt no.)").fill("PPF-778");
    await panel.getByRole("button", { name: "Send to HR" }).click();
    await expect(panel).toContainText("With HR");
    await expect(panel.getByRole("button", { name: "Send to HR" })).toHaveCount(0);                  // locked while HR looks at it
    expect(theirProblems()).toEqual([]);
  });

  test("HR sees it waiting, verifies a lower amount for the figure", async () => {
    await page.goto("/payroll/leave");
    await page.getByRole("tab", { name: "Tax declarations" }).click();
    const list = page.getByLabel("Tax declarations", { exact: true }).first();
    await expect(list).toContainText(NAME);
    await expect(list).toContainText("With HR");
    await page.getByRole("button", { name: `Review ${NAME}` }).click();
    const detail = page.getByRole("region", { name: `Declaration of ${NAME}` });
    await expect(detail).toContainText("Proof: PPF-778");
    await detail.getByLabel("Verified amount for PPF").fill("80000");
    await detail.getByRole("button", { name: "Verify" }).click();
    await expect(detail).toContainText("Verified ₹80,000.00 of ₹1,00,000.00");
    await expect(page.getByLabel("Tax declarations", { exact: true }).first()).toContainText("Verified");
    expect(problems()).toEqual([]);
  });

  test("the employee sees it verified and cannot change it", async () => {
    await theirs.reload();
    const panel = theirs.getByLabel("My tax declaration");
    await expect(panel).toContainText("Verified");
    await expect(panel).toContainText("Verified ₹80,000.00 of ₹1,00,000.00");
    await expect(panel.getByRole("button", { name: "Save draft" })).toHaveCount(0);
    await panel.getByRole("button", { name: "Show Form 16 Part B" }).click();
    await expect(panel.getByLabel(/^Form 16 Part B for /)).toContainText("Taxable income");
    expect(theirProblems()).toEqual([]);
  });

  test("HR returns it with a note and the employee can change it again", async () => {
    await page.reload();
    await page.getByRole("tab", { name: "Tax declarations" }).click();
    await page.getByRole("button", { name: `Review ${NAME}` }).click();
    const detail = page.getByRole("region", { name: `Declaration of ${NAME}` });
    const back = detail.getByRole("button", { name: "Return with note" });
    await expect(back).toBeDisabled();
    await detail.getByLabel("Why you are returning it").fill("Attach the PPF passbook");
    await back.click();
    await expect(detail).toContainText("Sent back with: Attach the PPF passbook");
    await theirs.reload();
    const panel = theirs.getByLabel("My tax declaration");
    await expect(panel).toContainText("HR's note: Attach the PPF passbook");
    await expect(panel.getByRole("button", { name: "Send to HR" })).toBeEnabled();
    expect(problems()).toEqual([]);
  });
});
