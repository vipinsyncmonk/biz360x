import { expect, test, type Page } from "@playwright/test";
import { OWNER, signInQuietly, watch } from "./helpers";

// The owner invites; the invited person then signs in. Both sign in without a code (see signInQuietly): the hourly codes are used up by other files.
const ACCOUNTANT = "e2e-accountant@example.in";

/** An owner invites an Accountant from a preset; the Accountant lands on a finance home with only the screens they can use. */
test.describe.serial("roles: invite with a preset, and what that person sees", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("the owner invites an accountant from a preset and adjusts one line", async () => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "+ Invite person" }).click();
    const form = page.getByRole("form", { name: "Invite a person" });
    await form.getByLabel("Work email").fill(ACCOUNTANT);
    await form.getByLabel("Name (optional)").fill("Anil Accounts");
    await form.getByLabel("Start from a role").selectOption("accountant");
    await expect(form.getByLabel("Title (optional)")).toHaveValue("Accountant");
    await expect(form.getByText(/Does not approve, file or see payroll/)).toBeVisible();
    await expect(form.getByLabel("Finance and books")).toHaveValue("write");
    await expect(form.getByLabel("Payroll and salaries")).toHaveValue("");                    // no access
    await form.getByLabel("Document vault").selectOption("read");                               // one line changed from the preset
    await form.getByRole("button", { name: "Send invite" }).click();
    const row = page.getByRole("row", { name: /Anil Accounts/ }).first();          // the person’s row; the editor opens in a row below it
    await expect(row).toContainText("Accountant");
    await expect(row).toContainText("Finance and books: Prepare");
    await expect(row).toContainText("Document vault: View");
    await expect(row).not.toContainText("Payroll");
    expect(problems()).toEqual([]);
  });

  test("the accountant signs in to a finance home and a sidebar without payroll or settings", async ({ browser, baseURL }) => {
    const theirs = await (await browser.newContext({ baseURL })).newPage();
    const theirProblems = watch(theirs);
    await signInQuietly(theirs, ACCOUNTANT);
    await expect(theirs.getByText(/· Accountant$/)).toBeVisible();
    const tiles = theirs.getByLabel("Your numbers");
    await expect(tiles).toContainText("Cash in bank");
    await expect(tiles).toContainText("Value of stock");
    await expect(theirs.getByRole("heading", { name: "Waiting on you" })).toBeVisible();
    await expect(theirs.getByRole("heading", { name: "Next 5 weeks" })).toBeVisible();         // the preset can read compliance, so the calendar is there
    const nav = theirs.getByRole("navigation", { name: "Workspace" });
    await expect(nav.getByRole("link", { name: "Finance & Books" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Inventory" })).toBeVisible();
    for (const hidden of ["Payroll & HR", "Settings", "Startup", "Legal & Contracts", "Group view"]) await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);
    await theirs.goto("/payroll");                                                              // typing the address does not get them in
    await expect(theirs.getByRole("alert").filter({ hasText: /permission/i }).first()).toBeVisible();
    expect(theirProblems()).toEqual([]);
    await theirs.context().close();
  });

  test("the owner makes them an HR manager and the access follows", async ({ browser, baseURL }) => {
    await page.getByRole("button", { name: "Change access of Anil Accounts" }).click();
    const form = page.getByRole("form", { name: "Access of Anil Accounts" });
    await form.getByLabel("Start from a role").selectOption("hr_manager");
    await form.getByLabel("Title (optional)").fill("HR Manager");
    await form.getByRole("button", { name: "Save access" }).click();
    const row = page.getByRole("row", { name: /Anil Accounts/ }).first();          // the person’s row; the editor opens in a row below it
    await expect(row).toContainText("HR Manager");
    await expect(row).toContainText("HR: onboarding, leave and policies: Approve");
    const theirs = await (await browser.newContext({ baseURL })).newPage();
    await signInQuietly(theirs, ACCOUNTANT);
    await expect(theirs.getByLabel("Your numbers")).toContainText("Leave waiting for a decision");
    const nav = theirs.getByRole("navigation", { name: "Workspace" });
    await expect(nav.getByRole("link", { name: "Payroll & HR" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Finance & Books" })).toHaveCount(0);
    await theirs.context().close();
    expect(problems()).toEqual([]);
  });
});
