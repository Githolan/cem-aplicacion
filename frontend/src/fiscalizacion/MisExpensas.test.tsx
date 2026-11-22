import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { CAMBIO_AL_LEER, GASTO_DEL_PERIODO, GASTO_POR_PERIODO, NO_FIGURA, RUTA_MIS_EXPENSAS, SIN_LIQUIDACION, segunLaLiquidacion } from "./MisExpensas";

// Mis expensas (H5 D3-A, app 0119). Datos SINTETICOS: ningún edificio, unidad ni importe real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const U2 = "33333333-3333-4333-8333-333333333333";
const BASE = `/api/v1/consorcios/${C1}`;

type MiCuenta = components["schemas"]["MiCuenta"];
type Resumen = components["schemas"]["ResumenLiquidacion"];

const PERIODOS = [
  { periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z" },
  { periodo: "2026-09-01", confirmada_en: "2026-10-05T15:00:00Z" },
];
// La importación oficial vigente de cada período.
const OFICIAL: Record<string, string> = { "2026-10-01": "44444444-4444-4444-8444-444444444444", "2026-09-01": "55555555-5555-4555-8555-555555555555" };
const REEMPLAZO = "66666666-6666-4666-8666-666666666666";
const unidad = (extra: Partial<components["schemas"]["UnidadEnLaLiquidacion"]>) => ({
  unidad_id: U1, designacion: "1-A", figura: true, saldo_anterior: "100.00", pagos: "100.00", deuda: "0.00",
  interes: "0.00", disponible: null, grupos: [], otros_gastos: null, redondeo: null, total: "1326.00", ...extra,
});  // prettier-ignore
const cuentaDe = (periodo: string, total: string): MiCuenta => ({
  periodos: PERIODOS,
  liquidacion: {
    importacion_id: OFICIAL[periodo] ?? "", periodo, confirmada_en: PERIODOS.find((p) => p.periodo === periodo)?.confirmada_en ?? "", vencimientos: ["13/10/2026", "23/10/2026"],
    unidades: [unidad({ total }), unidad({ unidad_id: U2, designacion: "1° A", figura: false, saldo_anterior: null, pagos: null, deuda: null, interes: null, total: null })],
  },
});  // prettier-ignore
const resumenDe = (periodo: string): Resumen => ({
  periodos: PERIODOS,
  liquidacion: {
    importacion_id: OFICIAL[periodo] ?? "", periodo, confirmada_en: PERIODOS.find((p) => p.periodo === periodo)?.confirmada_en ?? "",
    rubros: [{ numero: 1, nombre: "Servicios públicos SINTETICOS", total: "1500.50" }, { numero: 2, nombre: "Mantenimiento SINTETICO", total: "999.99" }],
    grupos: [{ nombre: "Gastos Comunes", total: "2000.00" }], total: "2500.49", vencimientos: ["13/10/2026", "23/10/2026"],
  },
  // La serie no depende del período elegido (app 0120).
  gasto_por_periodo: [{ periodo: "2026-09-01", total: "2300.00" }, { periodo: "2026-10-01", total: "2500.49" }],
});  // prettier-ignore

type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

const pedidos = (simulado: ReturnType<typeof vi.fn>, camino: string): Request[] =>
  simulado.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => new URL(p.url).pathname === camino);

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const PROPIETARIA = alcanza("cuenta.ver_propia", "fiscalizacion.ver_resumen");
const periodoDe = (p: Request) => new URL(p.url).searchParams.get("periodo") ?? "2026-10-01";

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_MIS_EXPENSAS]}>
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

