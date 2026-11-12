/** The module map: every module in the product, which stage it is for, and whether it has a screen yet. `href` is the screen that covers it, or null when it is planned. */
export type Track = "Business" | "Startup" | "MSME";
export const TRACKS: Track[] = ["Business", "Startup", "MSME"];
const ALL: Track[] = ["Business", "Startup", "MSME"];

export interface Module { name: string; about: string; icon: string; tracks: Track[]; href: string | null }
export interface Layer { key: string; title: string; about: string; modules: Module[] }

const I = {
  pie: "M12 3a9 9 0 1 0 9 9h-9z", people: "M16 11a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0", mail: "M3 6h18v12H3zM3 6l9 7 9-7", factory: "M3 21V10l6 4V10l6 4V6h6v15z",
  rupee: "M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6", spark: "M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6zM9 12l2 2 4-4", bell: "M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 0 0 4 0", board: "M4 6h16M4 12h16M4 18h10",
  legal: "M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z", badge: "M5 4h14v16l-7-4-7 4z", eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
  book: "M4 4h16v16H4zM4 10h16M10 4v16", gst: "M7 3h10l2 4H5zM5 7v14h14V7M9 13h6M9 17h4", tax: "M6 3h9l3 3v15H6zM9 12h6M9 16h6M9 8h3", bank: "M3 10l9-6 9 6M5 10v9M19 10v9M9 10v9M15 10v9M3 21h18",
  up: "M4 19l5-6 4 3 7-9M15 7h5v5", cart: "M3 4h3l2 12h11l2-8H7M10 20h.01M17 20h.01", box: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10", build: "M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6",
  graph: "M5 5h4v4H5zM15 5h4v4h-4zM10 15h4v4h-4zM7 9l4 6M17 9l-4 6", ledger: "M5 3h12a2 2 0 0 1 2 2v16H7a2 2 0 0 1-2-2zM9 7h6M9 11h6", flow: "M4 6h6v4H4zM14 14h6v4h-6zM7 10v4a2 2 0 0 0 2 2h5",
  key: "M15 7a4 4 0 1 1-3.9 5H3v3h3v3h3v-3h2.1A4 4 0 0 1 15 7z", ship: "M3 17l2 3h14l2-3M5 17l-1-6h16l-1 6M12 11V4l5 4M12 4L7 8", folder: "M3 7h6l2 2h10v11H3z",
};

const m = (name: string, about: string, icon: string, tracks: Track[], href: string | null): Module => ({ name, about, icon, tracks, href });

export const LAYERS: Layer[] = [
  { key: "L4 · GROW", title: "Grow", about: "Capital, credit and the government support you are entitled to.", modules: [
    m("Funding & Cap Table", "Rounds, CCPS, dilution scenarios, allotments and the returns that follow.", I.pie, ["Startup"], "/startup"),
    m("ESOP Manager", "Pool, grants and vesting. Exercise and perquisite tax come later.", I.people, ["Startup"], "/startup"),
    m("Investor Relations", "Monthly update drafted from the books. Data rooms for diligence.", I.mail, ["Startup"], "/startup"),
    m("Schemes & Subsidies", "Udyam-linked eligibility for central and state schemes.", I.factory, ["MSME"], "/msme"),
    m("Credit & TReDS", "Invoice discounting and delayed-payment claims.", I.rupee, ["MSME", "Business"], "/msme"),
    m("Insights & Copilot", "Cited answers and drafts from your own data.", I.spark, ALL, "/copilot"),
  ] },
  { key: "L3 · GOVERN", title: "Govern", about: "The compliance engine and everything that proves you followed the law.", modules: [
    m("Compliance Engine", "Obligation graph, calendar, late-fee clock, thresholds.", I.shield, ALL, "/compliance"),
    m("Notice Desk", "GST, Income Tax and MCA notices, replies and hearings.", I.bell, ALL, "/notices"),
    m("Board & Secretarial", "Meetings, minutes, resolutions.", I.board, ["Business", "Startup"], "/board"),
    m("Legal & Contracts", "Contracts, key terms, signing and renewal alerts.", I.legal, ALL, "/legal"),
    m("Licenses & Registrations", "Shops Act, trade, factory, IEC, FSSAI and renewals.", I.badge, ALL, null),
    m("Audit & Controls", "Maker–checker, CARO and internal-control evidence.", I.eye, ["Business"], null),
  ] },
  { key: "L2 · RUN", title: "Run", about: "Day-to-day operations that generate the data compliance depends on.", modules: [
    m("Finance & Books", "Ledger, bank matching, reconciliation, month-end close.", I.book, ALL, "/finance"),
    m("GST & Indirect Tax", "GSTR-1 and 3B, 2B matching, e-invoice, e-way bill.", I.gst, ALL, "/gst"),
    m("Direct Tax & TDS", "TDS deposits and returns, advance tax, Form 26AS.", I.tax, ALL, "/tds"),
    m("Payroll & HR", "Salary, PF, ESI, PT, employees and the bank file.", I.people, ALL, "/payroll"),
    m("Banking & Payments", "Vendor runs, payout approvals, UPI collections.", I.bank, ALL, null),
    m("Sales & Receivables", "Quotes, invoices, dunning, MSME-aware ageing.", I.up, ALL, "/billing"),
    m("Procurement & Vendors", "Purchase orders, bills, vendor KYC, 45-day payment tracking.", I.cart, ["Business", "MSME"], null),
    m("Inventory & Dispatch", "Stock on hand at average cost, movements, counts and write-offs. BOM and dispatch come later.", I.box, ["MSME", "Business"], "/inventory"),
    m("Exports & Imports", "Shipping bills, the nine-month clock for proceeds, LUT, RoDTEP and drawback, bills of entry.", I.ship, ["Business", "MSME"], "/trade"),
  ] },
  { key: "L1 · FOUNDATION", title: "Foundation", about: "Shared core every module writes to. Always on.", modules: [
    m("Entity & Incorporation", "Name approval, SPICe+, LLP, changes, multi-entity.", I.build, ALL, null),
    m("Obligation Graph & Rules", "Versioned rules mapped to statute and threshold.", I.graph, ALL, "/compliance"),
    m("Unified Ledger", "One double-entry source of truth across modules.", I.ledger, ALL, "/finance"),
    m("Workflow & Approvals", "Owners, maker–checker and approval requests.", I.flow, ALL, "/"),
    m("Identity & Roles", "Founders, CA, CS, HR, investors with scoped access.", I.key, ALL, "/settings"),
    m("Document Vault", "Certificates, returns, challans, acknowledgements.", I.folder, ALL, "/documents"),
  ] },
];

