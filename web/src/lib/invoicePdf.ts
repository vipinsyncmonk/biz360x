/** Fetch an invoice PDF with the signed-in session (a plain link cannot carry the token) and show it in a tab or save it as a file. */
import { api, toProblem, ApiProblem } from "./api";

export const pdfFileName = (number: string | null) => `${(number ?? "draft").replace(/[\\/:*?"<>|\s]+/g, "-")}.pdf`;

async function fetchPdf(entityId: string, docId: string, download: boolean): Promise<Blob> {
  const { data, error, response } = await api.GET("/entities/{entityId}/sales-documents/{id}/pdf", {
    params: { path: { entityId, id: docId }, query: { download } }, parseAs: "blob",
  });
  if (error !== undefined || !data) throw response ? await toProblem(response) : new ApiProblem(0, "network", "Could not reach the server");
  return data as Blob;
}

/** Open in a new tab. The tab is opened first, in the click, so the browser's pop-up blocker allows it. */
export async function viewInvoicePdf(entityId: string, docId: string): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const blob = await fetchPdf(entityId, docId, false);
    const url = URL.createObjectURL(blob);
    if (tab) tab.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 5 * 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

export async function downloadInvoicePdf(entityId: string, docId: string, number: string | null): Promise<void> {
  const blob = await fetchPdf(entityId, docId, true);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = pdfFileName(number);
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
