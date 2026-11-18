import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { Link, MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { ContextoVersion } from "../version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "abababab-abab-4bab-8bab-abababababab";
const AZUL = { id: C1, nombre: "Edificio Azul", vinculos: [] };
const VERDE = { id: C2, nombre: "Edificio Verde", vinculos: [] };
const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: "ana@ejemplo.com",
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};
const SALUD = { estado: "ok", version: "0.1.0", sha_imagen: "x", sha_contratos: "", version_contrato: "0.1.0" };
const ADMINISTRA = ["egresos.preparar_orden", "contactos.ver"];
const CONSEJO = ["egresos.revisar_orden", "egresos.conciliar_pago", "contactos.ver"];
const TOTP_ACTIVO = { activo: true, activado_en: "2026-10-15T20:00:00Z", codigos_recuperacion_restantes: 10, verificado_en: null };

const bandejaDe = (id: string) => `/api/v1/consorcios/${id}/pagos/bandeja`;

function elemento(n: number, grupo: string) {
  return {
    orden_id: `3333333${n}-3333-4333-8333-333333333333`,
    proveedor: `Proveedor ${n}`,
    concepto: "Abono",
    importe_centavos: 100_000,
    moneda: "ARS",
    paso: grupo === "esperando" ? "espera_conciliar" : "aprobar",
    grupo,
    le_toca_a: grupo === "esperando" ? "consejo" : "vos",
    // Si cuenta como pendiente lo dice el servidor con la tabla de la pantalla (tanda 2).
    pendiente: grupo !== "esperando",
    situacion: grupo === "esperando" ? "falta_conciliar" : "en_revision",
    estado_visible: grupo === "esperando" ? "falta_conciliar" : "esperando_aprobacion",
    principal: grupo === "esperando" ? null : "aprobar",
    creada_en: "2026-11-09T15:00:00Z",
    actualizada_en: "2026-11-09T15:00:00Z",
    posible_duplicada_de: null,
  };
}

const conElementos = (...grupos: string[]) => () =>
  Response.json({ elementos: grupos.map((g, i) => elemento(i, g)), avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 });
/** Pagos conciliados sin cotejar: el servidor los cuenta solo para quien coteja (P-24). */
const conPorCotejar = (n: number) => () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: n, sin_explicar: 0 });

type Respuesta = () => Response;

function backend({
  consorcios = [AZUL],
  acciones = {},
  bandejas = {},
  otras = {},
}: {
  consorcios?: object[];
  acciones?: Record<string, readonly string[]>;
  bandejas?: Record<string, Respuesta>;
  otras?: Record<string, Respuesta>;
}) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    const clave = `${pedido.method} ${camino}`;
    const otra = otras[clave];
    if (otra !== undefined) return otra();
    if (camino === "/api/v1/auth/sesion") return Response.json(SESION);
    if (camino === "/health") return Response.json(SALUD);
    if (camino === "/api/v1/me/consorcios") return Response.json(consorcios);
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: [] });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    const deAcciones = /^\/api\/v1\/consorcios\/([^/]+)\/mis-acciones$/.exec(camino);
    if (deAcciones !== null) return Response.json({ acciones: acciones[deAcciones[1] ?? ""] ?? [] });
    const deBandeja = /^\/api\/v1\/consorcios\/([^/]+)\/pagos\/bandeja$/.exec(camino);
    const bandeja = deBandeja === null ? undefined : bandejas[deBandeja[1] ?? ""];
    if (bandeja !== undefined) return bandeja();
    throw new Error(`pedido no previsto: ${clave}`);
  });
}

function pedidosA(simulado: ReturnType<typeof backend>, camino: string): number {
  return simulado.mock.calls.filter((c) => new URL((c[0] as Request).url).pathname === camino).length;
}

function abrir(entrada: string | { pathname: string; state: unknown } = "/") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[entrada]}>
            <App />
            <Link to="/cuenta/segundo-factor" state={{ volver: "/pagos" }}>
              Ir al alta
            </Link>
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}


