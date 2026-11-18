import { describe, expect, it } from "vitest";
import { blankDetails, detailsBody, detailsProblem, requestWhat } from "./self";

const form = (o: Partial<ReturnType<typeof blankDetails>> = {}) => ({ ...blankDetails(), ...o });

describe("my own details form", () => {
  it("needs at least one thing to change", () => { expect(detailsProblem(form())).toMatch(/at least one/); });
  it("checks a PAN", () => {
    expect(detailsProblem(form({ pan: "ABCD1234" }))).toMatch(/PAN/);
    expect(detailsProblem(form({ pan: "abcpr1234k" }))).toBeNull();
  });
  it("wants the account number and IFSC together", () => {
    expect(detailsProblem(form({ account: "123456789012" }))).toMatch(/together/);
    expect(detailsProblem(form({ ifsc: "HDFC0001234" }))).toMatch(/together/);
    expect(detailsProblem(form({ account: "12ab", ifsc: "HDFC0001234" }))).toMatch(/6 to 18 digits/);
    expect(detailsProblem(form({ account: "123456789012", ifsc: "HDFC1001234" }))).toMatch(/IFSC/);
    expect(detailsProblem(form({ account: "123456789012", ifsc: "hdfc0001234" }))).toBeNull();
  });
  it("wants a mobile number with the country code", () => {
    expect(detailsProblem(form({ mobile: "9812345678" }))).toMatch(/country code/);
    expect(detailsProblem(form({ mobile: "+919812345678" }))).toBeNull();
  });
  it("sends only what was filled in, in capitals", () => {
    expect(detailsBody(form({ pan: " abcpr1234k ", mobile: "" }))).toEqual({ pan: "ABCPR1234K" });
    expect(detailsBody(form({ account: "123456789012", ifsc: "hdfc0001234", mobile: "+919812345678" }))).toEqual({ bank_account: { account_no: "123456789012", ifsc: "HDFC0001234" }, mobile: "+919812345678" });
  });
  it("says what is waiting", () => {
    const r = { id: "1", pan_last4: "234K", bank_last4: "9012", bank_ifsc: "HDFC0001234", mobile: "+91", requested_at: "", decided_at: null, hr_note: null };
    expect(requestWhat(r)).toBe("PAN ending 234K, bank account ending 9012 and a new mobile number");
    expect(requestWhat({ ...r, bank_last4: null, mobile: null })).toBe("PAN ending 234K");
  });
});
