import { expect, test } from "@playwright/test";
import { signIn, watch } from "./helpers";

const CSV = [
  "Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance",
  '02/04/23,NEFT CR-ACME TRADERS-E2E ALPHA,HDFCR52023040200111,,"10,000.00","10,000.00"',
  '03/04/23,UPI-E2E BETA,402211223399,"2,500.00",,"7,500.00"',
  "31/02/23,Not a real date,,5.00,,",
].join("\n");

/** A bank statement CSV is read, checked against the account, imported, and a second import adds nothing. */
test("importing a bank statement", async ({ browser, baseURL }) => {
  const page = await (await browser.newContext({ baseURL })).newPage();
  const problems = watch(page);
  await signIn(page, "priya@example.in");

  await page.goto("/finance/import");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Import a bank statement");
  await expect(page.getByRole("heading", { name: "Add the bank account first" })).toBeVisible();     // a new workspace has no bank account
  await page.getByLabel("Bank", { exact: true }).fill("HDFC Bank");
  await page.getByLabel("Last four digits").fill("4412");
  await page.getByRole("button", { name: "Add bank account" }).click();
  await expect(page.getByLabel("Bank account")).toContainText("HDFC Bank ····4412");
  const send = async () => {
    await page.locator('input[type="file"]').setInputFiles({ name: "apr-2023.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
    await expect(page.getByText("2 lines", { exact: true })).toBeVisible();
  };
  await send();
  await expect(page.getByText("Running balance agrees")).toBeVisible();
  await expect(page.getByText("1 row skipped")).toBeVisible();
  await expect(page.getByText("E2E ALPHA", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "Check against the account" }).click();
  await expect(page.getByText("2 new, 0 already on the account")).toBeVisible();
  await page.getByRole("button", { name: "Import 2 lines" }).click();
  await expect(page.getByRole("status")).toContainText("Imported 2 lines");

  await send();                                                   // the same file again: everything is already there
  await page.getByRole("button", { name: "Check against the account" }).click();
  await expect(page.getByText("0 new, 2 already on the account")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Import 0 line/ })).toBeDisabled();

  await page.goto("/finance");
  await expect(page.getByText("E2E ALPHA").first()).toBeVisible();
  expect(problems()).toEqual([]);
});
