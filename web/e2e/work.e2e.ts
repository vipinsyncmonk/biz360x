import { expect, test, type Page } from "@playwright/test";
import { OWNER, signInQuietly, watch } from "./helpers";

// The owner sets who does what. Signs in without a code (see signInQuietly): the demo people's hourly codes are used up by other files.
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** The owner names who prepares GST, the open GST filings land in that person's queue, and a task is given and finished. */
test.describe.serial("work: who does what, and the queue it fills", () => {
  let page: Page;
  let problems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    page = await (await browser.newContext({ baseURL })).newPage();
    problems = watch(page);
    await signInQuietly(page, OWNER);
  });
  test.afterAll(async () => { await page.context().close(); });

  test("the owner names who prepares and approves GST, and open filings follow", async () => {
    await page.goto("/settings");
    const section = page.getByLabel("Who does what");
    await expect(section.getByText(/never to the person who raised it/)).toBeVisible();
    await expect(section.getByRole("row", { name: /Payroll run/ })).toContainText("Whoever starts it");
    const save = section.getByRole("button", { name: "Save who does what" });
    await expect(save).toBeDisabled();                                                      // nothing changed yet
    await section.getByLabel("Prepares GST returns").selectOption({ label: "Ananya Rao" });
    await section.getByLabel("Approves GST returns").selectOption({ label: "Ananya Rao" });
    await save.click();
    await expect(section.getByRole("status")).toContainText("Saved");
    await expect(save).toBeDisabled();
    await page.reload();
    await expect(page.getByLabel("Who does what").getByLabel("Prepares GST returns")).not.toHaveValue("");
    expect(problems()).toEqual([]);
  });

  test("those filings are now in the owner's queue, soonest first", async () => {
    await page.goto("/");
    const queue = page.getByLabel("Your work");
    await expect(queue.getByText(/^Filing/).first()).toBeVisible();
    await expect(queue.getByRole("link", { name: /^Open GST/ }).first()).toBeVisible();
    await expect(queue.getByText("Nothing is waiting on you here.")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  test("a task is given to a person, shows in their queue and is finished", async () => {
    const queue = page.getByLabel("Your work");
    await queue.getByRole("button", { name: "+ Task" }).click();
    const form = queue.getByRole("form", { name: "Add a task" });
    await expect(form.getByRole("button", { name: "Add task" })).toBeDisabled();
    await form.getByLabel("What needs doing").fill("Renew the trade licence");
    await form.getByLabel("Give it to").selectOption({ label: "Ananya Rao" });
    await form.getByLabel("Due (optional)").fill(iso(new Date()));
    await form.getByRole("button", { name: "Add task" }).click();
    const row = queue.locator(".row").filter({ hasText: "Renew the trade licence" });
    await expect(row).toContainText("Task");
    await expect(row).toContainText("Due today");
    await row.getByRole("button", { name: "Mark done: Renew the trade licence" }).click();
    await expect(queue.getByText("Renew the trade licence")).toHaveCount(0);
    expect(problems()).toEqual([]);
  });

  test("the queue fits a phone", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
});
