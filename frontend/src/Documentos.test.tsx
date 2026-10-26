import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { File as ArchivoNode } from "node:buffer";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { App } from "./App";
import { ACEPTA, mensajeDeError, tamano, TOPE_BYTES } from "./Documentos";
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
    const respuesta = respuestas[clave];
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
  fireEvent.change(screen.getByLabelText("Archivo"), { target: { files: [elegido] } });
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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /documentos", () => {
  it("el inicio tiene el enlace a los documentos", async () => {
    vi.stubGlobal("fetch", backend({ "GET /health": () => Response.json({ estado: "ok" }) }));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Documentos del edificio" })).getAttribute("href")).toBe("/documentos");
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

  it("«Actualizar» vuelve a pedir la lista (sin repetir solo)", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await tarjeta("Acta de asamblea ordinaria");
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await tarjeta("Acta de asamblea ordinaria");
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(2);
  });

  it("sin permiso para ver (403) lo dice sin error, sin lista ni formulario de carga", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}`]: () => error("sin_permiso", 403) }));
    abrir();
    expect(await screen.findByText(/No tenés acceso a los documentos de este edificio/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cargar un documento" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 4 })).toBeNull();
    expect(document.querySelector(".aviso-error")).toBeNull();
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
    expect(screen.getByLabelText("Archivo").getAttribute("accept")).toBe(ACEPTA);
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
  });

  it("un archivo de más de 20 MB o de otro tipo se rechaza antes de mandar nada", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const grande = pdf("grande.pdf");
    Object.defineProperty(grande, "size", { value: TOPE_BYTES + 1 });
    await completarNuevo(grande);
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    const aviso = await screen.findByText("El archivo pasa de 20 MB, el máximo. Elegí uno más chico.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    elegir(archivo(["hola"], "notas.txt", "text/plain"));
    fireEvent.click(screen.getByRole("button", { name: "Cargar el documento" }));
    expect(await screen.findByText("Solo se aceptan archivos PDF, JPEG o PNG. Elegí otro archivo.")).toBeTruthy();
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
    expect(document.activeElement).toBe(screen.getByLabelText("Archivo"));
    elegir(pdf("contrato.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "Subir el archivo" }));
    expect(await screen.findByText("El archivo pasa de 20 MB y no se guardó. Elegí uno más chico.")).toBeTruthy();
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
    expect(ACEPTA.split(",").filter((t) => t.includes("/")).sort()).toEqual(tipos);
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
