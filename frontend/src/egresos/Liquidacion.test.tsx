import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { mesSiguiente, periodo } from "./Liquidacion";

// Liquidación neutral (app 0057). Datos ficticios: ningún proveedor ni importe real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const L1 = "22222222-2222-4222-8222-222222222222";
const O1 = "33333333-3333-4333-8333-333333333333";
const G1 = "44444444-4444-4444-8444-444444444444";
const E1 = "55555555-5555-4555-8555-555555555555";
const U1 = "66666666-6666-4666-8666-666666666666";
const U2 = "77777777-7777-4777-8777-777777777777";
const PERSONA = "88888888-8888-4888-8888-888888888888";
const BASE = `/api/v1/consorcios/${C1}`;

type Detalle = components["schemas"]["LiquidacionDetalle"];

const LIQUIDACION = {
  id: L1, periodo: "2026-10-01", version: 1, estado: "borrador", preparada_por: PERSONA,
  creada_en: "2026-10-31T12:00:00Z",
};
const LIQUIDABLE = {
  orden_id: O1, concepto: "Limpieza de octubre", razon_social: "Limpieza Ficticia SRL",
  importe_centavos: 100000, moneda: "ARS", pagada_el: "2026-10-30",
};
const GRUPO = { grupo: "expensas_comunes", esquema_id: E1, esquema_version: 1, vigente_desde: "2024-01-01" };
const SIN_GASTOS: Detalle = {
  liquidacion: LIQUIDACION, gastos: [], grupos: [GRUPO],
  simulacion: { moneda: null, total_centavos: 0, grupos: [], unidades: [], bloqueo: null },
};
const CON_GASTO: Detalle = {
  ...SIN_GASTOS,
  gastos: [{ ...LIQUIDABLE, id: G1, grupo: "expensas_comunes", esquema_id: E1, esquema_version: 1,
             agregado_por: PERSONA, agregado_en: "2026-10-31T12:05:00Z" }],
  simulacion: {
    moneda: "ARS", total_centavos: 100000, bloqueo: null,
    grupos: [{ grupo: "expensas_comunes", esquema_id: E1, esquema_version: 1, total_centavos: 100000 }],
    unidades: [
      { unidad_id: U1, designacion: "1° A", total_centavos: 60016, por_grupo: { expensas_comunes: 60016 } },
      { unidad_id: U2, designacion: "1° B", total_centavos: 39984, por_grupo: { expensas_comunes: 39984 } },
    ],
  },
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response | Promise<Response>>;

/** La pantalla del código pregunta si hay segundo factor; salvo que la prueba diga otra cosa, lo hay. */
const CON_SEGUNDO_FACTOR: Respuestas = {
  "GET /api/v1/auth/totp": () =>
    Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
};

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? CON_SEGUNDO_FACTOR[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);
const VERIFICADO = () =>
  Response.json({
    verificado_en: "2026-10-31T12:00:00Z",
    vale_hasta: "2026-10-31T12:10:00Z",
    codigos_recuperacion_restantes: 9,
  });

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/liquidacion"]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
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

describe("liquidación (borrador)", () => {
  it("dice el período en palabras", () => {
    expect(periodo("2026-10-01")).toBe("octubre de 2026");
  });

  it("abre el borrador, agrega una orden pagada con su grupo, muestra la simulación y la quita", async () => {
    let liquidaciones: unknown[] = [];
    let detalle = SIN_GASTOS;
    let liquidables: unknown[] = [LIQUIDABLE];
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: liquidaciones }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: liquidables }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(detalle),
      [`POST ${BASE}/liquidaciones`]: () => {
        liquidaciones = [LIQUIDACION];
        return Response.json({ id: L1 }, { status: 201 });
      },
      [`POST ${BASE}/liquidaciones/${L1}/gastos`]: () => {
        detalle = CON_GASTO;
        liquidables = [];
        return Response.json({ id: G1 }, { status: 201 });
      },
      [`POST ${BASE}/liquidaciones/${L1}/gastos/${G1}/quitar`]: () => {
        detalle = SIN_GASTOS;
        liquidables = [LIQUIDABLE];
        return Response.json({ id: G1 });
      },
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();

    expect(await screen.findByText("Todavía no hay liquidaciones en este edificio.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    expect(await screen.findByText("Listo: abriste el borrador del período.")).toBeTruthy();
    const [preparar] = pedidos(api, "POST", `${BASE}/liquidaciones`);
    expect(await preparar!.json()).toEqual({ periodo: "2026-10-01" });
    expect(preparar!.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(preparar!.headers.get("Idempotency-Key")).toBeTruthy();

    const borrador = await screen.findByRole("region", { name: "Borrador de octubre de 2026" });
    expect(within(borrador).getByText("Todavía no agregaste gastos.")).toBeTruthy();
    fireEvent.change(within(borrador).getByLabelText("Orden pagada"), { target: { value: O1 } });
    fireEvent.change(within(borrador).getByLabelText("Grupo de gasto"), { target: { value: "expensas_comunes" } });
    fireEvent.click(within(borrador).getByRole("button", { name: "Agregar el gasto" }));
    expect(await screen.findByText("Listo: agregaste el gasto. La simulación ya lo reparte.")).toBeTruthy();
    const [agregar] = pedidos(api, "POST", `${BASE}/liquidaciones/${L1}/gastos`);
    expect(await agregar!.json()).toEqual({ orden_id: O1, grupo: "expensas_comunes" });
    // P3 139: la fecha con la que el gasto entra en el período, sin pasarla por la hora local.
    expect(within(borrador).getByText(/pagada el 2026-10-30\)/)).toBeTruthy();

    const tabla = await screen.findByRole("table");
    const filas = within(tabla).getAllByRole("row");
    expect(filas.map((f) => f.textContent)).toEqual([
      "Unidadexpensas_comunesTotal",
      expect.stringContaining("1° A"),
      expect.stringContaining("1° B"),
    ]);
    expect(within(tabla).getByText(/Total .*1\.000,00, repartido por unidad/)).toBeTruthy();
    expect(screen.getByText("No hay órdenes pagadas hasta el fin de octubre de 2026 para agregar.")).toBeTruthy();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /^Quitar Limpieza Ficticia SRL, .*1\.000,00$/ }));
    expect(await screen.findByText("Listo: quitaste el gasto del borrador.")).toBeTruthy();
    expect(await screen.findByText("Todavía no agregaste gastos.")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("si el esquema no coincide con las unidades, dice por qué no se reparte", async () => {
    const bloqueado: Detalle = {
      ...CON_GASTO,
      simulacion: { ...CON_GASTO.simulacion, unidades: [], bloqueo: "el esquema no coincide con las unidades activas" },
    };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(bloqueado),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "octubre de 2026, versión 1 (borrador)" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "No se puede repartir: el esquema no coincide con las unidades activas",
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("una liquidación que no es borrador no ofrece quitar ni agregar", async () => {
    const publicada = { ...LIQUIDACION, estado: "publicada" };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [publicada] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [LIQUIDABLE] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json({ ...CON_GASTO, liquidacion: publicada }),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    expect(await screen.findByText(/Limpieza Ficticia SRL: /)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Quitar/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar el gasto" })).toBeNull();
  });

  it("descarta el borrador con su motivo, y sin motivo no se puede", async () => {
    let liquidaciones: unknown[] = [LIQUIDACION];
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: liquidaciones }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () =>
        Response.json({ ...SIN_GASTOS, liquidacion: liquidaciones[0] }),
      [`POST ${BASE}/liquidaciones/${L1}/descartar`]: () => {
        liquidaciones = [{ ...LIQUIDACION, estado: "descartada" }];
        return Response.json({ id: L1 });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    const boton = await screen.findByRole("button", { name: "Descartar el borrador" });
    expect((boton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Motivo para descartar el borrador"),
      { target: { value: "  SINTETICO: período equivocado " } });
    fireEvent.click(boton);
    expect(await screen.findByText(/Listo: descartaste el borrador/)).toBeTruthy();
    const [descarte] = pedidos(api, "POST", `${BASE}/liquidaciones/${L1}/descartar`);
    expect(await descarte!.json()).toEqual({ motivo: "SINTETICO: período equivocado" });
    expect(descarte!.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByRole("button", { name: /versión 1 \(descartada\)/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Descartar el borrador" })).toBeNull();
  });

  it("el motivo escrito para un borrador no pasa a otro", async () => {
    const L2 = L1.replace("2222-4222", "2223-4222");
    const noviembre = { ...LIQUIDACION, id: L2, periodo: "2026-11-01" };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION, noviembre] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(SIN_GASTOS),
      [`GET ${BASE}/liquidaciones/${L2}`]: () =>
        Response.json({ ...SIN_GASTOS, liquidacion: noviembre }),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    fireEvent.change(await screen.findByLabelText("Motivo para descartar el borrador"),
      { target: { value: "SINTETICO: el de octubre" } });
    fireEvent.click(screen.getByRole("button", { name: /noviembre de 2026, versión 1/ }));
    const campo = await screen.findByLabelText("Motivo para descartar el borrador");
    expect((campo as HTMLInputElement).value).toBe("");
    const boton = screen.getByRole("button", { name: "Descartar el borrador" });
    expect((boton as HTMLButtonElement).disabled).toBe(true);
  });

  it("con gastos en una moneda solo ofrece órdenes de esa moneda", async () => {
    const enDolares = { ...LIQUIDABLE, orden_id: O1.replace("3333", "9999"), moneda: "USD",
                        razon_social: "Proveedor en dólares SRL" };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () =>
        Response.json({ elementos: [{ ...LIQUIDABLE, orden_id: O1.replace("3333", "8888") }, enDolares] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(CON_GASTO),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    const select = await screen.findByLabelText("Orden pagada");
    const opciones = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(opciones.some((t) => t?.includes("Limpieza Ficticia SRL"))).toBe(true);
    expect(opciones.some((t) => t?.includes("Proveedor en dólares SRL"))).toBe(false);
  });

  it("solo ofrece lo pagado hasta el último día del período, como el backend (P3 136)", async () => {
    const elUltimoDia = { ...LIQUIDABLE, orden_id: O1.replace("3333", "8888"), pagada_el: "2026-10-31" };
    const alOtroDia = { ...LIQUIDABLE, orden_id: O1.replace("3333", "9999"), pagada_el: "2026-11-01",
                        razon_social: "Pagada en noviembre SRL" };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [elUltimoDia, alOtroDia] }),
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(SIN_GASTOS),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    const select = await screen.findByLabelText("Orden pagada");
    const opciones = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(opciones.some((t) => t?.includes("Limpieza Ficticia SRL"))).toBe(true); // control: el 31 entra
    expect(opciones.some((t) => t?.includes("Pagada en noviembre SRL"))).toBe(false);
  });

  it("el mes siguiente cruza el año sin pasar por la zona del navegador", () => {
    expect([mesSiguiente("2026-10-01"), mesSiguiente("2026-12-01"), mesSiguiente("2027-01-01")])
      .toEqual(["2026-11-01", "2027-01-01", "2027-02-01"]);
  });

  it("un período mal escrito se explica sin ir al backend", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const campo = await screen.findByLabelText("Período");
    // Donde el navegador no tiene `type="month"`, el campo es texto libre.
    campo.setAttribute("type", "text");
    fireEvent.change(campo, { target: { value: "10/2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    expect(await screen.findByText(/Escribí el período como año y mes/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/liquidaciones`)).toEqual([]);
  });

  it("la lectura pide el segundo factor y se reintenta después del código", async () => {
    let verificado = false;
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () =>
        verificado ? Response.json({ elementos: [] }) : error("segundo_factor_requerido", 403),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () =>
        verificado ? Response.json({ elementos: [] }) : error("segundo_factor_requerido", 403),
      "POST /api/v1/auth/totp/verificar": () => {
        verificado = true;
        return VERIFICADO();
      },
    }));
    abrir();
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
    expect(await screen.findByText("Todavía no hay liquidaciones en este edificio.")).toBeTruthy();
  });

  it("un 403 al leer lleva al inicio, y un rechazo del backend al actuar se explica", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/auth/sesion": () => Response.json({}),
      "GET /health": () => error("servicio_no_disponible", 503),
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => error("sin_permiso", 403),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => error("sin_permiso", 403),
    }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    expect(screen.queryByText(/La liquidación la prepara la administración/)).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/liquidaciones`]: () => error("liquidacion_no_admite", 409),
    }));
    abrir();
    fireEvent.change(await screen.findByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    expect(await screen.findByText(/ya hay un borrador de ese período/)).toBeTruthy();
  });

  const LISTAS = {
    "GET /api/v1/me/consorcios": CONSORCIOS,
    [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION] }),
    [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
  };

  it("si el período ya tiene borrador, «Abrir el borrador» abre ese y no pide otro", async () => {
    const api = backend({ ...LISTAS, [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(SIN_GASTOS) });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.change(await screen.findByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    expect(await screen.findByRole("region", { name: "Borrador de octubre de 2026" })).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/liquidaciones`)).toEqual([]);
  });

  it.each([
    ["otro estado", { ...LIQUIDACION, estado: "descartada" }],
    ["otro período", { ...LIQUIDACION, periodo: "2026-09-01" }],
  ])("control: con una liquidación de %s sí se pide un borrador nuevo", async (_, otra) => {
    const api = backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [otra] }),
      [`POST ${BASE}/liquidaciones`]: () => error("liquidacion_no_admite", 409),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.change(await screen.findByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/liquidaciones`)).toHaveLength(1));
  });

  it("repetir el mismo acto tras un 500 lleva la misma clave; otro período, otra", async () => {
    const api = backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/liquidaciones`]: () => error("error_interno", 500),
    });
    const intentos = () => pedidos(api, "POST", `${BASE}/liquidaciones`);
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.change(await screen.findByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    await screen.findByText(/El servicio no está disponible/);
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    await waitFor(() => expect(intentos()).toHaveLength(2));
    await screen.findByText(/El servicio no está disponible/);
    fireEvent.change(screen.getByLabelText("Período"), { target: { value: "2026-11" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    await waitFor(() => expect(intentos()).toHaveLength(3));
    const [primera, segunda, tercera] = intentos().map((q) => q.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(tercera).not.toBe(segunda);
  });

  it("si falló el detalle de la abierta, se repite solo al volver la red", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones/${L1}`]: () => (caido ? error("error_interno", 500) : Response.json(SIN_GASTOS)),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    await screen.findByRole("alert");
    caido = false;
    fireEvent(window, new Event("online"));
    expect(await screen.findByRole("region", { name: "Borrador de octubre de 2026" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("descartar cierra la descartada y vuelve a la lista, sin volver a pedirla", async () => {
    const api = backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(SIN_GASTOS),
      [`POST ${BASE}/liquidaciones/${L1}/descartar`]: () => Response.json({ id: L1 }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    fireEvent.change(await screen.findByLabelText("Motivo para descartar el borrador"), { target: { value: "SINTETICO" } });
    const pedidasAntes = pedidos(api, "GET", `${BASE}/liquidaciones/${L1}`).length;
    fireEvent.click(screen.getByRole("button", { name: "Descartar el borrador" }));
    expect(await screen.findByText(/Listo: descartaste el borrador/)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Borrador de octubre de 2026" })).toBeNull();
    expect(screen.getByRole("button", { name: /octubre de 2026, versión 1/ }).getAttribute("aria-pressed")).toBe("false");
    expect(pedidos(api, "GET", `${BASE}/liquidaciones/${L1}`)).toHaveLength(pedidasAntes);
  });

  it("quitar un gasto deja abierto el borrador: el descarte es el único que lo cierra", async () => {
    vi.stubGlobal("fetch", backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones/${L1}`]: () => Response.json(CON_GASTO),
      [`POST ${BASE}/liquidaciones/${L1}/gastos/${G1}/quitar`]: () => Response.json({ id: G1 }),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Quitar/ }));
    expect(await screen.findByText(/Listo: quitaste el gasto/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "Borrador de octubre de 2026" })).toBeTruthy();
  });

  it("si falla el detalle de la abierta, la lista sigue: se reintenta o se vuelve a ella", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones/${L1}`]: () => (caido ? error("error_interno", 500) : Response.json(SIN_GASTOS)),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /octubre de 2026, versión 1/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("No pudimos abrir esta liquidación");
    expect(screen.getByRole("list", { name: "Liquidaciones" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Volver a la lista" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    const boton = screen.getByRole("button", { name: /octubre de 2026, versión 1/ });
    expect(boton.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(boton);
    const reintentar = await screen.findByRole("button", { name: "Reintentar" });
    caido = false;
    fireEvent.click(reintentar);
    expect(await screen.findByRole("region", { name: "Borrador de octubre de 2026" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("si falla la lista, Reintentar la vuelve a pedir y no hay salida de más", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      ...LISTAS,
      [`GET ${BASE}/liquidaciones`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [LIQUIDACION] })),
    }));
    abrir();
    const boton = await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.queryByRole("button", { name: "Volver a la lista" })).toBeNull();
    caido = false;
    fireEvent.click(boton);
    expect(await screen.findByRole("list", { name: "Liquidaciones" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
