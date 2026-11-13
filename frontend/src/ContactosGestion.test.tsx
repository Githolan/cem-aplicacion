import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { mensajeDeError, PATRON_TELEFONO } from "./ContactosGestion";
import { SIN_CONEXION } from "./fallas";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const K1 = "33333333-3333-4333-8333-333333333333";
const K2 = "44444444-4444-4444-8444-444444444444";
const P1 = "55555555-5555-4555-8555-555555555555";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];
const BASE = `/api/v1/consorcios/${C1}/contactos`;
const GESTION = `${BASE}/gestion`;

type Contacto = components["schemas"]["ContactoGestion"];

/** Un contacto del contrato: si le falta un campo, el tipo no compila. */
function contacto(id: string, nombre: string, extra: Partial<Contacto> = {}): Contacto {
  return {
    id,
    funcion: "emergencias",
    naturaleza: "servicio",
    nombre,
    telefono: "0800-555-1234",
    indicaciones: null,
    vigente_desde: "2026-10-01",
    vigente_hasta: null,
    actualizado_en: "2026-10-20T15:00:00Z",
    verificado_en: null,
    observado: false,
    consentimiento_referencia: null,
    observacion: null,
    actualizado_por: P1,
    verificado_por: null,
    observado_por: null,
    ...extra,
  };
}

const LISTA = {
  elementos: [
    contacto(K1, "Plomería Rápida", { indicaciones: "De 8 a 20; fuera de hora, el número de guardia" }),
    contacto(K2, "Marta Ruiz", {
      funcion: "encargado",
      naturaleza: "profesional",
      telefono: "11 4000-0000",
      consentimiento_referencia: "Nota firmada del 20-10",
      observado: true,
      observacion: "El teléfono no atiende",
      observado_por: P1,
    }),
  ],
};

