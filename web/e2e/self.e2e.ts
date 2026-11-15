import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signInQuietly, watch } from "./helpers";

// Everyone signs in without a code (see signInQuietly): the hourly codes are used up by other files.
const KIRAN = "e2e-kiran@example.in";
const NAME = "E2E Self Kiran";
const POLICY = "E2E Self-service policy";

/** A weekday at least two weeks ahead, as YYYY-MM-DD, so one day of leave is never a weekly off. */
function aWednesday(): string {
  const d = new Date();
  d.setDate(d.getDate() + 14);
  d.setDate(d.getDate() + ((3 - d.getDay() + 7) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** HR opens an employee's own sign-in; the employee, who is not a member of the company, accepts and uses their own page. */
test.describe.serial("employee self-service: HR opens it, and what the employee sees and does", () => {
  let page: Page;                 // the owner
  let theirs: Page;               // the employee
  let problems: () => string[];
  let theirProblems: () => string[];
  let company = "";

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
    const token = await apiToken(page, OWNER);
    const me = await apiGet<{ entities: Array<{ id: string; legal_name: string }> }>(page, token, "/me");
    const eid = me.entities[0]!.id;
    company = me.entities[0]!.legal_name;
    await apiPost(page, token, `/entities/${eid}/employees/onboard`, { full_name: NAME, employee_code: "SELF1", date_of_joining: "2026-01-05", work_state: "29", ctc_annual: "900000.00",
      designation: "Analyst", email: KIRAN });
    await apiPost(page, token, `/entities/${eid}/hr/policy-documents`, { title: POLICY, category: "conduct", body: "Treat colleagues with respect. Keep company data safe." });
  });
  test.afterAll(async () => { await theirs?.context().close(); await page.context().close(); });

  test("HR opens the sign-in for a person from the leave screen", async () => {
    await page.goto("/payroll/leave");
    await page.getByRole("tab", { name: "Employee sign-in" }).click();
    const panel = page.getByLabel("Employees' own sign-in");
    await expect(panel).toContainText("They are not a member of the company and see nothing else");
    await page.getByRole("button", { name: `Open sign-in for ${NAME}` }).click();
    await expect(page.getByRole("button", { name: `Withdraw for ${NAME}` })).toBeVisible();
    await expect(panel).toContainText("waiting for them to sign in and accept");
    expect(problems()).toEqual([]);
  });

  test("the employee has no workspace, sees the invitation and lands on their own page", async ({ browser, baseURL }) => {
    theirs = await (await browser.newContext({ baseURL })).newPage();
    theirProblems = watch(theirs);
    await signInQuietly(theirs, KIRAN);
    const card = theirs.getByLabel("Your employee page");
    await expect(card).toContainText(`${company} has opened your employee page`);
    await expect(card).toContainText("You will not see anything else of the company");
    await card.getByRole("button", { name: `Open my employee page at ${company}` }).click();
    await theirs.waitForURL("**/self");
    await expect(theirs.getByRole("heading", { level: 1 })).toHaveText(company);
    await expect(theirs.getByText(`${NAME} · SELF1 · Analyst`)).toBeVisible();
    const record = theirs.getByLabel("My record");
    await expect(record).toContainText("Joining");
    await expect(record.getByLabel("For you to do")).toContainText(/Read and acknowledge \d+ polic/);
    await expect(theirs.getByRole("link", { name: "Company workspace" })).toHaveCount(0);
    await expect(theirs.getByLabel("My payslips")).toContainText("No payslips yet");
    expect(theirProblems()).toEqual([]);
  });

  test("they apply for leave, see it waiting, and withdraw it", async () => {
    const leave = theirs.getByLabel("My leave");
    await expect(leave.getByLabel("Leave balances")).toContainText(/left of/);
    await leave.getByRole("button", { name: "Apply for leave" }).click();
    const form = theirs.getByRole("form", { name: "Apply for leave" });
    await expect(form.getByRole("button", { name: "Send request" })).toBeDisabled();
    const day = aWednesday();
    await form.getByLabel("First day").fill(day);
    await form.getByLabel("Last day").fill(day);
    await form.getByLabel("Reason (optional)").fill("Family function");
    await form.getByRole("button", { name: "Send request" }).click();
    await expect(leave.getByRole("status")).toContainText("Your request for 1 day is with your company for approval");
    await expect(leave.getByText("Waiting", { exact: true })).toBeVisible();
    await expect(leave.getByLabel("Leave balances")).toContainText("1 waiting");
    await leave.getByRole("button", { name: /^Withdraw my / }).click();
    await expect(leave.getByText("Withdrawn", { exact: true })).toBeVisible();
    await expect(leave.getByRole("button", { name: /^Withdraw my / })).toHaveCount(0);
    expect(theirProblems()).toEqual([]);
  });

  test("they read a policy and acknowledge it themselves", async () => {
    const policies = theirs.getByLabel("Company policies");
    await policies.getByRole("button", { name: `Read ${POLICY}` }).click();
    await expect(policies.getByLabel(`${POLICY}, the text`)).toContainText("Treat colleagues with respect");
    await policies.getByRole("button", { name: "I have read and accept this" }).click();
    await expect(policies.getByText(/^You acknowledged this on /)).toBeVisible();
    await expect(policies.getByRole("button", { name: "I have read and accept this" })).toHaveCount(0);
    expect(theirProblems()).toEqual([]);
  });

  test("the company's screens are closed to them: every one leads back to their own page", async () => {
    for (const path of ["/", "/payroll", "/finance", "/settings"]) {
      await theirs.goto(path);
      await theirs.waitForURL("**/self");
    }
    await expect(theirs.getByRole("heading", { level: 1 })).toHaveText(company);
    expect(theirProblems().filter((p) => !p.startsWith("page error"))).toEqual([]);
  });

  test("the page fits a phone", async () => {
    await theirs.setViewportSize({ width: 390, height: 844 });
    await theirs.goto("/self");
    await expect(theirs.getByLabel("My leave")).toBeVisible();
    const wide = await theirs.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(wide).toBeLessThanOrEqual(1);
    await theirs.setViewportSize({ width: 1280, height: 800 });
  });

  test("HR sees the sign-in as active and ends it; the employee's page closes at once", async () => {
    await page.reload();
    await page.getByRole("tab", { name: "Employee sign-in" }).click();
    const end = page.getByRole("button", { name: `End access for ${NAME}` });
    await expect(end).toBeVisible();
    await page.getByRole("tab", { name: /^Requests/ }).click();
    await expect(page.getByText(NAME).first()).toBeVisible();                        // the leave they applied for reached HR
    await page.getByRole("tab", { name: "Employee sign-in" }).click();
    await end.click();
    await expect(page.getByRole("button", { name: `Open sign-in for ${NAME}` })).toBeVisible();
    await theirs.goto("/self");
    await expect(theirs.getByRole("heading", { level: 1 })).toHaveText("No employee page yet");
    expect(problems()).toEqual([]);
  });
});
