import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signInQuietly, watch } from "./helpers";

const RAVI = "e2e-files-ravi@example.in";
const MIRA = "e2e-files-mira@example.in";
const TARA = "e2e-files-tara@example.in";
const RAVI_NAME = "E2E Files Ravi";
const MIRA_NAME = "E2E Files Mira";
const TARA_NAME = "E2E Files Tara";
// a 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

function nextWeekday(daysAhead: number): string {
  const d = new Date(); d.setDate(d.getDate() + daysAhead);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function openOwnPage(page: Page, email: string) {
  await signInQuietly(page, email);
  await page.getByLabel("Your employee page").getByRole("button", { name: /^Open my employee page at / }).click();
  await page.waitForURL("**/self");
}

/** Address, proofs, tasks, a manager who decides leave, and the invitation email. */
test.describe.serial("people files: the employee keeps them, HR reviews them, a manager decides leave", () => {
  let hr: Page;
  let ravi: Page;
  let mira: Page;
  let problems: () => string[];
  let raviProblems: () => string[];
  let miraProblems: () => string[];
  let eid = "";
  let raviId = "";

  test.beforeAll(async ({ browser, baseURL }) => {
    hr = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(hr);
    await signInQuietly(hr, OWNER);
    const token = await apiToken(hr, OWNER);
    eid = (await apiGet<{ entities: Array<{ id: string }> }>(hr, token, "/me")).entities[0]!.id;
    const hire = async (name: string, code: string, email: string) => {
      const made = await apiPost<{ employee: { id: string } }>(hr, token, `/entities/${eid}/employees/onboard`, { full_name: name, employee_code: code, date_of_joining: "2026-01-05", work_state: "29", ctc_annual: "900000.00", designation: "Staff", email });
      return made.employee.id;
    };
    raviId = await hire(RAVI_NAME, "FIL1", RAVI);
    const miraId = await hire(MIRA_NAME, "FIL2", MIRA);
    await apiPost(hr, token, `/entities/${eid}/employees/${raviId}/self-service`, {});
    await apiPost(hr, token, `/entities/${eid}/employees/${miraId}/self-service`, {});
  });
  test.afterAll(async () => { await ravi?.context().close(); await mira?.context().close(); await hr.context().close(); });

  test("the employee saves an address and an emergency contact", async ({ browser, baseURL }) => {
    ravi = await (await browser.newContext({ baseURL })).newPage();
    raviProblems = watch(ravi);
    await openOwnPage(ravi, RAVI);
    const panel = ravi.getByLabel("My address and emergency contact");
    await panel.getByLabel("Address line 1", { exact: true }).fill("12 Park Street");
    await expect(panel).toContainText("Give the whole address");
    await expect(panel.getByRole("button", { name: "Save" })).toBeDisabled();
    await panel.getByLabel("City", { exact: true }).fill("Bengaluru");
    await panel.getByLabel("State code (GST code, like 29)").fill("29");
    await panel.getByLabel("PIN code").fill("560001");
    await panel.getByLabel("Emergency contact: name").fill("Sita Rao");
    await panel.getByLabel("Relation").fill("Mother");
    await panel.getByLabel("Emergency contact: mobile (with country code)").fill("9812345678");
    await expect(panel).toContainText("Put the country code");
    await panel.getByLabel("Emergency contact: mobile (with country code)").fill("+919812345678");
    await panel.getByRole("button", { name: "Save" }).click();
    await expect(panel).toContainText("Saved.");
    await ravi.reload();
    await expect(ravi.getByLabel("My address and emergency contact").getByLabel("City", { exact: true })).toHaveValue("Bengaluru");
    expect(raviProblems()).toEqual([]);
  });

  test("they upload a proof, and only a PDF, JPEG or PNG is taken", async () => {
    const panel = ravi.getByLabel("My proofs");
    await panel.getByLabel("What it is").fill("Aadhaar card");
    await panel.getByLabel("File").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
    await expect(panel).toContainText("Upload a PDF, JPEG or PNG");
    await expect(panel.getByRole("button", { name: "Upload" })).toBeDisabled();
    await panel.getByLabel("File").setInputFiles({ name: "aadhaar.png", mimeType: "image/png", buffer: PNG });
    await panel.getByRole("button", { name: "Upload" }).click();
    const item = panel.getByRole("list", { name: "Uploaded files" });
    await expect(item).toContainText("Aadhaar card");
    await expect(item).toContainText("With HR");
    expect(raviProblems()).toEqual([]);
  });

  test("HR accepts the file, hands out a task and names the manager", async () => {
    await hr.goto("/payroll/leave");
    await hr.getByRole("tab", { name: "People files" }).click();
    const panel = hr.getByLabel("People files", { exact: true }).first();
    await panel.getByLabel("Person").selectOption({ label: `FIL1 · ${RAVI_NAME}` });
    const files = hr.getByLabel(`Files of ${RAVI_NAME}`);
    await expect(files.getByLabel("Address line 1", { exact: true })).toHaveValue("12 Park Street");
    await expect(files).toContainText("Aadhaar card");
    await files.getByRole("button", { name: "Accept Aadhaar card" }).click();
    await expect(files).toContainText("Accepted");
    await files.getByLabel("Ask them to").fill("Send your last payslip");
    await files.getByRole("button", { name: "Give task" }).click();
    await expect(files).toContainText("Send your last payslip");
    await files.getByRole("combobox", { name: "Reports to" }).selectOption({ label: `FIL2 · ${MIRA_NAME}` });
    await files.getByRole("button", { name: "Save manager" }).click();
    await expect(files).toContainText("Manager saved.");
    expect(problems()).toEqual([]);
  });

  test("the employee sees the decision and the task, and ticks it", async () => {
    await ravi.reload();
    await expect(ravi.getByLabel("My proofs").getByRole("list", { name: "Uploaded files" })).toContainText("Accepted");
    await expect(ravi.getByLabel("My proofs").getByRole("button", { name: "Remove Aadhaar card" })).toHaveCount(0);   // decided: it stays
    const tasks = ravi.getByLabel("My tasks");
    await expect(tasks).toContainText("1 open");
    await tasks.getByRole("checkbox", { name: "Send your last payslip" }).click();
    await expect(tasks.getByRole("checkbox", { name: "Send your last payslip" })).toBeChecked();
    await expect(tasks).not.toContainText("1 open");
    expect(raviProblems()).toEqual([]);
  });

  test("their manager approves their leave from their own page", async ({ browser, baseURL }) => {
    const rt = await apiToken(ravi, RAVI);
    const accept = await apiGet<{ employments: Array<{ employee_id: string }> }>(ravi, rt, "/me/employments");
    expect(accept.employments[0]!.employee_id).toBe(raviId);
    const types = await apiGet<{ data: Array<{ code: string; paid: boolean }> }>(hr, await apiToken(hr, OWNER), `/entities/${eid}/leave-types`);
    const paid = types.data.find((t) => t.paid)!;
    const day = nextWeekday(21);
    await apiPost(ravi, rt, `/self/${raviId}/leave-requests`, { leave_type: paid.code, from: day, to: day, reason: "Wedding" });

    mira = await (await browser.newContext({ baseURL })).newPage();
    miraProblems = watch(mira);
    await openOwnPage(mira, MIRA);
    const team = mira.getByLabel("My team");
    await expect(team).toContainText(RAVI_NAME);
    await expect(team).toContainText("report to you");
    await team.getByRole("button", { name: new RegExp(`^Approve leave of ${RAVI_NAME}`) }).click();
    await expect(team).toContainText("Approved");
    await expect(team.getByRole("button", { name: new RegExp(`^Approve leave of ${RAVI_NAME}`) })).toHaveCount(0);
    expect(miraProblems()).toEqual([]);
    // HR sees who decided
    await hr.goto("/payroll/leave");
    await expect(hr.getByText(`decided by ${MIRA_NAME}, their manager`)).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("an invitation is emailed when the sign-in is opened, and cannot be sent again at once", async () => {
    const token = await apiToken(hr, OWNER);
    await apiPost(hr, token, `/entities/${eid}/employees/onboard`, { full_name: TARA_NAME, employee_code: "FIL3", date_of_joining: "2026-01-05", work_state: "29", ctc_annual: "900000.00", designation: "Staff", email: TARA });
    await hr.goto("/payroll/leave");
    await hr.getByRole("tab", { name: "Employee sign-in" }).click();
    const panel = hr.getByLabel("Employees' own sign-in");
    await expect(panel).toContainText("Opening it emails them an invitation");
    await panel.getByRole("button", { name: `Open sign-in for ${TARA_NAME}` }).click();
    await expect(panel).toContainText("Emailed; waiting for them to sign in and accept");
    await panel.getByRole("button", { name: `Send the invitation again to ${TARA_NAME}` }).click();
    await expect(hr.getByText("An invitation was sent a moment ago")).toBeVisible();
    expect(problems().filter((p) => !/409/.test(p))).toEqual([]);
  });
});
