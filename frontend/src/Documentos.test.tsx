import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { File as ArchivoNode } from "node:buffer";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { alcanza, alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { aceptar, hoyDelDispositivo, TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "./archivos";
import { CAMBIAR_ARCHIVO } from "./SubirArchivo";
import { ARCHIVO_EN_REVISION, archivosDeLaFicha, LO_QUE_CARGO, mensajeDeError, REFRESCO_CUARENTENA_MS, RESPALDA_ALGO, RETIRAR_DOCUMENTO, SIN_CARGAS_PROPIAS, tamano } from "./Documentos";
import { LIMITE_DE_PEDIDOS, SIN_CONEXION } from "./fallas";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const D1 = "33333333-3333-4333-8333-333333333333";
const D2 = "44444444-4444-4444-8444-444444444444";
const D3 = "66666666-6666-4666-8666-666666666666";
const V1 = "77777777-7777-4777-8777-777777777777";
const V2 = "88888888-8888-4888-8888-888888888888";
const V3 = "99999999-9999-4999-8999-999999999999";
const V4 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];
const BASE = `/api/v1/consorcios/${C1}/documentos`;
const versiones = (documento: string) => `${BASE}/${documento}/versiones`;

type Documento = components["schemas"]["DocumentoEnLista"];
type Version = components["schemas"]["VersionEnLista"];

/** Una versión del contrato: si le falta un campo, el tipo no compila. */
function version(id: string, extra: Partial<Version> = {}): Version {
  return {
    id,
    estado: "disponible",
    motivo_rechazo: null,
    tipo: "application/pdf",
    tamano: 1_258_291,
    sha256: "a".repeat(64),
    subida_en: "2026-10-20T15:00:00Z",
    ...extra,
  };
}

function documento(id: string, titulo: string, extra: Partial<Documento> = {}): Documento {
  return {
    id,
    categoria: "actas_y_decisiones",
    titulo,
    fecha: "2026-10-15",
    cargado_en: "2026-10-20T15:00:00Z",
    versiones: [],
    ...extra,
  };
}

const LISTA = {
  documentos: [
    documento(D1, "Acta de asamblea ordinaria", {
      // De la más nueva a la más vieja: la nueva en revisión, la anterior disponible.
      versiones: [version(V2, { estado: "en_cuarentena", tamano: 2048 }), version(V1)],
    }),
    documento(D2, "Póliza de incendio", {
      categoria: "seguros",
      fecha: "2026-03-01",
      versiones: [version(V3, { estado: "rechazado", motivo_rechazo: "pdf_cifrado", tipo: "image/png", tamano: 500 })],
    }),
    documento(D3, "Contrato de limpieza", { categoria: "contratos_proveedores" }),
  ],
};

function error(codigo: string, status: number, detalle: unknown = null, headers: HeadersInit = {}) {
  return Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status, headers });
}

