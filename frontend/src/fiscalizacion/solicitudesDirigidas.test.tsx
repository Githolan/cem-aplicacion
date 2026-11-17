import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Navegacion } from "../Navegacion";
import { edificiosDeDestinatario } from "./solicitudesDirigidas";
import { TITULO_SOLICITUDES } from "./Solicitudes";

// MEN-04 (informe de fricción del 17-11-2026, tanda 8): «Solicitudes formales» en el menú también para
// el destinatario que tiene alguna dirigida a él, y para nadie más sin el panel. Datos ficticios.

type Consulta = {
  porEdificio: ReadonlyMap<string, readonly string[]> | null;
  consultando: boolean;
  falla: string | null;
  reintentar: () => void;
  reintentando: boolean;
};

const consulta = vi.hoisted(() => ({ actual: null as unknown as Consulta }));

vi.mock("../permisos", async (original) => ({
  ...(await original<typeof import("../permisos")>()),
  useConsultaDeAcciones: () => consulta.actual,
}));

const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const DESTINATARIO = ["documentos.ver", "solicitud_formal.responder"];
const CON_PANEL = ["fiscalizacion.ver_panel", "solicitud_formal.responder"];

const sabido = (porEdificio: ReadonlyMap<string, readonly string[]>): Consulta => ({
  porEdificio,
  consultando: false,
  falla: null,
  reintentar: vi.fn(),
  reintentando: false,
});

/** La API de solicitudes por edificio: cuántas ve la persona en cada uno. */
function conSolicitudes(cantidad: Record<string, number>) {
  const api = vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    const consorcio = /^\/api\/v1\/consorcios\/([^/]+)\/solicitudes-formales$/.exec(camino)?.[1];
    if (pedido.method !== "GET" || consorcio === undefined) throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
    return Response.json({
      elementos: Array.from({ length: cantidad[consorcio] ?? 0 }, () => ({})),
      puede_preparar: false,
      plazo_por_defecto_dias: 15,
      plazo_minimo_dias: 5,
      plazo_maximo_dias: 60,
    });
  });
  vi.stubGlobal("fetch", api);
  return api;
}

function menu() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Navegacion />
    </MemoryRouter>,
  );
  return screen.getByRole("navigation", { name: "Secciones" });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MEN-04: «Solicitudes formales» para el destinatario", () => {
  it("se lee solo donde la persona abre la pantalla sin ver el panel", () => {
    expect(edificiosDeDestinatario(new Map([[C2, DESTINATARIO], [C1, CON_PANEL], ["c3", ["documentos.ver"]]]))).toEqual([C2]);
    expect(edificiosDeDestinatario(null)).toEqual([]);
  });

  it("con una solicitud dirigida a la persona, el menú la ofrece", async () => {
    const api = conSolicitudes({ [C1]: 1 });
    consulta.actual = sabido(new Map([[C1, DESTINATARIO]]));
    const navegacion = menu();
    expect((await within(navegacion).findByRole("link", { name: TITULO_SOLICITUDES })).getAttribute("href")).toBe("/solicitudes");
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("control: sin ninguna, el menú no la ofrece", async () => {
    const api = conSolicitudes({ [C1]: 0 });
    consulta.actual = sabido(new Map([[C1, DESTINATARIO]]));
    const navegacion = menu();
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    // La lectura terminó y la entrada sigue sin estar.
    await new Promise((listo) => setTimeout(listo, 0));
    expect(within(navegacion).queryByRole("link", { name: TITULO_SOLICITUDES })).toBeNull();
  });

  it("quien ve el panel la tiene siempre, sin leer las solicitudes", () => {
    const api = conSolicitudes({});
    consulta.actual = sabido(new Map([[C1, CON_PANEL]]));
    const navegacion = menu();
    expect(within(navegacion).getByRole("link", { name: TITULO_SOLICITUDES })).toBeTruthy();
    expect(api).not.toHaveBeenCalled();
  });
});
