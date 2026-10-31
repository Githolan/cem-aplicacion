import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { porConciliar, porConfirmar } from "./ConciliacionPagos";
import { AVISO_SIN_CUENTA, AVISO_VERIFICAR_CUENTA, comprobantesDisponibles, porPagar, situacion } from "./PagosManuales";

// Pago manual del piloto (app 0055). Todos los datos son ficticios: CUIT y CBU inventados, sin
// ningún proveedor ni cuenta real, y nada se transfiere.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const P1 = "22222222-2222-4222-8222-222222222222";
const O1 = "33333333-3333-4333-8333-333333333333";
const X1 = "44444444-4444-4444-8444-444444444444";
const PAGO = "55555555-5555-4555-8555-555555555555";
const PERSONA = "66666666-6666-4666-8666-666666666666";
const DOC = "77777777-7777-4777-8777-777777777777";
const ARCHIVO = "88888888-8888-4888-8888-888888888888";
const DECL = "99999999-9999-4999-8999-999999999999";
const CUENTA = "0000003100000000001234";
const H1 = "a".repeat(64);
const BASE = `/api/v1/consorcios/${C1}`;

type Orden = components["schemas"]["OrdenPago"];
type ConPago = components["schemas"]["OrdenConPagoManual"];
type Pago = components["schemas"]["PagoManual"];
type Version = components["schemas"]["VersionOrdenPago"];

function orden(extra: Partial<Orden> = {}): Orden {
  return {
    id: O1,
    proveedor_id: P1,
    razon_social: "Limpieza Ficticia SRL",
    importe_centavos: 1_500_000,
    moneda: "ARS",
    concepto: "Limpieza de octubre",
    usa_fondo_reserva: false,
    estado: "aprobada",
    version_vigente: 1,
    franja: "cuenta_cambiada",
    alerta_fraccionamiento: false,
    condicion_beneficiario: "cuenta_cambiada",
    aprobaciones: 1,
    aprobaciones_requeridas: 1,
    aprobada_en: "2026-10-31T12:00:00Z",
    aprobacion_vence_en: "2026-11-07T12:00:00Z",
    motivo_rechazo: null,
    preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z",
    actualizada_en: "2026-10-31T12:00:00Z",
    ...extra,
  };
}

function version(): Version {
  return {
    numero: 1,
    proveedor_id: P1,
    proveedor_version: 1,
    razon_social: "Limpieza Ficticia SRL",
    cuit: "20000000001",
    cuenta_ultimos_digitos: CUENTA.slice(-4),
    importe_centavos: 1_500_000,
    moneda: "ARS",
    concepto: "Limpieza de octubre",
    comprobante: "Factura B 0001-00000001",
    usa_fondo_reserva: false,
    condicion_beneficiario: "cuenta_cambiada",
    franja: "cuenta_cambiada",
    alerta_fraccionamiento: false,
    umbral_importe_centavos: null,
    umbral_moneda: null,
    contrato_id: null,
    aprobaciones_requeridas: 1,
    aprobada_por: [PERSONA],
    huella: H1,
    preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z",
  };
}

function pago(extra: Partial<Pago> = {}): Pago {
  return {
    id: PAGO,
    corrige_a: null,
    numero_operacion: "0001234567",
    fecha_operacion: "2026-10-31",
    importe_centavos: 1_500_000,
    moneda: "ARS",
    documento_id: DOC,
    version_documento_id: ARCHIVO,
    registrado_por: PERSONA,
    registrado_en: "2026-10-31T13:00:00Z",
    conciliacion: null,
    motivo_conciliacion: null,
    conciliado_por: null,
    conciliado_en: null,
    ...extra,
  };
}

function conPago(estado: Orden["estado"], pagos: Pago[] = [], extra: Partial<ConPago["ejecucion"]> = {}): ConPago {
  return {
    orden: orden({ estado }),
    ejecucion: {
      id: X1,
      version: 1,
      creada_en: "2026-10-31T12:30:00Z",
      resultado: null,
      referencia: null,
      pagos,
      declaraciones: [],
      ...extra,
    },
  };
}

