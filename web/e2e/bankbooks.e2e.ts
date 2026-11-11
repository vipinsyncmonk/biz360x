import { expect, test, type Page } from "@playwright/test";
import { OWNER, apiGet, apiPost, apiToken, signIn, watch } from "./helpers";

const today = new Date().toISOString().slice(0, 10);
const CARD = "ATD/Auto Debit CC1xx6441";
const UPI1 = "UPI/110204743611/UPI Pay/8792003390@ibl//ICI8d18597f26b645cb84742310b7fb5163/";
const UPI2 = "UPI/110204743999/UPI Pay/8792003390@ibl//ICIaaaa/";

/** The lines of a real statement: a card payment is suggested from the starter list and booked in one click; a UPI payment to a bare handle has nothing to go on,
 * so it is classified once and the rule books the other line to the same handle. */
test("a statement line is booked from a suggestion, and a rule books the similar lines", async ({ browser, baseURL }) => {
  const page: Page = await (await browser.newContext({ baseURL })).newPage();
  const problems = watch(page);
  await signIn(page, OWNER);
  const token = await apiToken(page, OWNER);
  const eid = (await apiGet<{ entities: Array<{ id: string }> }>(page, token, "/me")).entities[0]!.id;
  const bank = (await apiGet<{ data: Array<{ id: string }> }>(page, token, `/entities/${eid}/bank-accounts`)).data[0]!;
  await apiPost(page, token, `/entities/${eid}/accounts`, { code: "5599", name: "Staff welfare", type: "expense" });
  await apiPost(page, token, `/entities/${eid}/bank-accounts/${bank.id}/transactions`, {
    transactions: [
      { external_id: `bb-${Date.now()}-1`, txn_date: today, amount: "-45000.00", description: CARD },
      { external_id: `bb-${Date.now()}-2`, txn_date: today, amount: "-2500.00", description: UPI1 },
      { external_id: `bb-${Date.now()}-3`, txn_date: today, amount: "-1800.00", description: UPI2 },
    ],
  });

  await page.goto("/finance");
  const card = page.getByRole("row").filter({ hasText: CARD });
  await expect(card.getByText("Book to 2590 Credit card payable")).toBeVisible();
  await expect(card.getByText(/credit-card bill/).first()).toBeVisible();                                  // why, in words
  await card.getByRole("button", { name: "Accept and book" }).click();
  await expect(card.getByText("Matched")).toBeVisible();

  const upi1 = page.getByRole("row").filter({ hasText: "110204743611" });
  await expect(upi1.getByText("No suggestion")).toBeVisible();
  await upi1.getByRole("button", { name: "Classify" }).click();
  const form = page.getByRole("form", { name: "Book this bank line" });
  await form.getByLabel("Book to").selectOption({ label: "5599 · Staff welfare" });
  await form.getByLabel("Next time").selectOption("counterparty");
  await form.getByLabel(/Also book the 1 other open line/).check();
  await form.getByRole("button", { name: "Book it" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Booked" })).toContainText("Booked, and 1 similar line as well.");
  await expect(upi1.getByText("Matched")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "110204743999" }).getByText("Matched")).toBeVisible();

  const rules = page.getByRole("region", { name: "Booking rules" });
  await expect(rules.getByText("Payments to or from 8792003390@ibl")).toBeVisible();
  await expect(rules.getByText("5599 · Staff welfare")).toBeVisible();
  await rules.getByRole("button", { name: /Remove rule/ }).click();
  await expect(rules).toHaveCount(0);                                                                       // no rules left: the panel goes away
  expect(problems()).toEqual([]);
});
