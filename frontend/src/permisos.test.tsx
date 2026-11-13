import { focusManager, onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { accionesDeLasPoliticas } from "./accionesDePrueba";
import { App } from "./App";
import { CATEGORIAS_OPERATIVAS } from "./Documentos";
import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { cumple, cumpleEnAlguno, Exige, REQUISITOS, type Requisito } from "./permisos";
import { ContextoVersion } from "./version";

const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const EDIFICIOS = [
  { id: C1, nombre: "Edificio Azul", vinculos: [] },
  { id: C2, nombre: "Edificio Verde", vinculos: [] },
];
const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const INICIO = { level: 1, name: "Consorcio en mano" };

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

type Respuesta = () => Response | Promise<Response>;

const sinRed: Respuesta = () => Promise.reject(new TypeError("Failed to fetch"));

/** Lo que alcanza la persona en cada edificio. */
type Acciones = Record<string, readonly string[]>;

/** Una lectura vacía que sirve a todas las pantallas: trae los campos que cada contrato da siempre (la
 * bandeja de pagos, `avisos_rechazo` y `por_cotejar`). */
const LECTURA_VACIA = () =>
  Response.json({
    elementos: [],
    documentos: [],
    avisos_rechazo: [],
    por_cotejar: 0,
    actualizada_en: null,
    zona_horaria: "America/Argentina/Buenos_Aires",
  });

/** Responde por ruta. `acciones` decide `mis-acciones`; lo demás de un edificio, `lectura`. */
function backend(acciones: Acciones, extra: Record<string, Respuesta> = {}, lectura: Respuesta = LECTURA_VACIA) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    const propia = extra[camino];
    if (propia !== undefined) return propia();
    if (camino === "/api/v1/auth/sesion") return Response.json({});
    if (camino === "/health") return Response.json({ estado: "ok", version: "1", version_contrato: "1", sha_imagen: "abc1234" });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: [] });
    if (camino === "/api/v1/me/consorcios") return Response.json(EDIFICIOS.filter((e) => e.id in acciones));
    const [, consorcio, resto] = /^\/api\/v1\/consorcios\/([^/]+)\/(.+)$/.exec(camino) ?? [];
    if (resto === "mis-acciones") return Response.json({ acciones: acciones[consorcio ?? ""] });
    if (resto !== undefined) return lectura();
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function caminos(simulado: ReturnType<typeof vi.fn>): string[] {
  return simulado.mock.calls.map((c) => new URL((c as unknown as [Request])[0].url).pathname);
}

