/** The export flow's forms: a customer outside India, the exchange rate on an export invoice, the LUT and IEC, and the customs details that turn a draft shipping bill into a filed one.
 * The server decides what is an export, what tax applies and every rupee value; these helpers only shape what is sent and say what is missing. */

export const COUNTRIES: Array<{ code: string; name: string; currency: string }> = [
  { code: "IN", name: "India", currency: "INR" }, { code: "US", name: "United States", currency: "USD" }, { code: "GB", name: "United Kingdom", currency: "GBP" },
  { code: "AE", name: "United Arab Emirates", currency: "AED" }, { code: "SG", name: "Singapore", currency: "SGD" }, { code: "DE", name: "Germany", currency: "EUR" },
  { code: "FR", name: "France", currency: "EUR" }, { code: "NL", name: "Netherlands", currency: "EUR" }, { code: "IT", name: "Italy", currency: "EUR" }, { code: "ES", name: "Spain", currency: "EUR" },
  { code: "AU", name: "Australia", currency: "AUD" }, { code: "CA", name: "Canada", currency: "CAD" }, { code: "JP", name: "Japan", currency: "JPY" }, { code: "CN", name: "China", currency: "CNY" },
  { code: "BD", name: "Bangladesh", currency: "USD" }, { code: "LK", name: "Sri Lanka", currency: "USD" }, { code: "SA", name: "Saudi Arabia", currency: "SAR" },
  { code: "ZA", name: "South Africa", currency: "ZAR" }, { code: "OTHER", name: "Another country (enter the code)", currency: "USD" },
];
export const countryName = (code: string) => COUNTRIES.find((c) => c.code === code)?.name ?? code;
const RATE = /^\d{1,8}(\.\d{1,4})?$/;
const positive = (v: string) => /[1-9]/.test(v);

// ---- a customer, here or abroad
export interface CustomerForm { name: string; country: string; otherCountry: string; currency: string; gstin: string; terms: string }
export const blankCustomer = (): CustomerForm => ({ name: "", country: "IN", otherCountry: "", currency: "", gstin: "", terms: "" });
export const customerCountry = (f: CustomerForm) => (f.country === "OTHER" ? f.otherCountry.trim().toUpperCase() : f.country);
export function customerProblem(f: CustomerForm): string | null {
  if (f.name.trim().length < 2) return "Enter the customer's name";
  const c = customerCountry(f);
  if (!/^[A-Z]{2}$/.test(c)) return "Enter the two-letter country code, such as US";
  if (c === "IN") {
    if (f.gstin.trim() && !/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/i.test(f.gstin.trim())) return "That does not look like a GSTIN. It has 15 characters, such as 29AABCA1111A1Z5";
  } else {
    if (!/^[A-Za-z]{3}$/.test(f.currency.trim()) || f.currency.trim().toUpperCase() === "INR") return "Choose the currency this customer is invoiced in, such as USD";
  }
  if (f.terms && !/^\d{1,3}$/.test(f.terms)) return "Payment terms are a number of days";
  return null;
}
export const customerBody = (f: CustomerForm) => {
  const c = customerCountry(f);
  return {
    legal_name: f.name.trim(), is_customer: true, country: c,
    ...(c !== "IN" ? { currency: f.currency.trim().toUpperCase() } : f.gstin.trim() ? { gstin: f.gstin.trim().toUpperCase() } : {}),
    ...(f.terms ? { payment_terms_days: Number(f.terms) } : {}),
  };
};

// ---- the rate on an export invoice
export function rateProblem(currency: string, rate: string): string | null {
  if (!/^[A-Za-z]{3}$/.test(currency.trim()) || currency.trim().toUpperCase() === "INR") return "Choose the currency the customer pays in";
  if (!RATE.test(rate.trim()) || !positive(rate)) return "Enter the exchange rate in rupees per unit, such as 83.2150";
  return null;
}

// ---- the LUT and the IEC
export interface RegistrationForm { kind: "lut" | "iec"; number: string; from: string; until: string }
export const blankRegistration = (kind: "lut" | "iec" = "lut", year = new Date().getFullYear(), month = new Date().getMonth() + 1): RegistrationForm => {
  const start = month >= 4 ? year : year - 1;                 // a LUT runs for the financial year
  return { kind, number: "", from: kind === "lut" ? `${start}-04-01` : "", until: kind === "lut" ? `${start + 1}-03-31` : "" };
};
export function registrationProblem(f: RegistrationForm): string | null {
  const n = f.number.trim().toUpperCase();
  if (f.kind === "iec" && !/^[A-Z0-9]{10}$/.test(n)) return "An IEC has ten letters and digits, as on your IEC certificate";
  if (f.kind === "lut") {
    if (!/^[A-Z0-9]{10,20}$/.test(n)) return "Enter the LUT reference (ARN) from the GST portal, such as AD2904260000001";
    if (!f.from || !f.until) return "Enter the dates the LUT is valid for";
    if (f.until < f.from) return "The LUT cannot end before it starts";
  }
  return null;
}
export const registrationBody = (f: RegistrationForm) => ({
  kind: f.kind, number: f.number.trim().toUpperCase(), ...(f.kind === "lut" ? { valid_from: f.from, valid_until: f.until } : {}),
});

// ---- the customs details on a draft shipping bill
export interface CustomsForm { number: string; date: string; port: string; rate: string }
export const blankCustoms = (rate = ""): CustomsForm => ({ number: "", date: "", port: "", rate });
export function customsProblem(f: CustomsForm, today: string): string | null {
  if (!/^[0-9A-Za-z/-]{1,20}$/.test(f.number.trim())) return "Enter the shipping bill number as customs printed it, for example 7712345";
  if (!f.date) return "Enter the shipping bill date";
  if (f.date > today) return "A shipping bill cannot be dated in the future";
  if (!/^[A-Za-z]{2}[A-Za-z0-9]{4}$/.test(f.port.trim())) return "Enter the port code, such as INMAA1";
  if (!RATE.test(f.rate.trim()) || !positive(f.rate)) return "Enter the customs exchange rate on the shipping bill, such as 83.2150";
  return null;
}
export const customsBody = (f: CustomsForm) => ({ sb_number: f.number.trim().toUpperCase(), sb_date: f.date, port_code: f.port.trim().toUpperCase(), exchange_rate: f.rate.trim() });

/** What a draft shipping bill is waiting for, in a sentence. */
export const draftText = (invoice: string | null, invoiceStatus: string | null) =>
  invoiceStatus === "cancelled" ? `Invoice ${invoice} was cancelled. This draft can be ignored.`
    : `Opened from export invoice ${invoice ?? ""}. Add the number, port and date customs issued to file it.`.replace("invoice  ", "invoice ");
