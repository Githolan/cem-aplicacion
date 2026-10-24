import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { App } from "./App";
import { mensajeDeError } from "./Padron";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const U1 = "33333333-3333-4333-8333-333333333333";
const U2 = "44444444-4444-4444-8444-444444444444";
const V_PROP = "55555555-5555-4555-8555-555555555555";
const V_INQ = "66666666-6666-4666-8666-666666666666";
const V_FUT = "77777777-7777-4777-8777-777777777777";
const V_OTRO = "88888888-8888-4888-8888-888888888888";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];
const BASE = `/api/v1/consorcios/${C1}`;
const RUTA_PADRON = `${BASE}/padron`;
const confirmarRuta = (v: string) => `${BASE}/vinculos/${v}/confirmar`;
const finalizarRuta = (v: string) => `${BASE}/vinculos/${v}/finalizar`;

type Fila = components["schemas"]["FilaPadron"];

/** Una fila del contrato: si le falta un campo, el tipo no compila. */
function fila(vinculo_id: string, nombre: string, extra: Partial<Fila> = {}): Fila {
  return {
    vinculo_id,
    unidad_id: U1,
    designacion: "1° A",
    nombre,
    tipo: "propietario",
    vigente_desde: "2024-03-01",
    vigente_hasta: null,
    confirmado: false,
    iniciado: true,
    ...extra,
  };
}

