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
import { aceptar, TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "./archivos";
import { mensajeDeError, REFRESCO_CUARENTENA_MS, tamano } from "./Documentos";
import { SIN_CONEXION } from "./fallas";
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

async function tarjeta(titulo: string): Promise<HTMLElement> {
  return (await screen.findByRole("heading", { level: 4, name: titulo })).closest("li") as HTMLElement;
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
  it("el inicio tiene el enlace a los documentos", async () => {
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
    expect((await screen.findByRole("link", { name: "Documentos del edificio" })).getAttribute("href")).toBe("/documentos");
  });

  it("si el archivo no se puede bajar, lo dice en la pantalla y no navega", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click");
    vi.stubGlobal(
      "fetch",
      conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => error("archivo_vencido", 410) }),
    );
    abrir();
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    const aviso = await screen.findByText("Ese archivo ya no está disponible.");
    // El aviso queda al final de la página: el foco lo lleva a la vista en un teléfono.
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    expect(clic).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 1, name: /Documentos/ })).toBeTruthy();
  });

  it("sin conexión al descargar, el aviso también toma el foco", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => Promise.reject(new TypeError("Failed to fetch")) }),
    );
    abrir();
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    const aviso = await screen.findByText(SIN_CONEXION);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
  });

  it("control: una descarga buena no le deja el foco a un aviso que llegue después", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const redireccion = Response.error();
    Object.defineProperty(redireccion, "type", { value: "opaqueredirect" });
    let caida = false;
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => redireccion,
        [`GET ${BASE}`]: () => (caida ? error("servicio_no_disponible", 503) : Response.json(LISTA)),
      }),
    );
    abrir();
    fireEvent.click((await screen.findAllByRole("link", { name: "Descargar" }))[0]!);
    await waitFor(() => expect(clic).toHaveBeenCalledOnce());
    caida = true;
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    const aviso = await screen.findByText(/El servicio no está disponible/);
    await new Promise((r) => setTimeout(r, 0));
    expect(document.activeElement).not.toBe(aviso);
  });

  it("si el archivo está, navega a la descarga sin mostrar error", async () => {
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const redireccion = Response.error();
    Object.defineProperty(redireccion, "type", { value: "opaqueredirect" });
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/${D1}/versiones/${V1}/descargar`]: () => redireccion }));
    abrir();
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
    const acta = await tarjeta("Acta de asamblea ordinaria");
    expect(screen.getByRole("heading", { level: 3, name: "Actas y decisiones" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Seguros" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Contratos con proveedores" })).toBeTruthy();
    // Una categoría sin documentos no aparece.
    expect(screen.queryByRole("heading", { level: 3, name: "Presupuestos" })).toBeNull();
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

    const poliza = await tarjeta("Póliza de incendio");
    expect(within(poliza).getByText("Rechazado: el PDF tiene contraseña")).toBeTruthy();
    expect(within(poliza).getByText(/PNG de 1 KB/)).toBeTruthy();
    expect(within(poliza).queryByRole("link", { name: "Descargar" })).toBeNull();
    expect(within(poliza).getByRole("button", { name: "Subir archivo" })).toBeTruthy();

    const contrato = await tarjeta("Contrato de limpieza");
    expect(within(contrato).getByText("Todavía no tiene un archivo.")).toBeTruthy();
    expect(within(contrato).getByRole("button", { name: "Subir archivo" })).toBeTruthy();
    // Control: en toda la página hay una sola descarga, y la descarga nunca se pidió con fetch.
    expect(screen.getAllByRole("link", { name: "Descargar" })).toHaveLength(1);
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
    const hoja = await screen.findByRole("dialog", { name: "Foto del tablero" });
    expect((await within(hoja).findByRole("img", { name: "Foto del tablero" })).getAttribute("src")).toBe("blob:sintetico");
    expect(hoja.className).toContain("hoja--ancha");
    // En StrictMode el efecto corre dos veces; el primero se descarta. Lo que importa: se pidió por `…/contenido`.
    expect(pedidos(simulado, "GET", contenido).length).toBeGreaterThan(0);
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/descargar"))).toBe(false);
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // Control: el foco vuelve a «Ver».
    expect(document.activeElement).toBe(ver[0]);
  });

  it("mientras llega la lista dice «Consultando…», sin documentos", async () => {
    const simulado = conLista({ [`GET ${BASE}`]: () => new Promise<Response>(() => undefined) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    // Control: ya se pidió la lista, así que es su carga y no la de los edificios.
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(1));
    expect(screen.getByText("Consultando…")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 4 })).toBeNull();
  });

  it("un edificio sin documentos lo dice, sin categorías vacías, y deja cargar el primero", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Response.json({ documentos: [] }) }));
    abrir();
    expect(await screen.findByText("El edificio todavía no tiene documentos cargados.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3 })).toBeNull();
    expect(screen.getByRole("button", { name: "Cargar un documento" })).toBeTruthy();
  });

  it("sin conexión al pedir la lista lo dice, sin documentos", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => Promise.reject(new TypeError("Failed to fetch")) }));
    abrir();
    expect(await screen.findByText("No pudimos mostrar la lista.")).toBeTruthy();
    expect(screen.getByText(SIN_CONEXION)).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 4 })).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    const listo = await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/);
    expect(listo.textContent).toMatch(/tocá «Actualizar»/);
    expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(1);
  });

  it("con la lista caída, lo escrito en la carga sigue al tocar «Actualizar»", async () => {
    const simulado = conLista({ [`GET ${BASE}`]: () => Promise.reject(new TypeError("Failed to fetch")) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Cargar un documento" }));
    escribir("Título", "Presupuesto de pintura");
    const titulo = screen.getByLabelText("Título") as HTMLInputElement;
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(2));
    await screen.findByText("No pudimos mostrar la lista.");
    // El mismo campo: el formulario no se desarmó mientras se consultaba.
    expect(screen.getByLabelText("Título")).toBe(titulo);
    expect(titulo.value).toBe("Presupuesto de pintura");
  });

  it("«Actualizar» vuelve a pedir la lista", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await tarjeta("Acta de asamblea ordinaria");
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(2);
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
    expect(await screen.findByRole("heading", { level: 2, name: "Cargar un documento" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Documentos" })).toBeNull();
    expect(screen.queryByText(/No tenés acceso/)).toBeNull();
    expect(screen.queryByText(/tu mandato te permite/)).toBeNull();
    expect(screen.queryByRole("heading", { level: 4 })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    expect(document.querySelector(".aviso-error")).toBeNull();
    // El administrador externo solo carga (documento 08 §4): el formulario ya está abierto.
    expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
    llenarNuevo(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    const listo = await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/);
    // Sin lista no hay botón «Actualizar», y el aviso no lo nombra.
    expect(listo.textContent).not.toMatch(/Actualizar/);
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
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    expect(await screen.findByText("No pudimos mostrar la lista.")).toBeTruthy();
    const reintento = await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
    expect(reintento.tagName).toBe("H3");
    elegir(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    const listo = await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/);
    // Con la lista caída sí está «Actualizar», y el aviso lo nombra (control del caso sin lista).
    expect(listo.textContent).toMatch(/tocá «Actualizar»/);
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
    await screen.findByLabelText("Categoría");
    llenarNuevo(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    const reintento = await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
    expect(reintento.tagName).toBe("H3"); // bajo el h2 de la sección, sin saltar niveles
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(document.body, reglas)).violations.map((v) => v.id)).toEqual([]);
    elegir(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/)).toBeTruthy();
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
    expect(document.activeElement).toBe(screen.getByLabelText("Categoría"));
    expect(screen.getByLabelText("Archivo del documento").getAttribute("accept")).toBe(aceptar(TIPOS_DE_DOCUMENTO));
    // Todos los caminos a la vez: soltar, la cámara, la galería y el archivo.
    expect(screen.getByText("Soltar el archivo acá")).toBeTruthy();
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"]) expect(screen.getByRole("button", { name: nombre })).toBeTruthy();
    expect(screen.getByLabelText("Foto del documento").getAttribute("capture")).toBe("environment");
    llenarNuevo(pdf());
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));

    const aviso = await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»\. Queda «En revisión»/);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
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
    // Con la lista a la vista el formulario se cierra: la pantalla siguiente es la lista actualizada.
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
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    const aviso = await screen.findByText("Hay que elegir el archivo.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    elegir(archivo(["hola"], "notas.txt", "text/plain"));
    expect(await screen.findByText("Solo se aceptan archivos PDF, JPEG o PNG. Hay que elegir otro archivo.")).toBeTruthy();
    // Control: con un archivo justo de 20 MB, sí sale el pedido.
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
    const justo = pdf("justo.pdf");
    Object.defineProperty(justo, "size", { value: TOPE_BYTES });
    elegir(justo);
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    await waitFor(() => expect(pedidos(simulado, "POST", BASE)).toHaveLength(1));
  });

  it("un 403 al crear dice que no puede cargar esa categoría y no sube nada", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => error("sin_permiso", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarNuevo();
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    expect(await screen.findByText("No tenés permiso para cargar documentos de esa categoría.")).toBeTruthy();
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
    expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/versiones"))).toBe(false);
    // El formulario sigue abierto para elegir otra categoría.
    expect(screen.getByRole("button", { name: "Cargar el documento" })).toBeTruthy();
  });

  it("«Subir archivo» de un documento existente explica un 413 y un 415 del servidor", async () => {
    let respuesta = () => error("archivo_demasiado_grande", 413);
    const simulado = conLista({ [`POST ${versiones(D3)}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const contrato = await tarjeta("Contrato de limpieza");
    fireEvent.click(within(contrato).getByRole("button", { name: "Subir archivo" }));
    expect(document.activeElement).toBe(within(contrato).getByRole("button", { name: "Sacar una foto" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    expect(
      await screen.findByText(
        "Creamos el documento «Presupuesto de pintura», pero el archivo no se subió. Hay muchas cargas en curso. Probá de nuevo en un momento.",
      ),
    ).toBeTruthy();
    // La lista nueva trae el documento sin archivo, con el formulario para subirlo abierto.
    const tarjetaNueva = await tarjeta("Presupuesto de pintura");
    expect(within(tarjetaNueva).getByRole("heading", { level: 5, name: "Subir el archivo de «Presupuesto de pintura»" })).toBeTruthy();
    expect(within(tarjetaNueva).getByText(/Elegido: acta\.pdf/)).toBeTruthy();
    respuesta = () =>
      Response.json(
        { id: V4, estado: "en_cuarentena", tipo: "application/pdf", tamano: BYTES_PDF.length, sha256: "b".repeat(64) },
        { status: 201 },
      );
    fireEvent.click(within(tarjetaNueva).getByRole("button", { name: "Subir el archivo" }));
    await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/);
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
    expect(await screen.findByText(/No pudimos consultar tus edificios/)).toBeTruthy();
    // Control: la primera carga pidió una vez la pantalla y otra permisos.tsx, aun con StrictMode.
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(2);
    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await tarjeta("Acta de asamblea ordinaria")).toBeTruthy();
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
    expect(screen.queryByText(/No pudimos consultar tus edificios/)).toBeNull();
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

    it("tras subir, el aviso dice que la lista se actualiza sola y ya no manda a tocar «Actualizar»", async () => {
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
      fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
      const listo = await screen.findByText(/^Listo: subimos el archivo de «Presupuesto de pintura»/);
      expect(listo.textContent).toMatch(/la lista se actualiza sola/);
      expect(listo.textContent).not.toMatch(/Actualizar/);
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
      await tarjeta(ACTA);
      fireEvent.click(screen.getByRole("button", { name: "Cargar un documento" }));
      escribir("Título", "Presupuesto de pintura");
      caida = true;
      await adelantar(REFRESCO_CUARENTENA_MS);
      expect(await screen.findByText(/La lista puede no estar al día: tocá «Actualizar»\./)).toBeTruthy();
      expect(screen.getByRole("heading", { level: 4, name: ACTA })).toBeTruthy();
      expect(screen.queryByText("No pudimos mostrar la lista.")).toBeNull();
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("Presupuesto de pintura");
      await adelantar(REFRESCO_CUARENTENA_MS * 3);
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(2);
    });

    it("control: la misma falla al tocar «Actualizar» sí dice que no se pudo mostrar la lista", async () => {
      let caida = false;
      const simulado = conLista({
        [`GET ${BASE}`]: () => (caida ? error("servicio_no_disponible", 503) : Response.json(EN_REVISION)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await tarjeta(ACTA);
      caida = true;
      fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
      expect(await screen.findByText("No pudimos mostrar la lista.")).toBeTruthy();
      expect(screen.queryByText(/La lista puede no estar al día/)).toBeNull();
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
      "Revisá el título. El título no puede llevar teléfonos ni correos.",
    );
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

    expect(await screen.findByText(/^Todavía no participás de ningún edificio./)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
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
    // Control: el documento de otra categoría también espera su archivo, y no se ofrece.
    const contrato = await tarjeta("Contrato sin archivo");
    expect(within(contrato).getByText("Todavía no tiene un archivo.")).toBeTruthy();
    expect(within(contrato).queryByRole("button", { name: "Subir archivo" })).toBeNull();
  });

  it("solo con `documentos.ver` no hay carga, ni formulario ni «Subir archivo»", async () => {
    vi.stubGlobal("fetch", conAcciones("documentos.ver"));
    abrir();
    await tarjeta("Contrato de limpieza");
    expect(screen.queryByRole("heading", { level: 2, name: "Cargar un documento" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Subir archivo" })).toBeNull();
  });

  it("sin `documentos.ver`, quien carga no pide la lista y no ve ningún texto sobre ella", async () => {
    const simulado = conAcciones("documentos.cargar");
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByLabelText("Categoría")).toBeTruthy();
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

    it("encuentra el formulario abierto, sin el botón para abrirlo, sin «Cancelar» y sin que le muevan el foco", async () => {
      vi.stubGlobal("fetch", soloCarga());
      abrir();
      await screen.findByLabelText("Categoría");
      expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Cancelar" })).toBeNull();
      expect(document.activeElement).toBe(document.body);
      expect(screen.getByRole("button", { name: "Cargar el documento" })).toBeTruthy();
    });

    it("terminada la carga, el formulario queda abierto y vacío para el siguiente, con el aviso", async () => {
      const simulado = soloCarga({ [`POST ${versiones(D3)}`]: subidaEnRevision });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await screen.findByLabelText("Categoría");
      const campoArchivo = screen.getByLabelText("Archivo del documento");
      llenarNuevo(pdf());
      fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
      const aviso = await screen.findByText("Listo: subimos el archivo de «Presupuesto de pintura». Queda «En revisión» mientras lo analizamos.");
      await waitFor(() => expect(document.activeElement).toBe(aviso));
      expect((screen.getByLabelText("Título") as HTMLInputElement).value).toBe("");
      expect((screen.getByLabelText("Fecha del documento") as HTMLInputElement).value).toBe("");
      expect(screen.queryByText(/Elegido:/)).toBeNull();
      // El campo del sistema no guarda el archivo ya elegido: se vacía al elegir.
      expect((screen.getByLabelText("Archivo del documento") as HTMLInputElement).value).toBe("");
      expect(screen.getByLabelText("Archivo del documento")).toBe(campoArchivo);
      // Control: sin lista a la vista no hay nada que volver a pedir.
      expect(pedidos(simulado, "GET", BASE)).toHaveLength(0);
      expect(screen.queryByRole("heading", { level: 2, name: "Documentos" })).toBeNull();

      llenarNuevo(pdf("otro.pdf"));
      fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
      await waitFor(() => expect(pedidos(simulado, "POST", versiones(D3))).toHaveLength(2));
      const [primera, segunda] = pedidos(simulado, "POST", BASE).map((p) => p.headers.get("Idempotency-Key"));
      expect(segunda).not.toBe(primera);
    });

    it("si cancela el reintento del archivo, vuelve al formulario vacío y no repite el documento", async () => {
      const simulado = soloCarga({ [`POST ${versiones(D3)}`]: () => error("error_interno", 500) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await screen.findByLabelText("Categoría");
      llenarNuevo(pdf());
      fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
      await screen.findByRole("heading", { name: "Subir el archivo de «Presupuesto de pintura»" });
      const cancelar = screen.getByRole("button", { name: "Cancelar" }) as HTMLButtonElement;
      await waitFor(() => expect(cancelar.disabled).toBe(false));
      fireEvent.click(cancelar);
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
    expect(await screen.findByText("No pudimos mostrar la lista.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Cargar un documento" })).toBeNull();
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
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
  });
});
