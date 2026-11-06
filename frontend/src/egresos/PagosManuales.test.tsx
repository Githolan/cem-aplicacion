import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { VOLVIO_LA_RED } from "./comun";
import { porConciliar, porConfirmar } from "./ConciliacionPagos";
import {
  AVISO_SIN_CUENTA,
  AVISO_VERIFICAR_CUENTA,
  comprobantesDisponibles,
  comprobantesEnRevision,
  porPagar,
  situacion,
} from "./PagosManuales";

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
    aprobacion_vence_en: "2099-11-07T12:00:00Z",
    aprobada_por_vos: false,
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
    fuera_de_termino: false,
    ...extra,
  };
}

function conPago(estado: Orden["estado"], pagos: Pago[] = [], extra: Partial<ConPago["ejecucion"]> = {}): ConPago {
  return {
    orden: orden({ estado }),
    puede_conciliar: true,
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
        orden_pago_id: O1,
        versiones: [{ id: ARCHIVO, estado: "disponible" }],
      },
      {
        // Una factura sin orden (subida en Documentos): el registro no la ata y no se ofrece.
        id: X1,
        categoria: "facturas_y_comprobantes",
        titulo: "Factura de otra cosa",
        fecha: "2026-10-30",
        cargado_en: "2026-10-30T13:00:00Z",
        orden_pago_id: null,
        versiones: [{ id: ARCHIVO, estado: "disponible" }],
      },
    ],
  });

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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Solo el intervalo: `findBy…` sigue esperando con su temporizador real. */
const conEsperaManual = () => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
const ESPERA = 3000;
const volvioLaRed = () => fireEvent(window, new Event("online"));