function abrirApp(ruta: string | { pathname: string; state: unknown } = "/") {
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

/** Una ruta protegida sola, para mirar `Exige` sin una pantalla de verdad. */
function abrirExige(requisito: Requisito) {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={consultas}>
      <MemoryRouter initialEntries={["/protegida"]}>
        <Routes>
          <Route path="/" element={<h1>El inicio</h1>} />
          <Route
            path="/protegida"
            element={
              <Exige requisito={requisito}>
                <h1>La protegida</h1>
              </Exige>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PROTEGIDA = { level: 1, name: "La protegida" };
const EL_INICIO = { level: 1, name: "El inicio" };

// jsdom no trae matchMedia: `/pagos` pregunta el ancho, y acá es el de un teléfono.
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("cumple", () => {
  it("alcanza con una condición, y cada condición pide todas sus acciones", () => {
    const requisito = [["a", "b"], ["c"]];
    expect(cumple(["a", "b"], requisito)).toBe(true);
    expect(cumple(["c"], requisito)).toBe(true);
    // Control: la mitad de una condición no cumple.
    expect(cumple(["a"], requisito)).toBe(false);
    expect(cumple([], requisito)).toBe(false);
  });

  it("en alguno de los edificios: uno solo alcanza", () => {
    const requisito = [["a"]];
    expect(cumpleEnAlguno(new Map([[C1, []], [C2, ["a"]]]), requisito)).toBe(true);
    expect(cumpleEnAlguno(new Map([[C1, ["b"]], [C2, []]]), requisito)).toBe(false);
    expect(cumpleEnAlguno(new Map(), requisito)).toBe(false);
  });
});

describe("Exige", () => {
  const REQUISITO = [["padron.ver"]];

  it("sin permiso lleva al inicio, sin mostrar lo protegido", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: ["contactos.ver"] }));
    abrirExige(REQUISITO);
    expect(await screen.findByRole("heading", EL_INICIO)).toBeTruthy();
    expect(screen.queryByRole("heading", PROTEGIDA)).toBeNull();
  });

  it("control: con permiso en cualquiera de los edificios la muestra y se queda", async () => {
    const simulado = backend({ [C1]: [], [C2]: ["padron.ver"] });
    vi.stubGlobal("fetch", simulado);
    abrirExige(REQUISITO);
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C2}/mis-acciones`));
    await new Promise((r) => setTimeout(r));
    expect(screen.getByRole("heading", PROTEGIDA)).toBeTruthy();
    expect(screen.queryByRole("heading", EL_INICIO)).toBeNull();
  });

  it("mientras carga no redirige; cuando llega que no puede, sí", async () => {
    let soltar: (r: Response) => void = () => undefined;
    const simulado = backend(
      { [C1]: [] },
      { [`/api/v1/consorcios/${C1}/mis-acciones`]: () => new Promise<Response>((r) => (soltar = r)) },
    );
    vi.stubGlobal("fetch", simulado);
    abrirExige(REQUISITO);
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/mis-acciones`));
    expect(screen.getByRole("heading", PROTEGIDA)).toBeTruthy();
    expect(screen.queryByRole("heading", EL_INICIO)).toBeNull();
    // Control: la espera era por la respuesta, no porque la ruta nunca redirija.
    soltar(Response.json({ acciones: [] }));
    expect(await screen.findByRole("heading", EL_INICIO)).toBeTruthy();
  });

  it.each<[string, Record<string, Respuesta>]>([
    ["sin red en los edificios", { "/api/v1/me/consorcios": sinRed }],
    ["con el servicio caído en los edificios", { "/api/v1/me/consorcios": () => error("servicio_no_disponible", 503) }],
    ["sin red en las acciones", { [`/api/v1/consorcios/${C1}/mis-acciones`]: sinRed }],
    ["con un 500 en las acciones", { [`/api/v1/consorcios/${C1}/mis-acciones`]: () => error("error_interno", 500) }],
  ])("%s no redirige: no se sabe, y decide el servidor", async (_, extra) => {
    const simulado = backend({ [C1]: [] }, extra);
    vi.stubGlobal("fetch", simulado);
    abrirExige(REQUISITO);
    await waitFor(() => expect(caminos(simulado)).toContain(Object.keys(extra)[0]));
    await new Promise((r) => setTimeout(r));
    expect(screen.getByRole("heading", PROTEGIDA)).toBeTruthy();
    expect(screen.queryByRole("heading", EL_INICIO)).toBeNull();
  });
});

