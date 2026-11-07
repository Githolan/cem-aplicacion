import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "./api/tipos";
import { alcanza, alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { borrarCopias, PREFIJO_COPIA, vigentes } from "./copiaContactos";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const K1 = "33333333-3333-4333-8333-333333333333";
const K2 = "44444444-4444-4444-8444-444444444444";
const RUTA = (c: string) => `/api/v1/consorcios/${c}/contactos`;
const ZONA = "America/Argentina/Buenos_Aires";

type Contacto = components["schemas"]["Contacto"];
type Lista = components["schemas"]["ListaContactos"];

/** Un contacto del contrato: si le falta un campo, el tipo no compila. */
function contacto(id: string, nombre: string, extra: Partial<Contacto> = {}): Contacto {
  return {
    id,
    funcion: "emergencias",
    naturaleza: "empresa",
    nombre,
    telefono: "0800-555-1234",
    indicaciones: null,
    vigente_desde: "2026-01-01",
    vigente_hasta: null,
    actualizado_en: "2026-10-20T15:00:00Z",
    verificado_en: null,
    observado: false,
    ...extra,
  };
}

const LISTA: Lista = {
  elementos: [contacto(K1, "Ascensores del Sur"), contacto(K2, "Guardia de gas", { vigente_hasta: "2026-11-01" })],
  actualizada_en: "2026-10-20T15:00:00Z",
  zona_horaria: ZONA,
};
const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
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

const sinRed = () => Promise.reject(new TypeError("Failed to fetch"));

/** Cuántas veces se pidió un camino. */
function veces(simulado: ReturnType<typeof vi.fn>, camino: string): number {
  return simulado.mock.calls.filter((c) => new URL((c as unknown as [Request])[0].url).pathname === camino).length;
}

function abrir(ruta = "/contactos") {
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

function copiaGuardada(consorcio = C1): unknown {
  const texto = localStorage.getItem(PREFIJO_COPIA + consorcio);
  return texto === null ? null : JSON.parse(texto);
}

function guardar(consorcio: string, nombre: string, lista: Lista, guardada_en = "2026-10-21T12:00:00Z") {
  localStorage.setItem(PREFIJO_COPIA + consorcio, JSON.stringify({ consorcio_id: consorcio, nombre, guardada_en, lista }));
}

async function nombres(): Promise<string[]> {
  const lista = await screen.findByRole("list", { name: "Contactos de emergencia" });
  return within(lista)
    .getAllByRole("heading", { level: 3 })
    .map((h) => h.textContent ?? "");
}

// Los datos son del 20 y 21-10-2026 y la guardia de gas vence el 1-11: sin un reloj fijo, desde
// ese día las pruebas sin conexión fallaban solas (falló el 1-11-2026). Las que prueban el
// vencimiento ponen su propia hora.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-21T12:00:00-03:00") });
  localStorage.clear();
});

