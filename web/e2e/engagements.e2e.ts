import { expect, test, type Page } from "@playwright/test";
import { FIRM, OWNER, signInQuietly, watch } from "./helpers";

// The owner engages; the invited practitioner then signs in. All sign in without a code (see signInQuietly): the hourly codes are used up by other files.
const CONSULTANT = "e2e-gst-consultant@example.in";

/** A company invites a GST consultant; the consultant, who has no workspace, accepts and sees only what the engagement gives. */
test.describe.serial("engagements: invite a practitioner, and what they see once they accept", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("an auditor can only be given viewing, and needs an end date", async () => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "+ Engage practitioner" }).click();
    const form = page.getByRole("form", { name: "Engage a practitioner" });
    await form.getByLabel("Practitioner or firm").fill("E2E Auditor");
    await form.getByLabel("Start from").selectOption("statutory_auditor");
    await expect(form.getByLabel("Engaged for")).toHaveValue("statutory_audit");
    await expect(form.getByLabel("Finance and books: practitioner access").locator("option")).toHaveText(["No access", "View", "View and query"]);
    await expect(form.getByRole("alert")).toContainText("An audit engagement needs an end date");
    await expect(form.getByRole("button", { name: "Save record" })).toBeDisabled();
    await form.getByRole("button", { name: "Cancel" }).click();
  });

  test("the owner invites a GST consultant from a preset", async () => {
    await page.getByRole("button", { name: "+ Engage practitioner" }).click();
    const form = page.getByRole("form", { name: "Engage a practitioner" });
    await form.getByLabel("Practitioner or firm").fill("E2E Gita GST");
    await form.getByLabel("Their email").fill(CONSULTANT);
    await form.getByLabel("Start from").selectOption("gst_consultant");
    await expect(form.getByLabel("Engaged for")).toHaveValue("gst");
    await expect(form.getByLabel("GST: practitioner access")).toHaveValue("certify");
    await expect(form.getByLabel("Payroll and salaries: practitioner access")).toHaveValue("");
    await form.getByRole("button", { name: "Send invitation" }).click();
    await expect(page.getByText(`GST · invited at ${CONSULTANT}`, { exact: false })).toBeVisible();
    await expect(page.getByText("Invited", { exact: true })).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the consultant has no workspace, sees the invitation, accepts and lands in the company with GST only", async ({ browser, baseURL }) => {
    const theirs = await (await browser.newContext({ baseURL })).newPage();
    const theirProblems = watch(theirs);
    await signInQuietly(theirs, CONSULTANT);
    const card = theirs.getByLabel("Invitations");
    await expect(card).toContainText(/invites you for GST/);
    await expect(card).toContainText("GST: Prepare and file");
    await card.getByRole("button", { name: /^Accept the invitation from/ }).click();
    await theirs.waitForURL((u) => u.pathname === "/");
    await expect(theirs.getByText(/· GST$/)).toBeVisible();                                    // the title is the service they are engaged for
    const nav = theirs.getByRole("navigation", { name: "Workspace" });
    await expect(nav.getByRole("link", { name: "GST & Tax" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Sales & Billing" })).toBeVisible();
    for (const hidden of ["Payroll & HR", "Finance & Books", "Settings", "Compliance"]) await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);
    await expect(theirs.getByLabel("Invitations")).toHaveCount(0);                             // answered
    await expect(theirs.getByLabel("Your work")).toBeVisible();
    expect(theirProblems()).toEqual([]);
    await theirs.context().close();
  });

  test("the company sees the engagement as active and can name the consultant to prepare GST", async () => {
    await page.reload();
    await expect(page.getByText("GST · as a person", { exact: false })).toBeVisible();
    const who = page.getByLabel("Who does what");
    await expect(who.getByLabel("Prepares GST returns")).toHaveValue(/.+/);                    // taken on when they accepted
    await expect(who.getByLabel("Prepares GST returns").locator("option:checked")).toHaveText("E2E Gita GST · GST");
    await expect(who.getByLabel("Approves GST returns").locator("option", { hasText: "E2E Gita GST" })).toHaveCount(0);     // a practitioner never approves
    expect(problems()).toEqual([]);
  });

  test("a firm's partner sees who works on which client", async ({ browser, baseURL }) => {
    const firm = await (await browser.newContext({ baseURL })).newPage();
    const firmProblems = watch(firm);
    await signInQuietly(firm, FIRM);
    await firm.getByRole("navigation", { name: "Workspace" }).getByRole("link", { name: "Group view" }).click();      // only people in a firm have this link
    await firm.waitForURL("**/group");
    const panel = firm.getByLabel("Who works on which client");
    await expect(panel.getByText(/Staff see only the clients ticked for them/)).toBeVisible();
    await expect(panel).not.toContainText("Loading");
    expect(firmProblems()).toEqual([]);
    await firm.context().close();
  });
});