describe("inicio por rol", () => {
  const MI_CUENTA = "Cuenta";
  const INVITAR = "Invitar a una persona";
  const MANDATOS = "Mandatos y decisiones";
  const PADRON = "Padrón del edificio";
  const CONTACTOS = "Contactos de emergencia";
  const DOCUMENTOS = "Documentos del edificio";
  const PAGOS = "Pagos";
  const LIQUIDACION = "Liquidación (borrador)";
  const TODOS = [INVITAR, MANDATOS, PADRON, CONTACTOS, DOCUMENTOS, PAGOS, LIQUIDACION];

  async function enlaces() {
    await screen.findByRole("link", { name: MI_CUENTA });
    await screen.findByRole("link", { name: CONTACTOS });
    await screen.findByRole("heading", INICIO);
    await new Promise((r) => setTimeout(r));
    return TODOS.filter((t) => screen.queryAllByRole("link", { name: t }).length > 0);
  }

  const VECINO = ["consorcios.mis", "consorcios.mis_acciones", "contactos.ver", "padron.ver_propio", "unidades.listar"];
  const CONSEJO = [
    ...VECINO,
    "mandatos.ver", "decisiones.ver", "padron.ver", "documentos.ver", "documentos.cargar_operativos", "contactos.gestionar",
    "egresos.revisar_orden", "egresos.aprobar_orden", "egresos.conciliar_pago",
  ];
  const ADMINISTRADOR = [
    ...VECINO,
    "mandatos.ver", "decisiones.ver", "decisiones.registrar", "padron.ver", "documentos.ver", "documentos.cargar",
    "contactos.gestionar", "invitaciones.crear", "egresos.preparar_orden", "egresos.ejecutar_pago", "liquidacion.preparar",
  ];

  it("el vecino ve solo sus contactos", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: VECINO }));
    abrirApp();
    expect(await enlaces()).toEqual([CONTACTOS]);
  });

  it("el Consejo ve lo que fiscaliza y no lo de la administración", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: CONSEJO }));
    abrirApp();
    expect(await enlaces()).toEqual([MANDATOS, PADRON, CONTACTOS, DOCUMENTOS, PAGOS]);
  });

  it("la administración ve lo que opera y no lo del Consejo", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: ADMINISTRADOR }));
    abrirApp();
    expect(await enlaces()).toEqual([INVITAR, MANDATOS, PADRON, CONTACTOS, DOCUMENTOS, PAGOS, LIQUIDACION]);
  });

  it("los enlaces son la suma de los edificios: Consejo en uno, vecino en otro", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: VECINO, [C2]: CONSEJO }));
    abrirApp();
    expect(await enlaces()).toEqual([MANDATOS, PADRON, CONTACTOS, DOCUMENTOS, PAGOS]);
  });

  it.each<[string, Record<string, Respuesta>]>([
    ["sin conexión", { "/api/v1/me/consorcios": sinRed }],
    ["sin saber qué alcanza", { [`/api/v1/consorcios/${C1}/mis-acciones`]: sinRed }],
  ])("%s quedan solo Cuenta y Contactos", async (_, extra) => {
    vi.stubGlobal("fetch", backend({ [C1]: ADMINISTRADOR }, extra));
    abrirApp();
    expect(await enlaces()).toEqual([CONTACTOS]);
  });

  it("«Pagos para aclarar» aparece solo si la lista trae algo", async () => {
    const CON_ALGO = { "/api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ orden_id: "x" }] }) };
    const nombre = "Pagos para aclarar (de quien dejó de administrar)";
    vi.stubGlobal("fetch", backend({ [C1]: VECINO }, CON_ALGO));
    abrirApp();
    expect(await screen.findByRole("link", { name: nombre })).toBeTruthy();
    cleanup();

    // Control: con la lista vacía no aparece, y el pedido salió igual.
    const simulado = backend({ [C1]: VECINO });
    vi.stubGlobal("fetch", simulado);
    abrirApp();
    await enlaces();
    await waitFor(() => expect(caminos(simulado)).toContain("/api/v1/pagos-por-aclarar"));
    await new Promise((r) => setTimeout(r));
    expect(screen.queryByRole("link", { name: nombre })).toBeNull();
  });
});

