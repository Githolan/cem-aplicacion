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
import { respondido } from "./FichaReclamo";
import { ATENDIDOS, RUTA_RECLAMOS, TITULO_RECLAMOS } from "./Reclamos";
import { ATIENDE_RECLAMO, PREPARAR } from "./Solicitudes";
import { PASAR_A_SOLICITUD, RESPONDER_RECLAMO, RETIRAR_RECLAMO, TEXTO_CON_CUENTA } from "./textosReclamos";

// «Reclamos sobre la liquidación», para quien atiende (RF34, app 0123). Datos SINTETICOS: ningún
// edificio, unidad ni persona real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const OFICIAL = "44444444-4444-4444-8444-444444444444";
const R1 = "77777777-7777-4777-8777-777777777777";
const R2 = "88888888-8888-4888-8888-888888888888";
const BASE = `/api/v1/consorcios/${C1}`;
const RECLAMOS = `${BASE}/reclamos-liquidacion`;
const TEXTO = "El saldo anterior no coincide. PRUEBA SINTETICA.";

type Reclamo = components["schemas"]["Reclamo"];

const ABIERTO: Reclamo = {
  id: R1, liquidacion_id: OFICIAL, periodo: "2026-10-01", liquidacion_vigente: true, unidad_id: U1, designacion: "1-A",
  campo: "saldo_unidad", presentado_por_nombre: "Beto SINTETICO", presentado_en: "2026-11-22T10:00:00Z", estado: "presentado",
  propio: false, texto: TEXTO, acciones: ["responder", "pasar_a_solicitud"],
  historia: [{ estado: "presentado", texto: null, solicitud_id: null, solicitud_numero: null, registrado_por_nombre: "Beto SINTETICO", registrado_en: "2026-11-22T10:00:00Z" }],
};  // prettier-ignore
const RESPONDIDO: Reclamo = {
  ...ABIERTO, id: R2, estado: "respondido", acciones: [],
  historia: [...ABIERTO.historia, { estado: "respondido", texto: "Se revisó.", solicitud_id: null, solicitud_numero: null, registrado_por_nombre: "Ana Consejo", registrado_en: "2026-11-22T12:00:00Z" }],
};  // prettier-ignore
const resumen = ({ id, liquidacion_id, periodo, liquidacion_vigente, unidad_id, designacion, campo, presentado_por_nombre, presentado_en, estado, propio }: Reclamo) =>
  ({ id, liquidacion_id, periodo, liquidacion_vigente, unidad_id, designacion, campo, presentado_por_nombre, presentado_en, estado, propio });  // prettier-ignore
const LISTA = { elementos: [ABIERTO, RESPONDIDO].map(resumen), atiende: true, largo_texto: 2000, largo_motivo: 500 };
const SOLICITUDES = { elementos: [], puede_preparar: true, plazo_por_defecto_dias: 15, plazo_minimo_dias: 5, plazo_maximo_dias: 60, hay_administrador_vigente: true };

type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

const pedidos = (simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] =>
  simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const CONSEJO = alcanza("reclamos_liquidacion.atender", "reclamos_liquidacion.ver", "solicitud_formal.preparar", "fiscalizacion.ver_panel");
const error = (estado: number, codigo: string) => Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status: estado });

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_RECLAMOS]}>
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