function error(codigo: string, status: number, detalle: unknown = null) {
  return Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status });
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
    [`GET ${GESTION}`]: () => Response.json(LISTA),
    ...extra,
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta: string | { pathname: string; state: unknown } = "/contactos/gestion") {
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

/** La fila del contacto: un botón cuyo nombre empieza con el título. */
async function filaDe(titulo: string): Promise<HTMLElement> {
  return screen.findByRole("button", { name: new RegExp(`^${titulo}( |$)`) });
}

/** Abre la ficha del contacto desde su fila (con el foco en la fila, como al tocarla) y la devuelve. */
async function abrirFicha(titulo: string): Promise<HTMLElement> {
  const fila = await filaDe(titulo);
  fila.focus();
  fireEvent.click(fila);
  return screen.getByRole("dialog", { name: titulo });
}

function escribir(etiqueta: string | RegExp, valor: string) {
  fireEvent.change(screen.getByLabelText(etiqueta), { target: { value: valor } });
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

describe("página /contactos/gestion", () => {
  const desdeLaLista = (gestion: typeof LISTA = LISTA) =>
    conLista({
      [`GET ${BASE}`]: () => Response.json({ ...LISTA, actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" }),
      [`GET ${GESTION}`]: () => Response.json(gestion),
    });

  it("desde /contactos, «Agregar un contacto» abre el alta en la gestión", async () => {
    vi.stubGlobal("fetch", desdeLaLista());
    abrir("/contactos");
    const agregar = await screen.findByRole("link", { name: "Agregar un contacto" });
    expect(agregar.getAttribute("href")).toBe("/contactos/gestion");
    fireEvent.click(agregar);
    expect(await screen.findByRole("heading", { name: "Agregar un contacto" })).toBeTruthy();
    expect(screen.getByLabelText("Nombre")).toBeTruthy();
  });

  it("control: la gestión abierta sin pedir nada no abre ningún formulario", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await filaDe("Encargado/a: Marta Ruiz");
    expect(screen.queryByRole("heading", { name: "Agregar un contacto" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    expect(screen.queryByText(/¿Retirar el contacto/)).toBeNull();
  });

  it("desde /contactos, «Retirar» en un contacto abre la pregunta de ese contacto, y «Corregir» sus datos", async () => {
    vi.stubGlobal("fetch", desdeLaLista());
    abrir("/contactos");
    fireEvent.click(within(await abrirFicha("Encargado/a: Marta Ruiz")).getByRole("link", { name: "Retirar" }));
    expect(await screen.findByText("¿Retirar el contacto Marta Ruiz? No se puede deshacer.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sí, retirar" }).className).toBe("peligro");
    // Solo ese: el otro contacto sigue sin nada abierto.
    expect(screen.queryByText(/¿Retirar el contacto Plomería Rápida/)).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", desdeLaLista());
    abrir("/contactos");
    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("link", { name: "Corregir" }));
    expect(await screen.findByRole("heading", { name: "Corregir el contacto" })).toBeTruthy();
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida");
  });

  // Recargar la página deja el pedido en la historia pero no el edificio elegido en memoria
  // (`permisos.tsx`): un render nuevo, con su propio QueryClient, es esa recarga.
  const C2 = "22222222-2222-4222-8222-222222222222";
  const BASE2 = `/api/v1/consorcios/${C2}/contactos`;
  const DOS_EDIFICIOS = [...CONSORCIOS, { id: C2, nombre: "Edificio Bordó", vinculos: [] }];
  const K3 = "66666666-6666-4666-8666-666666666666";
  const conDosEdificios = (extra: Respuestas = {}) =>
    conLista({
      "GET /api/v1/me/consorcios": () => Response.json(DOS_EDIFICIOS),
      [`GET ${BASE2}/gestion`]: () => Response.json({ elementos: [contacto(K3, "Gas Ficticio")] }),
      ...extra,
    });
  const recargar = (state: unknown) => abrir({ pathname: "/contactos/gestion", state });

  it("al recargar, «Agregar un contacto» pedido para el segundo edificio se abre y se manda a ese edificio", async () => {
    const simulado = conDosEdificios({
      [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }),
      [`POST ${BASE2}`]: () => Response.json({ id: K3 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    recargar({ consorcio: C2, accion: "agregar" });
    expect(await screen.findByRole("heading", { name: "Agregar un contacto" })).toBeTruthy();
    expect((screen.getByLabelText("Edificio") as HTMLSelectElement).value).toBe(C2);
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "Seguros del Plata");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    expect(pedidos(simulado, "POST", BASE2)).toHaveLength(1);
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
  });

  it("al recargar, «Retirar» pedido en el segundo edificio abre el contacto de ese edificio", async () => {
    vi.stubGlobal("fetch", conDosEdificios());
    recargar({ consorcio: C2, accion: "retirar", contacto: K3 });
    expect(await screen.findByText("¿Retirar el contacto Gas Ficticio? No se puede deshacer.")).toBeTruthy();
    expect((screen.getByLabelText("Edificio") as HTMLSelectElement).value).toBe(C2);
  });

  it("control: un pedido para un edificio que no se gestiona no abre nada ni manda nada, tampoco en el primero", async () => {
    const simulado = conDosEdificios({
      [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    recargar({ consorcio: "99999999-9999-4999-8999-999999999999", accion: "agregar" });
    await filaDe("Encargado/a: Marta Ruiz");
    expect(screen.queryByRole("heading", { name: "Agregar un contacto" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
  });

  it("control: si el contacto ya no está en la gestión, queda la lista sin nada abierto", async () => {
    vi.stubGlobal("fetch", desdeLaLista({ elementos: [LISTA.elementos[0]!] }));
    abrir("/contactos");
    fireEvent.click(within(await abrirFicha("Encargado/a: Marta Ruiz")).getByRole("link", { name: "Retirar" }));
    await filaDe("Emergencias: Plomería Rápida");
    expect(screen.queryByText(/¿Retirar el contacto/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Corregir el contacto" })).toBeNull();
  });

  it("sin sesión (401) manda a /entrar y borra las copias de /contactos", async () => {
    localStorage.setItem(`cem-contactos:${C1}`, "{}");
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${GESTION}`]: () => error("no_autenticado", 401),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
    expect(localStorage.length).toBe(0);
  });

  it("muestra cada contacto con lo de gestión, y la lista no se guarda en el dispositivo", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    expect(within(plomeria).getByText("Servicio")).toBeTruthy();
    expect(within(plomeria).getByText("0800-555-1234")).toBeTruthy();
    expect(within(plomeria).getByText("Desde el 1 de octubre de 2026, sin fecha de fin")).toBeTruthy();
    expect(within(plomeria).getByText("Sin comprobar")).toBeTruthy();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    expect(within(marta).getByText("Nota firmada del 20-10")).toBeTruthy();
    expect(within(marta).getByText("El teléfono no atiende")).toBeTruthy();
    // Nada de la lista de gestión queda en el almacenamiento (§5): ni la copia de /contactos.
    expect(Object.keys(localStorage)).toEqual([]);
    // Control: la página sí pidió la lista de gestión y no la general.
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(1);
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(0);
  });

  it("un edificio sin contactos lo dice y deja agregar el primero", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => Response.json({ elementos: [] }) }));
    abrir();
    expect(await screen.findByText("El edificio no tiene contactos de emergencia cargados.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos para gestionar" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
  });

  it("sin conexión al pedir la lista lo dice, sin lista pero con el botón para agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => Promise.reject(new TypeError("Failed to fetch")) }));
    abrir();
    expect(await screen.findByText("No se pudo mostrar la lista.")).toBeTruthy();
    expect(screen.getByText(SIN_CONEXION)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos para gestionar" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    // La gestión nunca guarda nada en el dispositivo, tampoco cuando falla.
    expect(Object.keys(localStorage)).toEqual([]);
  });

  it("con la lista caída se agrega igual, y lo escrito sigue mientras se vuelve a consultar", async () => {
    let caida = true;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => (caida ? Promise.reject(new TypeError("Failed to fetch")) : Response.json(LISTA)),
      [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    const nombre = screen.getByLabelText("Nombre") as HTMLInputElement;
    escribir("Nombre", "Bomberos");
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    await filaDe("Emergencias: Plomería Rápida");
    // El mismo campo: el formulario no se desarmó mientras llegaba la lista.
    expect(screen.getByLabelText("Nombre")).toBe(nombre);
    expect(nombre.value).toBe("Bomberos");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    expect(await screen.findByText("Listo: el contacto Bomberos quedó agregado.")).toBeTruthy();
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
  });

  it("la lista que no se pudo consultar se reintenta en la misma pantalla y el error se va", async () => {
    let caida = true;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => (caida ? Promise.reject(new TypeError("Failed to fetch")) : Response.json(LISTA)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText("No se pudo mostrar la lista.")).toBeTruthy();
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    expect(await filaDe("Emergencias: Plomería Rápida")).toBeTruthy();
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2);
    expect(screen.queryByText(SIN_CONEXION)).toBeNull();
    // Control: con la lista a la vista no hay nada que reintentar.
    expect(screen.queryByRole("button", { name: "Consultar de nuevo" })).toBeNull();
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
    expect(await filaDe("Emergencias: Plomería Rápida")).toBeTruthy();
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
  });

  it("un 403 al leer la lista de gestión lleva al inicio, sin lista ni botón para agregar", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        "GET /api/v1/auth/sesion": () => Response.json({}),
        "GET /health": () => error("servicio_no_disponible", 503),
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
        [`GET ${GESTION}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    expect(screen.queryByText(/no tiene permiso/)).toBeNull();
    expect(screen.queryByRole("list", { name: "Contactos para gestionar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar un contacto" })).toBeNull();
  });

  it("agrega con CSRF, una clave de reintento y el cuerpo del contrato (lo vacío va nulo), y vuelve a pedir la lista", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Función"));
    expect(screen.queryByLabelText(/Dónde consta que aceptó figurar/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "  Seguros del Plata  ");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));

    const aviso = await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    const [pedido] = pedidos(simulado, "POST", BASE);
    expect(pedido?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.json()).toEqual({
      funcion: "aseguradora",
      naturaleza: "empresa",
      nombre: "Seguros del Plata",
      telefono: "0810-222-3333",
      consentimiento_referencia: null,
      indicaciones: null,
      vigente_desde: null,
    });
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
  });

  it("un profesional pide la constancia y el teléfono; otra naturaleza manda la constancia nula", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "profesional" } });
    const constancia = screen.getByLabelText(/Dónde consta que aceptó figurar/);
    expect(constancia.hasAttribute("required")).toBe(true);
    expect(screen.getByLabelText("Teléfono").hasAttribute("required")).toBe(true);
    escribir(/Dónde consta que aceptó figurar/, "Nota firmada del 20-10");
    // Si cambia de idea, la constancia escrita no viaja con otra naturaleza.
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "organismo" } });
    expect(screen.queryByLabelText(/Dónde consta que aceptó figurar/)).toBeNull();
    expect(screen.getByLabelText(/^Teléfono/).hasAttribute("required")).toBe(false);
    escribir("Nombre", "Bomberos");
    escribir(/^Teléfono/, "100");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    await screen.findByText("Listo: el contacto Bomberos quedó agregado.");
    const cuerpo = (await pedidos(simulado, "POST", BASE)[0]?.json()) as Record<string, unknown>;
    expect([cuerpo.naturaleza, cuerpo.consentimiento_referencia]).toEqual(["organismo", null]);
  });

  it("corregir arranca con los datos del contacto y manda un PUT con todos los campos", async () => {
    const simulado = conLista({ [`PUT ${BASE}/${K2}`]: () => Response.json({ id: K2 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Marta Ruiz");
    expect((screen.getByLabelText(/Dónde consta que aceptó figurar/) as HTMLInputElement).value).toBe("Nota firmada del 20-10");
    expect(screen.queryByLabelText(/^Desde/)).toBeNull();
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: el contacto Marta Ruiz quedó corregido.");
    expect(await pedidos(simulado, "PUT", `${BASE}/${K2}`)[0]?.json()).toEqual({
      funcion: "encargado",
      naturaleza: "profesional",
      nombre: "Marta Ruiz",
      telefono: "11 4000-0001",
      consentimiento_referencia: "Nota firmada del 20-10",
      indicaciones: null,
    });
  });

  it("mientras se agrega un contacto, las filas no abren otra ficha; al terminar, sí", async () => {
    let soltar: () => void = () => undefined;
    const simulado = conLista({
      [`POST ${BASE}`]: () =>
        new Promise<Response>((listo) => {
          soltar = () => listo(Response.json({ id: K1 }, { status: 201 }));
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "Seguros del Plata");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    const fila = await screen.findByRole("button", { name: /Encargado\/a: Marta Ruiz/ });
    await waitFor(() => expect((fila as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(fila);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar el contacto" })).toBeTruthy();
    soltar();
    await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    // Control: terminado el pedido, la fila vuelve a abrir la ficha.
    expect(await abrirFicha("Encargado/a: Marta Ruiz")).toBeTruthy();
  });

  it("después de un cambio, la lista vieja no se puede usar hasta que llega la nueva", async () => {
    let soltar: () => void = () => undefined;
    let vueltas = 0;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => {
        vueltas += 1;
        if (vueltas === 1) return Response.json(LISTA);
        return new Promise<Response>((listo) => {
          soltar = () => listo(Response.json(LISTA));
        });
      },
      [`PUT ${BASE}/${K2}`]: () => Response.json({ id: K2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: el contacto Marta Ruiz quedó corregido.");
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    // Ni la ficha ni las filas: nada de la lista vieja queda a mano.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryAllByRole("button", { name: "Corregir" })).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Contactos para gestionar" })).toBeNull();
    expect(screen.getByText("Consultando…")).toBeTruthy();
    soltar();
    // Control: con la lista nueva, las filas vuelven y la ficha ofrece corregir otra vez.
    expect(within(await abrirFicha("Encargado/a: Marta Ruiz")).getByRole("button", { name: "Corregir" })).toBeTruthy();
  });

  it("retirar, desde la ficha, pregunta de una en dos toques; «No» no envía y «Sí, retirar» sí", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    expect(screen.getByText("¿Retirar el contacto Plomería Rápida? No se puede deshacer.")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Sí, retirar" }));
    // Control: el paso intermedio ya no existe, y abrir la pregunta no mandó nada.
    expect(screen.queryByRole("button", { name: "Retirar el contacto" })).toBeNull();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(0);
    // «No» deja la ficha como estaba.
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    await screen.findByText("Listo: el contacto Plomería Rápida se retiró.");
    const [pedido] = pedidos(simulado, "POST", `${BASE}/${K1}/retirar`);
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.text()).toBe("");
  });

  it("si retirar falla, la pregunta sigue para reintentar con la misma clave", async () => {
    const ruta = `${BASE}/${K1}/retirar`;
    const simulado = conLista({ [`POST ${ruta}`]: () => error("servicio_no_disponible", 503) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    const si = screen.getByRole("button", { name: "Sí, retirar" }) as HTMLButtonElement;
    fireEvent.click(si);
    const aviso = await screen.findByText(/El servicio no está disponible/);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    // El rechazo va dentro de la ficha abierta: lo de atrás queda inerte.
    expect(plomeria.contains(aviso)).toBe(true);
    expect(aviso.getAttribute("role")).toBe("alert");
    expect(si.disabled).toBe(false);
    fireEvent.click(si);
    await waitFor(() => expect(pedidos(simulado, "POST", ruta)).toHaveLength(2));
    const [primera, segunda] = pedidos(simulado, "POST", ruta).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("retirar un contacto que ya terminó lo explica, cierra la pregunta y recarga la lista", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => error("contacto_terminado", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    expect(await screen.findByText("Ese contacto ya no está vigente: para cambiarlo, hay que agregar uno nuevo.")).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Emergencias: Plomería Rápida");
    expect(screen.queryByRole("button", { name: "Sí, retirar" })).toBeNull();
    // No había nada escrito: retirar no abre Agregar.
    expect(screen.queryByLabelText("Nombre")).toBeNull();
  });

  it("comprobar y señalar mandan sus pedidos; señalar lleva el motivo", async () => {
    const simulado = conLista({
      [`POST ${BASE}/${K1}/verificar`]: () => Response.json({ id: K1 }),
      [`POST ${BASE}/${K1}/senalar`]: () => Response.json({ id: K1 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    let plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Comprobado" }));
    fireEvent.click(screen.getByRole("button", { name: "Lo comprobé" }));
    await screen.findByText("Listo: quedó la constancia de que el contacto Plomería Rápida se comprobó.");
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/verificar`)).toHaveLength(1);
    plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Motivo"));
    escribir("Motivo", " Cambió de dirección ");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    await screen.findByText("Listo: quedó señalado el dato incorrecto del contacto Plomería Rápida.");
    expect(await pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)[0]?.json()).toEqual({ motivo: "Cambió de dirección" });
  });

  it("G-58: un 422 del nombre se dice en una frase corta y el motivo va junto al nombre", async () => {
    const simulado = conLista({
      [`PUT ${BASE}/${K1}`]: () =>
        error("peticion_invalida", 422, {
          campos: ["body.nombre"],
          mensajes: { "body.nombre": "Sin teléfonos ni correos acá: el número va solo en el teléfono." },
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Llamar al 11 2345 6789");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    const resumen = await screen.findByText(/^Hay que corregir el nombre/);
    expect((resumen.textContent ?? "").length).toBeLessThan(100);
    const nombre = screen.getByLabelText("Nombre");
    expect(nombre.getAttribute("aria-invalid")).toBe("true");
    const descripciones = (nombre.getAttribute("aria-describedby") ?? "").split(" ").map((i) => document.getElementById(i)?.textContent);
    expect(descripciones).toContain("Sin teléfonos ni correos acá: el número va solo en el teléfono.");
    // Control: el teléfono no queda marcado.
    expect(screen.getByLabelText(/^Teléfono/).getAttribute("aria-invalid")).toBeNull();
  });

  it("G-57: el teléfono mal escrito lo dice con su propio texto, junto al campo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    const telefono = screen.getByLabelText(/^Teléfono/) as HTMLInputElement;
    fireEvent.change(telefono, { target: { value: "abc" } });
    expect(telefono.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("El teléfono va solo con números, espacios, paréntesis o guiones.")).toBeTruthy();
    expect(telefono.validationMessage).toBe("El teléfono va solo con números, espacios, paréntesis o guiones.");
    // Control: bien escrito, el aviso se va.
    fireEvent.change(telefono, { target: { value: "011 4567-8901" } });
    expect(telefono.getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText("El teléfono va solo con números, espacios, paréntesis o guiones.")).toBeNull();
    expect(telefono.validationMessage).toBe("");
  });

  it("G-59: «Cancelar» en «Agregar un contacto» se lleva el error", async () => {
    vi.stubGlobal("fetch", conLista({ [`POST ${BASE}`]: () => error("peticion_invalida", 422, { campos: ["body.nombre"] }) }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    escribir("Nombre", "Cerrajería Ficticia");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    // Control: el error se ve mientras el formulario está abierto.
    expect(await screen.findByText(/^Hay que corregir el nombre/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("button", { name: "Agregar el contacto" })).toBeNull();
    expect(screen.queryByText(/^Hay que corregir el nombre/)).toBeNull();
  });

  it("un 422 dice qué campo revisar y por qué; un 409 contacto_terminado lo explica, recarga la lista y lo escrito pasa a Agregar", async () => {
    let respuesta = () => error("peticion_invalida", 422, { campos: ["body.nombre"] });
    const simulado = conLista({
      [`PUT ${BASE}/${K1}`]: () => respuesta(),
      [`POST ${BASE}`]: () => Response.json({ id: K2 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Llamar al 11 2345 6789");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Hay que corregir el nombre: el motivo está junto a cada campo.")).toBeTruthy();
    // El formulario sigue abierto para corregir.
    expect(screen.getByRole("button", { name: "Guardar la corrección" })).toBeTruthy();
    respuesta = () => error("contacto_terminado", 409);
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    const aviso = await screen.findByText("Ese contacto ya no está vigente: para cambiarlo, hay que agregar uno nuevo.");
    expect(screen.queryByRole("button", { name: "Guardar la corrección" })).toBeNull();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    // Lo escrito sigue, ahora en Agregar, y sale como un pedido nuevo y no como otro PUT.
    expect(screen.getByRole("heading", { level: 3, name: "Agregar un contacto" })).toBeTruthy();
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    expect(await screen.findByText("Listo: el contacto Plomería Rápida SRL quedó agregado.")).toBeTruthy();
    const correcciones = pedidos(simulado, "PUT", `${BASE}/${K1}`);
    expect(correcciones).toHaveLength(2);
    const [alta] = pedidos(simulado, "POST", BASE);
    expect(alta?.headers.get("Idempotency-Key")).not.toBe(correcciones[1]?.headers.get("Idempotency-Key"));
    expect(await alta?.json()).toEqual({
      funcion: "emergencias",
      naturaleza: "servicio",
      nombre: "Plomería Rápida SRL",
      telefono: "0800-555-1234",
      consentimiento_referencia: null,
      indicaciones: "De 8 a 20; fuera de hora, el número de guardia",
      vigente_desde: null,
    });
  });

  it("corregir un contacto que ya no está (404) también deja lo escrito en Agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`PUT ${BASE}/${K1}`]: () => error("no_encontrado", 404) }));
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Ese contacto ya no está en este edificio.")).toBeTruthy();
    expect((await screen.findByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida SRL");
    expect(screen.getByRole("button", { name: "Agregar el contacto" })).toBeTruthy();
  });

  it("control: otro 409 al corregir cierra el formulario, porque un PUT viejo desharía el cambio de otro", async () => {
    const simulado = conLista({ [`PUT ${BASE}/${K1}`]: () => error("conflicto", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText(/Otra persona cambió este dato recién/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Emergencias: Plomería Rápida");
    expect(screen.queryByRole("button", { name: "Guardar la corrección" })).toBeNull();
    expect(screen.queryByLabelText("Nombre")).toBeNull();
  });

  it("si señalar choca con la lista, el motivo sigue cuando la lista nueva trae el contacto", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/senalar`]: () => error("conflicto", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    expect(await screen.findByText(/Otra persona cambió este dato recién/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    const nueva = await screen.findByRole("dialog", { name: "Emergencias: Plomería Rápida" });
    expect((within(nueva).getByLabelText("Motivo") as HTMLInputElement).value).toBe("No atiende");
  });

  it("control: si la lista nueva ya no trae el contacto, el formulario de señalar se va", async () => {
    let terminado = false;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => Response.json(terminado ? { elementos: LISTA.elementos.slice(1) } : LISTA),
      [`POST ${BASE}/${K1}/senalar`]: () => {
        terminado = true;
        return error("contacto_terminado", 409);
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    expect(await screen.findByText(/^Ese contacto ya no está vigente/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Encargado/a: Marta Ruiz");
    expect(screen.queryByRole("heading", { level: 3, name: "Emergencias: Plomería Rápida" })).toBeNull();
    expect(screen.queryByLabelText("Motivo")).toBeNull();
    // Un motivo no alcanza para un contacto nuevo: señalar no abre Agregar.
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
  });

  it("un 5xx conserva la clave para el reintento; un 4xx definitivo la renueva; cambiar los datos también", async () => {
    let respuesta = () => error("servicio_no_disponible", 503);
    const simulado = conLista({ [`POST ${BASE}/${K1}/senalar`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    const enviar = () => fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    enviar();
    await screen.findByText(/El servicio no está disponible/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(2));
    respuesta = () => error("peticion_invalida", 422, { campos: ["body.motivo"] });
    enviar();
    await screen.findByText(/^Hay que corregir el motivo/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(4));
    escribir("Motivo", "No atiende nunca");
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(5));
    const claves = pedidos(simulado, "POST", `${BASE}/${K1}/senalar`).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves[0]).toBe(claves[1]);
    expect(claves[1]).toBe(claves[2]);
    expect(claves[3]).not.toBe(claves[2]);
    expect(claves[4]).not.toBe(claves[3]);
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la lista, la ficha y cada formulario abierto", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await filaDe("Encargado/a: Marta Ruiz");
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    // La ficha va en un portal, fuera del contenedor: se revisa la página entera.
    const faltas = async () => (await axe.run(document.body, reglas)).violations.map((v) => v.id);
    expect(await faltas()).toEqual([]);
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    expect(await faltas()).toEqual([]);
    for (const [boton, cierre] of [
      ["Corregir", "Cancelar"],
      ["Comprobado", "Cancelar"],
      ["Señalar un error", "Cancelar"],
      ["Retirar", "No"],
    ] as const) {
      fireEvent.click(within(marta).getByRole("button", { name: boton }));
      expect(await faltas()).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: cierre }));
    }
    fireEvent.click(within(marta).getByRole("button", { name: "Cerrar" }));
    fireEvent.click(screen.getByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "profesional" } });
    expect(await faltas()).toEqual([]);
  });

  it("las rutas, el encabezado de reintento, los cuerpos y los topes son los del contrato", () => {
    type Operacion = {
      parameters?: { name: string; required?: boolean }[];
      requestBody?: { content: { "application/json"?: { schema: { $ref: string } } } };
    };
    const rutas = contrato.paths as Record<string, Record<string, Operacion> | undefined>;
    expect(rutas["/api/v1/consorcios/{consorcio_id}/contactos/gestion"]?.get).toBeTruthy();
    const conClave = [
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos"]?.post, "ContactoNuevo"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}"]?.put, "DatosContacto"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/senalar"]?.post, "Senalamiento"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/retirar"]?.post, null],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/verificar"]?.post, null],
    ] as const;
    for (const [op, esquema] of conClave) {
      expect(op?.parameters?.find((p) => p.name === "Idempotency-Key")?.required).toBe(true);
      expect(op?.requestBody?.content["application/json"]?.schema.$ref ?? null).toBe(
        esquema === null ? null : `#/components/schemas/${esquema}`,
      );
    }
    const esquemas = contrato.components.schemas as Record<
      string,
      { properties?: Record<string, { maxLength?: number; anyOf?: { maxLength?: number; pattern?: string }[] }> }
    >;
    expect(esquemas.DatosContacto?.properties?.nombre?.maxLength).toBe(120);
    expect(esquemas.Senalamiento?.properties?.motivo?.maxLength).toBe(300);
    const telefono = esquemas.DatosContacto?.properties?.telefono?.anyOf?.find((t) => t.pattern !== undefined);
    expect(telefono?.pattern).toBe("^[+0-9][0-9 ()-]{2,29}$");
    // El navegador compila `pattern` con la marca `v`, donde los paréntesis y el guion sin escapar
    // son inválidos y el atributo se ignora sin validar nada. El de la página compila y acepta lo
    // mismo que el contrato; control: el del contrato tal cual no compila con `v`.
    const pagina = new RegExp(`^(?:${PATRON_TELEFONO})$`, "v");
    const delContrato = new RegExp(telefono?.pattern ?? "");
    for (const valor of ["0800-555-1234", "+54 11 4000 0000", "(011) 4321-9876", "11x", "1"])
      expect(pagina.test(valor)).toBe(delContrato.test(valor));
    expect(() => new RegExp(`^(?:${(telefono?.pattern ?? "").slice(1, -1)})$`, "v")).toThrow();
    expect(mensajeDeError(500, undefined, "agregar")).toMatch(/No pudimos completar|servicio/);
  });
});