describe("si el inicio no pudo saber qué alcanza", () => {
  const PADRON = { name: "Padrón del edificio" };
  const ACCIONES = `/api/v1/consorcios/${C1}/mis-acciones`;
  const SOLO_AZUL = () => Response.json(EDIFICIOS.slice(0, 1));

  it.each<[string, Respuesta, string]>([
    ["sin red", sinRed, SIN_CONEXION],
    ["con un 503", () => error("servicio_no_disponible", 503), SERVICIO_NO_DISPONIBLE],
    ["con un 500", () => error("error_interno", 500), SERVICIO_NO_DISPONIBLE],
  ])("%s lo dice, y Reintentar trae las secciones", async (_, falla, mensaje) => {
    let caido = true;
    vi.stubGlobal(
      "fetch",
      backend({ [C1]: ["padron.ver", "contactos.ver"] }, { "/api/v1/me/consorcios": () => (caido ? falla() : SOLO_AZUL()) }),
    );
    abrirApp();

    const aviso = await screen.findByRole("alert");
    expect(aviso.textContent).toBe(`Puede que falten secciones. ${mensaje}`);
    expect(screen.queryByRole("link", PADRON)).toBeNull();

    caido = false;
    fireEvent.click(within(aviso.parentElement!).getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByRole("link", PADRON)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("una falla solo en lo que alcanza en un edificio también se dice y se reintenta", async () => {
    let caido = true;
    vi.stubGlobal(
      "fetch",
      backend({ [C1]: [] }, {
        [ACCIONES]: () => (caido ? error("error_interno", 500) : Response.json({ acciones: ["padron.ver"] })),
      }),
    );
    abrirApp();
    const aviso = await screen.findByRole("alert");

    caido = false;
    fireEvent.click(within(aviso.parentElement!).getByRole("button", { name: "Reintentar" }));

    expect(await within(screen.getByRole("main")).findByRole("link", PADRON)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("control: con todo leído no hay aviso, aunque la persona no alcance nada", async () => {
    const simulado = backend({ [C1]: [] });
    vi.stubGlobal("fetch", simulado);
    abrirApp();
    await waitFor(() => expect(caminos(simulado)).toContain(ACCIONES));
    await screen.findByRole("heading", INICIO);
    await new Promise((r) => setTimeout(r));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("al volver la conexión lo consulta de nuevo, sin tocar nada", async () => {
    let caido = true;
    const simulado = backend(
      { [C1]: ["padron.ver"] },
      { "/api/v1/me/consorcios": () => (caido ? sinRed() : SOLO_AZUL()) },
    );
    vi.stubGlobal("fetch", simulado);
    abrirApp();
    await screen.findByRole("alert");
    // Control: sin que vuelva la conexión no se repite sola.
    expect(caminos(simulado).filter((c) => c === "/api/v1/me/consorcios")).toHaveLength(1);

    caido = false;
    act(() => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });

    expect(await screen.findByRole("link", PADRON)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("el edificio elegido", () => {
  const AMBOS = ["padron.ver", "documentos.ver"];
  const MARCA = { name: "Consorcio en Mano" };
  const DOCUMENTOS = { name: "Documentos del edificio" };

  async function elegirElVerde() {
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C2 } });
  }

  async function pasarADocumentos() {
    fireEvent.click(screen.getByRole("link", MARCA));
    fireEvent.click(await screen.findByRole("link", DOCUMENTOS));
    await screen.findByRole("heading", { level: 1, name: "Documentos del edificio" });
  }

  it("sigue elegido en la pantalla siguiente", async () => {
    const simulado = backend({ [C1]: AMBOS, [C2]: AMBOS });
    vi.stubGlobal("fetch", simulado);
    abrirApp("/padron");
    await elegirElVerde();

    await pasarADocumentos();

    const selector = (await screen.findByLabelText("Edificio")) as HTMLSelectElement;
    expect(selector.value).toBe(C2);
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C2}/documentos`));
    // Control: el primero no se pidió; sin el recuerdo sería el elegido.
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C1}/documentos`);
  });

  it("no queda en el dispositivo: otra carga de la página empieza por el primero", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: AMBOS, [C2]: AMBOS }));
    abrirApp("/padron");
    await elegirElVerde();
    await waitFor(() => expect((screen.getByLabelText("Edificio") as HTMLSelectElement).value).toBe(C2));
    expect(Object.values(localStorage).join()).not.toContain(C2);
    expect(Object.values(sessionStorage).join()).not.toContain(C2);
    cleanup();

    abrirApp("/padron");

    const selector = (await screen.findByLabelText("Edificio")) as HTMLSelectElement;
    expect(selector.value).toBe(C1);
  });

  it("si la pantalla siguiente no sirve en ese edificio, usa uno que sirva", async () => {
    const simulado = backend({ [C1]: AMBOS, [C2]: ["padron.ver"] });
    vi.stubGlobal("fetch", simulado);
    abrirApp("/padron");
    await elegirElVerde();

    await pasarADocumentos();

    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/documentos`));
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C2}/documentos`);
  });

  it("al salir de la cuenta se olvida: quien entra después empieza por el primero", async () => {
    let adentro = true;
    vi.stubGlobal(
      "fetch",
      backend({ [C1]: ["padron.ver"], [C2]: ["padron.ver"] }, {
        "/api/v1/auth/sesion": () => (adentro ? Response.json({}) : error("no_autenticado", 401)),
        "/api/v1/auth/sesiones": () => Response.json({ elementos: [] }),
        "/api/v1/auth/totp": () => Response.json({ activo: false, llaves: 0 }),
        "/api/v1/auth/contrasena": () => Response.json({ definida: false, cambiada_en: null }),
        "/api/v1/privacidad/casos": () => Response.json({ elementos: [] }),
        "/api/v1/auth/salir": () => {
          adentro = false;
          return new Response(null, { status: 204 });
        },
        "/api/v1/auth/codigo": () => {
          adentro = true;
          return Response.json({});
        },
      }),
    );
    abrirApp("/padron");
    await elegirElVerde();
    fireEvent.click(screen.getByRole("link", MARCA));
    fireEvent.click(await screen.findByRole("link", { name: "Cuenta" }));
    fireEvent.click(await screen.findByRole("button", { name: "Salir" }));

    fireEvent.click(await screen.findByRole("button", { name: "Ya tengo un código" }));
    const codigo = within(screen.getByRole("region", { name: "Escribir el código del correo" }));
    fireEvent.change(codigo.getByLabelText("Correo"), { target: { value: "otra@ejemplo.com" } });
    fireEvent.change(codigo.getByLabelText(/^Código de 6 números/), { target: { value: "042917" } });
    fireEvent.submit(codigo.getByRole("button", { name: "Entrar con el código" }).closest("form")!);
    fireEvent.click(await within(screen.getByRole("main")).findByRole("link", { name: "Padrón del edificio" }));

    const selector = (await screen.findByLabelText("Edificio")) as HTMLSelectElement;
    expect(selector.value).toBe(C1);
  });
});

describe("los códigos existen en la política", () => {
  const catalogo = new Set(accionesDeLasPoliticas());
  const desconocidos = (codigos: readonly string[]) => [...new Set(codigos)].filter((c) => !catalogo.has(c));

  function archivos(carpeta: string): string[] {
    return readdirSync(carpeta, { withFileTypes: true }).flatMap((e) => {
      const ruta = join(carpeta, e.name);
      if (e.isDirectory()) return e.name === "api" ? [] : archivos(ruta);
      return /\.tsx?$/.test(e.name) && !/\.test\.|accionesDePrueba/.test(e.name) ? [ruta] : [];
    });
  }

  /** Los códigos entre comillas de cada `puede(...)` y de lo que la pantalla le pasa. */
  function codigosDePuede(): string[] {
    return archivos(resolve(process.cwd(), "src")).flatMap((ruta) => {
      const texto = readFileSync(ruta, "utf8");
      const llamadas = [...texto.matchAll(/puede\(([^)]*)\)/g)].map((m) => m[1] ?? "");
      const deCarga = /function accionesDeCarga[\s\S]*?\n}/.exec(texto)?.[0] ?? "";
      return [...llamadas, deCarga].flatMap((c) => [...c.matchAll(/"([a-z_]+\.[a-z_]+)"/g)].map((m) => m[1] ?? ""));
    });
  }

  it("control: el catálogo es el real y una acción inventada no pasa", () => {
    expect(catalogo.size).toBeGreaterThan(40);
    expect(catalogo.has("egresos.ejecutar_pago")).toBe(true);
    expect(desconocidos(["egresos.ejecutar_pago", "egresos.no_existe"])).toEqual(["egresos.no_existe"]);
  });

  it("cada código de REQUISITOS es una acción de la política", () => {
    const codigos = Object.values(REQUISITOS).flatMap((r) => r.flat());
    expect(codigos.length).toBeGreaterThan(10);
    expect(desconocidos(codigos)).toEqual([]);
  });

  it("cada código de cada `puede(...)` es una acción de la política", () => {
    const codigos = codigosDePuede();
    // Control: la búsqueda encuentra los que sabemos que están, en cada forma de usarlos.
    for (const conocido of ["egresos.preparar_orden", "padron.confirmar_vinculo", "documentos.cargar_operativos", "invitaciones.revocar"])
      expect(codigos).toContain(conocido);
    expect(desconocidos(codigos)).toEqual([]);
  });

  it("las categorías operativas son las del contrato", () => {
    const contrato = readFileSync(resolve(process.cwd(), "../contratos/cem_contracts/documentos.py"), "utf8");
    const bloque = /CATEGORIAS_OPERATIVAS = frozenset\(([\s\S]*?)\n\)/.exec(contrato)?.[1] ?? "";
    const nombres = [...bloque.matchAll(/CategoriaDocumento\.([A-Z_]+)/g)].map((m) => (m[1] ?? "").toLowerCase());
    // Control: el bloque se leyó; sin él, una lista vacía no probaría nada.
    expect(nombres).toHaveLength(3);
    expect([...CATEGORIAS_OPERATIVAS].sort()).toEqual(nombres.sort());
  });
});