const CUENTA_DEL_ACTO = {
  ejecucion_id: X1,
  version: 1,
  huella: H1,
  proveedor_id: P1,
  proveedor_version: 1,
  razon_social: "Limpieza Ficticia SRL",
  cuit: "20000000001",
  cuenta_destino: CUENTA,
  importe_centavos: 1_500_000,
  moneda: "ARS",
  concepto: "Limpieza de octubre",
  comprobante: "Factura B 0001-00000001",
  aprobada_en: "2026-10-31T12:00:00Z",
  aprobacion_vence_en: "2026-11-07T12:00:00Z",
};

const DOCUMENTOS = () =>
  Response.json({
    documentos: [
      {
        id: DOC,
        categoria: "facturas_y_comprobantes",
        titulo: "Transferencia Limpieza Ficticia",
        fecha: "2026-10-31",
        cargado_en: "2026-10-31T13:00:00Z",
        versiones: [{ id: ARCHIVO, estado: "disponible" }],
      },
    ],
  });

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

function abrir(ruta: string) {
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

async function sinFaltasDeAccesibilidad(contenedor: HTMLElement) {
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations.map((v) => v.id)).toEqual([]);
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

describe("lo común del pago manual", () => {
  it("dice en palabras en qué está cada pago y cuál hay que pagar", () => {
    expect(situacion(conPago("en_ejecucion"))).toBe("Lista para pagar");
    expect(porPagar(conPago("en_ejecucion"))).toBe(true);
    expect(situacion(conPago("en_verificacion", [pago()]))).toBe("Pago registrado: pendiente de conciliación");
    expect(porPagar(conPago("en_verificacion", [pago()]))).toBe(false);
    const observado = conPago("en_verificacion", [pago({ conciliacion: "observado", motivo_conciliacion: "La fecha no coincide" })]);
    expect(porPagar(observado)).toBe(true);
    expect(situacion(observado)).toContain("La fecha no coincide");
    expect(situacion(conPago("pagada", [pago({ conciliacion: "conforme" })], { referencia: "0001234567" }))).toBe(
      "Pagada (operación 0001234567)",
    );
    expect(porPagar(conPago("pagada"))).toBe(false);
  });

  it("solo ofrece comprobantes de la categoría que pasaron el antivirus", () => {
    const base = { fecha: "2026-10-31", cargado_en: "2026-10-31T13:00:00Z", titulo: "x" };
    const lista = [
      { ...base, id: "a", categoria: "facturas_y_comprobantes" as const, versiones: [{ id: "1", estado: "disponible" as const }] },
      { ...base, id: "b", categoria: "facturas_y_comprobantes" as const, versiones: [{ id: "2", estado: "en_cuarentena" as const }] },
      { ...base, id: "c", categoria: "contratos_proveedores" as const, versiones: [{ id: "3", estado: "disponible" as const }] },
    ];
    expect(comprobantesDisponibles(lista as never).map((d) => d.id)).toEqual(["a"]);
  });

  it("el Consejo concilia el último registro sin conciliar y confirma una declaración sin pago vigente", () => {
    expect(porConciliar(conPago("en_verificacion", [pago()]))?.id).toBe(PAGO);
    expect(porConciliar(conPago("en_verificacion", [pago({ conciliacion: "observado" })]))).toBeUndefined();
    const declarada = conPago("en_ejecucion", [], {
      declaraciones: [
        { id: DECL, motivo: "Rechazo del banco", declarado_por: PERSONA, declarado_en: "2026-10-31T14:00:00Z",
          confirmado_por: null, confirmado_en: null, confirmable: true },
      ],
    });
    expect(porConfirmar(declarada)?.id).toBe(DECL);
    expect(porConfirmar({ ...declarada, ejecucion: { ...declarada.ejecucion, pagos: [pago()] }, orden: orden({ estado: "en_verificacion" }) })).toBeUndefined();
    // Lo decide `confirmable` (el orden real de los hechos, calculado por el backend), no la hora.
    const [d] = declarada.ejecucion.declaraciones;
    const observado = [pago({ conciliacion: "observado", registrado_en: "2026-10-31T15:00:00Z" })];
    const superada = conPago("en_verificacion", observado, { declaraciones: [{ ...d!, confirmable: false }] });
    expect(porConfirmar(superada)).toBeUndefined();
    const posterior = conPago("en_verificacion", observado, { declaraciones: [{ ...d!, confirmable: true }] });
    expect(porConfirmar(posterior)?.id).toBe(DECL);
  });
});

describe("pagos a realizar", () => {
  function simulado(extra: Respuestas = {}) {
    return backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [orden()] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/documentos`]: DOCUMENTOS,
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden(), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => Response.json({ id: O1, version: 1 }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
      ...extra,
    });
  }

  it("la cuenta completa llega recién en el acto de ejecución, con el aviso de verificarla", async () => {
    const api = simulado();
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    const boton = await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" });
    // Antes del acto: ningún pedido de la cuenta y ninguna cuenta completa en pantalla.
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`)).toHaveLength(0);
    expect(container.textContent).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
    fireEvent.click(boton);
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(screen.getByText(AVISO_VERIFICAR_CUENTA)).toBeTruthy();
    // Emitió la versión y la huella vistas, y pidió la cuenta con las mismas.
    const [emision] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`);
    expect(await emision?.json()).toEqual({ version: 1, huella: H1 });
    const [acto] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`);
    expect(await acto?.json()).toEqual({ version: 1, huella: H1 });
    await sinFaltasDeAccesibilidad(container);
    // Cerrar descarta la cuenta.
    fireEvent.click(screen.getByRole("button", { name: "Cerrar sin registrar" }));
    await waitFor(() => expect(container.textContent).not.toContain(CUENTA));
  });

  it("registra la evidencia escrita por la persona, no la copiada de la orden", async () => {
    const api = simulado();
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
    expect((screen.getByLabelText(/^Importe transferido/) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(/^CBU\/CVU de destino/) as HTMLInputElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText(/^Importe transferido/), { target: { value: "15.000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: "0000003 1000000000 01234" } });
    fireEvent.change(screen.getByLabelText("Comprobante de la transferencia"), { target: { value: DOC } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
    expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
    const [registro] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`);
    expect(registro?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await registro?.json()).toEqual({
      version: 1,
      huella: H1,
      numero_operacion: "0001-234567",
      fecha_operacion: "2026-10-31",
      importe_centavos: 1_500_000,
      moneda: "ARS",
      cuenta_destino: CUENTA,
      documento_id: DOC,
    });
  });

  it("el acto pide el segundo factor y se reintenta después del código", async () => {
    let primera = true;
    const api = simulado({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => {
        if (primera) {
          primera = false;
          return error("segundo_factor_requerido", 403);
        }
        return Response.json(CUENTA_DEL_ACTO);
      },
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden({ estado: "en_ejecucion" }), versiones: [version()] }),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    // Ya emitida: no se vuelve a emitir.
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`)).toHaveLength(0);
  });

  it("con la aprobación vencida y un registro observado, corrige sin volver a ver la cuenta", async () => {
    const observado = conPago("en_verificacion", [pago({ conciliacion: "observado", motivo_conciliacion: "No se lee" })]);
    const api = simulado({
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [observado] }),
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden({ estado: "en_verificacion" }), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error("aprobacion_vencida", 409),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    expect(screen.queryByText("CBU/CVU")).toBeNull();
    await sinFaltasDeAccesibilidad(container);
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText(/^Importe transferido/), { target: { value: "15.000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.change(screen.getByLabelText("Comprobante de la transferencia"), { target: { value: DOC } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
    expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
    const [registro] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`);
    expect(await registro?.json()).toMatchObject({ version: 1, huella: H1, cuenta_destino: CUENTA });
  });

  it.each(["proveedor_cambiado", "proveedor_inactivo", "contrato_no_vigente"])("con %s y la orden emitida, registra sin volver a ver la cuenta", async (codigo) => {
    const api = simulado({
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [conPago("en_ejecucion")] }),
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden({ estado: "en_ejecucion" }), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error(codigo, 409),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    expect(screen.getByLabelText("Número de operación")).toBeTruthy();
  });

  it("con la aprobación vencida y sin registro, declara que no se pagó sin ver la cuenta", async () => {
    const api = simulado({
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [conPago("en_ejecucion")] }),
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden({ estado: "en_ejecucion" }), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error("aprobacion_vencida", 409),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    fireEvent.click(screen.getByRole("button", { name: "No se pudo pagar" }));
    fireEvent.change(screen.getByLabelText("Por qué no se pagó"), { target: { value: "Venció antes de transferir" } });
    fireEvent.click(screen.getByRole("button", { name: "Declarar que no se pagó" }));
    expect(await screen.findByText(/Listo: declaraste que no se pagó/)).toBeTruthy();
    const [declaracion] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado`);
    expect(await declaracion?.json()).toEqual({ version: 1, motivo: "Venció antes de transferir" });
  });

  it("una orden sin ejecución manual esperando su pago no abre la ficha sin cuenta", async () => {
    const api = simulado({
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [orden()] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error("aprobacion_vencida", 409),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(AVISO_SIN_CUENTA)).toBeNull();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
  });

  it("quien aprobó la orden no recibe la cuenta: el acto responde 403 y no hay ficha", async () => {
    const api = simulado({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error("sin_permiso", 403),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    expect(screen.queryByText(AVISO_VERIFICAR_CUENTA)).toBeNull();
  });
});

describe("conciliación de pagos", () => {
  it("el Consejo concilia conforme un pago registrado, sin ver la cuenta completa", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/pagos-manuales/revision`]: () => Response.json({ elementos: [conPago("en_verificacion", [pago()])] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`]: () =>
        Response.json({ orden_id: O1, estado: "pagada" }),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText("0001234567")).toBeTruthy();
    expect(within(lista).getByRole("link", { name: "Ver el comprobante" }).getAttribute("href")).toBe(
      `${BASE}/documentos/${DOC}/versiones/${ARCHIVO}/descargar`,
    );
    expect(container.textContent).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
    fireEvent.click(within(lista).getByRole("button", { name: "Conforme" }));
    expect(await screen.findByText(/La orden quedó pagada/)).toBeTruthy();
    const [conciliacion] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`);
    expect(await conciliacion?.json()).toEqual({ resultado: "conforme" });
  });

  it("observar exige el motivo, y quien registró el pago ve por qué no puede conciliarlo", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/pagos-manuales/revision`]: () => Response.json({ elementos: [conPago("en_verificacion", [pago()])] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`]: () => error("no_es_quien_ejecuto", 403),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/conciliacion");
    fireEvent.click(await screen.findByRole("button", { name: "Observar" }));
    expect((screen.getByRole("button", { name: "Observar el pago" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Qué no coincide"), { target: { value: "La fecha no coincide" } });
    fireEvent.click(screen.getByRole("button", { name: "Observar el pago" }));
    expect(await screen.findByText(/quien la vio o registró no concilia ni confirma/)).toBeTruthy();
    const [pedido] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`);
    expect(await pedido?.json()).toEqual({ resultado: "observado", motivo: "La fecha no coincide" });
  });

  it("confirma que no se pagó una orden declarada por quien la ejecutaba", async () => {
    const declarada = conPago("en_ejecucion", [], {
      declaraciones: [
        { id: DECL, motivo: "Rechazo del banco", declarado_por: PERSONA, declarado_en: "2026-10-31T14:00:00Z",
          confirmado_por: null, confirmado_en: null, confirmable: true },
      ],
    });
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/pagos-manuales/revision`]: () => Response.json({ elementos: [declarada] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado/${DECL}/confirmacion`]: () =>
        Response.json({ orden_id: O1, estado: "fallida" }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/conciliacion");
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar que no se pagó" }));
    expect(await screen.findByText(/La orden quedó fallida/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado/${DECL}/confirmacion`)).toHaveLength(1);
  });
});
