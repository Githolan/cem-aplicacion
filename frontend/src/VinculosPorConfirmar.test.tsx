import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ContextoVersion } from "./version";

// G-26 (sí del propietario del 14-11-2026): el inicio de quien confirma vínculos dice cuántos esperan.

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
const CONFIRMA = ["padron.ver", "padron.confirmar_vinculo"];
const SOLO_VE = ["padron.ver"];
const PREPARA = ["egresos.preparar_orden"];
const DETALLE_DE_SIEMPRE = "Confirmar o finalizar vínculos";

const padronDe = (id: string) => `/api/v1/consorcios/${id}/padron`;
const filas = (sinConfirmar: number, confirmadas: number) => () =>
  Response.json({
    elementos: [
      ...Array.from({ length: sinConfirmar }, () => ({ confirmado: false })),
      ...Array.from({ length: confirmadas }, () => ({ confirmado: true })),
    ],
  });
const vacia = () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 });

type Respuesta = () => Response;

function backend({
  consorcios = [AZUL],
  acciones = {},
  padrones = {},
}: {
  consorcios?: object[];
  acciones?: Record<string, readonly string[]>;
  padrones?: Record<string, Respuesta>;
}) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/api/v1/auth/sesion") return Response.json(SESION);
    if (camino === "/health") return Response.json(SALUD);
    if (camino === "/api/v1/me/consorcios") return Response.json(consorcios);
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: [] });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    if (camino.endsWith("/pagos/bandeja")) return vacia();
    const deAcciones = /^\/api\/v1\/consorcios\/([^/]+)\/mis-acciones$/.exec(camino);
    if (deAcciones !== null) return Response.json({ acciones: acciones[deAcciones[1] ?? ""] ?? [] });
    const dePadron = /^\/api\/v1\/consorcios\/([^/]+)\/padron$/.exec(camino);
    const padron = dePadron === null ? undefined : padrones[dePadron[1] ?? ""];
    if (padron !== undefined) return padron();
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function pedidosA(simulado: ReturnType<typeof backend>, camino: string): number {
  return simulado.mock.calls.filter((c) => new URL((c[0] as Request).url).pathname === camino).length;
}

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/"]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

function conAncho(pc: boolean) {
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: pc && consulta === "(min-width: 64.0625em)",
    media: consulta,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

const filaDelPadron = (detalle: string | RegExp) => screen.findByRole("link", { name: "Padrón del edificio", description: detalle });

beforeEach(() => conAncho(false));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("inicio: vínculos que esperan confirmación (G-26)", () => {
  it("cuenta los que esperan, sin los confirmados", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONFIRMA }, padrones: { [C1]: filas(2, 3) } }));
    abrir();
    expect((await filaDelPadron("2 vínculos esperan confirmación")).getAttribute("href")).toBe("/padron");
  });

  it("con uno va en singular", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONFIRMA }, padrones: { [C1]: filas(1, 0) } }));
    abrir();
    expect(await filaDelPadron("1 vínculo espera confirmación")).toBeTruthy();
  });

  it("suma los edificios donde confirma, y no pide el de donde solo ve", async () => {
    const simulado = backend({
      consorcios: [AZUL, VERDE],
      acciones: { [C1]: CONFIRMA, [C2]: SOLO_VE },
      padrones: { [C1]: filas(2, 0), [C2]: filas(5, 0) },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await filaDelPadron("2 vínculos esperan confirmación")).toBeTruthy();
    expect(pedidosA(simulado, padronDe(C2))).toBe(0);
  });

  it("control: dos edificios donde confirma se suman", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ consorcios: [AZUL, VERDE], acciones: { [C1]: CONFIRMA, [C2]: CONFIRMA }, padrones: { [C1]: filas(2, 0), [C2]: filas(5, 1) } }),
    );
    abrir();
    expect(await filaDelPadron("7 vínculos esperan confirmación")).toBeTruthy();
  });

  it("sin ninguno esperando, la fila dice lo de siempre", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONFIRMA }, padrones: { [C1]: filas(0, 4) } }));
    abrir();
    expect(await filaDelPadron(DETALLE_DE_SIEMPRE)).toBeTruthy();
  });

  it("quien solo ve el padrón no recibe la cuenta ni la pide", async () => {
    const simulado = backend({ acciones: { [C1]: SOLO_VE }, padrones: { [C1]: filas(3, 0) } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await filaDelPadron(DETALLE_DE_SIEMPRE)).toBeTruthy();
    expect(pedidosA(simulado, padronDe(C1))).toBe(0);
  });

  it("si no se pudo leer lo dice, sin inventar un cero", async () => {
    const falla = () => Response.json({ codigo: "error_interno", mensaje: "x", id_correlacion: "c" }, { status: 500 });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONFIRMA }, padrones: { [C1]: falla } }));
    abrir();
    expect(await filaDelPadron("No se pudieron leer los vínculos sin confirmar")).toBeTruthy();
  });

  it("en la PC, con vínculos esperando, el Padrón entra en «Para hacer»", async () => {
    conAncho(true);
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: [...PREPARA, ...CONFIRMA] }, padrones: { [C1]: filas(2, 0) } }));
    abrir();
    const grupo = within(await screen.findByRole("region", { name: "Para hacer" }));
    expect(await grupo.findByRole("link", { name: "Padrón del edificio", description: "2 vínculos esperan confirmación" })).toBeTruthy();
  });

  it("control: en la PC, sin vínculos esperando, «Para hacer» no lo trae", async () => {
    conAncho(true);
    const simulado = backend({ acciones: { [C1]: [...PREPARA, ...CONFIRMA] }, padrones: { [C1]: filas(0, 2) } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const grupo = within(await screen.findByRole("region", { name: "Para hacer" }));
    await grupo.findByRole("link", { name: "Pagos" });
    await vi.waitFor(() => expect(pedidosA(simulado, padronDe(C1))).toBeGreaterThan(0));
    expect(grupo.queryByRole("link", { name: "Padrón del edificio" })).toBeNull();
  });
});