// Cada pantalla, con la acción que la abre, la lectura que la llena y los edificios que ofrece.
const PANTALLAS: [string, string, string, string][] = [
  ["/invitaciones", "invitaciones.crear", "invitaciones", "Invitar a una persona"],
  ["/mandatos", "mandatos.ver", "mandatos", "Mandatos y decisiones"],
  ["/padron", "padron.ver", "padron", "Padrón del edificio"],
  ["/contactos", "contactos.ver", "contactos", "Contactos de emergencia"],
  ["/contactos/gestion", "contactos.gestionar", "contactos/gestion", "Gestionar los contactos de emergencia"],
  ["/documentos", "documentos.ver", "documentos", "Documentos del edificio"],
  ["/egresos/proveedores", "egresos.preparar_orden", "proveedores", "Proveedores"],
  ["/pagos", "egresos.preparar_orden", "pagos/bandeja", "Pagos"],
  ["/pagos/nueva", "egresos.preparar_orden", "proveedores", "Preparar una orden"],
  ["/pagos/consulta", "egresos.revisar_orden", "pagos/consulta", "Consultar pagos"],
  ["/liquidacion", "liquidacion.preparar", "liquidaciones", "Liquidación (borrador)"],
];

const CON_ACCIONES: [string, readonly string[], string, string][] = PANTALLAS.map(
  ([ruta, accion, lectura, titulo]) => [ruta, [accion], lectura, titulo],
);