const PADRON = {
  elementos: [
    fila(V_PROP, "Ana Ruiz", { confirmado: true }),
    fila(V_INQ, "Beto Pérez", { tipo: "inquilino", vigente_hasta: "2027-01-01" }),
    fila(V_FUT, "Carla Gómez", { unidad_id: U2, designacion: "2° B", vigente_desde: "2099-01-01", iniciado: false }),
  ],
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function conPadron(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET ${RUTA_PADRON}`]: () => Response.json(PADRON),
    ...extra,
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/padron") {
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

/** La tarjeta del vínculo cuyo título es `titulo`. */
async function tarjeta(titulo: string): Promise<HTMLElement> {
  return (await screen.findByRole("heading", { level: 3, name: titulo })).closest("li") as HTMLElement;
}

function escribir(etiqueta: string, valor: string) {
  fireEvent.change(screen.getByLabelText(etiqueta), { target: { value: valor } });
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /padron", () => {
  it("el inicio tiene el enlace al padrón", () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ estado: "ok" })));
    abrir("/");
    expect(screen.getByRole("link", { name: "Padrón del edificio" }).getAttribute("href")).toBe("/padron");
  });

  it("sin sesión (401) manda a /entrar", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("muestra unidad, nombre, carácter, vigencia y confirmación de cada vínculo, sin correo ni teléfono", async () => {
    // Aunque una respuesta trajera correo o teléfono (el contrato no los tiene), la pantalla solo
    // pinta los datos de D6.
    const conPrivados = {
      elementos: PADRON.elementos.map((f) => ({ ...f, correo: "privado@ejemplo.com", telefono: "+54 11 4321-9876" })),
    };
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => Response.json(conPrivados) }));
    const { container } = abrir();
    const ana = await tarjeta("1° A: Ana Ruiz");
    expect(within(ana).getByText("Propietario o propietaria")).toBeTruthy();
    expect(within(ana).getByText("Desde el 1 de marzo de 2024, sin fecha de fin")).toBeTruthy();
    expect(within(ana).getByText("Confirmado con el título")).toBeTruthy();
    const beto = await tarjeta("1° A: Beto Pérez");
    expect(within(beto).getByText("Inquilino o inquilina")).toBeTruthy();
    // `vigente_hasta` es exclusivo: el último día es el anterior.
    expect(within(beto).getByText("Desde el 1 de marzo de 2024; último día: 31 de diciembre de 2026")).toBeTruthy();
    expect(within(beto).getByText("Sin confirmar")).toBeTruthy();
    const lista = screen.getByRole("list", { name: "Padrón" });
    expect(within(lista).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "1° A: Ana Ruiz",
      "1° A: Beto Pérez",
      "2° B: Carla Gómez",
    ]);
    // Ni correo ni teléfono en el texto, los atributos ni los campos de la página (la cabecera
    // común queda afuera: sus rutas de recursos no vienen de la API).
    const principal = container.querySelector("main") as HTMLElement;
    expect(principal.innerHTML).not.toMatch(/@|privado|9876/);
    expect(principal.querySelector('input[type="email"], input[type="tel"], a[href^="mailto:"], a[href^="tel:"]')).toBeNull();
  });

  it("ofrece confirmar solo sin confirmar, y finalizar solo si el vínculo ya empezó", async () => {
    vi.stubGlobal("fetch", conPadron());
    abrir();
    const ana = await tarjeta("1° A: Ana Ruiz");
    expect(within(ana).queryByRole("button", { name: "Confirmar" })).toBeNull();
    expect(within(ana).getByRole("button", { name: "Finalizar" })).toBeTruthy();
    const beto = await tarjeta("1° A: Beto Pérez");
    expect(within(beto).getByRole("button", { name: "Confirmar" })).toBeTruthy();
    expect(within(beto).getByRole("button", { name: "Finalizar" })).toBeTruthy();
    const carla = await tarjeta("2° B: Carla Gómez");
    expect(within(carla).getByText(/todavía no empezó/)).toBeTruthy();
    expect(within(carla).getByRole("button", { name: "Confirmar" })).toBeTruthy();
    expect(within(carla).queryByRole("button", { name: "Finalizar" })).toBeNull();
  });

  it("si el vínculo empezó lo dice la API con la fecha del consorcio, no Buenos Aires ni el reloj", async () => {
    // 24-10 a las 20 GMT: en Buenos Aires es el 24 (17 h) y en un consorcio de Kiritimati
    // (GMT+14) ya es el 25. Un instante fijo en GMT: no depende de la zona de la PC.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-24T20:00:00Z"));
    const padron = {
      elementos: [
        fila(V_INQ, "Beto Pérez", { vigente_desde: "2026-10-25", iniciado: true }),
        fila(V_FUT, "Carla Gómez", { unidad_id: U2, designacion: "2° B", vigente_desde: "2026-10-26", iniciado: false }),
      ],
    };
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => Response.json(padron) }));
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    expect(within(beto).queryByText(/todavía no empezó/)).toBeNull();
    expect(within(beto).getByRole("button", { name: "Finalizar" })).toBeTruthy();
    // Control inverso: todavía futuro en la fecha del consorcio.
    const carla = await tarjeta("2° B: Carla Gómez");
    expect(within(carla).getByText(/todavía no empezó/)).toBeTruthy();
    expect(within(carla).queryByRole("button", { name: "Finalizar" })).toBeNull();
  });

  it("un vínculo futuro y confirmado no tiene acciones", async () => {
    const padron = { elementos: [fila(V_FUT, "Carla Gómez", { vigente_desde: "2099-01-01", confirmado: true, iniciado: false })] };
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => Response.json(padron) }));
    abrir();
    const carla = await tarjeta("1° A: Carla Gómez");
    expect(within(carla).queryAllByRole("button")).toEqual([]);
  });

  it("sin `padron.ver` (403) lo dice y no muestra la lista", async () => {
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => error("sin_permiso", 403) }));
    abrir();
    expect(await screen.findByText("No tenés permiso para ver el padrón de este edificio.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Padrón" })).toBeNull();
  });

  it("confirma con CSRF, una clave de reintento y la referencia; actualiza la fila y vuelve a pedir el padrón", async () => {
    const confirmado = { vinculo_id: V_INQ, unidad_id: U1, tipo: "inquilino", confirmado: true, confirmado_en: "2026-10-24T12:00:00Z" };
    const despues = { elementos: PADRON.elementos.map((f) => (f.vinculo_id === V_INQ ? { ...f, confirmado: true } : f)) };
    let vueltas = 0;
    const simulado = conPadron({
      [`GET ${RUTA_PADRON}`]: () => Response.json(vueltas++ === 0 ? PADRON : despues),
      [`POST ${confirmarRuta(V_INQ)}`]: () => Response.json(confirmado),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    const campo = screen.getByLabelText("Referencia del título");
    expect(document.activeElement).toBe(campo);
    expect(campo.getAttribute("maxlength")).toBe("300");
    escribir("Referencia del título", "  Contrato de locación del 01-03-2024  ");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));

    const aviso = await screen.findByText("Listo: confirmamos el vínculo de Beto Pérez con la unidad 1° A.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    const [pedido] = pedidos(simulado, "POST", confirmarRuta(V_INQ));
    expect(pedido?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.json()).toEqual({ referencia: "Contrato de locación del 01-03-2024" });
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_PADRON).length).toBeGreaterThanOrEqual(2));
    const actualizada = await tarjeta("1° A: Beto Pérez");
    expect(within(actualizada).getByText("Confirmado con el título")).toBeTruthy();
    expect(within(actualizada).queryByRole("button", { name: "Confirmar" })).toBeNull();
  });

  it("si falla la recarga después de confirmar, el aviso de éxito queda junto al error de la recarga", async () => {
    const confirmado = { vinculo_id: V_INQ, unidad_id: U1, tipo: "inquilino", confirmado: true, confirmado_en: "2026-10-24T12:00:00Z" };
    let vueltas = 0;
    vi.stubGlobal(
      "fetch",
      conPadron({
        [`GET ${RUTA_PADRON}`]: () => (vueltas++ === 0 ? Response.json(PADRON) : error("servicio_no_disponible", 503)),
        [`POST ${confirmarRuta(V_INQ)}`]: () => Response.json(confirmado),
      }),
    );
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    escribir("Referencia del título", "Escritura 1234");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));
    await screen.findByText(/^Listo: confirmamos/);
    await waitFor(() => expect(vueltas).toBe(2));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("Listo: confirmamos el vínculo de Beto Pérez con la unidad 1° A.")).toBeTruthy();
    expect(screen.getByText("No pudimos mostrar el padrón.")).toBeTruthy();
  });

  it("finalizar pide el motivo y una confirmación explícita antes de enviar", async () => {
    const finalizado = { vinculo_id: V_INQ, unidad_id: U1, tipo: "inquilino", vigente_hasta: "2026-10-24", finalizado_en: "2026-10-24T12:00:00Z" };
    const despues = { elementos: PADRON.elementos.filter((f) => f.vinculo_id !== V_INQ) };
    let vueltas = 0;
    const simulado = conPadron({
      [`GET ${RUTA_PADRON}`]: () => Response.json(vueltas++ === 0 ? PADRON : despues),
      [`POST ${finalizarRuta(V_INQ)}`]: () => Response.json(finalizado),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Finalizar" }));
    expect(within(beto).getByText(/La persona pierde el acceso que le daba este vínculo. No se puede deshacer./)).toBeTruthy();
    escribir("Motivo", "Fin del contrato de locación");
    fireEvent.click(screen.getByRole("button", { name: "Finalizar el vínculo" }));

    // Todavía no se mandó nada: primero la pregunta, con el foco en «Sí, finalizar».
    expect(pedidos(simulado, "POST", finalizarRuta(V_INQ))).toEqual([]);
    const pregunta = screen.getByRole("group", { name: "¿Finalizar el vínculo de Beto Pérez con la unidad 1° A? No se puede deshacer." });
    const si = within(pregunta).getByRole("button", { name: "Sí, finalizar" });
    expect(document.activeElement).toBe(si);
    fireEvent.click(si);

    expect(
      await screen.findByText("Listo: finalizamos el vínculo de Beto Pérez con la unidad 1° A. Desde el 24 de octubre de 2026 ya no vale."),
    ).toBeTruthy();
    const [pedido] = pedidos(simulado, "POST", finalizarRuta(V_INQ));
    expect(pedido?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.json()).toEqual({ motivo: "Fin del contrato de locación" });
    await waitFor(() => expect(screen.queryByRole("heading", { level: 3, name: "1° A: Beto Pérez" })).toBeNull());
  });

  it("«No» en la pregunta de finalizar vuelve al formulario sin enviar", async () => {
    const simulado = conPadron();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Finalizar" }));
    escribir("Motivo", "Venta");
    fireEvent.click(screen.getByRole("button", { name: "Finalizar el vínculo" }));
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Motivo"));
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(within(beto).getByRole("button", { name: "Finalizar" })).toBeTruthy();
    expect(pedidos(simulado, "POST", finalizarRuta(V_INQ))).toEqual([]);
  });

  it.each([
    [403, "sin_permiso", "No tenés permiso para confirmar este vínculo."],
    [404, "no_encontrado", "Ese vínculo ya no está en el padrón de este edificio."],
    [409, "vinculo_ya_confirmado", "Ese vínculo ya estaba confirmado."],
    [409, "vinculo_finalizado", "Ese vínculo ya terminó."],
    [422, "datos_invalidos", "Revisá la referencia:"],
  ])("un %i (%s) al confirmar se explica sin inventar detalles", async (estado, codigo, texto) => {
    const simulado = conPadron({ [`POST ${confirmarRuta(V_INQ)}`]: () => error(codigo, estado) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    escribir("Referencia del título", "Escritura 1234");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));
    expect(await screen.findByText(new RegExp(`^${texto.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`))).toBeTruthy();
    // Un 404 o un 409 dejan la lista vieja: se vuelve a pedir.
    if (estado === 404 || estado === 409)
      await waitFor(() => expect(pedidos(simulado, "GET", RUTA_PADRON).length).toBeGreaterThanOrEqual(2));
  });

  it("un 409 vinculo_no_iniciado al finalizar lo explica y recarga el padrón", async () => {
    const simulado = conPadron({ [`POST ${finalizarRuta(V_INQ)}`]: () => error("vinculo_no_iniciado", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Finalizar" }));
    escribir("Motivo", "Venta");
    fireEvent.click(screen.getByRole("button", { name: "Finalizar el vínculo" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, finalizar" }));
    expect(await screen.findByText("Ese vínculo todavía no empezó: no se puede finalizar.")).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_PADRON).length).toBeGreaterThanOrEqual(2));
  });

  it("un rechazo al finalizar deja el foco en el aviso, no en el campo del motivo", async () => {
    vi.stubGlobal("fetch", conPadron({ [`POST ${finalizarRuta(V_INQ)}`]: () => error("sin_permiso", 403) }));
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Finalizar" }));
    escribir("Motivo", "Venta");
    fireEvent.click(screen.getByRole("button", { name: "Finalizar el vínculo" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, finalizar" }));
    const aviso = await screen.findByText(/^No tenés permiso para finalizar este vínculo/);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    // jsdom no agrupa los renders como el navegador: el orden de los efectos lo vigila padron.e2e.ts.
    // El formulario sigue abierto con el motivo, para corregir o cancelar.
    expect((screen.getByLabelText("Motivo") as HTMLInputElement).value).toBe("Venta");
  });

  it("un 5xx conserva la clave para el reintento; un 4xx definitivo la renueva", async () => {
    let respuesta = () => error("servicio_no_disponible", 503);
    const simulado = conPadron({ [`POST ${confirmarRuta(V_INQ)}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    escribir("Referencia del título", "Escritura 1234");
    const enviar = () => fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));
    enviar();
    await screen.findByText(/El servicio no está disponible/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", confirmarRuta(V_INQ))).toHaveLength(2));
    respuesta = () => error("datos_invalidos", 422);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", confirmarRuta(V_INQ))).toHaveLength(3));
    await screen.findByText(/^Revisá la referencia/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", confirmarRuta(V_INQ))).toHaveLength(4));
    const claves = pedidos(simulado, "POST", confirmarRuta(V_INQ)).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves[0]).toBe(claves[1]);
    expect(claves[1]).toBe(claves[2]);
    expect(claves[3]).not.toBe(claves[2]);
  });

  it("cambiar el texto es otro intento: otra clave", async () => {
    const simulado = conPadron({ [`POST ${confirmarRuta(V_INQ)}`]: () => error("servicio_no_disponible", 503) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    escribir("Referencia del título", "Escritura 1234");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));
    await screen.findByText(/El servicio no está disponible/);
    escribir("Referencia del título", "Escritura 1235");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el vínculo" }));
    await waitFor(() => expect(pedidos(simulado, "POST", confirmarRuta(V_INQ))).toHaveLength(2));
    const [a, b] = pedidos(simulado, "POST", confirmarRuta(V_INQ)).map((p) => p.headers.get("Idempotency-Key"));
    expect(a).not.toBe(b);
  });

  it("el padrón de un edificio que llega tarde no pisa al del edificio elegido después (E35)", async () => {
    let soltarC1: () => void = () => undefined;
    const tarde = new Promise<void>((r) => {
      soltarC1 = r;
    });
    const padronC2 = { elementos: [fila(V_OTRO, "Dora Sosa", { designacion: "PB" })] };
    const simulado = vi.fn(async (pedido: Request) => {
      const camino = new URL(pedido.url).pathname;
      if (camino === "/api/v1/me/consorcios")
        return Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]);
      if (camino === RUTA_PADRON) {
        await tarde;
        return Response.json(PADRON);
      }
      if (camino === `/api/v1/consorcios/${C2}/padron`) return Response.json(padronC2);
      throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C2 } });
    expect(await tarjeta("PB: Dora Sosa")).toBeTruthy();
    soltarC1();
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_PADRON).length).toBeGreaterThanOrEqual(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByRole("heading", { level: 3, name: "PB: Dora Sosa" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "1° A: Ana Ruiz" })).toBeNull();
  });

  it("con dos edificios se elige el edificio y cada uno trae su padrón", async () => {
    const padronC2 = { elementos: [fila(V_OTRO, "Dora Sosa", { designacion: "PB" })] };
    const simulado = backend({
      "GET /api/v1/me/consorcios": () =>
        Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
      [`GET ${RUTA_PADRON}`]: () => Response.json(PADRON),
      [`GET /api/v1/consorcios/${C2}/padron`]: () => Response.json(padronC2),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await tarjeta("1° A: Ana Ruiz");
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await tarjeta("PB: Dora Sosa")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "1° A: Ana Ruiz" })).toBeNull();
  });

  it("sin edificios lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => Response.json([]) }));
    abrir();
    expect(await screen.findByText("Todavía no participás de ningún edificio.")).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la lista y con cada formulario abierto", async () => {
    vi.stubGlobal("fetch", conPadron());
    const { container } = abrir();
    const beto = await tarjeta("1° A: Beto Pérez");
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    fireEvent.click(within(beto).getByRole("button", { name: "Confirmar" }));
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.click(within(beto).getByRole("button", { name: "Finalizar" }));
    escribir("Motivo", "Venta");
    fireEvent.click(screen.getByRole("button", { name: "Finalizar el vínculo" }));
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
  });

  it("las rutas, el encabezado de reintento y los cuerpos son los del contrato", () => {
    type Operacion = {
      parameters?: { name: string; required?: boolean }[];
      requestBody?: { content: { "application/json": { schema: { $ref: string } } } };
    };
    const rutas = contrato.paths as Record<string, Record<string, Operacion> | undefined>;
    expect(rutas["/api/v1/consorcios/{consorcio_id}/padron"]?.get).toBeTruthy();
    for (const [accion, esquema] of [
      ["confirmar", "ConfirmacionVinculo"],
      ["finalizar", "FinVinculo"],
    ] as const) {
      const op = rutas[`/api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/${accion}`]?.post;
      expect(op?.parameters?.find((p) => p.name === "Idempotency-Key")?.required).toBe(true);
      expect(op?.requestBody?.content["application/json"].schema.$ref).toBe(`#/components/schemas/${esquema}`);
    }
    const esquemas = contrato.components.schemas as Record<string, { properties?: Record<string, { maxLength?: number }> }>;
    expect(Object.keys(esquemas.FilaPadron?.properties ?? {}).sort()).toEqual(
      ["confirmado", "designacion", "iniciado", "nombre", "tipo", "unidad_id", "vigente_desde", "vigente_hasta", "vinculo_id"].sort(),
    );
    expect(esquemas.ConfirmacionVinculo?.properties?.referencia?.maxLength).toBe(300);
    expect(esquemas.FinVinculo?.properties?.motivo?.maxLength).toBe(300);
    expect(mensajeDeError(500, undefined, "confirmar")).toMatch(/No pudimos completar|servicio/);
  });
});

