/** Money arrives as a two-decimal string ("283200.00"). It is never turned into a float: parse to paise (BigInt) and format. */

export function toPaise(v: string | number | null | undefined): bigint {
  if (v === null || v === undefined || v === "") return 0n;
  const s = String(v).trim();
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?\d*$/.exec(s);
  if (!m) throw new Error(`not a money amount: ${s}`);
  const paise = BigInt(m[2]!) * 100n + BigInt((m[3] ?? "").padEnd(2, "0") || "0");
  return m[1] ? -paise : paise;
}

/** Indian digit grouping: 1234567 -> 12,34,567 */
function group(int: string): string {
  if (int.length <= 3) return int;
  const last3 = int.slice(-3);
  const rest = int.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last3}`;
}

export function formatInr(v: string | number | null | undefined, opts: { decimals?: boolean } = {}): string {
  const p = toPaise(v);
  const neg = p < 0n;
  const abs = neg ? -p : p;
  const int = (abs / 100n).toString();
  const frac = (abs % 100n).toString().padStart(2, "0");
  const body = group(int) + (opts.decimals === false ? "" : `.${frac}`);
  return `${neg ? "-" : ""}₹${body}`;
}

export function sumMoney(values: Array<string | null | undefined>): string {
  const total = values.reduce<bigint>((a, v) => a + toPaise(v), 0n);
  const neg = total < 0n;
  const abs = neg ? -total : total;
  return `${neg ? "-" : ""}${abs / 100n}.${(abs % 100n).toString().padStart(2, "0")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-30" -> "30 Oct". Dates are plain calendar dates; no timezone conversion. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const [, m, d] = iso.slice(0, 10).split("-");
  return `${d} ${MONTHS[Number(m) - 1] ?? ""}`;
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() + n);
  return x;
}

export function greeting(name: string, hour: number): string {
  const first = name.trim().split(/\s+/)[0] || "there";
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${first}`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return ((parts[0]![0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]![0] ?? "") : "")).toUpperCase();
}
