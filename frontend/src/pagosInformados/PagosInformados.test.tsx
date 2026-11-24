import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza } from "../accionesDePrueba";
import { App } from "../App";
import { textoParaRevisar, textoPorCompletar, textoPorConfirmar } from "../inicio/pendientes";
import { ContextoVersion } from "../version";
import {
  ARCHIVO_DESCARTADO,
  COMPLETAR,
  CONFIRMAR,
  DESCARTAR,
  enPesos,
  INFORMAR,
  PAGO_RECHAZADO,
  POR_CONFIRMAR,
  POR_CORREO,
  pagoInformado,
  RECHAZAR,
  RUTA_COTEJO_PAGOS_INFORMADOS,
  RUTA_PAGOS_INFORMADOS,
  textoCotejo,
  TITULO_COTEJO,
} from "./textos";

// La pantalla de los pagos informados, sus fichas y sus ventanas (PI-S6, app 0129 a 0134), y el
// cotejo con la liquidación. Datos SINTETICOS: ningún edificio, unidad ni persona real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const U2 = "23232323-2323-4232-8232-232323232323";
const P1 = "33333333-3333-4333-8333-333333333333";
const P2 = "34343434-3434-4343-8343-343434343434";
const V1 = "44444444-4444-4444-8444-444444444444";
const D1 = "55555555-5555-4555-8555-555555555555";
const R1 = "66666666-6666-4666-8666-666666666666";
const BASE = `/api/v1/consorcios/${C1}`;
const PAGOS = `${BASE}/pagos-informados`;
const CORREO = `${PAGOS}/por-correo`;
const NUMERO = "000123456 SINTETICO";
/** Como lee la página Testing Library: el espacio duro de `Intl` («$ 45.000,00») queda común. */
const comoSeLee = (texto: string) => texto.replace(/\s+/g, " ");

type Pago = components["schemas"]["PagoInformado"];
type Recibido = components["schemas"]["PagoPorCorreo"];

const pago = (cambios: Partial<Pago> = {}): Pago => ({
  id: P1, unidad_id: U1, unidad_designacion: "1-A", importe: "45000.00", fecha_pago: "2026-10-10", medio: "transferencia",
  numero_operacion: NUMERO, comprobante_version_id: V1, comprobante_estado: "disponible", origen: "app", estado: "informado",
  corrige_a: null, corregido_por: null, informado_por_nombre: "Ana SINTETICA", informado_en: "2026-10-10T12:00:00Z",
  propio: true, historia: [], acciones: [], ...cambios,
});  // prettier-ignore
const RECIBIDO: Recibido = {
  id: R1, recibido_en: "2026-11-02T13:00:00Z", asunto: "Expensas de octubre SINTETICO", comprobante_version_id: V1,
  comprobante_tipo: "application/pdf", comprobante_estado: "disponible", leido: true, legible: true, importe: "52000.50",
  moneda: "ARS", numero_operacion: "777 SINTETICO", fecha_pago: "2026-11-01",
};  // prettier-ignore
const lista = (elementos: Pago[], cambios: Partial<components["schemas"]["ListaPagosInformados"]> = {}) => ({
  elementos, largo_numero_operacion: 60, largo_motivo: 500, unidades: [{ id: U1, designacion: "1-A" }],
  por_confirmar: elementos.filter((p) => p.acciones.includes("confirmar")).length, ...cambios,
});  // prettier-ignore
const correo = (elementos: Recibido[] = [], direccion: string | null = null) => () => Response.json({ elementos, direccion });

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
const VECINA = alcanza("pagos_informados.ver", "pagos_informados.informar");
const CONSEJO = alcanza("pagos_informados.ver", "pagos_informados.confirmar", "fiscalizacion.ver_panel");
const error = (estado: number, codigo: string, mensaje = "x") => Response.json({ codigo, mensaje, detalle: null, id_correlacion: "c" }, { status: estado });
const respuesta = (cambios: Partial<Pago> = {}, ya = false) => () => Response.json({ ...pago(cambios), ya_informado: ya }, { status: ya ? 200 : 201 });
const subida = () =>
  Response.json({ documento_id: D1, archivo: { id: V1, estado: "en_cuarentena", tipo: "application/pdf", tamano: 4, sha256: "x" } }, { status: 201 });

