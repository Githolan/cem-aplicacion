import urlDelTrabajador from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";

// Abrir un PDF que subió una persona. Se dibuja sin evaluar código (`isEvalSupported: false`) y el
// trabajador sale del mismo origen (`worker-src 'self'`). La versión `legacy` corre también en los
// iPhone viejos. pdf.js se pide recién al ver un PDF: el resto de la aplicación no lo carga.

type PdfJs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");

const cargarPdfJs = () => import("pdfjs-dist/legacy/build/pdf.mjs");

export async function abrirPdf(datos: Uint8Array, cargar: () => Promise<Pick<PdfJs, "getDocument" | "GlobalWorkerOptions">> = cargarPdfJs) {
  const { getDocument, GlobalWorkerOptions } = await cargar();
  GlobalWorkerOptions.workerSrc = urlDelTrabajador;
  return getDocument({ data: datos, isEvalSupported: false });
}