beforeEach(() => {
  // Sin ancho de PC: la ficha no se abre sola al lado de la bandeja.
  vi.stubGlobal("matchMedia", (consulta: string) => ({ matches: false, media: consulta, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("volver del alta de la verificación", () => {
  const conAlta = () =>
    backend({
      consorcios: [AZUL, VERDE],
      acciones: { [C1]: CONSEJO, [C2]: CONSEJO },
      bandejas: { [C1]: conElementos(), [C2]: conElementos() },
      otras: {
        "GET /api/v1/auth/totp": () => Response.json(TOTP_ACTIVO),
        "POST /api/v1/auth/totp/baja": () => new Response(null, { status: 204 }),
      },
    });

  async function darDeAltaYVolver() {
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await screen.findByText("Nada pendiente.");
    fireEvent.click(screen.getByRole("link", { name: "Ir al alta" }));
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar la confirmación con código" }));
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar" }));
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await screen.findByText("Listo: la confirmación con código quedó desactivada.");
  }

  it("sigue en el edificio con el que había llegado", async () => {
    const simulado = conAlta();
    vi.stubGlobal("fetch", simulado);
    abrir({ pathname: "/pagos", state: { consorcio: C2 } });

    await screen.findByText("Nada pendiente.");
    const antes = pedidosA(simulado, bandejaDe(C2));
    expect(antes).toBeGreaterThan(0);
    await darDeAltaYVolver();
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C2))).toBeGreaterThan(antes));
    expect(pedidosA(simulado, bandejaDe(C1))).toBe(0);
  });

  it("control: si llegó sin edificio, sigue en el primero", async () => {
    const simulado = conAlta();
    vi.stubGlobal("fetch", simulado);
    abrir("/pagos");

    await screen.findByText("Nada pendiente.");
    const antes = pedidosA(simulado, bandejaDe(C1));
    expect(antes).toBeGreaterThan(0);
    await darDeAltaYVolver();
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C1))).toBeGreaterThan(antes));
    expect(pedidosA(simulado, bandejaDe(C2))).toBe(0);
  });
});

describe("salidas de la bandeja según el rol", () => {
  // «Proveedores» también está en la navegación: se mira el de la bandeja, que es el último.
  const clase = (nombre: string) => screen.getAllByRole("link", { name: nombre }).at(-1)?.classList;
  // Comentario 24 (09-11-2026): un solo botón lleno; los demás con borde, sin relleno.
  const principal = (nombre: string) => clase(nombre)?.contains("enlace-boton--principal");
  const conBorde = (nombre: string) => clase(nombre)?.contains("enlace-boton--borde");

  it("quien prepara: «Preparar una orden» manda; consultar y proveedores van detrás", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: conElementos() } }));
    abrir("/pagos");

    await screen.findByRole("link", { name: "Consultar pagos" });
    await screen.findByRole("link", { name: "Preparar una orden" });
    expect(principal("Preparar una orden")).toBe(true);
    expect(conBorde("Preparar una orden")).toBe(false);
    expect(conBorde("Consultar pagos")).toBe(true);
    expect(conBorde("Proveedores")).toBe(true);
    expect(principal("Consultar pagos")).toBe(false);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Cotejar con el banco" })).toBeNull());
  });

  it("quien concilia, con pagos por cotejar: una fila lleva al cotejo (BAN-07) y no ofrece preparar", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conPorCotejar(2) } }));
    abrir("/pagos");

    const fila = await screen.findByRole("link", { name: "Para cotejar 2 pagos" });
    expect(fila.getAttribute("href")).toBe("/pagos/cotejo");
    expect(fila.classList.contains("fila-registro")).toBe(true);
    expect(principal("Consultar pagos")).toBe(false);
    expect(conBorde("Consultar pagos")).toBe(true);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Preparar una orden" })).toBeNull());
  });
});