export const allModules = (): Module[] => LAYERS.flatMap((l) => l.modules);
export const switchedOn = (mod: Module, track: Track | "All") => track === "All" || mod.tracks.includes(track);
export const hasScreen = (mod: Module) => mod.href !== null;

export function countLine(track: Track | "All"): string {
  const all = allModules();
  return track === "All" ? `${all.length} modules across ${LAYERS.length} layers` : `${all.filter((x) => switchedOn(x, track)).length} of ${all.length} modules switch on for ${track}`;
}
/** How much of the map has a screen: "20 of 27 modules have a screen". */
export function coverage(track: Track | "All"): { withScreen: number; total: number } {
  const mods = allModules().filter((x) => switchedOn(x, track));
  return { withScreen: mods.filter(hasScreen).length, total: mods.length };
}
export const coverageLine = (track: Track | "All") => { const c = coverage(track); return `${c.withScreen} of ${c.total} have a screen`; };

// ---- government and financial rails
export interface Rail { name: string; about: string; state: "sandbox" | "planned" }
/** "sandbox" means the code talks to a stand-in that behaves like the portal; nothing has been tried against a real one. */
export const RAILS: Rail[] = [
  { name: "GSTN via GSP", about: "Returns, 2A/2B, e-invoice IRP", state: "sandbox" }, { name: "E-way bill portal", about: "Generate and cancel EWBs", state: "sandbox" },
  { name: "MCA V3", about: "Master data, e-forms, SRNs", state: "sandbox" }, { name: "Income Tax & TRACES", about: "TDS, 26AS/AIS, challans", state: "sandbox" },
  { name: "EPFO & ESIC", about: "ECR, challans, UAN/IP", state: "sandbox" }, { name: "Udyam portal", about: "Registration and updates", state: "planned" },
  { name: "Account Aggregator", about: "Consented bank statements", state: "sandbox" }, { name: "Bank APIs", about: "Payouts and salary files", state: "sandbox" },
  { name: "MSME Samadhaan", about: "Delayed-payment claims", state: "sandbox" }, { name: "TReDS platforms", about: "Invoice discounting (recorded by hand)", state: "planned" },
  { name: "DigiLocker", about: "Verified documents", state: "planned" }, { name: "Aadhaar eSign & DSC", about: "Signing and filing", state: "planned" },
  { name: "Tally & Zoho Books", about: "Import existing books", state: "planned" },
];
export const railCounts = () => ({ sandbox: RAILS.filter((r) => r.state === "sandbox").length, planned: RAILS.filter((r) => r.state === "planned").length });
