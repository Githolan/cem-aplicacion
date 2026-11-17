import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PAGINAS_A_DIBUJAR, VisorDocumento } from "./VisorDocumento";

const pdf = vi.hoisted(() => ({ abrirPdf: vi.fn() }));
vi.mock("./pagos/pdf", async (original) => ({ ...(await original<typeof import("./pagos/pdf")>()), ...pdf }));
const PAGINA = { getViewport: () => ({ width: 100, height: 100 }), render: () => ({ promise: Promise.resolve() }) };

const C1 = "11111111-1111-4111-8111-111111111111";
const D1 = "33333333-3333-4333-8333-333333333333";
const V1 = "77777777-7777-4777-8777-777777777777";
const CONTENIDO = `/api/v1/consorcios/${C1}/documentos/${D1}/versiones/${V1}/contenido`;

const IMAGEN = () => new Response(new Blob(["PNG SINTETICO"], { type: "image/png" }), { headers: { "Content-Type": "image/png" } });
const PDF = () =>
  new Response(new Blob(["%PDF-1.4 SINTETICO"], { type: "application/pdf" }), { headers: { "Content-Type": "application/pdf" } });
const VENCIDO = () => Response.json({ codigo: "archivo_vencido", mensaje: "x", detalle: null, id_correlacion: "c" }, { status: 410 });

function backend(respuestas: (() => Response)[]) {
  return vi.fn(async (pedido: Request) => {
    if (new URL(pedido.url).pathname !== CONTENIDO) throw new Error(`pedido no previsto: ${pedido.url}`);
    const siguiente = respuestas.shift();
    if (siguiente === undefined) throw new Error("se pidió más veces de lo previsto");
    return siguiente();
  });
}

/** Una pantalla con «Ver», como la de Documentos. */
function Pantalla({ aEntrar }: { aEntrar: () => void }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setAbierto(true)}>
        Ver
      </button>
      {abierto && <VisorDocumento consorcio={C1} documento={D1} version={V1} nombre="Acta de asamblea" alCerrar={() => setAbierto(false)} aEntrar={aEntrar} />}
    </>
  );
}

function abrirVisor() {
  render(<Pantalla aEntrar={vi.fn()} />);
  const ver = screen.getByRole("button", { name: "Ver" });
  ver.focus();
  fireEvent.click(ver);
  return { ver, hoja: screen.getByRole("dialog", { name: "Acta de asamblea" }) };
}

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:sintetico", revokeObjectURL: () => undefined }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("VisorDocumento (comentario 32)", () => {
  it("abre una hoja ancha con el archivo, sin iframe, y el foco en «Cerrar»", async () => {
    vi.stubGlobal("fetch", backend([IMAGEN]));
    const { hoja } = abrirVisor();
    expect(hoja.getAttribute("aria-modal")).toBe("true");
    expect(hoja.className).toBe("hoja hoja--ancha");
    expect((await within(hoja).findByRole("img", { name: "Acta de asamblea" })).getAttribute("src")).toBe("blob:sintetico");
    expect(hoja.querySelector("iframe")).toBeNull();
    expect(document.activeElement).toBe(within(hoja).getByRole("button", { name: "Cerrar" }));
  });

  it("«Cerrar» la cierra y el foco vuelve a «Ver»", async () => {
    vi.stubGlobal("fetch", backend([IMAGEN]));
    const { ver, hoja } = abrirVisor();
    await within(hoja).findByRole("img");
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(ver);
  });

  it("Escape la cierra", async () => {
    vi.stubGlobal("fetch", backend([IMAGEN]));
    const { hoja } = abrirVisor();
    await within(hoja).findByRole("img");
    fireEvent.keyDown(within(hoja).getByRole("button", { name: "Cerrar" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("control: otra tecla no la cierra", async () => {
    vi.stubGlobal("fetch", backend([IMAGEN]));
    const { hoja } = abrirVisor();
    await within(hoja).findByRole("img");
    fireEvent.keyDown(within(hoja).getByRole("button", { name: "Cerrar" }), { key: "Enter" });
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("si el archivo no llega, lo dice y «Reintentar» lo vuelve a pedir", async () => {
    // StrictMode no está acá: un pedido por vuelta.
    const simulado = backend([VENCIDO, IMAGEN]);
    vi.stubGlobal("fetch", simulado);
    const { hoja } = abrirVisor();
    expect(await within(hoja).findByRole("alert")).toBeTruthy();
    expect(within(hoja).queryByRole("img")).toBeNull();
    fireEvent.click(within(hoja).getByRole("button", { name: "Reintentar" }));
    expect((await within(hoja).findByRole("img", { name: "Acta de asamblea" })).getAttribute("src")).toBe("blob:sintetico");
    await waitFor(() => expect(simulado).toHaveBeenCalledTimes(2));
    expect(within(hoja).queryByRole("alert")).toBeNull();
  });

  it("sin conexión lo dice y ofrece «Reintentar»", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("sin red"))));
    const { hoja } = abrirVisor();
    expect((await within(hoja).findByRole("alert")).textContent).toMatch(/conexión/i);
    expect(within(hoja).getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("DOC-15: un PDF largo se ve de a partes y «Ver las siguientes» suma páginas en la misma hoja", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as never);
    pdf.abrirPdf.mockImplementation(() =>
      Promise.resolve({ promise: Promise.resolve({ numPages: 12, getPage: () => Promise.resolve(PAGINA) }), destroy: () => undefined }),
    );
    vi.stubGlobal("fetch", backend([PDF]));
    const { hoja } = abrirVisor();
    expect(await within(hoja).findByText(`Se ven las primeras ${PAGINAS_A_DIBUJAR} páginas de 12.`)).toBeTruthy();
    expect(within(hoja).getAllByRole("img")).toHaveLength(PAGINAS_A_DIBUJAR);
    fireEvent.click(within(hoja).getByRole("button", { name: "Ver las siguientes" }));
    expect(await within(hoja).findByText("Se ven las primeras 10 páginas de 12.")).toBeTruthy();
    await waitFor(() => expect(within(hoja).getAllByRole("img")).toHaveLength(10));
    fireEvent.click(within(hoja).getByRole("button", { name: "Ver las siguientes" }));
    // Control: con todas a la vista, ni el aviso ni el botón.
    await waitFor(() => expect(within(hoja).getAllByRole("img")).toHaveLength(12));
    expect(within(hoja).queryByRole("button", { name: "Ver las siguientes" })).toBeNull();
    expect(within(hoja).queryByText(/^Se ven las primeras/)).toBeNull();
    // Sin pedir el archivo otra vez.
    expect(pdf.abrirPdf.mock.calls.length).toBeGreaterThanOrEqual(1);
  });
});