/** Lo que consulta el inicio con la sesión abierta; el estado del servicio no importa acá. */
const INICIO_CON_SESION = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () => error("servicio_no_disponible", 503),
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("página /contactos", () => {
  it("el inicio tiene el enlace a los contactos", async () => {
    vi.stubGlobal("fetch", backend(INICIO_CON_SESION));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Contactos de emergencia" })).getAttribute("href")).toBe("/contactos");
  });

  it("muestra la lista con su fecha, guarda la copia y nunca pide la lista de gestión", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
    // Los dos tienen el mismo número: cada uno con su enlace para llamar, sin separadores.
    expect(screen.getAllByRole("link", { name: "0800-555-1234" }).map((a) => a.getAttribute("href"))).toEqual([
      "tel:08005551234",
      "tel:08005551234",
    ]);
    expect(screen.getByText(/^Actualizado el /).textContent).not.toMatch(/\.\.$/);
    expect(screen.getByText(/^Último cambio de la lista: /)).toBeTruthy();
    expect((copiaGuardada() as { lista: Lista }).lista).toEqual(LISTA);
    const caminos = simulado.mock.calls.map((c) => new URL((c as unknown as [Request])[0].url).pathname);
    expect(caminos.some((p) => p.endsWith("/gestion"))).toBe(false);
    const { violations } = await axe.run(container);
    expect(violations).toEqual([]);
  });

  it("al sincronizar reemplaza la copia entera: lo retirado desaparece", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    const sinElRetirado = { ...LISTA, elementos: [LISTA.elementos[0]] };
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${RUTA(C1)}`]: () => Response.json(sinElRetirado),
      }),
    );
    abrir();
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur"]);
    expect((copiaGuardada() as { lista: Lista }).lista.elementos).toHaveLength(1);
  });

  it.each([
    [403, "sin_permiso"],
    [404, "no_encontrado"],
  ])("con %i borra la copia de ese edificio y lleva al inicio", async (estado, codigo) => {
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", LISTA);
    vi.stubGlobal(
      "fetch",
      backend({
        // Los dos edificios siguen en la cuenta: la copia de C2 no se poda por eso.
        "GET /api/v1/me/consorcios": () =>
          Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
        [`GET ${RUTA(C1)}`]: () => error(codigo, estado),
        "GET /api/v1/auth/sesion": () => Response.json({}),
        "GET /health": () => error("servicio_no_disponible", 503),
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    expect(screen.queryByText(/No tenés acceso/)).toBeNull();
    expect(copiaGuardada(C1)).toBeNull();
    // Control: la copia de otro edificio no se toca.
    expect(copiaGuardada(C2)).not.toBeNull();
  });

  it("con 401 borra todas las copias y manda a /entrar", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", LISTA);
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
    expect([copiaGuardada(C1), copiaGuardada(C2)]).toEqual([null, null]);
  });

  it("sin conexión muestra la copia con su fecha y oculta lo que ya venció", async () => {
    vi.setSystemTime(new Date("2026-11-01T12:00:00-03:00"));
    guardar(C1, "Edificio Azul", LISTA);
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": sinRed }));
    abrir();
    // `vigente_hasta` es exclusivo: el 1-11 la guardia de gas ya no figura.
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur"]);
    expect(screen.getByText(/Esta es la copia guardada el /)).toBeTruthy();
  });

  it("control: el día anterior al fin, sin conexión, el contacto se muestra", async () => {
    vi.setSystemTime(new Date("2026-10-31T23:30:00-03:00"));
    guardar(C1, "Edificio Azul", LISTA);
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": sinRed }));
    abrir();
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
  });

  it("si la lista no responde, muestra la copia de ese edificio", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    vi.stubGlobal(
      "fetch",
      backend({ "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS), [`GET ${RUTA(C1)}`]: sinRed }),
    );
    abrir();
    expect(await nombres()).toHaveLength(2);
    expect(screen.getByText(/Esta es la copia guardada el /)).toBeTruthy();
  });

  it("sin conexión y sin copia lo dice, sin inventar una lista", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": sinRed }));
    abrir();
    expect(await screen.findByText(/Este dispositivo no tiene contactos guardados/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos de emergencia" })).toBeNull();
  });

  it("sin copia, la lista que no respondió se reintenta y el error se va", async () => {
    let caida = true;
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => (caida ? error("servicio_no_disponible", 503) : Response.json(LISTA)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/Este dispositivo no tiene una copia guardada/)).toBeTruthy();
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
    expect(veces(simulado, RUTA(C1))).toBe(2);
    // Los edificios estaban: solo se volvió a pedir la lista (los dos pedidos son los de la entrada:
    // el de la pantalla y el de permisos.tsx).
    expect(veces(simulado, "/api/v1/me/consorcios")).toBe(2);
    expect(screen.queryByText(/Este dispositivo no tiene una copia guardada/)).toBeNull();
    // Control: con la lista a la vista no hay nada que reintentar.
    expect(screen.queryByRole("button", { name: "Consultar de nuevo" })).toBeNull();
  });

  it("con la copia a la vista, el reintento no la saca mientras consulta y después la reemplaza", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    let soltar: (r: Response) => void = () => undefined;
    let caida = true;
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => (caida ? sinRed() : new Promise<Response>((r) => (soltar = r))),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/Esta es la copia guardada el /)).toBeTruthy();
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    const consultando = await screen.findByRole("button", { name: "Consultando…" });
    expect(consultando.hasAttribute("disabled")).toBe(true);
    // Son teléfonos de emergencia: siguen a la vista mientras llega la respuesta.
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
    await waitFor(() => expect(veces(simulado, RUTA(C1))).toBe(2));
    soltar(Response.json({ ...LISTA, elementos: [contacto(K1, "Ascensores del Norte")] }));
    await waitFor(() => expect(screen.queryByText(/Esta es la copia guardada el /)).toBeNull());
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Norte"]);
  });

  it("si el reintento vuelve a fallar, la copia sigue y se puede probar otra vez", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => sinRed(),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Consultar de nuevo" }));
    await waitFor(() => expect(veces(simulado, RUTA(C1))).toBe(2));
    const otraVez = await screen.findByRole("button", { name: "Consultar de nuevo" });
    expect(otraVez.hasAttribute("disabled")).toBe(false);
    expect(screen.getByText(/Esta es la copia guardada el /)).toBeTruthy();
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
  });

  it("sin edificios a la vista, el reintento los vuelve a pedir y respeta el edificio elegido", async () => {
    const DOS = [...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }];
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", { ...LISTA, elementos: [contacto(K2, "Guardia verde")] });
    let caidos = true;
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => (caidos ? sinRed() : Response.json(DOS)),
      [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
      [`GET ${RUTA(C2)}`]: () => Response.json({ ...LISTA, elementos: [contacto(K2, "Guardia verde en línea")] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const edificio = (await screen.findByLabelText("Edificio")) as HTMLSelectElement;
    fireEvent.change(edificio, { target: { value: C2 } });
    expect(await nombres()).toEqual(["Emergencias: Guardia verde"]);
    fireEvent.click(await screen.findByRole("button", { name: "Consultar de nuevo" }));
    await waitFor(() => expect(veces(simulado, "/api/v1/me/consorcios")).toBe(3));
    // Falló otra vez: sigue en el edificio elegido, con su copia.
    expect(await screen.findByRole("button", { name: "Consultar de nuevo" })).toBeTruthy();
    expect((screen.getByLabelText("Edificio") as HTMLSelectElement).value).toBe(C2);
    caidos = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    await waitFor(() => expect(screen.queryByText(/Esta es la copia guardada el /)).toBeNull());
    expect(await nombres()).toEqual(["Emergencias: Guardia verde en línea"]);
    expect((screen.getByLabelText("Edificio") as HTMLSelectElement).value).toBe(C2);
    expect(veces(simulado, RUTA(C1))).toBe(0);
  });

  it("sin edificios ni copias, el reintento trae la lista", async () => {
    let caidos = true;
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => (caidos ? sinRed() : Response.json(CONSORCIOS)),
        [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
      }),
    );
    abrir();
    expect(await screen.findByText(/Este dispositivo no tiene contactos guardados/)).toBeTruthy();
    caidos = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
    expect(screen.queryByText(/Este dispositivo no tiene contactos guardados/)).toBeNull();
  });

  it("si el reintento dice que ya no participa de ningún edificio, la copia a la vista se va", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    let caidos = true;
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => (caidos ? sinRed() : Response.json([])) }));
    abrir();
    expect(await nombres()).toEqual(["Emergencias: Ascensores del Sur", "Emergencias: Guardia de gas"]);
    caidos = false;
    fireEvent.click(await screen.findByRole("button", { name: "Consultar de nuevo" }));
    expect(await screen.findByText(/^Todavía no participás de ningún edificio./)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos de emergencia" })).toBeNull();
    expect(copiaGuardada()).toBeNull();
  });

  it("mientras llega la lista del edificio dice «Consultando…», sin lista ni copia", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => new Promise<Response>(() => undefined),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    // Control: ya se pidió la lista del edificio, así que es su carga y no la de los edificios.
    await waitFor(() =>
      expect(simulado.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname === RUTA(C1))).toBe(true),
    );
    expect(screen.getByText("Consultando…")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos de emergencia" })).toBeNull();
    expect(copiaGuardada()).toBeNull();
  });

  it("un edificio sin contactos lo dice, y guarda la copia vacía", async () => {
    const vacia: Lista = { elementos: [], actualizada_en: null, zona_horaria: ZONA };
    vi.stubGlobal(
      "fetch",
      backend({ "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS), [`GET ${RUTA(C1)}`]: () => Response.json(vacia) }),
    );
    abrir();
    expect(await screen.findByText("El edificio no tiene contactos de emergencia cargados.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos de emergencia" })).toBeNull();
    // Sin conexión, después, se ve «sin contactos» y no una copia vieja.
    expect((copiaGuardada() as { lista: Lista }).lista).toEqual(vacia);
  });
});

describe("página /contactos: lo que no tiene que pasar", () => {
  it("un 401 en la lista del edificio borra todas las copias y manda a /entrar", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", LISTA);
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${RUTA(C1)}`]: () => error("no_autenticado", 401),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
    expect([copiaGuardada(C1), copiaGuardada(C2)]).toEqual([null, null]);
  });

  it("la copia de un edificio que ya no figura se borra, aunque otro siga", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", LISTA);
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
      }),
    );
    abrir();
    await nombres();
    expect(copiaGuardada(C2)).toBeNull();
    // Control: la del edificio que sigue queda, recién guardada.
    expect(copiaGuardada(C1)).not.toBeNull();
  });

  it("si el servicio está caído (503), muestra la copia y no una lista vacía", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => new Response("", { status: 503 }) }));
    abrir();
    expect(await nombres()).toHaveLength(2);
    expect(screen.getByText(/^El servicio no está disponible/).textContent).toMatch(/Esta es la copia guardada el /);
  });

  it("al cambiar de edificio, una respuesta vieja que llega tarde no pisa la nueva", async () => {
    let soltarAzul: (r: Response) => void = () => undefined;
    const azul = new Promise<Response>((r) => {
      soltarAzul = r;
    });
    const verde = { ...LISTA, elementos: [contacto(K1, "Plomería del Verde")] };
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () =>
          Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
        [`GET ${RUTA(C1)}`]: () => azul,
        [`GET ${RUTA(C2)}`]: () => Response.json(verde),
      }),
    );
    abrir();
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C2 } });
    expect(await nombres()).toEqual(["Emergencias: Plomería del Verde"]);
    soltarAzul(Response.json(LISTA));
    await new Promise((r) => setTimeout(r, 0));
    expect(await nombres()).toEqual(["Emergencias: Plomería del Verde"]);
    // Control: la respuesta tardía no se muestra, pero sí se guarda la copia del verde.
    expect((copiaGuardada(C2) as { lista: Lista }).lista).toEqual(verde);
  });

  it("un 403 que llega tarde, con otro edificio ya elegido, igual borra la copia del negado", async () => {
    guardar(C1, "Edificio Azul", LISTA);
    let soltarAzul: (r: Response) => void = () => undefined;
    const azul = new Promise<Response>((r) => {
      soltarAzul = r;
    });
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () =>
          Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
        [`GET ${RUTA(C1)}`]: () => azul,
        [`GET ${RUTA(C2)}`]: () => Response.json(LISTA),
      }),
    );
    abrir();
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C2 } });
    await nombres();
    // Control: hasta que llega la negación, la copia del azul sigue.
    expect(copiaGuardada(C1)).not.toBeNull();
    soltarAzul(error("sin_permiso", 403));
    await waitFor(() => expect(copiaGuardada(C1)).toBeNull());
    // La pantalla sigue en el verde.
    expect(screen.queryByText("No tenés acceso a los contactos de este edificio.")).toBeNull();
  });

  it("si el navegador no deja guardar, lo dice y no deja la copia vieja", async () => {
    guardar(C1, "Edificio Azul", { ...LISTA, elementos: [contacto(K2, "Lista vieja")] });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("lleno", "QuotaExceededError");
    });
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
      }),
    );
    abrir();
    expect(await screen.findByText(/Este navegador no deja guardar la lista/)).toBeTruthy();
    expect(copiaGuardada(C1)).toBeNull();
  });

  it("una copia dañada (zona o fecha inválidas) se descarta sin romper la página", async () => {
    guardar(C1, "Edificio Azul", { ...LISTA, zona_horaria: "Zona/Inexistente" });
    guardar(C2, "Edificio Verde", LISTA, "no es una fecha");
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": sinRed }));
    abrir();
    expect(await screen.findByText(/Este dispositivo no tiene contactos guardados/)).toBeTruthy();
    expect([copiaGuardada(C1), copiaGuardada(C2)]).toEqual([null, null]);
  });
});