describe("pantalla /reclamos", () => {
  it("lista los presentados arriba y los atendidos plegados; la ficha ofrece solo lo que dice la API", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: CONSEJO,
        [`GET ${RECLAMOS}`]: () => Response.json(LISTA),
        [`GET ${RECLAMOS}/${R2}`]: () => Response.json(RESPONDIDO),
      }),
    );
    const { container } = abrir();
    const abiertos = await screen.findByRole("list", { name: TITULO_RECLAMOS });
    expect(within(abiertos).getAllByRole("button")).toHaveLength(1);
    expect(within(abiertos).getByText(/Beto SINTETICO/)).toBeTruthy();
    expect(screen.getByText(`${ATENDIDOS} (1)`)).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
    // El respondido: sin acciones, con la respuesta en la historia.
    fireEvent.click(within(screen.getByRole("list", { name: ATENDIDOS, hidden: true })).getByRole("button", { hidden: true }));
    const ficha = await screen.findByRole("dialog");
    expect(within(ficha).getByText("Se revisó.")).toBeTruthy();
    expect(within(ficha).queryByRole("button", { name: RESPONDER_RECLAMO })).toBeNull();
    expect(within(ficha).queryByRole("button", { name: RETIRAR_RECLAMO })).toBeNull();
  });

  it("responde en su ventana: sin texto lo marca, con una cuenta lo rechaza y con texto lo registra", async () => {
    let envios = 0;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${RECLAMOS}`]: () => Response.json(LISTA),
      [`GET ${RECLAMOS}/${R1}`]: () => Response.json(ABIERTO),
      [`POST ${RECLAMOS}/${R1}/respuesta`]: () => {
        envios += 1;
        return envios === 1 ? error(422, "texto_con_cuenta") : Response.json({ ...ABIERTO, estado: "respondido", acciones: [] });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(within(await screen.findByRole("list", { name: TITULO_RECLAMOS })).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    fireEvent.click(within(ficha).getByRole("button", { name: RESPONDER_RECLAMO }));
    const v = await screen.findByRole("dialog", { name: RESPONDER_RECLAMO });
    fireEvent.click(within(v).getByRole("button", { name: "Responder" }));
    expect(await within(v).findByText("Falta escribir la respuesta.")).toBeTruthy();
    expect(pedidos(api, "POST", `${RECLAMOS}/${R1}/respuesta`)).toHaveLength(0);
    fireEvent.change(within(v).getByLabelText("Respuesta"), { target: { value: "Pagar a la cuenta 0000000000000000000000" } });
    fireEvent.click(within(v).getByRole("button", { name: "Responder" }));
    expect((await within(v).findAllByText(TEXTO_CON_CUENTA)).length).toBeGreaterThan(0);
    fireEvent.change(within(v).getByLabelText("Respuesta"), { target: { value: "Se revisó el pago." } });
    fireEvent.click(within(v).getByRole("button", { name: "Responder" }));
    expect(await screen.findByText(respondido)).toBeTruthy();
    const [primero, segundo] = pedidos(api, "POST", `${RECLAMOS}/${R1}/respuesta`);
    expect(await segundo?.json()).toEqual({ texto: "Se revisó el pago." });
    // Otro texto es otro pedido: otra clave.
    expect(segundo?.headers.get("Idempotency-Key")).not.toBe(primero?.headers.get("Idempotency-Key"));
  });

  it("«Pasar a solicitud formal» abre «Preparar una solicitud» redactada y la prepara citando el reclamo", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${RECLAMOS}`]: () => Response.json(LISTA),
      [`GET ${RECLAMOS}/${R1}`]: () => Response.json(ABIERTO),
      [`GET ${BASE}/solicitudes-formales`]: () => Response.json(SOLICITUDES),
      [`POST ${BASE}/solicitudes-formales`]: () => error(409, "reclamo_cerrado"),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(within(await screen.findByRole("list", { name: TITULO_RECLAMOS })).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    fireEvent.click(within(ficha).getByRole("button", { name: PASAR_A_SOLICITUD }));
    const v = await screen.findByRole("dialog", { name: PREPARAR });
    expect(within(v).getByText(ATIENDE_RECLAMO)).toBeTruthy();
    expect((within(v).getByLabelText(/Documentación o aclaración/) as HTMLTextAreaElement).value).toContain(TEXTO);
    fireEvent.click(within(v).getByRole("button", { name: "Guardar el borrador" }));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/solicitudes-formales`)).toHaveLength(1));
    const cuerpo = (await pedidos(api, "POST", `${BASE}/solicitudes-formales`)[0]?.json()) as Record<string, unknown>;
    expect(cuerpo.reclamo_id).toBe(R1);
    expect(cuerpo.referencia).toBe("Liquidación de 10/2026, unidad 1-A");
  });

  it("quien atiende sin preparar solicitudes no ve «Pasar a solicitud formal»", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: alcanza("reclamos_liquidacion.atender", "reclamos_liquidacion.ver"),
        [`GET ${RECLAMOS}`]: () => Response.json(LISTA),
        [`GET ${RECLAMOS}/${R1}`]: () => Response.json({ ...ABIERTO, acciones: ["responder"] }),
      }),
    );
    abrir();
    fireEvent.click(within(await screen.findByRole("list", { name: TITULO_RECLAMOS })).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    expect(within(ficha).getByRole("button", { name: RESPONDER_RECLAMO })).toBeTruthy();
    expect(within(ficha).queryByRole("button", { name: PASAR_A_SOLICITUD })).toBeNull();
  });
});
