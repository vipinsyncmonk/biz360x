/** Upload helpers for the document vault. The browser hashes the file; the server checks size and hash again before it accepts the bytes. */

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
export const toHex = hex;

/** SHA-256 of a file's bytes as 64 lower-case hex characters. */
export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", buf)));
}

const MIME_BY_EXT: Record<string, string> = {
  pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", txt: "text/plain", csv: "text/csv", json: "application/json",
  xml: "application/xml", doc: "application/msword", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", zip: "application/zip",
};
/** The type the browser reports, or one guessed from the extension. */
export function mimeOf(name: string, reported: string): string {
  if (reported && reported.length >= 3) return reported;
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return MIME_BY_EXT[ext] ?? "application/octet-stream";
}

/** "Board_minutes-Oct.pdf" -> "Board minutes Oct" */
export function titleFromFile(name: string): string {
  const base = name.includes(".") ? name.slice(0, name.lastIndexOf(".")) : name;
  return base.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 200);
}

export function uploadProblem(file: { name: string; size: number } | null, title: string): string | null {
  if (!file) return "Choose a file";
  if (file.size === 0) return "That file is empty";
  if (file.size > MAX_UPLOAD_BYTES) return `Files are limited to ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`;
  if (!title.trim()) return "Give the document a title";
  if (title.trim().length > 200) return "The title is too long";
  return null;
}

export interface UploadMeta { title: string; folder: string; period: string; validUntil: string }
export const registerBody = (file: { name: string; size: number }, mime: string, sha256: string, m: UploadMeta) => ({
  folder: m.folder, title: m.title.trim(), file_name: file.name, mime_type: mime, size_bytes: file.size, sha256,
  ...(m.period.trim() ? { period_label: m.period.trim() } : {}), ...(m.validUntil ? { valid_until: m.validUntil } : {}),
});

/** A file's bytes as base64, for the endpoints that take a file inside a JSON body. */
export function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** The upload address names the API's own host. The browser talks to the API through its own origin, so keep only the path. */
export function sameOriginUrl(url: string | null | undefined, apiBase = "/api"): string | null {
  if (!url) return null;
  try {
    const u = new URL(url, "http://placeholder.invalid");
    return u.pathname.startsWith("/v1/") ? `${apiBase}${u.pathname}${u.search}` : url;
  } catch { return url; }
}

export type UploadStage = "reading" | "registering" | "uploading" | "done" | "existing";
export const STAGE_LABEL: Record<UploadStage, string> = { reading: "Reading the file…", registering: "Registering…", uploading: "Uploading…", done: "Added to the vault", existing: "Already in the vault" };

export interface EditForm { title: string; folder: string; period: string; validUntil: string }
export const editProblem = (f: EditForm) => (f.title.trim() ? null : "A document needs a title");
export const editBody = (f: EditForm) => ({ title: f.title.trim(), folder: f.folder, period_label: f.period.trim() || null, valid_until: f.validUntil || null });

/** Documents that can be shared: the file has arrived. */
export const shareable = <T extends { download_url: string | null }>(docs: T[]): T[] => docs.filter((d) => d.download_url);