describe("lo común del pago manual", () => {
  it("dice en palabras en qué está cada pago y cuál hay que pagar", () => {
    expect(situacion(conPago("en_ejecucion"))).toBe("Lista para pagar");
    expect(porPagar(conPago("en_ejecucion"))).toBe(true);
    expect(situacion(conPago("en_verificacion", [pago()]))).toBe("Pago registrado: pendiente de conciliación");
    expect(porPagar(conPago("en_verificacion", [pago()]))).toBe(false);
    const observado = conPago("en_verificacion", [pago({ conciliacion: "observado", motivo_conciliacion: "La fecha no coincide" })]);
    expect(porPagar(observado)).toBe(true);
    expect(situacion(observado)).toBe("Pago observado por el Consejo: La fecha no coincide. Registralo de nuevo corregido.");
    // Quien aprobó no paga: el pedido de registrarlo de nuevo no es para esa persona.
    expect(situacion({ ...observado, orden: orden({ estado: "en_verificacion", aprobada_por_vos: true }) })).toBe(
      "Pago observado por el Consejo: La fecha no coincide.",
    );
    expect(situacion(conPago("pagada", [pago({ conciliacion: "conforme" })], { referencia: "0001234567" }))).toBe(
      "Pagada (operación 0001234567)",
    );
    expect(porPagar(conPago("pagada"))).toBe(false);
  });

  it("solo ofrece los comprobantes cargados desde el pago de esa orden que pasaron el antivirus", () => {
    const base = { fecha: "2026-10-31", cargado_en: "2026-10-31T13:00:00Z", titulo: "x", categoria: "facturas_y_comprobantes" as const };
    const lista = [
      { ...base, id: "a", orden_pago_id: O1, versiones: [{ id: "1", estado: "disponible" as const }] },
      { ...base, id: "b", orden_pago_id: O1, versiones: [{ id: "2", estado: "en_cuarentena" as const }] },
      { ...base, id: "c", orden_pago_id: X1, versiones: [{ id: "3", estado: "disponible" as const }] },
      { ...base, id: "d", orden_pago_id: null, versiones: [{ id: "4", estado: "disponible" as const }] },
    ];
    expect(comprobantesDisponibles(lista as never, O1).map((d) => d.id)).toEqual(["a"]);
    expect(comprobantesEnRevision(lista as never, O1)).toBe(1);
    expect(comprobantesEnRevision(lista as never, X1)).toBe(0);
  });

  it("el Consejo concilia el último registro sin conciliar y confirma una declaración sin pago vigente", () => {
    expect(porConciliar(conPago("en_verificacion", [pago()]))?.id).toBe(PAGO);
    expect(porConciliar(conPago("en_verificacion", [pago({ conciliacion: "observado" })]))).toBeUndefined();
    const declarada = conPago("en_ejecucion", [], {
      declaraciones: [
        { id: DECL, motivo: "Rechazo del banco", declarado_por: PERSONA, declarado_en: "2026-10-31T14:00:00Z",
          confirmado_por: null, confirmado_en: null, confirmable: true, puede_confirmar: true },
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

  it("sube el comprobante desde el pago: primero el documento con la orden, después el archivo", async () => {
    const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const api = simulado({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`POST ${BASE}/documentos/${NUEVO}/versiones`]: () =>
        Response.json({ id: ARCHIVO, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 202 }),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
    // Se ofrece el de esta orden, no la factura sin orden.
    const opciones = within(screen.getByLabelText("Comprobante de la transferencia")).getAllByRole("option");
    expect(opciones.map((o) => o.textContent)).toEqual(["Elegí el comprobante", "Transferencia Limpieza Ficticia (2026-10-31)"]);
    // Subir no es un paso aparte: alcanza con elegir el archivo.
    expect(screen.queryByRole("button", { name: "Subir el comprobante" })).toBeNull();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`)).toHaveLength(0);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    const [carga] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`);
    expect(carga?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await carga?.json()).toEqual({ titulo: "Transferencia a Limpieza Ficticia SRL", fecha: "2026-10-31" });
    const [subida] = pedidos(api, "POST", `${BASE}/documentos/${NUEVO}/versiones`);
    expect(subida?.headers.get("Content-Type")).toBe("application/pdf");
    expect(subida?.headers.get("Idempotency-Key")).toBeTruthy();
    // La lista se vuelve a pedir para que aparezca cuando el antivirus lo apruebe.
    await waitFor(() => expect(pedidos(api, "GET", `${BASE}/documentos`).length).toBeGreaterThan(1));
    await sinFaltasDeAccesibilidad(container);
  });

  it("si el archivo no entra, el reintento sube solo el archivo al mismo comprobante", async () => {
    const NUEVO = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    let intentos = 0;
    const api = simulado({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`POST ${BASE}/documentos/${NUEVO}/versiones`]: () =>
        ++intentos === 1
          ? error("archivo_demasiado_grande", 413)
          : Response.json({ id: ARCHIVO, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 202 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/El archivo no se subió/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar la subida" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`)).toHaveLength(1);
    expect(pedidos(api, "POST", `${BASE}/documentos/${NUEVO}/versiones`)).toHaveLength(2);
    // El campo del archivo queda vacío y no hay nada que reintentar.
    expect((screen.getByLabelText(/^Archivo del comprobante/) as HTMLInputElement).files).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Reintentar la subida" })).toBeNull();
  });

  it("si no vio la cuenta, la carga del comprobante se niega y no sube el archivo", async () => {
    const api = simulado({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`]: () => error("no_es_quien_ejecuto", 403),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/quien vio la cuenta en el acto de ejecución/)).toBeTruthy();
    expect(api.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/versiones"))).toBe(false);
  });

  it("un archivo que no es PDF, JPEG ni PNG no se manda", async () => {
    const api = simulado();
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
    const archivo = new File(["texto"], "comprobante.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/Solo se aceptan archivos PDF, JPEG o PNG/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`)).toHaveLength(0);
    // Ese archivo no va a entrar: no se ofrece repetirlo.
    expect(screen.queryByRole("button", { name: "Reintentar la subida" })).toBeNull();
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
    // La separación niega con el mismo código que la falta de facultad: el texto no afirma cuál fue.
    expect((await screen.findByRole("alert")).textContent).toMatch(/quien prepara, aprueba, ejecuta y concilia/);
    expect(container.textContent).not.toContain(CUENTA);
    expect(screen.queryByText(AVISO_VERIFICAR_CUENTA)).toBeNull();
  });

  const EJECUTAR = { name: "Ejecutar pago manual de Limpieza Ficticia SRL" };

  it.each([
    [true, 0],
    [false, 1],
  ])("orden aprobada: con `aprobada_por_vos` en %s la orden se ve y ejecutar aparece %i veces", async (aprobadaPorVos, veces) => {
    const aprobada = orden({ aprobada_por_vos: aprobadaPorVos });
    vi.stubGlobal("fetch", simulado({ [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [aprobada] }) }));
    abrir("/egresos/pagos");
    const lista = await screen.findByRole("list", { name: "Órdenes para pagar" });
    expect(within(lista).getByText("Limpieza de octubre")).toBeTruthy();
    expect(within(lista).queryAllByRole("button", EJECUTAR)).toHaveLength(veces);
  });

  it.each([
    [true, 0],
    [false, 1],
  ])("orden ya emitida: con `aprobada_por_vos` en %s la orden se ve y ejecutar aparece %i veces", async (aprobadaPorVos, veces) => {
    const emitida = { ...conPago("en_ejecucion"), orden: orden({ estado: "en_ejecucion", aprobada_por_vos: aprobadaPorVos }) };
    vi.stubGlobal(
      "fetch",
      simulado({
        [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [] }),
        [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [emitida] }),
      }),
    );
    abrir("/egresos/pagos");
    const lista = await screen.findByRole("list", { name: "Órdenes para pagar" });
    expect(within(lista).getByText("Lista para pagar")).toBeTruthy();
    expect(within(lista).queryAllByRole("button", EJECUTAR)).toHaveLength(veces);
  });

  it("una aprobación vencida no se ofrece para ejecutar", async () => {
    const vencida = orden({ aprobacion_vence_en: "2020-01-01T00:00:00Z" });
    const api = simulado({ [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [vencida] }) });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/pagos");
    expect(await screen.findByText("No hay órdenes aprobadas esperando su pago.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" })).toBeNull();
  });

  it("control: una aprobación que vence en el futuro sí se ofrece", async () => {
    const vigente = orden({ aprobacion_vence_en: "2099-01-01T00:00:00Z" });
    vi.stubGlobal("fetch", simulado({ [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [vigente] }) }));
    abrir("/egresos/pagos");
    expect(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" })).toBeTruthy();
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
    expect(await screen.findByText(/Quien vio la cuenta o registró un pago no lo concilia ni confirma que no se pagó/)).toBeTruthy();
    const [pedido] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`);
    expect(await pedido?.json()).toEqual({ resultado: "observado", motivo: "La fecha no coincide" });
  });

  it("un pago fuera de término se marca y, para darlo por conforme, pide el motivo (app 0067)", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/pagos-manuales/revision`]: () =>
        Response.json({ elementos: [conPago("en_verificacion", [pago({ fuera_de_termino: true })])] }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`]: () =>
        Response.json({ orden_id: O1, estado: "pagada" }),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText("Fuera de término")).toBeTruthy();
    fireEvent.click(within(lista).getByRole("button", { name: "Conforme" }));
    // No sale nada todavía: primero el motivo.
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`)).toHaveLength(0);
    const dar = screen.getByRole("button", { name: "Dar el pago por conforme" }) as HTMLButtonElement;
    expect(dar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Por qué lo aceptás fuera de término"), { target: { value: "El banco demoró" } });
    await sinFaltasDeAccesibilidad(container);
    fireEvent.click(dar);
    expect(await screen.findByText(/La orden quedó pagada/)).toBeTruthy();
    const [conciliacion] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`);
    expect(await conciliacion?.json()).toEqual({ resultado: "conforme", motivo: "El banco demoró" });
  });

  it("confirma que no se pagó una orden declarada por quien la ejecutaba", async () => {
    const declarada = conPago("en_ejecucion", [], {
      declaraciones: [
        { id: DECL, motivo: "Rechazo del banco", declarado_por: PERSONA, declarado_en: "2026-10-31T14:00:00Z",
          confirmado_por: null, confirmado_en: null, confirmable: true, puede_confirmar: true },
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

describe("pagos para aclarar (app 0067)", () => {
  const POR_ACLARAR = {
    consorcio_id: C1,
    consorcio: "Edificio Ficticio",
    orden_id: O1,
    version: 1,
    concepto: "Limpieza de octubre",
    importe_centavos: 1_500_000,
    moneda: "ARS",
    cuenta_vista_en: "2026-10-31T13:00:00Z",
    huella: H1,
    comprobante_id: null,
    comprobante_estado: null,
  };
  const DECLARAR = `${BASE}/pagos-por-aclarar/${O1}/no-realizado`;

  it("quien dejó de administrar declara que no transfirió, con el código si hace falta y la misma clave", async () => {
    let primera = true;
    let lista = [POR_ACLARAR];
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: lista }),
      [`POST ${DECLARAR}`]: () => {
        if (primera) {
          primera = false;
          return error("segundo_factor_requerido", 403);
        }
        lista = [];
        return Response.json({ id: DECL, orden_id: O1 }, { status: 201 });
      },
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/por-aclarar");
    const tarjetas = await screen.findByRole("list", { name: "Pagos para aclarar" });
    expect(within(tarjetas).getByText(/Edificio Ficticio/)).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
    const boton = screen.getByRole("button", { name: "No transferí" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Por qué no se transfirió"), { target: { value: "Renuncié antes de pagar" } });
    fireEvent.click(boton);
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
    expect(await screen.findByText(/Ahora lo confirma otro miembro del Consejo/)).toBeTruthy();
    expect(await screen.findByText("No tenés pagos para aclarar.")).toBeTruthy();
    const [uno, dos] = pedidos(api, "POST", DECLARAR);
    expect(await uno?.json()).toEqual({ version: 1, motivo: "Renuncié antes de pagar" });
    expect(dos?.headers.get("Idempotency-Key")).toBe(uno?.headers.get("Idempotency-Key"));
  });

  it("quien transfirió y dejó de administrar sube el comprobante y registra el pago (app 0068)", async () => {
    const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const CARGA = `${BASE}/pagos-por-aclarar/${O1}/comprobante`;
    const SUBIDA = `${CARGA}/${NUEVO}/versiones`;
    const REGISTRO = `${BASE}/pagos-por-aclarar/${O1}/pago`;
    let lista: object[] = [POR_ACLARAR];
    conEsperaManual();
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: lista }),
      [`POST ${CARGA}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`POST ${SUBIDA}`]: () => {
        lista = [{ ...POR_ACLARAR, comprobante_id: NUEVO, comprobante_estado: "en_cuarentena" }];
        return Response.json({ id: ARCHIVO, estado: "en_cuarentena" }, { status: 201 });
      },
      [`POST ${REGISTRO}`]: () => {
        lista = [];
        return Response.json({ id: PAGO, orden_id: O1 }, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/egresos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.getByText("Primero subí el comprobante de la transferencia.")).toBeTruthy();
    const registrar = screen.getByRole("button", { name: "Registrar el pago" }) as HTMLButtonElement;
    expect(registrar.disabled).toBe(true);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(await screen.findByText(/El antivirus revisa el comprobante/)).toBeTruthy();
    expect(registrar.disabled).toBe(true); // en cuarentena todavía no se registra
    const [carga] = pedidos(api, "POST", CARGA);
    expect(await carga?.json()).toEqual({ titulo: "Comprobante de la transferencia", fecha: "2026-10-31" });
    const [subida] = pedidos(api, "POST", SUBIDA);
    expect(subida?.headers.get("Content-Type")).toBe("application/pdf");
    expect(subida?.headers.get("Idempotency-Key")).toBeTruthy();
    // El antivirus lo aprueba: la pantalla lo ve sola, sin un botón, y se registra.
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    lista = [{ ...POR_ACLARAR, comprobante_id: NUEVO, comprobante_estado: "disponible" }];
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect(await screen.findByText(/ya podés registrar el pago/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText(/^Importe transferido/), { target: { value: "15000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: `${CUENTA.slice(0, 8)} ${CUENTA.slice(8)}` } });
    await sinFaltasDeAccesibilidad(container);
    fireEvent.click(registrar);
    expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
    expect(await screen.findByText("No tenés pagos para aclarar.")).toBeTruthy();
    const [registro] = pedidos(api, "POST", REGISTRO);
    expect(await registro?.json()).toEqual({
      version: 1,
      huella: H1,
      numero_operacion: "0001-234567",
      fecha_operacion: "2026-10-31",
      importe_centavos: 1_500_000,
      moneda: "ARS",
      cuenta_destino: CUENTA,
      documento_id: NUEVO,
    });
  });

  it("con un comprobante propio rechazado, sube otro archivo al mismo documento", async () => {
    const SUBIDA = `${BASE}/pagos-por-aclarar/${O1}/comprobante/${DOC}/versiones`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () =>
        Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "rechazado" }] }),
      [`POST ${SUBIDA}`]: () => Response.json({ id: ARCHIVO, estado: "en_cuarentena" }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.getByText(/El antivirus rechazó el archivo/)).toBeTruthy();
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(pedidos(api, "POST", SUBIDA)).toHaveLength(1);
    expect(pedidos(api, "POST", `${BASE}/pagos-por-aclarar/${O1}/comprobante`)).toHaveLength(0);
  });

  it("si el archivo no entró, se reintenta sin volver a elegirlo", async () => {
    const SUBIDA = `${BASE}/pagos-por-aclarar/${O1}/comprobante/${DOC}/versiones`;
    let intentos = 0;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () =>
        Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "rechazado" }] }),
      [`POST ${SUBIDA}`]: () =>
        ++intentos === 1 ? error("error_interno", 500) : Response.json({ id: ARCHIVO, estado: "en_cuarentena" }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/El archivo no se subió/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar la subida" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    const [primera, segunda] = pedidos(api, "POST", SUBIDA).map((q) => q.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(screen.queryByRole("button", { name: "Reintentar la subida" })).toBeNull();
  });

  it("sin pagos para aclarar lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/egresos/por-aclarar");
    expect(await screen.findByText("No tenés pagos para aclarar.")).toBeTruthy();
  });
});