describe("el selector de edificio", () => {
  it.each(CON_ACCIONES)("%s no ofrece ni consulta el edificio donde no sirve", async (ruta, acciones, lectura, titulo) => {
    const simulado = backend({ [C1]: [], [C2]: acciones });
    vi.stubGlobal("fetch", simulado);
    abrirApp(ruta);
    await screen.findByRole("heading", { level: 1, name: titulo });
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C2}/${lectura}`));
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C1}/${lectura}`);
    expect(screen.queryByLabelText("Edificio")).toBeNull();
  });

  it.each(CON_ACCIONES)("control: %s ofrece los dos si sirve en los dos", async (ruta, acciones, lectura, titulo) => {
    const simulado = backend({ [C1]: acciones, [C2]: acciones });
    vi.stubGlobal("fetch", simulado);
    abrirApp(ruta);
    await screen.findByRole("heading", { level: 1, name: titulo });
    const selector = await screen.findByLabelText("Edificio");
    expect(within(selector).getAllByRole("option").map((o) => o.textContent)).toEqual(["Edificio Azul", "Edificio Verde"]);
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/${lectura}`));
  });

  it("con tres edificios deja afuera solo el que no sirve", async () => {
    const C3 = "33333333-3333-4333-8333-333333333333";
    const simulado = backend({ [C1]: ["padron.ver"], [C2]: [] }, {
      "/api/v1/me/consorcios": () => Response.json([...EDIFICIOS, { id: C3, nombre: "Edificio Rojo", vinculos: [] }]),
      [`/api/v1/consorcios/${C3}/mis-acciones`]: () => Response.json({ acciones: ["padron.ver"] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrirApp("/padron");
    const selector = await screen.findByLabelText("Edificio");
    expect(within(selector).getAllByRole("option").map((o) => o.textContent)).toEqual(["Edificio Azul", "Edificio Rojo"]);
  });

  it("`state.consorcio` elige el edificio que trae la navegación, si la pantalla sirve ahí", async () => {
    const simulado = backend({ [C1]: ["egresos.preparar_orden"], [C2]: ["egresos.preparar_orden"] });
    vi.stubGlobal("fetch", simulado);
    abrirApp({ pathname: "/egresos/proveedores", state: { consorcio: C2 } });
    const selector = (await screen.findByLabelText("Edificio")) as HTMLSelectElement;
    expect(selector.value).toBe(C2);
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C2}/proveedores`));
    // Control: el primero no se pidió; sin `state` sería el elegido.
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C1}/proveedores`);
  });

  it("`state.consorcio` de un edificio donde no sirve cae en el primero que sí", async () => {
    const simulado = backend({ [C1]: ["egresos.preparar_orden"], [C2]: [] });
    vi.stubGlobal("fetch", simulado);
    abrirApp({ pathname: "/egresos/proveedores", state: { consorcio: C2 } });
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/proveedores`));
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C2}/proveedores`);
  });

  it("sin saber qué alcanza, la pantalla sigue con todos los edificios: decide el servidor", async () => {
    const simulado = backend({ [C1]: [], [C2]: [] }, {
      [`/api/v1/consorcios/${C1}/mis-acciones`]: () => error("error_interno", 500),
    });
    vi.stubGlobal("fetch", simulado);
    abrirApp("/padron");
    expect(await screen.findByLabelText("Edificio")).toBeTruthy();
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/padron`));
  });
});