type Respuestas = Record<string, () => Response | Promise<Response>>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function conLista(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET ${BASE}`]: () => Response.json(LISTA),
    // FL-DOC-04 (tanda 10): quien solo carga lee lo suyo; sin nada cargado, la lista vacía.
    [`GET ${BASE}/mis-cargas`]: () => Response.json({ elementos: [] }),
    ...extra,
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/documentos") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[ruta]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

const escaparRegex = (texto: string) => texto.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** La fila de un documento: el botón entero, con el título y el resumen (comentario 21). */
const fila = (titulo: string) => screen.findByRole("button", { name: new RegExp(`^${escaparRegex(titulo)} `) });

/** Abre la ficha del documento tocando su fila y la devuelve. */
async function tarjeta(titulo: string): Promise<HTMLElement> {
  fireEvent.click(await fila(titulo));
  return screen.findByRole("dialog", { name: titulo });
}

/** La ventana de una acción de la ficha, abierta encima de ella (tanda 1 del informe de fricción). */
const ventanaDe = (titulo: string) => screen.getByRole("dialog", { name: titulo });

/** El botón que envía la ventana de carga de un documento nuevo (DOC-01). */
const enviarCarga = () => within(ventanaDe("Cargar un documento")).getByRole("button", { name: "Cargar" });

/** Abre la ventana de carga de un documento nuevo. */
async function abrirCarga() {
  fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
}

function escribir(etiqueta: string | RegExp, valor: string) {
  fireEvent.change(screen.getByLabelText(etiqueta), { target: { value: valor } });
}

const BYTES_PDF = new TextEncoder().encode("%PDF-1.7\n%prueba\n");

/**
 * Un archivo como el que da el navegador. Es el `File` de Node y no el de jsdom: `Request` (el de
 * Node) manda un `Blob` propio tal cual, como hace el navegador, pero uno de jsdom lo convierte en
 * el texto «[object File]».
 */
function archivo(partes: (string | Uint8Array)[], nombre: string, tipo: string): File {
  return new ArchivoNode(partes, nombre, { type: tipo }) as unknown as File;
}

function pdf(nombre = "acta.pdf"): File {
  return archivo([BYTES_PDF], nombre, "application/pdf");
}

function elegir(elegido: File) {
  fireEvent.change(screen.getByLabelText("Archivo del documento"), { target: { files: [elegido] } });
}

/** Abre el formulario nuevo y lo completa con un presupuesto y el archivo dado. */
async function completarNuevo(elegido: File = pdf()) {
  fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
  llenarNuevo(elegido);
}

function llenarNuevo(elegido: File) {
  fireEvent.change(screen.getByLabelText("Categoría"), { target: { value: "presupuestos" } });
  escribir("Título", "  Presupuesto de pintura  ");
  escribir("Fecha del documento", "2026-10-22");
  elegir(elegido);
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /documentos", () => {
  // La pestaña del menú: el inicio no la repite (informe de fricción, INI-02).
  it("el menú tiene el enlace a los documentos", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/sesion": () => Response.json({}),
        "GET /health": () => error("servicio_no_disponible", 503),
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      }),
    );
    abrir("/");
    const menu = within(await screen.findByRole("navigation"));
    expect((await menu.findByRole("link", { name: "Documentos" })).getAttribute("href")).toBe("/documentos");
  });

  it("DOC-05: si el archivo no se puede bajar, lo dice en su ficha, que sigue abierta, y no navega", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click");
    vi.stubGlobal(
      "fetch",
      conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => error("archivo_vencido", 410) }),
    );
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    const ficha = screen.getByRole("dialog", { name: "Acta de asamblea ordinaria" });
    const aviso = await within(ficha).findByRole("alert");
    expect(aviso.textContent).toBe("Ese archivo ya no está disponible.");
    expect(clic).not.toHaveBeenCalled();
    // Al cerrar la ficha la falla se va: no queda en la página (control).
    fireEvent.click(within(ficha).getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("Ese archivo ya no está disponible.")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: /Documentos/ })).toBeTruthy();
  });

  it("DOC-05: sin conexión al descargar, también lo dice en la ficha", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => Promise.reject(new TypeError("Failed to fetch")) }),
    );
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    const ficha = screen.getByRole("dialog", { name: "Acta de asamblea ordinaria" });
    expect((await within(ficha).findByRole("alert")).textContent).toBe(SIN_CONEXION);
  });

  it("control: una descarga buena no deja ningún aviso en la ficha", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const redireccion = Response.error();
    Object.defineProperty(redireccion, "type", { value: "opaqueredirect" });
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => redireccion }));
    abrir();
    const ficha = await tarjeta("Acta de asamblea ordinaria");
    fireEvent.click((await within(ficha).findAllByRole("link", { name: "Descargar" }))[0]!);
    await waitFor(() => expect(clic).toHaveBeenCalledOnce());
    expect(within(ficha).queryByRole("alert")).toBeNull();
  });

  it("si el archivo está, navega a la descarga sin mostrar error", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const redireccion = Response.error();
    Object.defineProperty(redireccion, "type", { value: "opaqueredirect" });
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => redireccion }));
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    await waitFor(() => expect(clic).toHaveBeenCalledOnce());
    expect(screen.queryByText(/ya no está disponible/)).toBeNull();
  });

  it("sin sesión (401) manda a /entrar", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("agrupa por categoría, dice el estado de cada archivo y solo ofrece descargar los disponibles", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    // La fila dice la fecha y el estado del archivo más nuevo; el resto está en la ficha.
    expect(await screen.findByRole("button", { name: "Acta de asamblea ordinaria 15 de octubre de 2026 · En revisión" })).toBeTruthy();
    const acta = await tarjeta("Acta de asamblea ordinaria");
    expect(within(acta).getByText("Actas y decisiones")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Actas y decisiones" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Seguros" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Contratos con proveedores" })).toBeTruthy();
    // Una categoría sin documentos no aparece.
    expect(screen.queryByRole("heading", { level: 2, name: "Presupuestos" })).toBeNull();
    expect(within(acta).getByText("15 de octubre de 2026")).toBeTruthy();
    const archivos = within(within(acta).getByRole("list", { name: "Archivos de Acta de asamblea ordinaria" })).getAllByRole("listitem");
    expect(archivos.map((a) => a.querySelector("strong")?.textContent)).toEqual(["En revisión", "Disponible"]);
    expect(archivos[0]?.textContent).toContain("PDF de 2 KB");
    expect(archivos[1]?.textContent).toContain("PDF de 1,2 MB");
    // Una sola descarga: la del disponible, como enlace común a la ruta que responde el 302.
    const descargas = within(acta).getAllByRole("link", { name: "Descargar" });
    expect(descargas).toHaveLength(1);
    expect(descargas[0]?.getAttribute("href")).toBe(`${BASE}/${D1}/versiones/${V1}/descargar`);
    expect(within(archivos[0] as HTMLElement).queryByRole("link")).toBeNull();
    // Con un archivo en revisión, el documento no pide otro.
    expect(within(acta).queryByRole("button", { name: "Subir archivo" })).toBeNull();
    expect(within(acta).queryByRole("button", { name: "Subir otra versión" })).toBeNull();

    const poliza = await tarjeta("Póliza de incendio");
    expect(within(poliza).getByText("Rechazado: el PDF tiene contraseña")).toBeTruthy();
    expect(within(poliza).getByText(/PNG de 1 KB/)).toBeTruthy();
    expect(within(poliza).queryByRole("link", { name: "Descargar" })).toBeNull();
    expect(within(poliza).getByRole("button", { name: "Subir archivo" })).toBeTruthy();

    const contrato = await tarjeta("Contrato de limpieza");
    expect(within(contrato).getByText("Todavía no tiene un archivo.")).toBeTruthy();
    // G-04: sin archivo, subirlo es el botón principal (lleno, sin «secundario»).
    expect(within(contrato).getByRole("button", { name: "Subir archivo" }).classList.contains("secundario")).toBe(false);
    // Control: sin archivo no hay descarga, y la descarga nunca se pidió con fetch.
    expect(within(contrato).queryByRole("link", { name: "Descargar" })).toBeNull();
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/descargar"))).toBe(false);
  });

  it("«Ver» abre el archivo disponible en una hoja, sin bajarlo, y «Cerrar» la cierra", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:sintetico", revokeObjectURL: () => undefined }));
    const FOTO = documento(D1, "Foto del tablero", { versiones: [version(V2, { estado: "en_cuarentena" }), version(V1, { tipo: "image/png" })] });
    const contenido = `${BASE}/${D1}/versiones/${V1}/contenido`;
    const simulado = conLista({
      [`GET ${BASE}`]: () => Response.json({ documentos: [FOTO] }),
      [`GET ${contenido}`]: () => new Response(new Blob(["PNG"], { type: "image/png" }), { headers: { "Content-Type": "image/png" } }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const foto = await tarjeta("Foto del tablero");
    // Solo el disponible tiene «Ver», al lado de «Descargar»; el que está en revisión, no.
    const ver = within(foto).getAllByRole("button", { name: "Ver" });
    expect(ver).toHaveLength(1);
    expect(pedidos(simulado, "GET", contenido)).toHaveLength(0);
    ver[0]!.focus();
    fireEvent.click(ver[0]!);
    // El visor se abre encima de la ficha, que lleva el mismo título.
    await waitFor(() => expect(screen.getAllByRole("dialog", { name: "Foto del tablero" })).toHaveLength(2));
    const hoja = screen.getAllByRole("dialog", { name: "Foto del tablero" })[1] as HTMLElement;
    expect((await within(hoja).findByRole("img", { name: "Foto del tablero" })).getAttribute("src")).toBe("blob:sintetico");
    expect(hoja.className).toContain("hoja--ancha");
    // En StrictMode el efecto corre dos veces; el primero se descarta. Lo que importa: se pidió por `…/contenido`.
    expect(pedidos(simulado, "GET", contenido).length).toBeGreaterThan(0);
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/descargar"))).toBe(false);
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    // Queda la ficha, con el foco adentro (la pila de hojas lo devuelve a la hoja de abajo).
    const [ficha] = screen.getAllByRole("dialog");
    expect(foto.contains(document.activeElement)).toBe(true);
    expect(ficha).toBe(foto);
    // Control: al cerrar la ficha, el foco vuelve a la fila que la abrió.
    fireEvent.click(within(foto).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement?.textContent).toMatch(/^Foto del tablero /));
  });

  it("con un archivo disponible y ninguno en revisión, la ficha ofrece subir otra versión del mismo documento", async () => {
    const SOLO_DISPONIBLE = documento(D1, "Acta de asamblea ordinaria", { versiones: [version(V1)] });
    const simulado = conLista({
      [`GET ${BASE}`]: () => Response.json({ documentos: [SOLO_DISPONIBLE] }),
      [`POST ${versiones(D1)}`]: () =>
        Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const acta = await tarjeta("Acta de asamblea ordinaria");
    expect(within(acta).queryByRole("button", { name: "Subir archivo" })).toBeNull();
    fireEvent.click(within(acta).getByRole("button", { name: "Subir otra versión" }));
    // DOC-03: el archivo se elige en su ventana, encima de la ficha, sin los datos de la ficha.
    const ventana = ventanaDe("Subir el archivo de «Acta de asamblea ordinaria»");
    expect(within(ventana).queryByText("Fecha del documento")).toBeNull();
    fireEvent.change(within(ventana).getByLabelText("Archivo del documento"), { target: { files: [archivo([BYTES_PDF], "acta.pdf", "application/pdf")] } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText(/^Listo: el archivo de «Acta de asamblea ordinaria»/)).toBeTruthy();
    // Una versión nueva del mismo documento: no se creó otro. La ficha se cierra y queda el aviso.
    expect(pedidos(simulado, "POST", versiones(D1))).toHaveLength(1);
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("mientras llega la lista dice «Consultando…», sin documentos", async () => {
    const simulado = conLista({ [`GET ${BASE}`]: () => new Promise<Response>(() => undefined) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    // Control: ya se pidió la lista, así que es su carga y no la de los edificios.
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(1));
    expect(screen.getByText("Consultando…")).toBeTruthy();
    expect(document.querySelector(".fila-registro")).toBeNull();
  });

  it("un edificio sin documentos lo dice, sin categorías vacías, y deja cargar el primero", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Response.json({ documentos: [] }) }));
    abrir();
    expect(await screen.findByText("El edificio todavía no tiene documentos cargados.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(screen.getByRole("button", { name: "Cargar un documento" })).toBeTruthy();
  });

  it("sin conexión al pedir la lista lo dice, sin documentos", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Promise.reject(new TypeError("Failed to fetch")) }));
    abrir();
    expect(await screen.findByText(/^No se pudo mostrar la lista\./)).toBeTruthy();
    // DOC-14: el motivo va una vez, en la misma línea, junto a «Consultar de nuevo».
    expect(screen.getByText(/^No se pudo mostrar la lista\./).textContent).toBe(`No se pudo mostrar la lista. ${SIN_CONEXION}`);
    expect(screen.getAllByText(new RegExp(escaparRegex(SIN_CONEXION)))).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Consultar de nuevo" })).toBeTruthy();
    expect(document.querySelector(".fila-registro")).toBeNull();
  });

  it("con la lista caída, la carga sigue a la vista y el documento se carga igual", async () => {
    const simulado = conLista({
      [`GET ${BASE}`]: () => Promise.reject(new TypeError("Failed to fetch")),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        Response.json(
          { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
          { status: 201 },
        ),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    // DOC-08: con la lista (aunque esté caída) el estado lo dice la fila; el «Listo» es una línea.
    expect(await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.")).toBeTruthy();
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(1);
  });

  it("DOC-06: sin «Actualizar»; con la lista caída, «Consultar de nuevo» la vuelve a pedir", async () => {
    let caida = true;
    const simulado = conLista({ [`GET ${BASE}`]: () => (caida ? Promise.reject(new TypeError("Failed to fetch")) : Response.json(LISTA)) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/^No se pudo mostrar la lista\./);
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await fila("Acta de asamblea ordinaria")).toBeTruthy();
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(2);
    expect(screen.queryByText(/^No se pudo mostrar la lista\./)).toBeNull();
  });

  it("DOC-07: la lista no repite el título de la página con un encabezado propio", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await fila("Acta de asamblea ordinaria");
    expect(screen.queryByRole("heading", { level: 2, name: "Documentos" })).toBeNull();
    expect(screen.getByRole("region", { name: "Documentos" })).toBeTruthy();
  });

  it("DOC-09: los avisos van arriba, antes de la lista", async () => {
    vi.stubGlobal("fetch", conLista({ [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 }) }));
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    const listo = await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.");
    const lista = screen.getByRole("region", { name: "Documentos" });
    expect(listo.compareDocumentPosition(lista) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("con la lectura negada (403) y la carga alcanzada no hay lista ni mensaje, pero deja cargar (administrador externo)", async () => {
    const simulado = conLista({
      [`GET ${BASE}`]: () => error("sin_permiso", 403),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        Response.json(
          { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
          { status: 201 },
        ),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByRole("button", { name: "Cargar un documento" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Documentos" })).toBeNull();
    expect(screen.queryByText(/no tiene acceso/)).toBeNull();
    expect(screen.queryByText(/tu mandato te permite/)).toBeNull();
    expect(document.querySelector(".fila-registro")).toBeNull();
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    expect(document.querySelector(".aviso-error")).toBeNull();
    // El administrador externo solo carga (documento 08 §4): la carga, como para todos, en su ventana (DOC-02).
    fireEvent.click(screen.getByRole("button", { name: "Cargar un documento" }));
    llenarNuevo(pdf());
    fireEvent.click(enviarCarga());
    const listo = await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura»/);
    // Sin lista, el estado lo dice esta pantalla (DOC-08); no hay botón «Actualizar».
    expect(listo.textContent).toMatch(/Queda «En revisión».*esta pantalla avisa cuando termina\.$/);
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(1);
  });

  it("si la lista se cae después de crear y falla el archivo, el reintento sigue a mano", async () => {
    let creado = false;
    let intentos = 0;
    const simulado = conLista({
      // La lista anda hasta que se crea el documento; después se cae.
      [`GET ${BASE}`]: () => (creado ? error("error_interno", 500) : Response.json(LISTA)),
      [`POST ${BASE}`]: () => {
        creado = true;
        return Response.json({ id: D3 }, { status: 201 });
      },
      [`POST ${versiones(D3)}`]: () =>
        ++intentos === 1
          ? error("error_interno", 500)
          : Response.json(
              { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
              { status: 201 },
            ),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    await completarNuevo();
    fireEvent.click(enviarCarga());
    // DOC-13: la lista que estaba a la vista queda; el refresco caído lo dice en ella.
    expect(await screen.findByText(/La lista puede no estar al día\.$/)).toBeTruthy();
    const reintento = await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
    // La misma ventana pasa a subir solo el archivo, con el motivo adentro.
    const ventana = ventanaDe("Subir el archivo de «Presupuesto de pintura»");
    expect(reintento.closest("[role=dialog]")).toBe(ventana);
    expect(within(ventana).getByRole("alert").textContent).toMatch(/^El documento «Presupuesto de pintura» quedó creado/);
    elegir(pdf());
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.")).toBeTruthy();
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(2);
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
  });

  it("sin acceso a la lista, si falla el archivo se reintenta igual, sin crear otro documento", async () => {
    let intentos = 0;
    const simulado = conLista({
      [`GET ${BASE}`]: () => error("sin_permiso", 403),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        ++intentos === 1
          ? new Response(JSON.stringify({ codigo: "servicio_no_disponible", mensaje: "x", id_correlacion: "c" }), {
              status: 503,
              headers: { "Content-Type": "application/json", "Retry-After": "30" },
            })
          : Response.json(
              { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
              { status: 201 },
            ),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await abrirCarga();
    llenarNuevo(pdf());
    fireEvent.click(enviarCarga());
    await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(document.body, reglas)).violations.map((v) => v.id)).toEqual([]);
    elegir(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura»/)).toBeTruthy();
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(2);
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
  });

  it("carga en dos pasos: crea con JSON y sube el archivo crudo con su tipo, CSRF y claves distintas", async () => {
    const simulado = conLista({
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        Response.json(
          { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
          { status: 201 },
        ),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
    // G-06: el archivo va primero, y el foco también.
    const carga = ventanaDe("Cargar un documento");
    expect(document.activeElement).toBe(within(carga).getByRole("button", { name: "Sacar una foto" }));
    expect(screen.getByLabelText("Archivo del documento").getAttribute("accept")).toBe(aceptar(TIPOS_DE_DOCUMENTO));
    // Todos los caminos a la vez: soltar, la cámara, la galería y el archivo.
    expect(screen.getByText("Soltar el archivo acá")).toBeTruthy();
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"]) expect(screen.getByRole("button", { name: nombre })).toBeTruthy();
    expect(screen.getByLabelText("Foto del documento").getAttribute("capture")).toBe("environment");
    llenarNuevo(pdf());
    fireEvent.click(enviarCarga());

    const aviso = await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.");
    // El foco va al «Listo» recién cuando la ventana se cerró.
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    expect(screen.queryByRole("dialog")).toBeNull();
    const [creacion] = pedidos(simulado, "POST", BASE);
    expect(creacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(creacion?.headers.get("Content-Type")).toBe("application/json");
    expect(await creacion?.json()).toEqual({ categoria: "presupuestos", titulo: "Presupuesto de pintura", fecha: "2026-10-22" });
    const [subida] = pedidos(simulado, "POST", versiones(D3));
    expect(subida?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(subida?.headers.get("Content-Type")).toBe("application/pdf");
    expect(subida?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(creacion?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(subida?.headers.get("Idempotency-Key")).not.toBe(creacion?.headers.get("Idempotency-Key"));
    // El cuerpo es el archivo tal cual: ni JSON ni multipart.
    expect([...new Uint8Array(await (subida as Request).arrayBuffer())]).toEqual([...BYTES_PDF]);
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(2));
    // La ventana se cerró: la pantalla siguiente es la lista actualizada.
    expect(await screen.findByRole("button", { name: "Cargar un documento" })).toBeTruthy();
    expect(screen.queryByLabelText("Título")).toBeNull();
  });

  it("un archivo de más de 20 MB o de otro tipo se rechaza al elegirlo, antes de mandar nada", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const grande = pdf("grande.pdf");
    Object.defineProperty(grande, "size", { value: TOPE_BYTES + 1 });
    await completarNuevo(grande);
    expect((await screen.findByRole("alert")).textContent).toBe("El archivo pasa de 20 MB, el máximo. Hay que elegir uno más chico.");
    expect(screen.queryByText(/Elegido:/)).toBeNull();
    // El rechazado no quedó elegido: enviar pide el archivo.
    fireEvent.click(enviarCarga());
    // Decisión 4: el botón sigue activo y lo que falta se dice en la ventana, con el foco.
    const aviso = await within(ventanaDe("Cargar un documento")).findByText("Falta elegir el archivo.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    elegir(archivo(["hola"], "notas.txt", "text/plain"));
    expect(await screen.findByText("Solo se aceptan archivos PDF, JPEG o PNG. Hay que elegir otro archivo.")).toBeTruthy();
    // Control: con un archivo justo de 20 MB, sí sale el pedido.
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
    const justo = pdf("justo.pdf");
    Object.defineProperty(justo, "size", { value: TOPE_BYTES });
    elegir(justo);
    fireEvent.click(enviarCarga());
    await waitFor(() => expect(pedidos(simulado, "POST", BASE)).toHaveLength(1));
  });

  it("un 403 al crear dice que no puede cargar esa categoría y no sube nada", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => error("sin_permiso", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    expect(await screen.findByText("Esta cuenta no tiene permiso para cargar documentos de esa categoría.")).toBeTruthy();
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/versiones"))).toBe(false);
    // El formulario sigue abierto para elegir otra categoría.
    expect(enviarCarga()).toBeTruthy();
  });

  it("Codex en #257: si la sesión vence al crear, no sube nada y la ventana queda para repetir", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => error("no_autenticado", 401) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    await waitFor(() => expect(pedidos(simulado, "POST", BASE)).toHaveLength(1));
    // «seguir» no es el id de un documento: no hay pedido a /documentos/seguir/versiones.
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/versiones"))).toBe(false);
    expect(enviarCarga()).toBeTruthy();
  });

  it("«Subir archivo» de un documento existente explica un 413 y un 415 del servidor", async () => {
    let respuesta = () => error("archivo_demasiado_grande", 413);
    const simulado = conLista({ [`POST ${versiones(D3)}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const contrato = await tarjeta("Contrato de limpieza");
    fireEvent.click(within(contrato).getByRole("button", { name: "Subir archivo" }));
    expect(document.activeElement).toBe(within(ventanaDe("Subir el archivo de «Contrato de limpieza»")).getByRole("button", { name: "Sacar una foto" }));
    elegir(pdf("contrato.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText("El archivo pasa de 20 MB y no se guardó. Hay que elegir uno más chico.")).toBeTruthy();
    respuesta = () => error("tipo_no_admitido", 415);
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText(/^Solo se aceptan archivos PDF, JPEG o PNG, y el contenido tiene que coincidir/)).toBeTruthy();
    // Un rechazo definitivo renueva la clave: el próximo intento es otro.
    const claves = pedidos(simulado, "POST", versiones(D3)).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves).toHaveLength(2);
    expect(claves[0]).not.toBe(claves[1]);
    // Control: no se creó ningún documento nuevo.
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
  });

  it("G-01: el error de subir desde la ficha se dice dentro de su ventana, junto al botón", async () => {
    const simulado = conLista({ [`POST ${versiones(D3)}`]: () => error("archivo_demasiado_grande", 413) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const contrato = await tarjeta("Contrato de limpieza");
    fireEvent.click(within(contrato).getByRole("button", { name: "Subir archivo" }));
    const ventana = ventanaDe("Subir el archivo de «Contrato de limpieza»");
    // Sin archivo: el aviso queda en la ventana, que es lo que está a la vista.
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir el archivo" }));
    expect(await within(ventana).findByText("Falta elegir el archivo.")).toBeTruthy();
    expect(within(contrato).queryByText("Falta elegir el archivo.")).toBeNull();
    // El rechazo del servidor, también; el anterior se va con el envío nuevo.
    fireEvent.change(within(ventana).getByLabelText("Archivo del documento"), { target: { files: [pdf("contrato.pdf")] } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir el archivo" }));
    expect(await within(ventana).findByText("El archivo pasa de 20 MB y no se guardó. Hay que elegir uno más chico.")).toBeTruthy();
    expect(within(ventana).queryByText("Falta elegir el archivo.")).toBeNull();
  });

  it("G-08: un 422 de la fecha marca la fecha y no habla del título", async () => {
    const simulado = conLista({
      [`POST ${BASE}`]: () =>
        error("peticion_invalida", 422, { campos: ["body.fecha"], mensajes: { "body.fecha": "Escribir una fecha válida." } }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    expect(await screen.findByText("Hay que corregir la fecha: el motivo está junto a cada campo.")).toBeTruthy();
    // Ningún aviso de error habla del título (antes: «Revisá la fecha. El título no puede…»).
    expect([...document.querySelectorAll(".aviso-error")].map((e) => e.textContent).join(" ")).not.toMatch(/título/);
    const fecha = screen.getByLabelText("Fecha del documento");
    expect(fecha.getAttribute("aria-invalid")).toBe("true");
    const describe = (fecha.getAttribute("aria-describedby") ?? "").split(" ").map((i) => document.getElementById(i)?.textContent);
    expect(describe).toContain("Escribir una fecha válida.");
    // Control: el título no queda marcado.
    expect(screen.getByLabelText("Título").getAttribute("aria-invalid")).toBeNull();
  });

  it("G-08: un 422 del título dice qué no puede llevar, junto al título", async () => {
    const simulado = conLista({
      [`POST ${BASE}`]: () =>
        error("peticion_invalida", 422, {
          campos: ["body.titulo"],
          mensajes: { "body.titulo": "Sin teléfonos ni correos acá: el número va solo en el teléfono." },
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    expect(await screen.findByText("Hay que corregir el título: el motivo está junto a cada campo.")).toBeTruthy();
    const titulo = screen.getByLabelText("Título");
    expect(titulo.getAttribute("aria-invalid")).toBe("true");
    // Sin ayuda fija (DOC-10): lo describe solo el motivo.
    const ids = (titulo.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids.map((i) => document.getElementById(i)?.textContent)).toEqual(["El título no puede llevar teléfonos ni correos."]);
  });

  it("G-13: «Cancelar» se lleva el error del formulario", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => error("peticion_invalida", 422, { campos: ["body.fecha"] }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    // Control: el error se ve mientras el formulario está abierto.
    expect(await screen.findByText(/^Hay que corregir la fecha/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByText(/^Hay que corregir la fecha/)).toBeNull();
    // Y un formulario nuevo empieza sin marcas.
    fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
    expect(screen.getByLabelText("Fecha del documento").getAttribute("aria-invalid")).toBeNull();
  });

  it("G-13: cancelar la ventana se lleva el error de subir el archivo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    const contrato = await tarjeta("Contrato de limpieza");
    fireEvent.click(within(contrato).getByRole("button", { name: "Subir archivo" }));
    fireEvent.click(within(ventanaDe("Subir el archivo de «Contrato de limpieza»")).getByRole("button", { name: "Subir el archivo" }));
    expect((await screen.findAllByText(/^(Falta|Hay que) elegir el archivo\.$/)).length).toBeGreaterThan(0);
    fireEvent.click(within(ventanaDe("Subir el archivo de «Contrato de limpieza»")).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByText(/^(Falta|Hay que) elegir el archivo\.$/)).toBeNull();
    fireEvent.click(within(contrato).getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText(/^(Falta|Hay que) elegir el archivo\.$/)).toBeNull();
  });

  it("si el segundo paso falla, lo dice y deja reintentar solo el archivo, con la misma clave", async () => {
    const SUBIDO = documento(D3, "Presupuesto de pintura", { categoria: "presupuestos" });
    let creado = false;
    let respuesta = () => error("servicio_no_disponible", 503, null, { "Retry-After": "5" });
    const simulado = conLista({
      [`GET ${BASE}`]: () => Response.json(creado ? { documentos: [SUBIDO] } : { documentos: [] }),
      [`POST ${BASE}`]: () => {
        creado = true;
        return Response.json({ id: D3 }, { status: 201 });
      },
      [`POST ${versiones(D3)}`]: () => respuesta(),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    expect(
      await screen.findByText(
        "El documento «Presupuesto de pintura» quedó creado, pero el archivo no se subió. Hay muchas cargas en curso. Reintentar en un momento.",
      ),
    ).toBeTruthy();
    // La misma ventana sigue, ahora para subir solo el archivo de ese documento, con lo elegido.
    const tarjetaNueva = ventanaDe("Subir el archivo de «Presupuesto de pintura»");
    expect(within(tarjetaNueva).getByText(/Elegido: acta\.pdf/)).toBeTruthy();
    // DOC-16: con el archivo elegido, en lugar de las formas de elegir, un solo «Cambiar el archivo».
    expect(within(tarjetaNueva).getByRole("button", { name: CAMBIAR_ARCHIVO })).toBeTruthy();
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"])
      expect(within(tarjetaNueva).queryByRole("button", { name: nombre })).toBeNull();
    respuesta = () =>
      Response.json(
        { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
        { status: 201 },
      );
    fireEvent.click(within(tarjetaNueva).getByRole("button", { name: "Subir el archivo" }));
    await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura»/);
    // Un solo documento creado; el reintento del archivo repite la clave del intento caído (5xx).
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
    const subidas = pedidos(simulado, "POST", versiones(D3));
    expect(subidas).toHaveLength(2);
    expect(subidas[1]?.headers.get("Idempotency-Key")).toBe(subidas[0]?.headers.get("Idempotency-Key"));
    expect([...new Uint8Array(await (subidas[1] as Request).arrayBuffer())]).toEqual([...BYTES_PDF]);
  });

  it("los edificios que no se pudieron consultar se reintentan sin salir de la pantalla", async () => {
    let caido = true;
    const simulado = conLista({
      "GET /api/v1/me/consorcios": () => (caido ? error("servicio_no_disponible", 503) : Response.json(CONSORCIOS)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/No se pudieron consultar los edificios/)).toBeTruthy();
    // Control: la primera carga pidió una vez la pantalla y otra permisos.tsx, aun con StrictMode.
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(2);
    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await tarjeta("Acta de asamblea ordinaria")).toBeTruthy();
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
    expect(screen.queryByText(/No se pudieron consultar los edificios/)).toBeNull();
  });

  describe("mientras un archivo está en revisión", () => {
    const ACTA = "Acta de asamblea ordinaria";
    const EN_REVISION = { documentos: [documento(D1, ACTA, { versiones: [version(V2, { estado: "en_cuarentena" })] })] };
    const DISPONIBLE = { documentos: [documento(D1, ACTA, { versiones: [version(V2)] })] };

    /** `shouldAdvanceTime`: `findBy` y `waitFor` siguen andando; el intervalo lo adelanta la prueba. */
    const conReloj = () => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    const adelantar = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

    it("vuelve a pedir la lista cada intervalo y para cuando el archivo terminó", async () => {
      conReloj();
      const respuestas = [EN_REVISION, EN_REVISION, DISPONIBLE];
      const simulado = conLista({ [`GET ${BASE}`]: () => Response.json(respuestas.shift() ?? DISPONIBLE) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await tarjeta(ACTA);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
      // Control: antes del intervalo no se pide nada.
      await adelantar(REFRESCO_CUARENTENA_MS / 2);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
      await adelantar(REFRESCO_CUARENTENA_MS);
      await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(2));
      await adelantar(REFRESCO_CUARENTENA_MS);
      await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(3));
      expect(await screen.findByRole("link", { name: "Descargar" })).toBeTruthy();
      await adelantar(REFRESCO_CUARENTENA_MS * 3);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(3);
    });

    it("control: sin archivos en revisión la lista no se pide sola", async () => {
      conReloj();
      const simulado = conLista({ [`GET ${BASE}`]: () => Response.json(DISPONIBLE) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await tarjeta(ACTA);
      await adelantar(REFRESCO_CUARENTENA_MS * 3);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
    });

    it("para al salir de la pantalla", async () => {
      conReloj();
      const simulado = conLista({ [`GET ${BASE}`]: () => Response.json(EN_REVISION) });
      vi.stubGlobal("fetch", simulado);
      const { unmount } = abrir();
      await tarjeta(ACTA);
      // Control: el próximo refresco estaba programado, y salir lo cancela.
      expect(vi.getTimerCount()).toBeGreaterThan(0);
      unmount();
      // El siguiente refresco no queda: lo único que corre al salir es el borrado de las consultas.
      await adelantar(1);
      expect(vi.getTimerCount()).toBe(0);
      await adelantar(REFRESCO_CUARENTENA_MS * 3);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
    });

    it("DOC-08: tras subir, el «Listo» es una línea, la fila dice «En revisión» y la lista se refresca sola", async () => {
      conReloj();
      let subido = false;
      const simulado = conLista({
        [`GET ${BASE}`]: () => Response.json(subido ? EN_REVISION : { documentos: [] }),
        [`POST ${BASE}`]: () => Response.json({ id: D1 }, { status: 201 }),
        [`POST ${versiones(D1)}`]: () => {
          subido = true;
          return Response.json(
            { id: V2, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
            { status: 201 },
          );
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarNuevo();
      fireEvent.click(enviarCarga());
      expect(await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.")).toBeTruthy();
      await tarjeta(ACTA);
      const antes = pedidos(simulado, "GET", BASE).length;
      await adelantar(REFRESCO_CUARENTENA_MS);
      await waitFor(() => expect(pedidos(simulado, "GET", BASE).length).toBe(antes + 1));
    });

    it("un refresco que falla no saca la lista ni lo escrito, lo dice y no sigue solo", async () => {
      conReloj();
      let caida = false;
      const simulado = conLista({
        [`GET ${BASE}`]: () => (caida ? error("servicio_no_disponible", 503) : Response.json(EN_REVISION)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await fila(ACTA);
      fireEvent.click(screen.getByRole("button", { name: "Cargar un documento" }));
      escribir("Título", "Presupuesto de pintura");
      caida = true;
      await adelantar(REFRESCO_CUARENTENA_MS);
      // DOC-06: lo dice en la lista, con «Consultar de nuevo» al lado.
      expect(await screen.findByText(/La lista puede no estar al día\.$/)).toBeTruthy();
      expect(screen.getByRole("button", { name: "Consultar de nuevo" })).toBeTruthy();
      expect(await fila(ACTA)).toBeTruthy();
      expect(screen.queryByText(/^No se pudo mostrar la lista\./)).toBeNull();
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("Presupuesto de pintura");
      await adelantar(REFRESCO_CUARENTENA_MS * 3);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(2);
    });

    it("DOC-13: «Consultar de nuevo» tras un refresco caído deja la lista a la vista mientras llega la nueva", async () => {
      conReloj();
      let caida = false;
      const simulado = conLista({
        [`GET ${BASE}`]: () => (caida ? error("servicio_no_disponible", 503) : Response.json(EN_REVISION)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await fila(ACTA);
      caida = true;
      await adelantar(REFRESCO_CUARENTENA_MS);
      await screen.findByText(/La lista puede no estar al día\.$/);
      // Otra falla: la lista sigue y el aviso también, sin «No se pudo mostrar la lista».
      fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
      await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(3));
      expect(screen.queryByText("Consultando…")).toBeNull();
      expect(await screen.findByText(/La lista puede no estar al día\.$/)).toBeTruthy();
      expect(await fila(ACTA)).toBeTruthy();
      expect(screen.queryByText(/^No se pudo mostrar la lista\./)).toBeNull();
      // Control: al volver el servicio, el aviso se va.
      caida = false;
      fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
      await waitFor(() => expect(screen.queryByText(/La lista puede no estar al día/)).toBeNull());
      expect(await fila(ACTA)).toBeTruthy();
    });
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la lista y con cada formulario abierto", async () => {
    vi.stubGlobal("fetch", conLista());
    const { container } = abrir();
    const contrato = await tarjeta("Contrato de limpieza");
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    const faltas = async () => (await axe.run(container, reglas)).violations.map((v) => v.id);
    expect(await faltas()).toEqual([]);
    fireEvent.click(within(contrato).getByRole("button", { name: "Subir archivo" }));
    expect(await faltas()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.click(screen.getByRole("button", { name: "Cargar un documento" }));
    expect(await faltas()).toEqual([]);
  });

  it("las rutas, el encabezado de reintento, los cuerpos y los topes son los del contrato", () => {
    type Operacion = {
      parameters?: { name: string; required?: boolean }[];
      requestBody?: { content: Record<string, { schema: { $ref?: string } }> };
    };
    const rutas = contrato.paths as Record<string, Record<string, Operacion> | undefined>;
    expect(rutas["/api/v1/consorcios/{consorcio_id}/documentos"]?.get).toBeTruthy();
    expect(rutas["/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/descargar"]?.get).toBeTruthy();
    const crear = rutas["/api/v1/consorcios/{consorcio_id}/documentos"]?.post;
    const subir = rutas["/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones"]?.post;
    for (const op of [crear, subir]) expect(op?.parameters?.find((p) => p.name === "Idempotency-Key")?.required).toBe(true);
    expect(crear?.requestBody?.content["application/json"]?.schema.$ref).toBe("#/components/schemas/DocumentoNuevo");
    // Los tipos que la página acepta son exactamente los del cuerpo crudo del contrato.
    const tipos = Object.keys(subir?.requestBody?.content ?? {}).sort();
    expect(tipos).toEqual(["application/pdf", "image/jpeg", "image/png"]);
    expect(aceptar(TIPOS_DE_DOCUMENTO).split(",").filter((t) => t.includes("/")).sort()).toEqual(tipos);
    const esquemas = contrato.components.schemas as Record<string, { properties?: Record<string, { maxLength?: number }> }>;
    expect(esquemas.DocumentoNuevo?.properties?.titulo?.maxLength).toBe(200);
    expect(TOPE_BYTES).toBe(20 * 1024 * 1024);
    expect([tamano(1), tamano(1024), tamano(1025), tamano(1024 * 1024)]).toEqual(["1 KB", "1 KB", "2 KB", "1 MB"]);
    expect(mensajeDeError(503, undefined, "subir")).toMatch(/servicio no está disponible/);
    expect(mensajeDeError(422, "peticion_invalida", "crear", ["body.titulo"])).toBe(
      "Hay que corregir el título: el motivo está junto a cada campo.",
    );
    expect(mensajeDeError(422, "peticion_invalida", "crear", [])).toBe("Hay que revisar los datos del formulario.");
  });
});

describe("quien ya no participa de ningún edificio", () => {
  it("al consultar de nuevo llega al inicio, que dice cómo sumarse", async () => {
    let caidos = true;
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => {
          if (caidos) throw new TypeError("sin red");
          return Response.json([]);
        },
        "GET /api/v1/auth/sesion": () => Response.json({}),
        "GET /health": () => Response.json({ estado: "ok", version: "0", sha_imagen: "x", sha_contratos: "", version_contrato: "0" }),
        "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
      }),
    );
    abrir();
    const reintentar = await screen.findByRole("button", { name: "Consultar de nuevo" });
    caidos = false;
    fireEvent.click(reintentar);

    expect(await screen.findByText(/^Esta cuenta todavía no participa de ningún edificio./)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Documentos del edificio" })).toBeNull();
  });
});

describe("lo que no se puede hacer no aparece en /documentos", () => {
  const conAcciones = (...acciones: string[]) => conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza(...acciones) });
  const categorias = () =>
    within(screen.getByLabelText("Categoría")).getAllByRole("option").map((o) => o.textContent);
  const abrirCarga = async () => fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));

  it("con `documentos.cargar` se ofrecen todas las categorías", async () => {
    vi.stubGlobal("fetch", conAcciones("documentos.ver", "documentos.cargar"));
    abrir();
    await abrirCarga();
    expect(categorias()).toHaveLength(8);
  });

  it("con solo `documentos.cargar_operativos`, las actas, los presupuestos y el mantenimiento", async () => {
    vi.stubGlobal("fetch", conAcciones("documentos.ver", "documentos.cargar_operativos"));
    abrir();
    await abrirCarga();
    expect(categorias()).toEqual(["Actas y decisiones", "Presupuestos", "Mantenimiento y certificados"]);
  });

  it("«Subir archivo» sigue la categoría del documento", async () => {
    const LISTA_OPERATIVA = {
      documentos: [
        documento(D1, "Acta sin archivo"),
        documento(D3, "Contrato sin archivo", { categoria: "contratos_proveedores" }),
      ],
    };
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver", "documentos.cargar_operativos"),
        [`GET ${BASE}`]: () => Response.json(LISTA_OPERATIVA),
      }),
    );
    abrir();
    expect(within(await tarjeta("Acta sin archivo")).getByRole("button", { name: "Subir archivo" })).toBeTruthy();
    // Control: el documento de otra categoría también espera su archivo; quien no lo carga no lo ve
    // en la lista, porque no tiene nada que abrir ni que hacer (FL-DOC-02).
    expect(screen.queryByRole("button", { name: /^Contrato sin archivo / })).toBeNull();
  });

  it("solo con `documentos.ver` no hay carga, ni formulario ni «Subir archivo»", async () => {
    vi.stubGlobal("fetch", conAcciones("documentos.ver"));
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Subir archivo" })).toBeNull();
  });

  it("sin `documentos.ver`, quien carga no pide la lista y no ve ningún texto sobre ella", async () => {
    const simulado = conAcciones("documentos.cargar");
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByRole("button", { name: "Cargar un documento" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Documentos" })).toBeNull();
    expect(screen.queryByText(/tu mandato te permite/)).toBeNull();
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(0);
  });

  describe("quien solo carga", () => {
    const subidaEnRevision = () =>
      Response.json(
        { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
        { status: 201 },
      );
    const soloCarga = (extra: Respuestas = {}) =>
      conLista({
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.cargar"),
        [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
        ...extra,
      });

    it("DOC-02: encuentra el botón, sin un formulario abierto en la página; la carga va en su ventana", async () => {
      vi.stubGlobal("fetch", soloCarga());
      abrir();
      await abrirCarga();
      expect(within(ventanaDe("Cargar un documento")).getByLabelText("Categoría")).toBeTruthy();
      expect(enviarCarga()).toBeTruthy();
      // Control: al cancelar no queda ningún campo en la página.
      fireEvent.click(within(ventanaDe("Cargar un documento")).getByRole("button", { name: "Cancelar" }));
      expect(screen.queryByLabelText("Categoría")).toBeNull();
      expect(screen.getByRole("button", { name: "Cargar un documento" })).toBeTruthy();
    });

    it("terminada la carga, la ventana se cierra con el aviso y la siguiente empieza vacía", async () => {
      const simulado = soloCarga({ [`POST ${versiones(D3)}`]: subidaEnRevision });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await abrirCarga();
      llenarNuevo(pdf());
      fireEvent.click(enviarCarga());
      const aviso = await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura» se subió\. Queda «En revisión».*esta pantalla avisa cuando termina\.$/);
      await waitFor(() => expect(document.activeElement).toBe(aviso));
      expect(screen.queryByRole("dialog")).toBeNull();
      await abrirCarga();
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("");
      // La fecha vuelve a la de hoy, la que se propone siempre (G-06).
      expect((screen.getByLabelText("Fecha del documento") as HTMLInputElement).value).toBe(hoyDelDispositivo());
      expect(screen.queryByText(/Elegido:/)).toBeNull();
      // El campo del sistema no guarda el archivo ya elegido: se vacía al elegir.
      expect((screen.getByLabelText("Archivo del documento") as HTMLInputElement).value).toBe("");
      // Control: sin lista a la vista no hay nada que volver a pedir.
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(0);
      expect(screen.queryByRole("region", { name: "Documentos" })).toBeNull();

      llenarNuevo(pdf("otro.pdf"));
      fireEvent.click(enviarCarga());
      await waitFor(() => expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(2));
      const [primera, segunda] = pedidos(simulado, "POST", BASE).map((p) => p.headers.get("Idempotency-Key"));
      expect(segunda).not.toBe(primera);
    });

    it("si cancela el reintento del archivo, la ventana se cierra, la siguiente empieza vacía y no repite el documento", async () => {
      const simulado = soloCarga({ [`POST ${versiones(D3)}`]: () => error("error_interno", 500) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await abrirCarga();
      llenarNuevo(pdf());
      fireEvent.click(enviarCarga());
      await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
      const cancelar = screen.getByRole("button", { name: "Cancelar" }) as HTMLButtonElement;
      await waitFor(() => expect(cancelar.disabled).toBe(false));
      fireEvent.click(cancelar);
      await abrirCarga();
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("");
      expect(screen.queryByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeNull();
      expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
    });
  });

  it("control: solo con `documentos.ver` y la lista caída, la carga tampoco aparece", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver"),
        [`GET ${BASE}`]: () => Promise.reject(new TypeError("Failed to fetch")),
      }),
    );
    abrir();
    expect(await screen.findByText(/^No se pudo mostrar la lista\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
  });

  it("control: con la lectura negada y sin carga, un 403 lleva al inicio", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        "GET /api/v1/auth/sesion": () => Response.json({}),
        "GET /health": () => error("servicio_no_disponible", 503),
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver"),
        [`GET ${BASE}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
  });
});

describe("tanda 7 de fricción en la ficha del documento", () => {
  it("DOC-11: separa el vigente de las versiones anteriores", () => {
    const vigente = version(V1);
    const viejo = version(V2, { subida_en: "2026-09-01T10:00:00Z" });
    const revision = version(V3, { estado: "en_cuarentena" });
    expect(archivosDeLaFicha([revision, vigente, viejo])).toEqual({ aLaVista: [revision, vigente], anteriores: [viejo] });
    expect(archivosDeLaFicha([vigente])).toEqual({ aLaVista: [vigente], anteriores: [] });
    // Sin ninguno disponible, a la vista queda el más nuevo.
    const rechazado = version(V4, { estado: "rechazado", motivo_rechazo: "pdf_cifrado" });
    expect(archivosDeLaFicha([revision, rechazado])).toEqual({ aLaVista: [revision], anteriores: [rechazado] });
    expect(archivosDeLaFicha([])).toEqual({ aLaVista: [], anteriores: [] });
  });

  it("DOC-11: en la ficha, las anteriores van plegadas bajo «Versiones anteriores»", async () => {
    const lista = { documentos: [documento(D1, "Acta de asamblea ordinaria", { versiones: [version(V1), version(V2, { subida_en: "2026-09-01T10:00:00Z" })] })] };
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Response.json(lista) }));
    abrir();
    const ficha = await tarjeta("Acta de asamblea ordinaria");
    expect(within(within(ficha).getByRole("list", { name: "Archivos de Acta de asamblea ordinaria" })).getAllByRole("listitem")).toHaveLength(1);
    const plegadas = within(ficha).getByText("Versiones anteriores (1)").closest("details");
    expect(plegadas?.open).toBe(false);
    expect(within(plegadas as HTMLElement).getAllByRole("listitem")).toHaveLength(1);
  });

  it("C8G-08: con un archivo en revisión, dice por qué no se puede subir otro", async () => {
    const lista = { documentos: [documento(D1, "Acta de asamblea ordinaria", { versiones: [version(V1, { estado: "en_cuarentena" })] })] };
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Response.json(lista) }));
    abrir();
    const ficha = await tarjeta("Acta de asamblea ordinaria");
    expect(within(ficha).getByText(ARCHIVO_EN_REVISION)).toBeTruthy();
    expect(within(ficha).queryByRole("button", { name: "Subir archivo" })).toBeNull();
  });

  it("C8G-08, control: sin carga de esa categoría no se explica nada", async () => {
    // Con el vigente disponible: sin él, quien no carga ni ve el documento (FL-DOC-02).
    const lista = { documentos: [documento(D1, "Acta de asamblea ordinaria", { versiones: [version(V2, { estado: "en_cuarentena" }), version(V1)] })] };
    vi.stubGlobal("fetch", conLista({
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver"),
      [`GET ${BASE}`]: () => Response.json(lista),
    }));
    abrir();
    const ficha = await tarjeta("Acta de asamblea ordinaria");
    expect(within(ficha).queryByText(ARCHIVO_EN_REVISION)).toBeNull();
  });

  it("DOC-08: el «Listo» de la carga se va cuando la lista muestra el archivo fuera de revisión", async () => {
    let estado: "en_cuarentena" | "disponible" = "en_cuarentena";
    let subido = false;
    const simulado = conLista({
      [`GET ${BASE}`]: () =>
        Response.json({ documentos: subido ? [documento(D3, "Presupuesto de pintura", { categoria: "presupuestos", versiones: [version(V4, { estado })] })] : [] }),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () => {
        subido = true;
        return Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", simulado);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    const LISTO = "Listo: el archivo de «Presupuesto de pintura» se subió.";
    await screen.findByText(LISTO);
    await fila("Presupuesto de pintura");
    // Control: mientras sigue en revisión, el «Listo» queda.
    expect(screen.getByText(LISTO)).toBeTruthy();
    estado = "disponible";
    await act(() => vi.advanceTimersByTimeAsync(REFRESCO_CUARENTENA_MS));
    await waitFor(() => expect(screen.queryByText(LISTO)).toBeNull());
    expect(await fila("Presupuesto de pintura")).toBeTruthy();
  });
});

describe("«Retirar el documento» (0089)", () => {
  const RETIRO = `${BASE}/${D3}/retiro`;
  const MOTIVO = "Contrato terminado: lo reemplaza el nuevo";
  const conAcciones = (acciones: string[], extra: Respuestas = {}) =>
    conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza(...acciones), ...extra });
  const ADMINISTRA = ["documentos.ver", "documentos.ver_restringidos", "documentos.cargar"];

  async function abrirRetiro(titulo = "Contrato de limpieza"): Promise<HTMLElement> {
    const ficha = await tarjeta(titulo);
    fireEvent.click(within(ficha).getByRole("button", { name: "Retirar el documento" }));
    // DOC-03: el retiro va en su ventana, encima de la ficha.
    return ventanaDe("Retirar el documento");
  }

  it("pide el motivo, avisa qué pasa, manda el retiro con su clave y vuelve a pedir la lista", async () => {
    const simulado = conAcciones(ADMINISTRA, { [`POST ${RETIRO}`]: () => Response.json({ id: D3 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const formulario = await abrirRetiro();
    // DOC-04: una sola línea de ayuda, sin «Obligatorio…», y el botón dice el verbo.
    expect(within(formulario).getByText("No se borra: queda en la exportación y en la auditoría.")).toBeTruthy();
    expect(within(formulario).queryByText(/^Obligatorio/)).toBeNull();
    expect(within(formulario).queryByRole("button", { name: "Retirar el documento" })).toBeNull();
    const enviar = within(formulario).getByRole("button", { name: "Retirar" }) as HTMLButtonElement;
    // Sin motivo no se manda: el botón sigue activo y marca el campo (decisión 4).
    expect(enviar.disabled).toBe(false);
    escribir("Motivo del retiro", "   ");
    fireEvent.click(enviar);
    expect(await within(formulario).findByText("Falta escribir el motivo del retiro.")).toBeTruthy();
    expect(screen.getByLabelText("Motivo del retiro").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(simulado, "POST", RETIRO)).toHaveLength(0);
    expect((screen.getByLabelText("Motivo del retiro") as HTMLTextAreaElement).maxLength).toBe(300);
    escribir("Motivo del retiro", `  ${MOTIVO} `);
    expect(within(formulario).queryByText("Falta escribir el motivo del retiro.")).toBeNull();
    // El principal de la ventana es el acto negativo: lleno y en rojo.
    expect(enviar.className).toBe("peligro");
    fireEvent.click(enviar);
    expect(await screen.findByText("Listo: se retiró «Contrato de limpieza». Ya no aparece en la lista.")).toBeTruthy();
    const [pedido] = pedidos(simulado, "POST", RETIRO);
    expect(await (pedido as Request).json()).toEqual({ motivo: MOTIVO });
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    await waitFor(() => expect(pedidos(simulado, "GET", BASE).length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByRole("dialog", { name: "Contrato de limpieza" })).toBeNull();
  });

  it("DOC-13: el retirado sale de la lista ya, sin esperar la nueva; la lista no se vacía mientras tanto", async () => {
    let soltar: (r: Response) => void = () => undefined;
    let vueltas = 0;
    const simulado = conAcciones(ADMINISTRA, {
      [`POST ${RETIRO}`]: () => Response.json({ id: D3 }),
      [`GET ${BASE}`]: () => (++vueltas === 1 ? Response.json(LISTA) : new Promise<Response>((r) => (soltar = r))),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const formulario = await abrirRetiro();
    escribir("Motivo del retiro", MOTIVO);
    fireEvent.click(within(formulario).getByRole("button", { name: "Retirar" }));
    await screen.findByText(/^Listo: se retiró/);
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(2));
    // Con la lista nueva en camino: el retirado ya no está y los demás siguen, sin «Consultando…».
    expect(screen.queryByRole("button", { name: /^Contrato de limpieza / })).toBeNull();
    expect(await fila("Acta de asamblea ordinaria")).toBeTruthy();
    expect(screen.queryByText("Consultando…")).toBeNull();
    soltar(Response.json({ documentos: LISTA.documentos.filter((d) => d.id !== D3) }));
  });

  it("«Cancelar» cierra la ventana sin mandar nada y deja la ficha", async () => {
    const simulado = conAcciones(ADMINISTRA);
    vi.stubGlobal("fetch", simulado);
    abrir();
    const formulario = await abrirRetiro();
    escribir("Motivo del retiro", MOTIVO);
    fireEvent.click(within(formulario).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByLabelText("Motivo del retiro")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Contrato de limpieza" })).toBeTruthy();
    expect(pedidos(simulado, "POST", RETIRO)).toHaveLength(0);
  });

  it("si el documento respalda algo guardado, lo dice y no lo da por retirado", async () => {
    vi.stubGlobal("fetch", conAcciones(ADMINISTRA, { [`POST ${RETIRO}`]: () => error("documento_referenciado", 409) }));
    abrir();
    const formulario = await abrirRetiro();
    escribir("Motivo del retiro", MOTIVO);
    fireEvent.click(within(formulario).getByRole("button", { name: "Retirar" }));
    // El rechazo queda en la ventana, con el motivo escrito, para cancelar sin volver a abrir la ficha.
    expect(
      (await within(formulario).findByRole("alert")).textContent,
    ).toBe(
      "El documento no se retiró. Este documento respalda algo guardado (una orden, un pago, un proveedor, un contrato o el acta de una decisión): no se puede retirar.",
    );
    expect((screen.getByLabelText("Motivo del retiro") as HTMLTextAreaElement).value).toBe(MOTIVO);
    expect(screen.queryByText(/^Listo: se retiró/)).toBeNull();
  });

  it("solo para quien carga la categoría y además tiene `documentos.ver`", async () => {
    // Control: con las tres, se ofrece.
    vi.stubGlobal("fetch", conAcciones(ADMINISTRA));
    abrir();
    expect(within(await tarjeta("Contrato de limpieza")).getByRole("button", { name: "Retirar el documento" })).toBeTruthy();
    cleanup();
    // Sin la facultad de ver (`documentos.ver_restringidos`): no.
    vi.stubGlobal("fetch", conAcciones(["documentos.ver", "documentos.cargar"]));
    abrir();
    expect(within(await tarjeta("Contrato de limpieza")).queryByRole("button", { name: "Retirar el documento" })).toBeNull();
    cleanup();
    // El Consejo que carga solo lo operativo: el acta sí, el contrato no.
    vi.stubGlobal("fetch", conAcciones(["documentos.ver", "documentos.ver_restringidos", "documentos.cargar_operativos"]));
    abrir();
    expect(within(await tarjeta("Acta de asamblea ordinaria")).getByRole("button", { name: "Retirar el documento" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar" }));
    // El contrato no tiene archivo y no carga esa categoría: ni siquiera lo ve en la lista (FL-DOC-02).
    expect(screen.queryByRole("button", { name: /^Contrato de limpieza / })).toBeNull();
  });

  it("sin faltas de accesibilidad detectables por axe con el formulario abierto, y sin voseo", async () => {
    vi.stubGlobal("fetch", conAcciones(ADMINISTRA));
    const { container } = abrir();
    const formulario = await abrirRetiro();
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    expect(formulario.textContent).not.toMatch(/\b(tenés|podés|escribí|elegí|tocá|subí|soltá|retirá)\b/);
  });
});

describe("tanda 6 de fricción en /documentos", () => {
  const ADMINISTRA = ["documentos.ver", "documentos.ver_restringidos", "documentos.cargar"];
  const conAcciones = (acciones: string[], extra: Respuestas = {}) =>
    conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza(...acciones), ...extra });

  // DOC-12: decisión del propietario, 18-11-2026 (deja de lado G-02 en esto): ni botón ni párrafo.
  it("DOC-12: un documento que respalda algo no ofrece «Retirar el documento» ni explica por qué", async () => {
    const lista = {
      documentos: [
        documento(D1, "Acta de asamblea ordinaria", { versiones: [version(V1)], respalda: true }),
        documento(D3, "Contrato de limpieza", { categoria: "contratos_proveedores", respalda: false }),
      ],
    };
    vi.stubGlobal("fetch", conAcciones(ADMINISTRA, { [`GET ${BASE}`]: () => Response.json(lista) }));
    abrir();
    const acta = await tarjeta("Acta de asamblea ordinaria");
    expect(within(acta).queryByRole("button", { name: RETIRAR_DOCUMENTO })).toBeNull();
    expect(within(acta).queryByText(RESPALDA_ALGO)).toBeNull();
    expect(within(acta).queryByText(/no se puede retirar/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar" }));
    // Control: el que no respalda nada sigue ofreciéndolo, sin el aviso.
    const contrato = await tarjeta("Contrato de limpieza");
    expect(within(contrato).getByRole("button", { name: RETIRAR_DOCUMENTO })).toBeTruthy();
    expect(within(contrato).queryByText(RESPALDA_ALGO)).toBeNull();
  });

  it("G-05: «Cargar un documento» está arriba, antes de la lista, y no repite su nombre adentro", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    const boton = await screen.findByRole("button", { name: "Cargar un documento" });
    const lista = screen.getByRole("region", { name: "Documentos" });
    expect(boton.compareDocumentPosition(lista) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(boton);
    // DOC-01: en su ventana, con el nombre una vez (el título) y el verbo en el botón.
    const carga = ventanaDe("Cargar un documento");
    expect(within(carga).queryByText("Datos del documento")).toBeNull();
    expect(within(carga).getAllByText("Cargar un documento")).toHaveLength(1);
    expect(within(carga).getByRole("button", { name: "Cargar" })).toBeTruthy();
  });

  it("G-06: el archivo va primero, propone el título y la fecha empieza en hoy, sin ayudas fijas (DOC-10)", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
    const carga = ventanaDe("Cargar un documento");
    const archivoCampo = within(carga).getByLabelText("Archivo del documento");
    const categoria = within(carga).getByLabelText("Categoría");
    expect(archivoCampo.compareDocumentPosition(categoria) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const fecha = within(carga).getByLabelText("Fecha del documento") as HTMLInputElement;
    expect(fecha.value).toBe(hoyDelDispositivo());
    expect(fecha.getAttribute("aria-describedby")).toBeNull();
    expect(within(carga).queryByText("Antes de mostrarlo lo revisa el antivirus.")).toBeNull();
    const titulo = within(carga).getByLabelText("Título") as HTMLInputElement;
    expect(titulo.value).toBe("");
    elegir(pdf("Acta 12 de asamblea.pdf"));
    expect(titulo.value).toBe("Acta 12 de asamblea");
    // Otro archivo cambia lo que propuso el anterior...
    elegir(pdf("Acta 13.pdf"));
    expect(titulo.value).toBe("Acta 13");
    // ...pero nunca lo que escribió la persona (control).
    escribir("Título", "Acta de la asamblea de octubre");
    elegir(pdf("otro.pdf"));
    expect(titulo.value).toBe("Acta de la asamblea de octubre");
  });

  describe("G-09: quien solo carga se entera de cómo terminó el antivirus", () => {
    const ESTADO_V4 = `${versiones(D3)}/${V4}/estado`;
    const subida = () =>
      Response.json(
        { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
        { status: 201 },
      );
    // Un segundo documento (D2, con su versión V3) que sigue en revisión.
    const ESTADO_V3 = `${versiones(D2)}/${V3}/estado`;
    const soloCarga = (estado: () => Response) => {
      let creados = 0;
      return conLista({
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.cargar"),
        [`POST ${BASE}`]: () => Response.json({ id: ++creados === 1 ? D3 : D2 }, { status: 201 }),
        [`POST ${versiones(D3)}`]: subida,
        [`GET ${ESTADO_V4}`]: estado,
        [`POST ${versiones(D2)}`]: () =>
          Response.json(
            { id: V3, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "c".repeat(64) },
            { status: 201 },
          ),
        [`GET ${ESTADO_V3}`]: () => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }),
      });
    };
    const RECHAZO = "«Rechazado»: el archivo de «Presupuesto de pintura» no pasó la revisión (el PDF tiene contraseña). No se va a mostrar: hay que subir otro.";

    /** Con el formulario nuevo a la vista, llena el de un segundo documento: el plano. */
    function llenarElPlano() {
      fireEvent.click(screen.getByRole("button", { name: "Cargar un documento" }));
      fireEvent.change(screen.getByLabelText("Categoría"), { target: { value: "presupuestos" } });
      escribir("Título", "Plano del edificio");
      elegir(pdf("plano.pdf"));
    }

    async function subirYEsperar(simulado: ReturnType<typeof soloCarga>) {
      vi.stubGlobal("fetch", simulado);
      abrir();
      await abrirCarga();
      llenarNuevo(pdf());
      vi.useFakeTimers({ shouldAdvanceTime: true });
      fireEvent.click(enviarCarga());
      await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura» se subió/);
    }

    it("sigue preguntando mientras está en revisión y dice «Disponible» al terminar, sin recargar", async () => {
      let vueltas = 0;
      const simulado = soloCarga(() =>
        Response.json(++vueltas < 2 ? { estado: "en_cuarentena", motivo_rechazo: null } : { estado: "disponible", motivo_rechazo: null }),
      );
      await subirYEsperar(simulado);
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(
        await screen.findByText("Listo: el archivo de «Presupuesto de pintura» pasó la revisión del antivirus y quedó «Disponible»."),
      ).toBeTruthy();
      const consultas = pedidos(simulado, "GET", ESTADO_V4).length;
      expect(consultas).toBeGreaterThanOrEqual(2); // la primera dijo «en revisión»
      // Terminado, no pregunta más (control de que la espera se cortó).
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(consultas);
    });

    it("si el antivirus lo rechaza, dice «Rechazado» con el motivo y ofrece subir otro para el mismo documento", async () => {
      const simulado = soloCarga(() => Response.json({ estado: "rechazado", motivo_rechazo: "pdf_cifrado" }));
      await subirYEsperar(simulado);
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(await screen.findByText(RECHAZO)).toBeTruthy();
      // Subir otro es un paso que la persona elige: el formulario no cambia solo.
      expect(screen.queryByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Subir otro archivo para «Presupuesto de pintura»" }));
      expect(screen.getByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeTruthy();
      // Con su formulario abierto, el botón ya no se ofrece.
      expect(screen.queryByRole("button", { name: "Subir otro archivo para «Presupuesto de pintura»" })).toBeNull();
    });

    it("revisión del PR #241: un rechazo que llega con otro documento a medio cargar no le cambia el destino al archivo elegido", async () => {
      const simulado = soloCarga(() => Response.json({ estado: "rechazado", motivo_rechazo: "pdf_cifrado" }));
      await subirYEsperar(simulado);
      llenarElPlano();
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(await screen.findByText(RECHAZO)).toBeTruthy();
      // El formulario sigue siendo el del documento nuevo, con lo elegido.
      expect(screen.queryByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeNull();
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("Plano del edificio");
      fireEvent.click(enviarCarga());
      await screen.findByText(/^Listo: el archivo de «Plano del edificio» se subió/);
      // El plano fue a un documento nuevo; el rechazado recibió un solo archivo, el suyo.
      expect(pedidos(simulado, "POST", BASE)).toHaveLength(2);
      expect(pedidos(simulado, "POST", versiones(D2))).toHaveLength(1);
      expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(1);
      // El rechazo sigue a la vista: ese documento todavía espera otro archivo.
      expect(screen.getByText(RECHAZO)).toBeTruthy();
    });

    it("revisión del PR #241: un 429 pasajero no corta la consulta: la vuelta siguiente trae el rechazo", async () => {
      let vueltas = 0;
      const simulado = soloCarga(() =>
        ++vueltas === 1 ? error("limite_de_pedidos", 429) : Response.json({ estado: "rechazado", motivo_rechazo: "pdf_cifrado" }),
      );
      await subirYEsperar(simulado);
      // Una vuelta, y se espera a que su 429 se procese antes de la siguiente.
      await act(() => vi.advanceTimersByTimeAsync(3_000));
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(1));
      await act(() => new Promise((listo) => setTimeout(listo, 100)));
      // El 429 no es un resultado: ni se dice como error final ni alarma con una sola falla.
      expect(screen.queryByText(LIMITE_DE_PEDIDOS)).toBeNull();
      expect(screen.queryByText(/No se pudo consultar cómo va/)).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(3_000));
      expect(await screen.findByText(RECHAZO)).toBeTruthy();
      expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(2);
    });

    it("revisión del PR #241: si el servicio sigue caído, lo dice y sigue preguntando; al volver, trae el resultado", async () => {
      let caido = true;
      const simulado = soloCarga(() =>
        caido ? error("servicio_no_disponible", 503) : Response.json({ estado: "disponible", motivo_rechazo: null }),
      );
      await subirYEsperar(simulado);
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(
        await screen.findByText(
          "No se pudo consultar cómo va el archivo de «Presupuesto de pintura» (el servicio no está disponible); esta pantalla lo sigue intentando.",
        ),
      ).toBeTruthy();
      const consultas = pedidos(simulado, "GET", ESTADO_V4).length;
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(pedidos(simulado, "GET", ESTADO_V4).length).toBeGreaterThan(consultas);
      caido = false;
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(
        await screen.findByText("Listo: el archivo de «Presupuesto de pintura» pasó la revisión del antivirus y quedó «Disponible»."),
      ).toBeTruthy();
      expect(screen.queryByText(/No se pudo consultar cómo va/)).toBeNull();
    });

    it("revisión del PR #241: un 404 es definitivo: lo dice, sin ofrecer subir otro, y deja de preguntar", async () => {
      const simulado = soloCarga(() => error("no_encontrado", 404));
      await subirYEsperar(simulado);
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(
        await screen.findByText(
          "No se puede saber cómo terminó la revisión del archivo de «Presupuesto de pintura»: ese documento o ese archivo ya no está en el edificio.",
        ),
      ).toBeTruthy();
      expect(screen.queryByRole("button", { name: /^Subir otro archivo/ })).toBeNull();
      const consultas = pedidos(simulado, "GET", ESTADO_V4).length;
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(consultas);
    });

    it("revisión del PR #241: lo que responde tarde el edificio anterior no aparece en el nuevo ni le cambia el formulario", async () => {
      const C2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
      let responder: (r: Response) => void = () => undefined;
      const tarde = new Promise<Response>((listo) => {
        responder = listo;
      });
      const simulado = conLista({
        "GET /api/v1/me/consorcios": () =>
          Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Bordó", vinculos: [] }]),
        [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.cargar"),
        [`GET /api/v1/consorcios/${C2}/mis-acciones`]: alcanza("documentos.cargar"),
        [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
        [`POST ${versiones(D3)}`]: subida,
        [`GET ${ESTADO_V4}`]: () => tarde,
      });
      await subirYEsperar(simulado);
      await act(() => vi.advanceTimersByTimeAsync(3_000));
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(1));
      // Con la consulta en vuelo, otro edificio; después llega el rechazo del anterior.
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      await screen.findByRole("button", { name: "Cargar un documento" });
      await act(async () => {
        responder(Response.json({ estado: "rechazado", motivo_rechazo: "pdf_cifrado" }));
        await new Promise((listo) => setTimeout(listo, 100));
      });
      expect(screen.queryByText(RECHAZO)).toBeNull();
      expect(screen.queryByRole("button", { name: /^Subir otro archivo/ })).toBeNull();
      expect(screen.queryByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeNull();
      // Y no se sigue preguntando por el archivo del edificio anterior.
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(pedidos(simulado, "GET", ESTADO_V4)).toHaveLength(1);
    });

    it("revisión del PR #241: subir otro documento antes de que termine el primero no deja de mirar el primero", async () => {
      let vueltas = 0;
      const simulado = soloCarga(() =>
        Response.json(++vueltas < 2 ? { estado: "en_cuarentena", motivo_rechazo: null } : { estado: "rechazado", motivo_rechazo: "pdf_cifrado" }),
      );
      await subirYEsperar(simulado);
      llenarElPlano();
      fireEvent.click(enviarCarga());
      await screen.findByText(/^Listo: el archivo de «Plano del edificio» se subió/);
      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(await screen.findByText(RECHAZO)).toBeTruthy();
      // El segundo, todavía en revisión, se sigue mirando (control de que no se cambió uno por otro).
      const delSegundo = pedidos(simulado, "GET", ESTADO_V3).length;
      expect(delSegundo).toBeGreaterThanOrEqual(1);
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      expect(pedidos(simulado, "GET", ESTADO_V3).length).toBeGreaterThan(delSegundo);
    });
  });
});


describe("Flujos 18-11, tanda 7", () => {
  it("FL-DOC-01: con la lista a la vista, el rechazo del antivirus se avisa con su botón en lugar del «Listo»", async () => {
    let estado: "en_cuarentena" | "rechazado" = "en_cuarentena";
    let subido = false;
    const simulado = conLista({
      [`GET ${BASE}`]: () =>
        Response.json({
          documentos: subido
            ? [documento(D3, "Presupuesto de pintura", { categoria: "presupuestos",
                versiones: [version(V4, { estado, motivo_rechazo: estado === "rechazado" ? "pdf_cifrado" : null })] })]
            : [],
        }),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () => {
        subido = true;
        return Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 });
      },
    });  // prettier-ignore
    vi.stubGlobal("fetch", simulado);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    const LISTO = "Listo: el archivo de «Presupuesto de pintura» se subió.";
    await screen.findByText(LISTO);
    await fila("Presupuesto de pintura");
    estado = "rechazado";
    await act(() => vi.advanceTimersByTimeAsync(REFRESCO_CUARENTENA_MS));
    expect(
      await screen.findByText("«Rechazado»: el archivo de «Presupuesto de pintura» no pasó la revisión (el PDF tiene contraseña). No se va a mostrar: hay que subir otro."),
    ).toBeTruthy();
    expect(screen.queryByText(LISTO)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Subir otro archivo para «Presupuesto de pintura»" }));
    expect(screen.getByRole("dialog", { name: "Subir el archivo de «Presupuesto de pintura»" })).toBeTruthy();
  });

  it("FL-DOC-01, control: si el antivirus lo aprueba, no queda ningún rechazo", async () => {
    let estado: "en_cuarentena" | "disponible" = "en_cuarentena";
    let subido = false;
    vi.stubGlobal("fetch", conLista({
      [`GET ${BASE}`]: () =>
        Response.json({ documentos: subido ? [documento(D3, "Presupuesto de pintura", { categoria: "presupuestos", versiones: [version(V4, { estado })] })] : [] }),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () => {
        subido = true;
        return Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 });
      },
    }));  // prettier-ignore
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    await screen.findByText("Listo: el archivo de «Presupuesto de pintura» se subió.");
    estado = "disponible";
    await act(() => vi.advanceTimersByTimeAsync(REFRESCO_CUARENTENA_MS));
    await waitFor(() => expect(screen.queryByText(/^Listo: el archivo/)).toBeNull());
    expect(screen.queryByText(/«Rechazado»/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Subir otro archivo/ })).toBeNull();
  });

  it("FL-DOC-02: a quien solo lee, la lista no le muestra documentos sin un archivo disponible", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver") }));
    abrir();
    expect(await fila("Acta de asamblea ordinaria")).toBeTruthy();
    // La póliza solo tiene un archivo rechazado y el contrato ninguno: no hay nada que abrir.
    expect(screen.queryByRole("button", { name: /^Póliza de incendio / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Contrato de limpieza / })).toBeNull();
    expect(screen.queryByText(/Sin archivo/)).toBeNull();
  });

  it("FL-DOC-02: si ningún documento tiene archivo para quien solo lee, dice que no hay documentos", async () => {
    vi.stubGlobal("fetch", conLista({
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver"),
      [`GET ${BASE}`]: () => Response.json({ documentos: [documento(D3, "Contrato de limpieza", { categoria: "contratos_proveedores" })] }),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText("El edificio todavía no tiene documentos cargados.")).toBeTruthy();
  });

  it("FL-DOC-02, control: quien carga la categoría ve los documentos que esperan su archivo", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.ver", "documentos.cargar") }));
    abrir();
    expect((await fila("Contrato de limpieza")).textContent).toMatch(/Sin archivo$/);
    expect(await fila("Póliza de incendio")).toBeTruthy();
  });

  it("FL-DOC-03: si el archivo no entra y se cancela, el documento queda arriba con su botón y no se crea otro", async () => {
    let intentos = 0;
    const simulado = conLista({
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.cargar"),
      [`POST ${BASE}`]: () => Response.json({ id: D3 }, { status: 201 }),
      [`POST ${versiones(D3)}`]: () =>
        ++intentos === 1
          ? error("error_interno", 500)
          : Response.json({ id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) }, { status: 201 }),
      [`GET ${versiones(D3)}/${V4}/estado`]: () => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(enviarCarga());
    const ventana = await screen.findByRole("dialog", { name: "Subir el archivo de «Presupuesto de pintura»" });
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    expect(await screen.findByText("El documento «Presupuesto de pintura» quedó creado sin archivo.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo de «Presupuesto de pintura»" }));
    const otra = screen.getByRole("dialog", { name: "Subir el archivo de «Presupuesto de pintura»" });
    elegir(pdf());
    fireEvent.click(within(otra).getByRole("button", { name: "Subir el archivo" }));
    await screen.findByText(/^Listo: el archivo de «Presupuesto de pintura» se subió/);
    // Se completó el mismo documento: uno solo creado, y el aviso del vacío ya no está.
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(2);
    expect(screen.queryByText(/quedó creado sin archivo/)).toBeNull();
  });

  it("FL-DOC-03, control: cancelar una carga que no creó nada no deja ningún aviso", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await completarNuevo();
    fireEvent.click(within(ventanaDe("Cargar un documento")).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Cargar un documento" })).toBeNull());
    expect(screen.queryByText(/sin archivo\.$/)).toBeNull();
  });
});

describe("tanda 10 de flujos: lo que cargó esta cuenta (FL-DOC-04)", () => {
  const MIS_CARGAS = `${BASE}/mis-cargas`;
  const carga = (version: string, titulo: string, estado: string, motivo: string | null = null) => ({
    documento_id: D3, version_id: version, titulo, categoria: "presupuestos", estado, motivo_rechazo: motivo,
    subida_en: "2026-11-18T12:00:00Z",
  });  // prettier-ignore
  const soloCarga = (respuesta: () => Response) =>
    conLista({ [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("documentos.cargar"), [`GET ${MIS_CARGAS}`]: respuesta });

  it("quien solo carga ve, al llegar, cada archivo suyo con su estado; el rechazado, con el motivo", async () => {
    vi.stubGlobal("fetch", soloCarga(() => Response.json({ elementos: [carga(V4, "Presupuesto de pintura", "rechazado", "pdf_cifrado"), carga(V3, "Plano del edificio", "disponible")] })));
    abrir();
    const lista = within(await screen.findByRole("list", { name: LO_QUE_CARGO }));
    expect(lista.getByText("Presupuesto de pintura")).toBeTruthy();
    expect(lista.getByText(/^Rechazado: el PDF tiene contraseña · subido el /)).toBeTruthy();
    expect(lista.getByText(/^Disponible · subido el /)).toBeTruthy();
    // Son filas para leer: no abren nada.
    expect(lista.queryByRole("button")).toBeNull();
  });

  it("lo que sigue en revisión se vuelve a mirar solo hasta que termina", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
    let estado = "en_cuarentena";
    const simulado = soloCarga(() => Response.json({ elementos: [carga(V4, "Presupuesto de pintura", estado)] }));
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/^En revisión · subido el /)).toBeTruthy();
    estado = "disponible";
    await act(() => vi.advanceTimersByTimeAsync(REFRESCO_CUARENTENA_MS));
    expect(await screen.findByText(/^Disponible · subido el /)).toBeTruthy();
    const lecturas = pedidos(simulado, "GET", MIS_CARGAS).length;
    // Terminado, no vuelve a preguntar (control de que la espera se cortó).
    await act(() => vi.advanceTimersByTimeAsync(REFRESCO_CUARENTENA_MS * 2));
    expect(pedidos(simulado, "GET", MIS_CARGAS)).toHaveLength(lecturas);
    vi.useRealTimers();
  });

  it("sin cargas lo dice; si la lectura falla, lo dice y deja consultar de nuevo", async () => {
    let falla = true;
    vi.stubGlobal("fetch", soloCarga(() => (falla ? error("error_interno", 500) : Response.json({ elementos: [] }))));
    abrir();
    expect(await screen.findByText(/^No se pudo mostrar lo que cargó esta cuenta\./)).toBeTruthy();
    falla = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await screen.findByText(SIN_CARGAS_PROPIAS)).toBeTruthy();
  });

  it("control: quien ve la lista general no recibe esta sección ni la pide", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText("Acta de asamblea ordinaria");
    expect(screen.queryByRole("heading", { name: LO_QUE_CARGO })).toBeNull();
    expect(pedidos(simulado, "GET", MIS_CARGAS)).toHaveLength(0);
  });
});