describe("fricción: el comprobante, la ficha y las lecturas", () => {
  const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const documento = (estado: string) => ({
    id: NUEVO, categoria: "facturas_y_comprobantes", titulo: "Transferencia nueva", fecha: "2026-10-31",
    cargado_en: "2026-10-31T13:00:00Z", orden_pago_id: O1, versiones: [{ id: ARCHIVO, estado }],
  });
  const POR_ACLARAR = {
    consorcio_id: C1, consorcio: "Edificio Ficticio", orden_id: O1, version: 1, concepto: "Limpieza de octubre",
    importe_centavos: 1_500_000, moneda: "ARS", cuenta_vista_en: "2026-10-31T13:00:00Z", huella: H1,
    comprobante_id: null, comprobante_estado: null,
  };

  function pagos(extra: Respuestas = {}) {
    return backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [orden()] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/documentos`]: DOCUMENTOS,
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden(), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => Response.json({ id: O1, version: 1 }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`POST ${BASE}/documentos/${NUEVO}/versiones`]: () =>
        Response.json({ id: ARCHIVO, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 202 }),
      ...extra,
    });
  }

  async function abrirFicha() {
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
  }

  async function subir() {
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
  }

  function escribirRegistroSinComprobante() {
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText(/^Importe transferido/), { target: { value: "15000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
  }

  function escribirRegistro() {
    escribirRegistroSinComprobante();
    fireEvent.change(screen.getByLabelText("Comprobante de la transferencia"), { target: { value: DOC } });
  }

  const elegido = () => (screen.getByLabelText("Comprobante de la transferencia") as HTMLSelectElement).value;
  const actualizar = () => screen.queryByRole("button", { name: "Actualizar comprobantes" });

  /** La lista de siempre, más un comprobante que el antivirus todavía revisa. */
  const conUnoEnRevision = async () => {
    const { documentos } = (await DOCUMENTOS().json()) as { documentos: object[] };
    return Response.json({ documentos: [...documentos, documento("en_cuarentena")] });
  };

  it("el comprobante subido se elige solo cuando el antivirus lo aprueba, sin tocar nada", async () => {
    conEsperaManual();
    let estado = "en_cuarentena";
    const api = pagos({
      [`GET ${BASE}/documentos`]: () => Response.json({ documentos: [documento(estado)] }),
    });
    vi.stubGlobal("fetch", api);
    await abrirFicha();
    await subir();
    // En cuarentena todavía no se elige ni se ofrece.
    expect(await screen.findByText(/En revisión: 1/)).toBeTruthy();
    expect(elegido()).toBe("");
    estado = "disponible";
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect(await screen.findByText("Listo: el comprobante pasó el antivirus y ya quedó elegido.")).toBeTruthy();
    expect(elegido()).toBe(NUEVO);
    // Elegido, deja de mirar.
    const lecturas = pedidos(api, "GET", `${BASE}/documentos`).length;
    await vi.advanceTimersByTimeAsync(ESPERA * 2);
    expect(pedidos(api, "GET", `${BASE}/documentos`)).toHaveLength(lecturas);
  });

  it("un comprobante rechazado por el antivirus no se elige y se dice", async () => {
    conEsperaManual();
    let estado = "en_cuarentena";
    vi.stubGlobal("fetch", pagos({
      [`GET ${BASE}/documentos`]: () => Response.json({ documentos: [documento(estado)] }),
    }));
    await abrirFicha();
    await subir();
    estado = "rechazado";
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect(await screen.findByText("El antivirus rechazó el archivo: subí otro.")).toBeTruthy();
    expect(elegido()).toBe("");
  });

  it("si falla actualizar los comprobantes, la ficha queda con lo escrito y lo avisa", async () => {
    conEsperaManual();
    let caido = false;
    vi.stubGlobal("fetch", pagos({
      [`GET ${BASE}/documentos`]: () => (caido ? error("error_interno", 500) : conUnoEnRevision()),
    }));
    await abrirFicha();
    escribirRegistro();
    expect(actualizar()).toBeNull();
    caido = true;
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect((await screen.findByRole("alert")).textContent).toContain("No pudimos actualizar los comprobantes");
    expect(screen.getByText(CUENTA)).toBeTruthy();
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
    expect(elegido()).toBe(DOC);
    caido = false;
    fireEvent.click(actualizar() as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(actualizar()).toBeNull();
  });

  it("si los comprobantes no llegaron al abrir, la ficha lo dice y deja actualizarlos", async () => {
    let caido = true;
    vi.stubGlobal("fetch", pagos({
      [`GET ${BASE}/documentos`]: () => (caido ? error("error_interno", 500) : DOCUMENTOS()),
    }));
    await abrirFicha();
    expect((await screen.findByRole("alert")).textContent).toContain("No pudimos actualizar los comprobantes");
    expect(within(screen.getByLabelText("Comprobante de la transferencia")).getAllByRole("option")).toHaveLength(1);
    caido = false;
    fireEvent.click(actualizar() as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(within(screen.getByLabelText("Comprobante de la transferencia")).getAllByRole("option")).toHaveLength(2);
  });

  it("control: con los comprobantes a la vista no hay nada que actualizar", async () => {
    vi.stubGlobal("fetch", pagos());
    await abrirFicha();
    expect(actualizar()).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("con la ficha abierta, los comprobantes que fallaron se repiten solos al volver la red", async () => {
    let caido = true;
    const api = pagos({ [`GET ${BASE}/documentos`]: () => (caido ? error("error_interno", 500) : DOCUMENTOS()) });
    vi.stubGlobal("fetch", api);
    await abrirFicha();
    await screen.findByRole("alert");
    escribirRegistroSinComprobante();
    caido = false;
    volvioLaRed();
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`)).toEqual([]);
  });

  it("si el antivirus agota la espera, lo dice y recién ahí ofrece actualizar", async () => {
    conEsperaManual();
    let estado = "en_cuarentena";
    const api = pagos({ [`GET ${BASE}/documentos`]: () => Response.json({ documentos: [documento(estado)] }) });
    vi.stubGlobal("fetch", api);
    await abrirFicha();
    await subir();
    await vi.advanceTimersByTimeAsync(ESPERA * 20);
    // Control: mientras todavía mira, no hay botón.
    expect(actualizar()).toBeNull();
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect((await screen.findByRole("alert")).textContent).toContain("El antivirus sigue revisando el comprobante");
    const lecturas = pedidos(api, "GET", `${BASE}/documentos`).length;
    await vi.advanceTimersByTimeAsync(ESPERA * 2);
    expect(pedidos(api, "GET", `${BASE}/documentos`)).toHaveLength(lecturas);
    estado = "disponible";
    fireEvent.click(actualizar() as HTMLElement);
    expect(await screen.findByText("Listo: el comprobante pasó el antivirus y ya quedó elegido.")).toBeTruthy();
    expect(elegido()).toBe(NUEVO);
    expect(actualizar()).toBeNull();
  });

  it("tocar «Actualizar comprobantes» con el archivo todavía en revisión vuelve a mirar solo", async () => {
    conEsperaManual();
    const api = pagos({ [`GET ${BASE}/documentos`]: () => Response.json({ documentos: [documento("en_cuarentena")] }) });
    vi.stubGlobal("fetch", api);
    await abrirFicha();
    await subir();
    await vi.advanceTimersByTimeAsync(ESPERA * 21);
    await screen.findByRole("alert");
    fireEvent.click(actualizar() as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    const lecturas = pedidos(api, "GET", `${BASE}/documentos`).length;
    await vi.advanceTimersByTimeAsync(ESPERA);
    expect(pedidos(api, "GET", `${BASE}/documentos`).length).toBeGreaterThan(lecturas);
  });

  describe("si la orden cambió con la ficha abierta", () => {
    const REGISTRO = `${BASE}/ordenes-pago/${O1}/pago-manual`;
    const H2 = "b".repeat(64);
    const nueva = { ...version(), numero: 2, huella: H2 };

    it("queda lo escrito, sobre la versión nueva y sin la cuenta de la anterior", async () => {
      let cambio = false;
      const api = pagos({
        [`GET ${BASE}/ordenes-pago/${O1}`]: () =>
          Response.json(
            cambio
              ? { orden: orden({ estado: "en_ejecucion", version_vigente: 2 }), versiones: [version(), nueva] }
              : { orden: orden(), versiones: [version()] },
          ),
        [`POST ${REGISTRO}`]: () => {
          if (cambio) return Response.json({ id: PAGO, orden_id: O1 }, { status: 201 });
          cambio = true;
          return error("version_desactualizada", 409);
        },
      });
      vi.stubGlobal("fetch", api);
      await abrirFicha();
      escribirRegistro();
      fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
      expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
      expect(screen.queryByText(CUENTA)).toBeNull();
      expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
      expect(elegido()).toBe(DOC);
      fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
      expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
      const intentos = pedidos(api, "POST", REGISTRO);
      expect(intentos).toHaveLength(2);
      expect(await (intentos[1] as Request).json()).toMatchObject({ version: 2, huella: H2 });
    });

    it("control: si ya no espera su pago, la ficha se cierra", async () => {
      let cambio = false;
      vi.stubGlobal("fetch", pagos({
        [`GET ${BASE}/ordenes-pago/${O1}`]: () =>
          Response.json({ orden: cambio ? orden({ estado: "pagada" }) : orden(), versiones: [version()] }),
        [`POST ${REGISTRO}`]: () => {
          cambio = true;
          return error("version_desactualizada", 409);
        },
      }));
      await abrirFicha();
      escribirRegistro();
      fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
      await waitFor(() => expect(screen.queryByLabelText("Número de operación")).toBeNull());
      expect(screen.queryByText(CUENTA)).toBeNull();
    });
  });

  async function registrarConRechazo(codigo: string) {
    vi.stubGlobal("fetch", pagos({ [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => error(codigo, 409) }));
    await abrirFicha();
    escribirRegistro();
    fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
  }

  it.each([
    ["operacion_de_otra_orden", /Ese número de operación ya respalda otra orden/],
    ["comprobante_de_otra_orden", /Ese comprobante ya respalda otro pago/],
  ])("%s es de tipeo: la ficha queda con lo escrito", async (codigo, texto) => {
    await registrarConRechazo(codigo);
    expect(await screen.findByText(texto)).toBeTruthy();
    expect(screen.getByText(CUENTA)).toBeTruthy();
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
  });

  it("si la orden cambió de verdad, la ficha sí se cierra", async () => {
    await registrarConRechazo("orden_no_admite");
    expect(await screen.findByText(/La orden ya no admite eso/)).toBeTruthy();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
    expect(screen.queryByText(CUENTA)).toBeNull();
  });

  it("si falla la recarga después de registrar, el «Listo» queda y se puede reintentar", async () => {
    let caido = false;
    vi.stubGlobal("fetch", pagos({
      [`GET ${BASE}/pagos-manuales`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [] })),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => {
        caido = true;
        return Response.json({ id: PAGO, orden_id: O1 }, { status: 201 });
      },
    }));
    await abrirFicha();
    escribirRegistro();
    fireEvent.click(screen.getByRole("button", { name: "Registrar el pago" }));
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.getByText(/Listo: registraste el pago/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  const LECTURAS = [
    ["/egresos/pagos", `GET ${BASE}/pagos-manuales`, "No hay órdenes aprobadas esperando su pago."],
    ["/egresos/conciliacion", `GET ${BASE}/pagos-manuales/revision`, "No hay pagos esperando conciliación."],
    ["/egresos/por-aclarar", "GET /api/v1/pagos-por-aclarar", "No tenés pagos para aclarar."],
  ] as const;

  const conLecturaCaida = (clave: string, caido: () => boolean) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/documentos`]: DOCUMENTOS,
      [clave]: () => (caido() ? error("error_interno", 500) : Response.json({ elementos: [] })),
    });

  it.each(LECTURAS)("%s: si la lectura falla, se repite sola al volver la red y lo dice", async (ruta, clave, vacia) => {
    let caido = true;
    vi.stubGlobal("fetch", conLecturaCaida(clave, () => caido));
    abrir(ruta);
    await screen.findByRole("button", { name: "Reintentar" });
    caido = false;
    volvioLaRed();
    expect(await screen.findByText(vacia)).toBeTruthy();
    expect(screen.getByText(VOLVIO_LA_RED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it.each(LECTURAS)("%s: si la lectura falla, Reintentar la vuelve a pedir", async (ruta, clave, vacia) => {
    let caido = true;
    vi.stubGlobal("fetch", conLecturaCaida(clave, () => caido));
    abrir(ruta);
    const boton = await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.getByRole("alert").textContent).toContain("El servicio no está disponible");
    caido = false;
    fireEvent.click(boton);
    expect(await screen.findByText(vacia)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  describe("pagos para aclarar", () => {
    const CON_ARCHIVO = { ...POR_ACLARAR, comprobante_id: NUEVO };

    it("el comprobante en revisión se actualiza solo y habilita registrar", async () => {
      conEsperaManual();
      let estado = "en_cuarentena";
      vi.stubGlobal("fetch", backend({
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...CON_ARCHIVO, comprobante_estado: estado }] }),
      }));
      abrir("/egresos/por-aclarar");
      fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
      const registrar = screen.getByRole("button", { name: "Registrar el pago" }) as HTMLButtonElement;
      expect(registrar.disabled).toBe(true);
      estado = "disponible";
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect(await screen.findByText(/ya podés registrar el pago/)).toBeTruthy();
      expect(registrar.disabled).toBe(false);
    });

    it("sin el formulario abierto no mira nada por su cuenta", async () => {
      conEsperaManual();
      const api = backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          Response.json({ elementos: [{ ...CON_ARCHIVO, comprobante_estado: "en_cuarentena" }] }),
      });
      vi.stubGlobal("fetch", api);
      abrir("/egresos/por-aclarar");
      await screen.findByRole("button", { name: "Sí transferí" });
      const lecturas = pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length;
      await vi.advanceTimersByTimeAsync(ESPERA * 2);
      expect(pedidos(api, "GET", "/api/v1/pagos-por-aclarar")).toHaveLength(lecturas);
    });

    const EN_REVISION = { ...CON_ARCHIVO, comprobante_estado: "en_cuarentena" };
    const actualizar = () => screen.queryByRole("button", { name: "Actualizar" });

    it("si falla la actualización, el formulario queda con lo escrito, lo avisa y deja actualizar", async () => {
      conEsperaManual();
      let caido = false;
      vi.stubGlobal("fetch", backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          caido ? error("error_interno", 500) : Response.json({ elementos: [EN_REVISION] }),
      }));
      abrir("/egresos/por-aclarar");
      fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
      fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
      expect(actualizar()).toBeNull();
      caido = true;
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect((await screen.findByRole("alert")).textContent).toContain("No pudimos actualizar la lista");
      expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
      caido = false;
      fireEvent.click(actualizar() as HTMLElement);
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
      expect(actualizar()).toBeNull();
    });

    it("la lista que falló con el formulario abierto se repite sola al volver la red", async () => {
      conEsperaManual();
      let caido = false;
      const api = backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          caido ? error("error_interno", 500) : Response.json({ elementos: [EN_REVISION] }),
      });
      vi.stubGlobal("fetch", api);
      abrir("/egresos/por-aclarar");
      fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
      caido = true;
      await vi.advanceTimersByTimeAsync(ESPERA);
      await screen.findByRole("alert");
      caido = false;
      volvioLaRed();
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    });

    it("si el antivirus agota la espera, lo dice y recién ahí ofrece actualizar", async () => {
      conEsperaManual();
      const api = backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [EN_REVISION] }) });
      vi.stubGlobal("fetch", api);
      abrir("/egresos/por-aclarar");
      fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
      await vi.advanceTimersByTimeAsync(ESPERA * 20);
      // Control: mientras todavía mira, no hay botón.
      expect(actualizar()).toBeNull();
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect((await screen.findByRole("alert")).textContent).toContain("El antivirus sigue revisando el comprobante");
      expect(actualizar()).toBeTruthy();
      const lecturas = pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length;
      await vi.advanceTimersByTimeAsync(ESPERA * 2);
      expect(pedidos(api, "GET", "/api/v1/pagos-por-aclarar")).toHaveLength(lecturas);
      // Tocar «Actualizar» con el archivo todavía en revisión vuelve a mirar solo.
      fireEvent.click(actualizar() as HTMLElement);
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
      const despues = pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length;
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect(pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length).toBeGreaterThan(despues);
    });
  });

  describe("ver el comprobante en la conciliación", () => {
    const DESCARGA = `${BASE}/documentos/${DOC}/versiones/${ARCHIVO}/descargar`;
    const conLista = (descarga: () => Response) =>
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/pagos-manuales/revision`]: () => Response.json({ elementos: [conPago("en_verificacion", [pago()])] }),
        [`GET ${DESCARGA}`]: descarga,
      });
    /** Lo que da el navegador con `redirect: "manual"` ante el 302 a la URL firmada. */
    const redireccion = () => {
      const r = new Response(null, { status: 200 });
      Object.defineProperty(r, "type", { value: "opaqueredirect" });
      Object.defineProperty(r, "ok", { value: false });
      Object.defineProperty(r, "status", { value: 0 });
      return r;
    };

    it("un error se dice en la pantalla y no se navega al JSON", async () => {
      const navega = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
      vi.stubGlobal("fetch", conLista(() => error("archivo_vencido", 410)));
      abrir("/egresos/conciliacion");
      fireEvent.click(await screen.findByRole("link", { name: "Ver el comprobante" }));
      expect(await screen.findByText("Este archivo ya no está disponible.")).toBeTruthy();
      expect(navega).not.toHaveBeenCalled();
      expect(screen.getByRole("list", { name: "Pagos para conciliar" })).toBeTruthy();
    });

    it("si está, sin seguir el 302 en el pedido, navega a la descarga", async () => {
      const destinos: string[] = [];
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        destinos.push(new URL(this.href).pathname);
      });
      const api = conLista(redireccion);
      vi.stubGlobal("fetch", api);
      abrir("/egresos/conciliacion");
      fireEvent.click(await screen.findByRole("link", { name: "Ver el comprobante" }));
      await waitFor(() => expect(destinos).toEqual([DESCARGA]));
      expect(pedidos(api, "GET", DESCARGA)[0]?.redirect).toBe("manual");
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("una respuesta 200 que no es la redirección no se toma por un éxito", async () => {
      const navega = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
      vi.stubGlobal("fetch", conLista(() => Response.json({})));
      abrir("/egresos/conciliacion");
      fireEvent.click(await screen.findByRole("link", { name: "Ver el comprobante" }));
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect(navega).not.toHaveBeenCalled();
    });
  });
});