describe("si no se sabe qué alcanza", () => {
  const ACCIONES = `/api/v1/consorcios/${C1}/mis-acciones`;
  const LECTURA = `/api/v1/consorcios/${C1}/proveedores`;
  const veces = (simulado: ReturnType<typeof vi.fn>, camino: string) => caminos(simulado).filter((c) => c === camino).length;
  // Con demora: al instante, la consulta pasa por «cargando» sin que la pantalla llegue a verlo.
  const conAccionesCaidas = () =>
    backend({ [C1]: [] }, {
      [ACCIONES]: async () => {
        await new Promise((r) => setTimeout(r, 20));
        return error("error_interno", 500);
      },
    });

  it("la pantalla que se monta no repite la consulta que acaba de fallar", async () => {
    const simulado = conAccionesCaidas();
    vi.stubGlobal("fetch", simulado);
    abrirApp("/pagos"); // su pantalla mira `usePuede`: la misma consulta, en un componente nuevo
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await waitFor(() => expect(caminos(simulado)).toContain(`/api/v1/consorcios/${C1}/pagos/bandeja`));
    await new Promise((r) => setTimeout(r, 60));
    expect(veces(simulado, ACCIONES)).toBe(1);
  });

  it("otra consulta al volver a la ventana no le saca el edificio a la pantalla", async () => {
    const simulado = conAccionesCaidas();
    vi.stubGlobal("fetch", simulado);
    abrirApp("/egresos/proveedores");
    await screen.findByRole("button", { name: "Dar de alta un proveedor" });
    const lecturas = veces(simulado, LECTURA);
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    // Control: la consulta se repitió de verdad; la pantalla no se volvió a montar por eso.
    await waitFor(() => expect(veces(simulado, ACCIONES)).toBe(2));
    await new Promise((r) => setTimeout(r, 60));
    focusManager.setFocused(undefined);
    expect(veces(simulado, LECTURA)).toBe(lecturas);
    expect(screen.getByRole("button", { name: "Dar de alta un proveedor" })).toBeTruthy();
  });
});

describe("una dirección abierta a mano sin permiso", () => {
  it.each(CON_ACCIONES)("%s lleva al inicio sin pedir sus datos", async (ruta, _acciones, lectura) => {
    const simulado = backend({ [C1]: ["consorcios.mis"] });
    vi.stubGlobal("fetch", simulado);
    abrirApp(ruta);
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(caminos(simulado)).not.toContain(`/api/v1/consorcios/${C1}/${lectura}`);
    expect(screen.queryByText(/no tiene (permiso|la facultad|acceso)/)).toBeNull();
  });
});

describe("un 403 al leer lleva al inicio", () => {
  const NEGADA = () => error("sin_permiso", 403);

  it.each(CON_ACCIONES)("%s, sin un mensaje de «sin permiso»", async (ruta, acciones) => {
    vi.stubGlobal("fetch", backend({ [C1]: acciones }, {}, NEGADA));
    abrirApp(ruta);
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByText(/no tiene (permiso|la facultad|acceso)/)).toBeNull();
  });

  it("control: un 500 no lleva al inicio, se queda a reintentar", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: ["padron.ver"] }, {}, () => error("error_interno", 500)));
    abrirApp("/padron");
    expect(await screen.findByRole("button", { name: "Consultar de nuevo" })).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
  });

  it("control: el segundo factor no es falta de permiso, pide el código ahí", async () => {
    const totp = { "/api/v1/auth/totp": () => Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 0 }) };
    vi.stubGlobal("fetch", backend({ [C1]: ["egresos.preparar_orden"] }, totp, () => error("segundo_factor_requerido", 403)));
    abrirApp("/egresos/proveedores");
    expect(await screen.findByLabelText("Código")).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
  });

  it("control: la sesión vieja no es falta de permiso, sigue en la pantalla", async () => {
    vi.stubGlobal("fetch", backend({ [C1]: ["egresos.preparar_orden"] }, {}, () => error("sesion_reciente_requerida", 403)));
    abrirApp("/egresos/proveedores");
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
  });
});
