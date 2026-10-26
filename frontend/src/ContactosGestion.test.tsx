import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { App } from "./App";
import { mensajeDeError, PATRON_TELEFONO } from "./ContactosGestion";
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
    const respuesta = respuestas[clave];
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

function abrir(ruta = "/contactos/gestion") {
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
  return (await screen.findByRole("heading", { level: 3, name: titulo })).closest("li") as HTMLElement;
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
  it("la página /contactos tiene el enlace a la gestión", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${BASE}`]: () => Response.json({ elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" }),
      }),
    );
    abrir("/contactos");
    expect((await screen.findByRole("link", { name: "Gestionar la lista" })).getAttribute("href")).toBe("/contactos/gestion");
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
    const plomeria = await tarjeta("Emergencias: Plomería Rápida");
    expect(within(plomeria).getByText("Servicio")).toBeTruthy();
    expect(within(plomeria).getByText("0800-555-1234")).toBeTruthy();
    expect(within(plomeria).getByText("Desde el 1 de octubre de 2026, sin fecha de fin")).toBeTruthy();
    expect(within(plomeria).getByText("Sin comprobar")).toBeTruthy();
    const marta = await tarjeta("Encargado o encargada: Marta Ruiz");
    expect(within(marta).getByText("Nota firmada del 20-10")).toBeTruthy();
    expect(within(marta).getByText("El teléfono no atiende")).toBeTruthy();
    // Nada de la lista de gestión queda en el almacenamiento (§5): ni la copia de /contactos.
    expect(Object.keys(localStorage)).toEqual([]);
    // Control: la página sí pidió la lista de gestión y no la general.
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(1);
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(0);
  });

  it("sin `contactos.gestionar` (403) lo dice, sin lista ni botón para agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => error("sin_permiso", 403) }));
    abrir();
    expect(await screen.findByText(/No tenés permiso para gestionar los contactos/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos para gestionar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar un contacto" })).toBeNull();
  });

  it("agrega con CSRF, una clave de reintento y el cuerpo del contrato (lo vacío va nulo), y vuelve a pedir la lista", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Función"));
    expect(screen.queryByLabelText("Constancia del consentimiento")).toBeNull();
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "  Seguros del Plata  ");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));

    const aviso = await screen.findByText("Listo: agregamos el contacto Seguros del Plata.");
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
    fireEvent.change(screen.getByLabelText("Naturaleza"), { target: { value: "profesional" } });
    const constancia = screen.getByLabelText("Constancia del consentimiento");
    expect(constancia.hasAttribute("required")).toBe(true);
    expect(screen.getByLabelText("Teléfono").hasAttribute("required")).toBe(true);
    escribir("Constancia del consentimiento", "Nota firmada del 20-10");
    // Si cambia de idea, la constancia escrita no viaja con otra naturaleza.
    fireEvent.change(screen.getByLabelText("Naturaleza"), { target: { value: "organismo" } });
    expect(screen.queryByLabelText("Constancia del consentimiento")).toBeNull();
    expect(screen.getByLabelText(/^Teléfono/).hasAttribute("required")).toBe(false);
    escribir("Nombre", "Bomberos");
    escribir(/^Teléfono/, "100");
    fireEvent.click(screen.getByRole("button", { name: "Agregar el contacto" }));
    await screen.findByText("Listo: agregamos el contacto Bomberos.");
    const cuerpo = (await pedidos(simulado, "POST", BASE)[0]?.json()) as Record<string, unknown>;
    expect([cuerpo.naturaleza, cuerpo.consentimiento_referencia]).toEqual(["organismo", null]);
  });

  it("corregir arranca con los datos del contacto y manda un PUT con todos los campos", async () => {
    const simulado = conLista({ [`PUT ${BASE}/${K2}`]: () => Response.json({ id: K2 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await tarjeta("Encargado o encargada: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Marta Ruiz");
    expect((screen.getByLabelText("Constancia del consentimiento") as HTMLInputElement).value).toBe("Nota firmada del 20-10");
    expect(screen.queryByLabelText(/^Desde/)).toBeNull();
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: corregimos el contacto Marta Ruiz.");
    expect(await pedidos(simulado, "PUT", `${BASE}/${K2}`)[0]?.json()).toEqual({
      funcion: "encargado",
      naturaleza: "profesional",
      nombre: "Marta Ruiz",
      telefono: "11 4000-0001",
      consentimiento_referencia: "Nota firmada del 20-10",
      indicaciones: null,
    });
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
    const marta = await tarjeta("Encargado o encargada: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: corregimos el contacto Marta Ruiz.");
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    expect(screen.queryAllByRole("button", { name: "Corregir" })).toHaveLength(0);
    expect(screen.getByText("Consultando…")).toBeTruthy();
    soltar();
    // Control: con la lista nueva, los botones vuelven.
    expect(await screen.findAllByRole("button", { name: "Corregir" })).toHaveLength(2);
  });

  it("retirar pide una confirmación explícita; «No» no envía y «Sí, retirar» sí", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await tarjeta("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "Retirar el contacto" }));
    expect(screen.getByText("¿Retirar el contacto Plomería Rápida? No se puede deshacer.")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Sí, retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Retirar el contacto" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    await screen.findByText("Listo: retiramos el contacto Plomería Rápida.");
    const [pedido] = pedidos(simulado, "POST", `${BASE}/${K1}/retirar`);
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.text()).toBe("");
  });

  it("comprobar y señalar mandan sus pedidos; señalar lleva el motivo", async () => {
    const simulado = conLista({
      [`POST ${BASE}/${K1}/verificar`]: () => Response.json({ id: K1 }),
      [`POST ${BASE}/${K1}/senalar`]: () => Response.json({ id: K1 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    let plomeria = await tarjeta("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Comprobado" }));
    fireEvent.click(screen.getByRole("button", { name: "Lo comprobé" }));
    await screen.findByText("Listo: quedó la constancia de que comprobaste el contacto Plomería Rápida.");
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/verificar`)).toHaveLength(1);
    plomeria = await tarjeta("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Motivo"));
    escribir("Motivo", " Cambió de dirección ");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    await screen.findByText("Listo: señalamos el dato incorrecto del contacto Plomería Rápida.");
    expect(await pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)[0]?.json()).toEqual({ motivo: "Cambió de dirección" });
  });

  it("un 422 dice qué campo revisar y por qué; un 409 contacto_terminado lo explica y recarga la lista", async () => {
    let respuesta = () => error("peticion_invalida", 422, { campos: ["body.nombre"] });
    const simulado = conLista({ [`PUT ${BASE}/${K1}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await tarjeta("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Llamar al 11 2345 6789");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText(/^Revisá el nombre\. Los teléfonos y correos van solo en «Teléfono»/)).toBeTruthy();
    // El formulario sigue abierto para corregir.
    expect(screen.getByRole("button", { name: "Guardar la corrección" })).toBeTruthy();
    respuesta = () => error("contacto_terminado", 409);
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Ese contacto ya no está vigente: para cambiarlo, agregá uno nuevo.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Guardar la corrección" })).toBeNull();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
  });

  it("un 5xx conserva la clave para el reintento; un 4xx definitivo la renueva; cambiar los datos también", async () => {
    let respuesta = () => error("servicio_no_disponible", 503);
    const simulado = conLista({ [`POST ${BASE}/${K1}/senalar`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await tarjeta("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    const enviar = () => fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    enviar();
    await screen.findByText(/El servicio no está disponible/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(2));
    respuesta = () => error("peticion_invalida", 422, { campos: ["body.motivo"] });
    enviar();
    await screen.findByText(/^Revisá el motivo/);
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

  it("no tiene faltas de accesibilidad detectables por axe, con la lista y con cada formulario abierto", async () => {
    vi.stubGlobal("fetch", conLista());
    const { container } = abrir();
    const marta = await tarjeta("Encargado o encargada: Marta Ruiz");
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    const faltas = async () => (await axe.run(container, reglas)).violations.map((v) => v.id);
    expect(await faltas()).toEqual([]);
    for (const boton of ["Corregir", "Comprobado", "Señalar un error", "Retirar"]) {
      fireEvent.click(within(marta).getByRole("button", { name: boton }));
      expect(await faltas()).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("Naturaleza"), { target: { value: "profesional" } });
    expect(await faltas()).toEqual([]);
  });

  it("las rutas, el encabezado de reintento, los cuerpos y los topes son los del contrato", () => {
    type Operacion = {
      parameters?: { name: string; required?: boolean }[];
      requestBody?: { content: { "application/json": { schema: { $ref: string } } } };
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
      expect(op?.requestBody?.content["application/json"].schema.$ref ?? null).toBe(
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