describe("copia de los contactos", () => {
  it("salir de la cuenta las borra todas", () => {
    guardar(C1, "Edificio Azul", LISTA);
    guardar(C2, "Edificio Verde", LISTA);
    localStorage.setItem("otra-clave", "x");
    borrarCopias();
    expect([copiaGuardada(C1), copiaGuardada(C2)]).toEqual([null, null]);
    // Control: lo que no es una copia de contactos queda.
    expect(localStorage.getItem("otra-clave")).toBe("x");
  });

  it("vigentes usa el fin exclusivo", () => {
    const k = contacto(K1, "X", { vigente_hasta: "2026-11-01" });
    expect(vigentes([k], "2026-10-31")).toHaveLength(1);
    expect(vigentes([k], "2026-11-01")).toHaveLength(0);
  });
});

describe("lo que no se puede hacer no aparece en /contactos", () => {
  const conAcciones = (...acciones: string[]) =>
    backend({
      "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
      [`GET ${RUTA(C1)}`]: () => Response.json(LISTA),
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("contactos.ver", ...acciones),
    });

  it("«Gestionar la lista» se ve con `contactos.gestionar`", async () => {
    vi.stubGlobal("fetch", conAcciones("contactos.gestionar"));
    abrir();
    expect((await screen.findByRole("link", { name: "Gestionar la lista" })).getAttribute("href")).toBe("/contactos/gestion");
    // Si el enlace se ve, la persona alcanza la acción: no hace falta aclarar para quién es.
    expect(screen.queryByText(/administración y Consejo/)).toBeNull();
  });

  it("control: sin `contactos.gestionar` la lista se ve y el enlace no", async () => {
    vi.stubGlobal("fetch", conAcciones());
    abrir();
    await nombres();
    expect(screen.queryByRole("link", { name: "Gestionar la lista" })).toBeNull();
    expect(screen.queryByText(/administración y Consejo/)).toBeNull();
  });
});