/**
 * Deja en vilo los pedidos a `camino` hasta que la prueba llame a `soltar`: así se ve el estado de
 * carga, que con un fetch simulado que responde al instante no llega a pintarse.
 */
function retener(simulado: (pedido: Request) => Promise<Response>, camino: string) {
  let soltar: () => void = () => undefined;
  const listo = new Promise<void>((r) => {
    soltar = r;
  });
  const retenido = vi.fn(async (pedido: Request) => {
    if (new URL(pedido.url).pathname === camino) await listo;
    return simulado(pedido);
  });
  return { retenido, soltar: () => soltar() };
}

describe("página /padron: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta los edificios dice «Consultando…»", async () => {
    const { retenido, soltar } = retener(conPadron(), "/api/v1/me/consorcios");
    vi.stubGlobal("fetch", retenido);
    abrir();
    expect(screen.getByRole("status").textContent).toBe("Consultando…");
    soltar();
    expect(await tarjeta("1° A: Ana Ruiz")).toBeTruthy();
    expect(screen.queryByText("Consultando…")).toBeNull();
  });

  it("carga: mientras llega el padrón, la sección dice «Consultando…»", async () => {
    const { retenido, soltar } = retener(conPadron(), RUTA_PADRON);
    vi.stubGlobal("fetch", retenido);
    abrir();
    const seccion = await screen.findByRole("region", { name: "Vínculos por unidad" });
    expect(within(seccion).getByText("Consultando…")).toBeTruthy();
    soltar();
    expect(await within(seccion).findByText("1° A: Ana Ruiz")).toBeTruthy();
  });

  it("vacía: un padrón sin vínculos lo dice", async () => {
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => Response.json({ elementos: [] }) }));
    abrir();
    expect(await screen.findByText("El padrón no tiene vínculos vigentes ni futuros.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Padrón" })).toBeNull();
  });

  it("una sola unidad: la lista muestra solo sus vínculos", async () => {
    const padron = { elementos: [fila(V_PROP, "Ana Ruiz")] };
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => Response.json(padron) }));
    abrir();
    const lista = await screen.findByRole("list", { name: "Padrón" });
    expect(within(lista).getAllByRole("listitem")).toHaveLength(1);
  });

  it("error al consultar el padrón: lo dice y deja de decir «Consultando…»", async () => {
    vi.stubGlobal("fetch", conPadron({ [`GET ${RUTA_PADRON}`]: () => error("servicio_no_disponible", 503) }));
    abrir();
    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    const seccion = screen.getByRole("region", { name: "Vínculos por unidad" });
    expect(within(seccion).queryByText("Consultando…")).toBeNull();
    expect(within(seccion).getByText("No pudimos mostrar el padrón.")).toBeTruthy();
  });

  it("sin conexión al consultar el padrón lo dice", async () => {
    const simulado = vi.fn(async (pedido: Request) => {
      if (new URL(pedido.url).pathname === RUTA_PADRON) throw new TypeError("Failed to fetch");
      return Response.json(CONSORCIOS);
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/Revisá la conexión/)).toBeTruthy();
  });
});