describe("lo que no se puede hacer no aparece en los pagos", () => {
  const conPagos = (...acciones: string[]) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden", "egresos.ejecutar_pago", ...acciones),
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: [orden()] }),
      [`GET ${BASE}/pagos-manuales`]: () => Response.json({ elementos: [] }),
      [`GET ${BASE}/documentos`]: DOCUMENTOS,
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: orden(), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => Response.json({ id: O1, version: 1 }),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
    });
  const abrirFicha = async () => {
    abrir("/egresos/pagos");
    fireEvent.click(await screen.findByRole("button", { name: "Ejecutar pago manual de Limpieza Ficticia SRL" }));
    await screen.findByText(CUENTA);
  };

  it("registrar el pago y subir el comprobante se ven con `documentos.cargar`", async () => {
    vi.stubGlobal("fetch", conPagos("documentos.cargar"));
    await abrirFicha();
    expect(screen.getByLabelText(/Archivo del comprobante/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Registrar el pago" })).toBeTruthy();
  });

  it("control: sin `documentos.cargar` no hay un registro que no se podría terminar: queda declarar", async () => {
    vi.stubGlobal("fetch", conPagos());
    await abrirFicha();
    expect(screen.queryByLabelText(/Archivo del comprobante/)).toBeNull();
    expect(screen.queryByText(/Desde que lo subís/)).toBeNull();
    expect(screen.queryByLabelText("Comprobante de la transferencia")).toBeNull();
    expect(screen.queryByRole("button", { name: "Registrar el pago" })).toBeNull();
    expect(screen.getByRole("button", { name: "No se pudo pagar" })).toBeTruthy();
  });

  const declarada = conPago("en_ejecucion", [], {
    declaraciones: [
      { id: DECL, motivo: "Rechazo del banco", declarado_por: PERSONA, declarado_en: "2026-10-31T14:00:00Z",
        confirmado_por: null, confirmado_en: null, confirmable: true, puede_confirmar: true },
    ],
  });
  const conConciliacion = (...acciones: string[]) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden", ...acciones),
      [`GET ${BASE}/pagos-manuales/revision`]: () =>
        Response.json({ elementos: [conPago("en_verificacion", [pago()]), { ...declarada, orden: orden({ id: X1, estado: "en_ejecucion" }) }] }),
    });

  it("«Conforme», «Observar» y «Confirmar» se ven con `egresos.conciliar_pago`", async () => {
    vi.stubGlobal("fetch", conConciliacion("egresos.conciliar_pago"));
    abrir("/egresos/conciliacion");
    expect(await screen.findByRole("button", { name: "Conforme" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Observar" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirmar que no se pagó" })).toBeTruthy();
  });

  const conRevision = (...elementos: ConPago[]) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden", "egresos.conciliar_pago"),
      [`GET ${BASE}/pagos-manuales/revision`]: () => Response.json({ elementos }),
    });

  it.each([
    [true, 1],
    [false, 0],
  ])("con `puede_conciliar` en %s el pago se ve y «Conforme» y «Observar» aparecen %i veces", async (puede, veces) => {
    vi.stubGlobal("fetch", conRevision({ ...conPago("en_verificacion", [pago()]), puede_conciliar: puede }));
    abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText("0001234567")).toBeTruthy();
    expect(within(lista).queryAllByRole("button", { name: "Conforme" })).toHaveLength(veces);
    expect(within(lista).queryAllByRole("button", { name: "Observar" })).toHaveLength(veces);
  });

  it.each([
    [true, 1],
    [false, 0],
  ])("fuera de término: con `puede_conciliar` en %s, escribir el motivo se pide %i veces", async (puede, veces) => {
    const fuera = conPago("en_verificacion", [pago({ fuera_de_termino: true })]);
    vi.stubGlobal("fetch", conRevision({ ...fuera, puede_conciliar: puede }));
    abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText(/Se transfirió después de que venció la aprobación\./)).toBeTruthy();
    expect(within(lista).queryAllByText(/escribí por qué lo aceptás/)).toHaveLength(veces);
  });

  it.each([
    [true, 1],
    [false, 0],
  ])("con `puede_confirmar` en %s la declaración se ve y «Confirmar» aparece %i veces", async (puede, veces) => {
    const [d] = declarada.ejecucion.declaraciones;
    vi.stubGlobal(
      "fetch",
      conRevision({ ...declarada, ejecucion: { ...declarada.ejecucion, declaraciones: [{ ...d!, puede_confirmar: puede }] } }),
    );
    abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText(/Rechazo del banco/)).toBeTruthy();
    expect(within(lista).queryAllByRole("button", { name: "Confirmar que no se pagó" })).toHaveLength(veces);
  });

  it("control: sin `egresos.conciliar_pago` los pagos se ven y ninguno de los tres botones", async () => {
    vi.stubGlobal("fetch", conConciliacion());
    abrir("/egresos/conciliacion");
    const lista = await screen.findByRole("list", { name: "Pagos para conciliar" });
    expect(within(lista).getByText("0001234567")).toBeTruthy();
    expect(within(lista).getByText(/Rechazo del banco/)).toBeTruthy();
    for (const nombre of ["Conforme", "Observar", "Confirmar que no se pagó"]) expect(screen.queryByRole("button", { name: nombre })).toBeNull();
  });
});
