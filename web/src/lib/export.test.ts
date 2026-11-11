import { describe, expect, it } from "vitest";
import { blankCustomer, blankCustoms, blankRegistration, customerBody, customerProblem, customsBody, customsProblem, draftText, rateProblem, registrationBody, registrationProblem } from "./export";
import { blankLine, docBody } from "./billing";

describe("a customer, here or abroad", () => {
  it("needs a name, and a customer abroad needs a currency but no GSTIN", () => {
    expect(customerProblem(blankCustomer())).toMatch(/name/);
    expect(customerProblem({ ...blankCustomer(), name: "Acme Inc", country: "US" })).toMatch(/currency/);
    expect(customerProblem({ ...blankCustomer(), name: "Acme Inc", country: "US", currency: "INR" })).toMatch(/currency/);
    expect(customerProblem({ ...blankCustomer(), name: "Acme Inc", country: "US", currency: "usd" })).toBeNull();
    expect(customerProblem({ ...blankCustomer(), name: "Local", gstin: "29AABCA1" })).toMatch(/GSTIN/);
    expect(customerProblem({ ...blankCustomer(), name: "Local", gstin: "29aabca1111a1z5" })).toBeNull();
    expect(customerProblem({ ...blankCustomer(), name: "Other", country: "OTHER", otherCountry: "Norway", currency: "NOK" })).toMatch(/two-letter/);
  });

  it("sends the currency for an overseas customer and the GSTIN for an Indian one, never both", () => {
    expect(customerBody({ ...blankCustomer(), name: " Acme Inc ", country: "US", currency: "usd", gstin: "29AABCA1111A1Z5", terms: "45" }))
      .toEqual({ legal_name: "Acme Inc", is_customer: true, country: "US", currency: "USD", payment_terms_days: 45 });
    expect(customerBody({ ...blankCustomer(), name: "Local", gstin: "29aabca1111a1z5" })).toEqual({ legal_name: "Local", is_customer: true, country: "IN", gstin: "29AABCA1111A1Z5" });
    expect(customerBody({ ...blankCustomer(), name: "Fjord", country: "OTHER", otherCountry: "no", currency: "NOK" })).toMatchObject({ country: "NO", currency: "NOK" });
  });
});

describe("the exchange rate on an export invoice", () => {
  it("needs a foreign currency and a rate in rupees per unit", () => {
    expect(rateProblem("", "83")).toMatch(/currency/);
    expect(rateProblem("INR", "83")).toMatch(/currency/);
    expect(rateProblem("USD", "")).toMatch(/rate/);
    expect(rateProblem("USD", "0")).toMatch(/rate/);
    expect(rateProblem("USD", "83.21501")).toMatch(/rate/);
    expect(rateProblem("USD", "83.2150")).toBeNull();
  });

  it("goes into the invoice request only when it is given", () => {
    const f = { registrationId: "R", partyId: "P", partyGstId: "", docDate: "2026-11-09", terms: "", po: "", lines: [{ ...blankLine(), description: "Valve", hsn_sac: "8481", qty: "1", rate: "100" }] };
    expect(docBody(f)).not.toHaveProperty("currency");
    expect(docBody(f)).not.toHaveProperty("exchange_rate");
    expect(docBody({ ...f, currency: "usd", rate: " 83.2150 " })).toMatchObject({ currency: "USD", exchange_rate: "83.2150" });
  });
  it("says the rates are in the customer's currency only when that is chosen and there is a currency", () => {
    const f = { registrationId: "R", partyId: "P", partyGstId: "", docDate: "2026-11-09", terms: "", po: "", lines: [{ ...blankLine(), description: "Valve", hsn_sac: "8481", qty: "1", rate: "100.25" }] };
    expect(docBody({ ...f, currency: "USD", rate: "83", priceIn: "foreign" })).toMatchObject({ priced_in_foreign: true, currency: "USD" });
    expect(docBody({ ...f, currency: "USD", rate: "83", priceIn: "inr" })).not.toHaveProperty("priced_in_foreign");
    expect(docBody({ ...f, priceIn: "foreign" })).not.toHaveProperty("priced_in_foreign");                      // a domestic invoice has no currency to price in
  });
});

describe("the LUT and the IEC", () => {
  it("a LUT runs for the financial year it is recorded in", () => {
    expect(blankRegistration("lut", 2026, 11)).toMatchObject({ from: "2026-04-01", until: "2027-03-31" });
    expect(blankRegistration("lut", 2027, 2)).toMatchObject({ from: "2026-04-01", until: "2027-03-31" });
    expect(blankRegistration("iec")).toMatchObject({ from: "", until: "" });
  });

  it("checks the reference, the dates and the ten characters of an IEC", () => {
    const lut = { ...blankRegistration("lut", 2026, 11), number: "ad2904260000001" };
    expect(registrationProblem(lut)).toBeNull();
    expect(registrationBody(lut)).toEqual({ kind: "lut", number: "AD2904260000001", valid_from: "2026-04-01", valid_until: "2027-03-31" });
    expect(registrationProblem({ ...lut, number: "SHORT" })).toMatch(/LUT reference/);
    expect(registrationProblem({ ...lut, until: "2026-03-31" })).toMatch(/cannot end/);
    expect(registrationProblem({ ...blankRegistration("iec"), number: "0512345678" })).toBeNull();
    expect(registrationProblem({ ...blankRegistration("iec"), number: "05123" })).toMatch(/ten/);
    expect(registrationBody({ ...blankRegistration("iec"), number: "0512345678" })).toEqual({ kind: "iec", number: "0512345678" });
  });
});

describe("the customs details on a draft shipping bill", () => {
  const f = { ...blankCustoms("83.0000"), number: "7790001", date: "2026-11-08", port: "inmaa1" };
  it("starts with the invoice's rate and wants everything customs printed", () => {
    expect(blankCustoms("83.0000").rate).toBe("83.0000");
    expect(customsProblem(f, "2026-11-09")).toBeNull();
    expect(customsProblem({ ...f, number: "" }, "2026-11-09")).toMatch(/number/);
    expect(customsProblem({ ...f, date: "" }, "2026-11-09")).toMatch(/date/);
    expect(customsProblem({ ...f, date: "2026-11-10" }, "2026-11-09")).toMatch(/future/);
    expect(customsProblem({ ...f, port: "MAA" }, "2026-11-09")).toMatch(/port/);
    expect(customsProblem({ ...f, rate: "" }, "2026-11-09")).toMatch(/rate/);
  });

  it("sends the number, date, port and rate in the form the server wants", () => {
    expect(customsBody(f)).toEqual({ sb_number: "7790001", sb_date: "2026-11-08", port_code: "INMAA1", exchange_rate: "83.0000" });
  });

  it("says what the draft waits for, or that its invoice was cancelled", () => {
    expect(draftText("EXP/001", "issued")).toMatch(/Opened from export invoice EXP\/001/);
    expect(draftText("EXP/001", "cancelled")).toMatch(/cancelled/);
  });
});
