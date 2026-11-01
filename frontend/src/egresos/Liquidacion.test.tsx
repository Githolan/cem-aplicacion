import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { periodo } from "./Liquidacion";

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
  importe_centavos: 100000, moneda: "ARS", pagada_en: "2026-10-30T12:00:00Z",
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

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
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

    const tabla = await screen.findByRole("table");
    const filas = within(tabla).getAllByRole("row");
    expect(filas.map((f) => f.textContent)).toEqual([
      "Unidadexpensas_comunesTotal",
      expect.stringContaining("1° A"),
      expect.stringContaining("1° B"),
    ]);
    expect(within(tabla).getByText(/Total .*1\.000,00, repartido por unidad/)).toBeTruthy();
    expect(screen.getByText("No hay órdenes pagadas para agregar.")).toBeTruthy();
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

  it("sin la facultad lo dice, y un rechazo del backend se explica", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => error("sin_permiso", 403),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => error("sin_permiso", 403),
    }));
    abrir();
    expect(await screen.findByText(/La liquidación la prepara la administración/)).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones`]: () => Response.json({ elementos: [LIQUIDACION] }),
      [`GET ${BASE}/liquidaciones/ordenes-liquidables`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/liquidaciones`]: () => error("liquidacion_no_admite", 409),
    }));
    abrir();
    fireEvent.change(await screen.findByLabelText("Período"), { target: { value: "2026-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Abrir el borrador" }));
    expect(await screen.findByText(/ya hay un borrador de ese período/)).toBeTruthy();
  });
});
