import { expect, test } from "@playwright/test";
import { signIn, watch } from "./helpers";

const csv = (s: string) => ({ name: "book.csv", mimeType: "text/csv", buffer: Buffer.from(s) });
const TB_BAD = "Account code,Account name,Type,Debit,Credit\nE91,E2E bank,asset,500000.00,\nE93,E2E capital,equity,,400000.00\n";
const TB_OK = "Account code,Account name,Type,Debit,Credit\nE91,E2E bank,asset,500000.00,\nE93,E2E capital,equity,,500000.00\n";
const INV = "Invoice no,Date,Customer,Customer GSTIN,Taxable value,CGST,SGST,Total,Outstanding\nE2E/001,10/02/2026,E2E Customer,29AABCE2222F1Z5,100000,9000,9000,118000,118000\n";

/** The opening position from earlier books: a file with a problem is refused with the row, a good one is checked, imported, listed and undone. */
test("importing the opening position", async ({ browser, baseURL }) => {
  const page = await (await browser.newContext({ baseURL })).newPage();
  const problems = watch(page);
  await signIn(page, "priya@example.in");

  await page.goto("/finance/history");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Opening position");
  await expect(page.getByText("Nothing imported yet.")).toBeVisible();

  const file = page.locator('input[type="file"]');
  await file.setInputFiles(csv(TB_BAD));
  await expect(page.getByText("2 rows", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Check the file" }).click();
  await expect(page.getByText("does not tie")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Import \d/ })).toHaveCount(0);

  await file.setInputFiles(csv(TB_OK));
  await page.getByRole("button", { name: "Check the file" }).click();
  await expect(page.getByText("Checked: nothing is wrong with 2 rows")).toBeVisible();
  await page.getByRole("button", { name: "Import 2 rows" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Imported 2 lines" })).toBeVisible();

  await page.getByRole("tab", { name: "Open sales invoices" }).click();
  await file.setInputFiles(csv(INV));
  await page.getByRole("button", { name: "Check the file" }).click();
  await expect(page.getByText("Checked: nothing is wrong with 1 row")).toBeVisible();
  await page.getByRole("button", { name: "Import 1 rows" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Imported 1 invoices" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Open sales invoices" })).toBeVisible();

  while (await page.getByRole("button", { name: "Undo", exact: true }).count()) {            // leave the books as they were
    await page.getByRole("button", { name: "Undo", exact: true }).first().click();
    await expect(page.getByRole("status").filter({ hasText: "Import undone." })).toBeVisible();
    await page.waitForTimeout(200);
  }
  await expect(page.getByText(/Undone/).first()).toBeVisible();
  expect(problems()).toEqual([]);
});
