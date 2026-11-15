import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signInQuietly, watch } from "./helpers";

// Everyone signs in without a code (see signInQuietly): the hourly codes are used up by other files.
const NEHA = "e2e-details-neha@example.in";
const NAME = "E2E Details Neha";

/** An employee ticks their own checklist item and asks for a PAN and bank account; HR confirms before the record changes. */
test.describe.serial("self-service details: the employee asks, HR confirms", () => {
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
    const made = await apiPost<{ employee: { id: string } }>(page, token, `/entities/${eid}/employees/onboard`, { full_name: NAME, employee_code: "DET1", date_of_joining: "2026-01-05", work_state: "29",
      ctc_annual: "900000.00", designation: "Analyst", email: NEHA });
    await apiPost(page, token, `/entities/${eid}/employees/${made.employee.id}/self-service`, {});
  });
  test.afterAll(async () => { await theirs?.context().close(); await page.context().close(); });

  test("the employee ticks the items that are theirs and only those", async ({ browser, baseURL }) => {
    theirs = await (await browser.newContext({ baseURL })).newPage();
    theirProblems = watch(theirs);
    await signInQuietly(theirs, NEHA);
    await theirs.getByLabel("Your employee page").getByRole("button", { name: /^Open my employee page at / }).click();
    await theirs.waitForURL("**/self");
    const list = theirs.getByLabel("Joining checklist");
    await expect(list).toContainText("Add your PAN and bank account under My details");
    const box = list.getByRole("checkbox", { name: /PF declaration/ });
    await box.click();
    await expect(box).toBeChecked();
    await expect(list.getByRole("checkbox")).toHaveCount(2);                                   // PF declaration and tax regime; the rest are HR's
    await box.click();
    await expect(box).not.toBeChecked();
    expect(theirProblems()).toEqual([]);
  });

  test("they ask for a PAN and a bank account, and the record stays as it was", async () => {
    const panel = theirs.getByLabel("My details");
    const send = panel.getByRole("button", { name: "Send to HR" });
    await expect(send).toBeDisabled();
    await panel.getByLabel("PAN").fill("ABCPN1234K");
    await panel.getByLabel("Salary account number").fill("123456789012");
    await expect(panel).toContainText("Give the account number and the IFSC together");
    await expect(send).toBeDisabled();
    await panel.getByLabel("IFSC").fill("HDFC0001234");
    await send.click();
    await expect(panel).toContainText("Waiting for HR: PAN ending 234K and bank account ending 9012");
    await expect(panel).toContainText("PAN not on file");
    expect(theirProblems()).toEqual([]);
  });

  test("HR sees it beside the record and confirms it", async () => {
    await page.goto("/payroll/leave");
    await page.getByRole("tab", { name: "Detail changes" }).click();
    const panel = page.getByLabel("Detail changes", { exact: true }).first();
    await expect(panel).toContainText(NAME);
    await expect(panel).toContainText("ABCPN1234K");
    await expect(panel).toContainText("…9012 (HDFC0001234)");
    await page.getByRole("button", { name: `Confirm change for ${NAME}` }).click();
    await expect(panel).toContainText("No changes waiting");
    expect(problems()).toEqual([]);
  });

  test("the employee's record now has them, and the checklist items tick themselves", async () => {
    await theirs.reload();
    await expect(theirs.getByLabel("My record")).toContainText("ends 234K");
    await expect(theirs.getByLabel("My details")).toContainText("HR confirmed your last change");
    await expect(theirs.getByLabel("Joining checklist").getByText("PAN on file: done")).toBeVisible();
    expect(theirProblems()).toEqual([]);
  });

  test("a refused change shows the reason", async () => {
    const panel = theirs.getByLabel("My details");
    await panel.getByLabel("Mobile (with country code)").fill("+919800000077");
    await panel.getByRole("button", { name: "Send to HR" }).click();
    await expect(panel).toContainText("Waiting for HR: a new mobile number");
    await page.reload();
    await page.getByRole("tab", { name: "Detail changes" }).click();
    const refuse = page.getByRole("button", { name: `Refuse change for ${NAME}` });
    await expect(refuse).toBeDisabled();                                                      // a reason is needed
    await page.getByLabel(`Reason for refusing ${NAME}`).fill("Use the number on your offer letter");
    await refuse.click();
    await theirs.reload();
    await expect(theirs.getByLabel("My details")).toContainText("HR did not accept your last change: Use the number on your offer letter");
    expect(problems()).toEqual([]);
  });
});
