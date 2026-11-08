import { expect, test, type Page } from "@playwright/test";
import { OWNER, signIn, watch } from "./helpers";

const MAKER = "priya@example.in";   // can use the copilot, cannot approve

/** The copilot answers from the records, cites them, and only suggests: a different person has to approve before anything happens. */
test.describe.serial("copilot", () => {
  let asker: Page;
  let approver: Page;
  let askerProblems: () => string[];

  test.beforeAll(async ({ browser, baseURL }) => {
    asker = await (await browser.newContext({ baseURL })).newPage();
    approver = await (await browser.newContext({ baseURL })).newPage();
    askerProblems = watch(asker);
    await signIn(asker, MAKER);
    await signIn(approver, OWNER);
  });
  test.afterAll(async () => { await asker.context().close(); await approver.context().close(); });

  test("opens, offers questions, and answers with sources", async () => {
    await asker.goto("/copilot");
    await expect(asker.getByRole("heading", { name: "Ask about your company" })).toBeVisible();
    await asker.getByRole("button", { name: "What filings are due or overdue this week?" }).click();
    const answer = asker.getByRole("article", { name: "Answer" });
    await expect(answer).toContainText(/open filings?/);
    await expect(answer.getByRole("button", { name: "Source 1" })).toBeVisible();
    await expect(asker.getByRole("complementary", { name: "Sources" }).getByRole("link").first()).toBeVisible();
    await expect(asker.locator("div.error[role=alert]")).toHaveCount(0);
  });

  test("a suggested action waits for someone else to approve it", async () => {
    const card = asker.getByRole("article", { name: "Answer" }).locator("div", { hasText: "Proposed action" }).first();
    await expect(card.getByText("Proposed", { exact: true })).toBeVisible();
    await card.getByRole("button", { name: "Send for approval" }).click();
    await card.getByRole("button", { name: "Send", exact: true }).click();
    await expect(card.getByText("Waiting for approval")).toBeVisible();

    // the person who asked cannot approve it; the owner can, from Today
    await approver.goto("/");
    const row = approver.locator(".row", { hasText: "Copilot:" }).first();
    await expect(row.getByText("Copilot suggestion")).toBeVisible();
    await row.getByRole("button", { name: "Approve" }).click();
    await expect(approver.locator(".row", { hasText: "Copilot:" })).toHaveCount(0);

    await card.getByRole("button", { name: "Check again" }).click();
    await expect(card.getByText("Done", { exact: true })).toBeVisible();
    await expect(card.getByText(/task was added|Follow-up tasks added/)).toBeVisible();
    expect(askerProblems()).toEqual([]);
  });

  test("a conversation is kept and listed", async () => {
    await asker.goto("/copilot");
    await expect(asker.getByRole("region", { name: "Conversations" }).getByRole("button", { name: /What filings are due/ })).toBeVisible();
  });
});