function abrir(ruta = RUTA_PAGOS_INFORMADOS) {
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

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("pantalla /pagos-informados", () => {
  it("quien informa ve sus pagos y lo recibido por correo; sin dirección configurada no la ofrece", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: VECINA,
        [`GET ${PAGOS}`]: () => Response.json(lista([pago(), pago({ id: P2, estado: "confirmado", fecha_pago: "2026-09-10" })])),
        [`GET ${CORREO}`]: correo([RECIBIDO]),
      }),
    );
    const { container } = abrir();
    expect(await screen.findByRole("button", { name: INFORMAR })).toBeTruthy();
    expect(screen.getByRole("heading", { name: POR_CORREO })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Expensas de octubre SINTETICO/ })).toBeTruthy();
    expect(screen.getByText("Ver los ya decididos (1)")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: POR_CONFIRMAR })).toBeNull();
    expect(screen.queryByText(/mandar el comprobante por correo/)).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("con la dirección configurada, la ofrece; sin unidades para informar, no hay «Informar un pago»", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: VECINA,
        [`GET ${PAGOS}`]: () => Response.json(lista([])),
        [`GET ${CORREO}`]: correo([], "pagos@cem-pagos-staging.holancloud.com"),
      }),
    );
    abrir();
    expect(await screen.findByText(/pagos@cem-pagos-staging\.holancloud\.com/)).toBeTruthy();
    cleanup();
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: CONSEJO,
        [`GET ${PAGOS}`]: () => Response.json(lista([], { unidades: [] })),
        [`GET ${CORREO}`]: correo([], "pagos@cem-pagos-staging.holancloud.com"),
      }),
    );
    abrir();
    expect(await screen.findByText("Todavía no hay pagos informados.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: INFORMAR })).toBeNull();
  });

  it("informar marca lo que falta, sube el comprobante de la unidad y lo cita en el pago", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: VECINA,
      [`GET ${PAGOS}`]: () => Response.json(lista([], { unidades: [{ id: U1, designacion: "1-A" }, { id: U2, designacion: "2-B" }] })),
      [`GET ${CORREO}`]: correo(),
      [`POST ${PAGOS}/comprobantes`]: subida,
      [`POST ${PAGOS}`]: respuesta(),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: INFORMAR }));
    const ventana = within(await screen.findByRole("dialog", { name: INFORMAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Informar el pago" }));
    expect(await ventana.findByText("Falta elegir la unidad.")).toBeTruthy();
    expect(ventana.getByText("Falta el importe.")).toBeTruthy();
    expect(ventana.getByText("Falta la fecha en que se pagó.")).toBeTruthy();
    expect(ventana.getByText("Falta el número de operación.")).toBeTruthy();
    expect(ventana.getByText("Falta el comprobante.")).toBeTruthy();
    expect(pedidos(api, "POST", PAGOS)).toHaveLength(0);
    fireEvent.change(ventana.getByLabelText("Unidad"), { target: { value: U2 } });
    fireEvent.change(ventana.getByLabelText("Importe pagado (en pesos)"), { target: { value: "45.000,50" } });
    fireEvent.change(ventana.getByLabelText("Fecha en que se pagó"), { target: { value: "2026-10-10" } });
    fireEvent.change(ventana.getByLabelText("Número de operación"), { target: { value: NUMERO } });
    fireEvent.change(ventana.getByLabelText("Archivo del comprobante", { selector: "input" }), {
      target: { files: [new File(["%PDF"], "comprobante.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(ventana.getByRole("button", { name: "Informar el pago" }));
    expect(await screen.findByText(pagoInformado(false))).toBeTruthy();
    const [comprobante] = pedidos(api, "POST", `${PAGOS}/comprobantes`);
    expect(new URL(comprobante?.url ?? "").searchParams.get("unidad_id")).toBe(U2);
    expect(comprobante?.headers.get("Content-Type")).toBe("application/pdf");
    const [informe] = pedidos(api, "POST", PAGOS);
    expect(informe?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await informe?.json()).toEqual({
      unidad_id: U2, importe: "45000.50", fecha_pago: "2026-10-10", medio: "transferencia", numero_operacion: NUMERO,
      comprobante_version_id: V1, corrige_a: null,
    });  // prettier-ignore
  });

  it("en efectivo no pide comprobante ni número, y un pago ya informado se dice como el mismo", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: VECINA,
      [`GET ${PAGOS}`]: () => Response.json(lista([])),
      [`GET ${CORREO}`]: correo(),
      [`POST ${PAGOS}`]: respuesta({}, true),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: INFORMAR }));
    const ventana = within(await screen.findByRole("dialog", { name: INFORMAR }));
    expect(ventana.getByText("Unidad 1-A")).toBeTruthy();
    fireEvent.change(ventana.getByLabelText("Medio de pago"), { target: { value: "efectivo" } });
    expect(ventana.queryByLabelText("Archivo del comprobante", { selector: "input" })).toBeNull();
    fireEvent.change(ventana.getByLabelText("Importe pagado (en pesos)"), { target: { value: "1000" } });
    fireEvent.change(ventana.getByLabelText("Fecha en que se pagó"), { target: { value: "2026-10-10" } });
    fireEvent.click(ventana.getByRole("button", { name: "Informar el pago" }));
    expect(await screen.findByText(pagoInformado(true))).toBeTruthy();
    const [informe] = pedidos(api, "POST", PAGOS);
    expect(await informe?.json()).toEqual({
      unidad_id: U1, importe: "1000.00", fecha_pago: "2026-10-10", medio: "efectivo", numero_operacion: null,
      comprobante_version_id: null, corrige_a: null,
    });  // prettier-ignore
  });

  it("el Consejo ve los que esperan, la ficha ofrece solo sus acciones y rechazar pide el motivo", async () => {
    const ajeno = pago({ propio: false, acciones: ["confirmar", "rechazar"], informado_por_nombre: "Bruno SINTETICO" });
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${PAGOS}`]: () => Response.json(lista([ajeno], { unidades: [] })),
      [`GET ${CORREO}`]: correo(),
      [`POST ${PAGOS}/${P1}/rechazo`]: () => Response.json(pago({ estado: "rechazado" })),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    const seccion = within((await screen.findByRole("heading", { name: POR_CONFIRMAR })).closest("section") as HTMLElement);
    fireEvent.click(seccion.getByRole("button", { name: (nombre) => nombre.startsWith(`Unidad 1-A · ${enPesos("45000.00")}`) && nombre.includes("Bruno SINTETICO") }));
    const ficha = within(await screen.findByRole("dialog"));
    expect(ficha.getByText("Bruno SINTETICO", { exact: false })).toBeTruthy();
    expect(ficha.getByRole("button", { name: CONFIRMAR })).toBeTruthy();
    expect(ficha.queryByRole("button", { name: "Corregir el pago" })).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(ficha.getByRole("button", { name: RECHAZAR }));
    const ventana = within(await screen.findByRole("dialog", { name: RECHAZAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Rechazar" }));
    expect(await ventana.findByText(/Falta escribir el motivo/)).toBeTruthy();
    expect(pedidos(api, "POST", `${PAGOS}/${P1}/rechazo`)).toHaveLength(0);
    fireEvent.change(ventana.getByLabelText("Motivo"), { target: { value: "No corresponde a esta unidad. SINTETICO" } });
    fireEvent.click(ventana.getByRole("button", { name: "Rechazar" }));
    expect(await screen.findByText(PAGO_RECHAZADO)).toBeTruthy();
    const [rechazo] = pedidos(api, "POST", `${PAGOS}/${P1}/rechazo`);
    expect(await rechazo?.json()).toEqual({ motivo: "No corresponde a esta unidad. SINTETICO" });
  });

  it("el rechazo del servidor se dice con su texto", async () => {
    const ajeno = pago({ propio: false, acciones: ["confirmar", "rechazar"] });
    const texto = "El antivirus todavía revisa el comprobante: el pago se confirma cuando lo acepte.";
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: CONSEJO,
        [`GET ${PAGOS}`]: () => Response.json(lista([ajeno], { unidades: [] })),
        [`GET ${CORREO}`]: correo(),
        [`POST ${PAGOS}/${P1}/confirmacion`]: () => error(409, "comprobante_en_revision", texto),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Unidad 1-A/ }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: CONFIRMAR }));
    const ventana = within(await screen.findByRole("dialog", { name: CONFIRMAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Sí, confirmar" }));
    expect(await ventana.findByText(texto)).toBeTruthy();
  });

  it("lo recibido por correo se completa con lo leído como propuesta, sin subir otro archivo", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: VECINA,
      [`GET ${PAGOS}`]: () => Response.json(lista([])),
      [`GET ${CORREO}`]: correo([RECIBIDO]),
      [`POST ${CORREO}/${R1}/informe`]: respuesta({ origen: "correo" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Expensas de octubre SINTETICO/ }));
    const ficha = within(await screen.findByRole("dialog"));
    expect(ficha.getByText(comoSeLee(enPesos("52000.50")))).toBeTruthy();
    fireEvent.click(ficha.getByRole("button", { name: COMPLETAR }));
    const ventana = within(await screen.findByRole("dialog", { name: COMPLETAR }));
    expect(ventana.queryByLabelText("Archivo del comprobante", { selector: "input" })).toBeNull();
    expect((ventana.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("777 SINTETICO");
    fireEvent.click(ventana.getByRole("button", { name: "Informar el pago" }));
    expect(await screen.findByText(pagoInformado(false))).toBeTruthy();
    const [informe] = pedidos(api, "POST", `${CORREO}/${R1}/informe`);
    expect(await informe?.json()).toEqual({
      unidad_id: U1, importe: "52000.50", fecha_pago: "2026-11-01", medio: "transferencia", numero_operacion: "777 SINTETICO",
    });  // prettier-ignore
    expect(pedidos(api, "POST", `${PAGOS}/comprobantes`)).toHaveLength(0);
  });

  it("descartar lo recibido pregunta antes y no informa nada", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: VECINA,
      [`GET ${PAGOS}`]: () => Response.json(lista([])),
      [`GET ${CORREO}`]: correo([RECIBIDO]),
      [`POST ${CORREO}/${R1}/descarte`]: () => Response.json({ id: R1, estado: "descartado" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Expensas de octubre SINTETICO/ }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: DESCARTAR }));
    const ventana = within(await screen.findByRole("dialog", { name: DESCARTAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Sí, descartar" }));
    expect(await screen.findByText(ARCHIVO_DESCARTADO)).toBeTruthy();
    expect(pedidos(api, "POST", `${CORREO}/${R1}/descarte`)).toHaveLength(1);
    expect(pedidos(api, "POST", PAGOS)).toHaveLength(0);
  });

  it("ayudando, lo recibido por correo no se lee y la lista de pagos se ve igual", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: VECINA,
        [`GET ${PAGOS}`]: () => Response.json(lista([pago()])),
        [`GET ${CORREO}`]: () => error(403, "ayuda_no_admitida"),
      }),
    );
    abrir();
    expect(await screen.findByRole("button", { name: /Unidad 1-A/ })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: POR_CORREO })).toBeNull();
  });
});

describe("pantalla /pagos-informados/cotejo", () => {
  const unidad = (cambios: Partial<components["schemas"]["UnidadCotejada"]>): components["schemas"]["UnidadCotejada"] => ({
    unidad_id: U1, unidad_designacion: "1-A", informado_confirmado: "45000.00", pagos_confirmados: 1, sin_pagos_informados: false,
    liquidacion: "45000.00", diferencia: "0.00", resultado: "coincide", ...cambios,
  });  // prettier-ignore

  it("dice los dos números de cada unidad y cuántas hay para revisar", async () => {
    const difiere = unidad({ unidad_id: U2, unidad_designacion: "2-B", liquidacion: "50000.00", diferencia: "5000.00", resultado: "difiere" });
    const coincide = unidad({});
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: CONSEJO,
        [`GET ${PAGOS}/cotejo`]: () =>
          Response.json({
            liquidacion: { importacion_id: D1, periodo: "2026-11-01", pagos_desde: "2026-10-01", pagos_hasta: "2026-10-31", unidades: [coincide, difiere], para_revisar: 1 },
          }),
      }),
    );
    const { container } = abrir(RUTA_COTEJO_PAGOS_INFORMADOS);
    const unidades = await screen.findByRole("list", { name: TITULO_COTEJO });
    expect(within(unidades).getByText(comoSeLee(textoCotejo(difiere)))).toBeTruthy();
    expect(within(unidades).getByText(comoSeLee(textoCotejo(coincide)))).toBeTruthy();
    expect(screen.getByText(/Una unidad para revisar/)).toBeTruthy();
    expect(screen.getByText(/del 01\/10\/2026 al 31\/10\/2026/)).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("sin liquidación vigente lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: VECINA,
        [`GET ${PAGOS}/cotejo`]: () => Response.json({ liquidacion: null }),
      }),
    );
    abrir(RUTA_COTEJO_PAGOS_INFORMADOS);
    expect(await screen.findByText("Todavía no hay una liquidación oficial confirmada para comparar.")).toBeTruthy();
  });
});

describe("textos del cotejo y del inicio", () => {
  it("el cotejo nunca dice que alguien se equivocó", () => {
    const base = { unidad_id: U1, unidad_designacion: "1-A", pagos_confirmados: 0, diferencia: null } as const;
    expect(textoCotejo({ ...base, informado_confirmado: "0.00", sin_pagos_informados: true, liquidacion: "3000.00", resultado: "difiere" })).toBe(
      `La liquidación dice ${enPesos("3000.00")} y no hay pagos informados y confirmados de esta unidad.`,
    );
    expect(textoCotejo({ ...base, informado_confirmado: "10.00", sin_pagos_informados: false, liquidacion: null, resultado: "sin_datos" })).toBe(
      `Lo informado y confirmado suma ${enPesos("10.00")}; la liquidación no trae el dato de esta unidad.`,
    );
  });

  it("el inicio cuenta solo lo que hay", () => {
    expect(textoPorConfirmar(0)).toBeNull();
    expect(textoPorConfirmar(1)).toBe("1 pago informado espera confirmación");
    expect(textoPorConfirmar(3)).toBe("3 pagos informados esperan confirmación");
    expect(textoParaRevisar(2)).toBe("2 unidades para revisar");
    expect(textoPorCompletar(1)).toBe("1 comprobante llegó por correo y falta completarlo");
    expect(textoPorCompletar(0)).toBeNull();
  });
});