describe("pantalla /mis-expensas", () => {
  it("muestra cada unidad propia según la liquidación de la administración y el gasto por rubro con su tabla", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIA,
      [`GET ${BASE}/mi-cuenta`]: (p) => Response.json(cuentaDe(periodoDe(p), periodoDe(p) === "2026-09-01" ? "1200.00" : "1326.00")),
      [`GET ${BASE}/resumen`]: (p) => Response.json(resumenDe(periodoDe(p))),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    expect(await screen.findByText(segunLaLiquidacion("2026-10-01", "2026-11-05T15:00:00Z"))).toBeTruthy();
    expect(screen.getByText(/según la liquidación de la administración, confirmada el/)).toBeTruthy();
    const propia = screen.getByRole("region", { name: "Unidad 1-A" });
    expect(within(propia).getByText("$ 1.326,00")).toBeTruthy();
    expect(within(propia).getByText("Saldo anterior")).toBeTruthy();
    expect(within(propia).getByText("Interés")).toBeTruthy();
    // La unidad propia que no figura lo dice, sin importes.
    expect(within(screen.getByRole("region", { name: "Unidad 1° A" })).getByText(NO_FIGURA)).toBeTruthy();
    // Los vencimientos, una sola vez.
    expect(screen.getAllByText("Vencimientos: 13/10/2026, 23/10/2026")).toHaveLength(1);
    // El gráfico lleva su tabla equivalente, con los mismos valores.
    const grafico = screen.getByRole("region", { name: GASTO_DEL_PERIODO });
    const tabla = within(grafico).getByRole("table", { name: GASTO_DEL_PERIODO });
    expect(within(tabla).getByRole("rowheader", { name: "Mantenimiento SINTETICO" })).toBeTruthy();
    expect(within(tabla).getByText("$ 999,99")).toBeTruthy();
    expect(screen.getByText("Total del gasto del período: $ 2.500,49")).toBeTruthy();
    // El gasto por período (app 0120), con su tabla equivalente: del más viejo al más nuevo.
    const serie = screen.getByRole("region", { name: GASTO_POR_PERIODO });
    const tablaSerie = within(serie).getByRole("table", { name: GASTO_POR_PERIODO });
    expect(within(tablaSerie).getAllByRole("row").slice(1).map((f) => f.textContent)).toEqual([
      "septiembre de 2026$ 2.300,00",
      "octubre de 2026$ 2.500,49",
    ]);
    // Sin diferencias ni facturas faltantes (RN33): eso es del panel completo.
    expect(screen.queryByText(/no cierra|diferencia|faltante/i)).toBeNull();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
    // Otro período: se pide con el período elegido.
    fireEvent.change(screen.getByLabelText("Período"), { target: { value: "2026-09-01" } });
    expect(await within(await screen.findByRole("region", { name: "Unidad 1-A" })).findByText("$ 1.200,00")).toBeTruthy();
    expect(pedidos(api, `${BASE}/mi-cuenta`).some((p) => periodoDe(p) === "2026-09-01")).toBe(true);
    expect(pedidos(api, `${BASE}/resumen`).some((p) => periodoDe(p) === "2026-09-01")).toBe(true);
  });

  it("sin liquidación confirmada lo dice, sin selector ni gráfico", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIA,
      [`GET ${BASE}/mi-cuenta`]: () => Response.json({ periodos: [], liquidacion: null }),
      [`GET ${BASE}/resumen`]: () => Response.json({ periodos: [], liquidacion: null, gasto_por_periodo: [] }),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText(SIN_LIQUIDACION)).toBeTruthy();
    expect(screen.queryByLabelText("Período")).toBeNull();
    expect(screen.queryByRole("region", { name: GASTO_DEL_PERIODO })).toBeNull();
    expect(screen.queryByRole("region", { name: GASTO_POR_PERIODO })).toBeNull();
  });

  it("con solo el resumen, no pide la cuenta de ninguna unidad", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("fiscalizacion.ver_resumen"),
      [`GET ${BASE}/resumen`]: (p) => Response.json(resumenDe(periodoDe(p))),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("region", { name: GASTO_DEL_PERIODO })).toBeTruthy();
    expect(pedidos(api, `${BASE}/mi-cuenta`)).toHaveLength(0);
    expect(screen.queryByRole("region", { name: /^Unidad / })).toBeNull();
    expect(screen.getByText("Vencimientos: 13/10/2026, 23/10/2026")).toBeTruthy();
  });

  it("el resumen se pide del período de la cuenta; si describe otra liquidación, se lee una vez más", async () => {
    // Entre la primera cuenta y el primer resumen se reemplazó la de octubre: el segundo par coincide.
    let resumenes = 0;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIA,
      [`GET ${BASE}/mi-cuenta`]: (p) => Response.json(cuentaDe(periodoDe(p), "1326.00")),
      [`GET ${BASE}/resumen`]: (p) => {
        resumenes += 1;
        const r = resumenDe(periodoDe(p));
        return Response.json(resumenes === 1 && r.liquidacion !== null ? { ...r, liquidacion: { ...r.liquidacion, importacion_id: REEMPLAZO } } : r);
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("region", { name: GASTO_DEL_PERIODO })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Unidad 1-A" })).toBeTruthy();
    expect(screen.queryByText(CAMBIO_AL_LEER)).toBeNull();
    expect(pedidos(api, `${BASE}/resumen`).length).toBeGreaterThanOrEqual(2);
    // El primer resumen ya va con el período que trajo la cuenta, aunque la persona no eligió ninguno.
    expect(new URL(pedidos(api, `${BASE}/resumen`)[0]?.url ?? "").searchParams.get("periodo")).toBe("2026-10-01");
  });

  it("si la cuenta y el resumen siguen describiendo liquidaciones distintas, avisa que se recargue y no mezcla datos", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIA,
      [`GET ${BASE}/mi-cuenta`]: (p) => Response.json(cuentaDe(periodoDe(p), "1326.00")),
      [`GET ${BASE}/resumen`]: (p) => {
        const r = resumenDe(periodoDe(p));
        return Response.json(r.liquidacion === null ? r : { ...r, liquidacion: { ...r.liquidacion, importacion_id: REEMPLAZO } });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", CAMBIO_AL_LEER);
    expect(screen.queryByRole("region", { name: /^Unidad / })).toBeNull();
    expect(screen.queryByRole("region", { name: GASTO_DEL_PERIODO })).toBeNull();
    // Sin bucle: una lectura más y nada después.
    const vistos = pedidos(api, `${BASE}/resumen`).length;
    await new Promise((listo) => setTimeout(listo, 300));
    expect(pedidos(api, `${BASE}/resumen`)).toHaveLength(vistos);
    expect(pedidos(api, `${BASE}/mi-cuenta`)).toHaveLength(vistos);
  });

  it("sin ninguna de las dos acciones, la dirección lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("documentos.ver"),
    }));  // prettier-ignore
    abrir();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Mis expensas" })).toBeNull());
  });
});
