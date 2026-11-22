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
import { REEMPLAZADA, RUTA_LIQUIDACION_OFICIAL, SIN_OFICIALES, VIGENTE } from "./LiquidacionOficial";

// Liquidación oficial, para el Consejo y la administración (H5 D3-A, app 0119). Datos SINTETICOS.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const A = "22222222-2222-4222-8222-222222222222";
const B = "33333333-3333-4333-8333-333333333333";
const U1 = "44444444-4444-4444-8444-444444444444";
const PERSONA = "88888888-8888-4888-8888-888888888888";
const BASE = `/api/v1/consorcios/${C1}`;

type Oficial = components["schemas"]["LiquidacionOficialResumen"];
type Detalle = components["schemas"]["LiquidacionOficial"];
type Campo = components["schemas"]["CampoOficial"];

const VIEJA: Oficial = {
  id: A, periodo: "2026-09-01", formato: "mis_expensas", decidida_por: PERSONA, decidida_en: "2026-10-05T15:00:00Z",
  vigente: false, reemplaza_a: null, motivo_reemplazo: null, reemplazada_por: B, diferencias: 1,
};  // prettier-ignore
const NUEVA: Oficial = {
  ...VIEJA, id: B, decidida_en: "2026-10-20T15:00:00Z", vigente: true, reemplaza_a: A,
  motivo_reemplazo: "Corrigió el rubro 2", reemplazada_por: null, diferencias: 0,
};  // prettier-ignore
const campo = (orden: number, tipo: Campo["tipo"], clave: string, valor: Record<string, unknown>, extra: Partial<Campo> = {}): Campo => ({
  orden, tipo, clave, valor, unidad_id: null, designacion: null, ...extra,
});  // prettier-ignore
const CAMPOS: Campo[] = [
  campo(0, "total", "total:gastos", { grupos: { "Gastos Comunes": "2000.00" }, total: "2500.49", vencimientos: ["13/10/2026"] }),
  campo(1, "rubro", "rubro:1", { numero: 1, nombre: "Servicios SINTETICOS", porcentaje: "60.01", total: "1500.50" }),
  campo(2, "gasto", "gasto:1:1", { rubro: 1, concepto: "Luz SINTETICA", importes: {}, total: "1000.00" }),
  campo(3, "unidad", "unidad:002", { uf: "002", depto: "1-A", nombre: "Beto SINTETICO" }, { unidad_id: U1, designacion: "1-A" }),
  campo(4, "saldo_unidad", "saldo_unidad:002", { uf: "002", depto: "1-A", saldo_anterior: "100.00", pagos: "100.00" }, { unidad_id: U1, designacion: "1-A" }),
  campo(5, "importe_unidad", "importe_unidad:002", { uf: "002", depto: "1-A", total: "1326.00" }, { unidad_id: U1, designacion: "1-A" }),
  campo(6, "importe_unidad", "importe_unidad:009", { uf: "009", depto: "Z-9", total: "50.00" }),
];
const detalleDe = (o: Oficial, extra: Partial<Detalle> = {}): Detalle => ({
  ...o, diferencias: [], campos: CAMPOS, ...extra,
});  // prettier-ignore

type Respuestas = Record<string, () => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const CONSEJO = alcanza("liquidacion.ver_importada", "fiscalizacion.ver_panel");

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_LIQUIDACION_OFICIAL]}>
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

describe("pantalla /liquidacion-oficial", () => {
  it("lista la vigente y la reemplazada, y abre una con rubros, gastos, unidades y lo que no cierra", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${BASE}/liquidacion-oficial`]: () => Response.json([NUEVA, VIEJA]),
      [`GET ${BASE}/liquidacion-oficial/${B}`]: () => Response.json(detalleDe(NUEVA)),
    }));  // prettier-ignore
    const { container } = abrir();
    const lista = await screen.findByRole("list", { name: "Liquidaciones oficiales" });
    expect(within(lista).getByRole("button", { name: new RegExp(VIGENTE) })).toBeTruthy();
    expect(within(lista).getByRole("button", { name: new RegExp(`${REEMPLAZADA}`) }).textContent).toContain("1 sin cerrar");
    fireEvent.click(within(lista).getByRole("button", { name: new RegExp(VIGENTE) }));
    expect(await screen.findByRole("heading", { name: "Período septiembre de 2026" })).toBeTruthy();
    expect(screen.getByText(/Reemplazó a la liquidación confirmada el .*\. Motivo: Corrigió el rubro 2/)).toBeTruthy();
    const rubros = screen.getByRole("table", { name: "Rubros" });
    expect(within(rubros).getByRole("rowheader", { name: "1. Servicios SINTETICOS" })).toBeTruthy();
    expect(within(rubros).getByText("$ 1.500,50")).toBeTruthy();
    const gastos = screen.getByRole("table", { name: "Gastos" });
    expect(within(gastos).getByRole("rowheader", { name: "Luz SINTETICA" })).toBeTruthy();
    expect(within(gastos).getByText("Servicios SINTETICOS")).toBeTruthy();
    const unidades = screen.getByRole("table", { name: "Unidades" });
    // Una fila por unidad: la del padrón con su designación y la que no está, con su UF.
    expect(within(unidades).getAllByRole("row")).toHaveLength(3);
    expect(within(unidades).getByRole("rowheader", { name: "1-A" })).toBeTruthy();
    expect(within(unidades).getByRole("rowheader", { name: "UF 009 Z-9" })).toBeTruthy();
    expect(within(unidades).getByText("$ 1.326,00")).toBeTruthy();
    expect(screen.getByText("Las cuentas de la liquidación cierran.")).toBeTruthy();
    expect(screen.getByText("Total del gasto: $ 2.500,49")).toBeTruthy();
    // Solo lectura: no ofrece ningún acto sobre la liquidación.
    expect(screen.queryByRole("button", { name: /Confirmar|Reemplazar|Descartar/ })).toBeNull();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("la reemplazada se lee como historia, con lo que no cierra y el motivo de quien la reemplazó", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${BASE}/liquidacion-oficial`]: () => Response.json([NUEVA, VIEJA]),
      [`GET ${BASE}/liquidacion-oficial/${A}`]: () =>
        Response.json(detalleDe(VIEJA, { diferencias: [{ regla: "totales_gastos", detalle: "total de gastos", pagina: 1, renglon: "295.0", esperado: "1500.50", informado: "1500.55" }] })),
    }));  // prettier-ignore
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(REEMPLAZADA) }));
    expect(await screen.findByText(/Reemplazada por la confirmada el .*\. Motivo: Corrigió el rubro 2/)).toBeTruthy();
    const difs = screen.getByRole("list", { name: "Lo que no cierra" });
    expect(within(difs).getByText(/total de gastos \(página 1\): suma \$ 1\.500,50, dice \$ 1\.500,55/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Volver a la lista" }));
    expect(await screen.findByRole("list", { name: "Liquidaciones oficiales" })).toBeTruthy();
  });

  it("sin ninguna oficial lo dice", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${BASE}/liquidacion-oficial`]: () => Response.json([]),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText(SIN_OFICIALES)).toBeTruthy();
  });

  it("sin `liquidacion.ver_importada`, la dirección lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("cuenta.ver_propia", "fiscalizacion.ver_resumen"),
    }));  // prettier-ignore
    abrir();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Liquidación oficial" })).toBeNull());
  });
});
