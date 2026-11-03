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
    fuera_de_termino: false,
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
    const boton = screen.getByRole("button", { name: "Subir el comprobante" });
    expect((boton as HTMLButtonElement).disabled).toBe(true);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.click(boton);
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
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
    expect(await screen.findByText(/El archivo no se subió/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`)).toHaveLength(1);
    expect(pedidos(api, "POST", `${BASE}/documentos/${NUEVO}/versiones`)).toHaveLength(2);
    // El campo del archivo queda vacío y el botón, deshabilitado.
    expect((screen.getByRole("button", { name: "Subir el comprobante" }) as HTMLButtonElement).disabled).toBe(true);
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
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
    expect(await screen.findByText(/Solo se aceptan archivos PDF, JPEG o PNG/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`)).toHaveLength(0);
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
    fireEvent.change(screen.getByLabelText(/^Archivo del comprobante/), { target: { files: [archivo] } });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(await screen.findByText(/El antivirus revisa el comprobante/)).toBeTruthy();
    expect(registrar.disabled).toBe(true); // en cuarentena todavía no se registra
    const [carga] = pedidos(api, "POST", CARGA);
    expect(await carga?.json()).toEqual({ titulo: "Transferencia: Limpieza de octubre", fecha: "2026-10-31" });
    const [subida] = pedidos(api, "POST", SUBIDA);
    expect(subida?.headers.get("Content-Type")).toBe("application/pdf");
    expect(subida?.headers.get("Idempotency-Key")).toBeTruthy();
    // El antivirus lo aprueba: «Actualizar» lo trae disponible y se registra.
    lista = [{ ...POR_ACLARAR, comprobante_id: NUEVO, comprobante_estado: "disponible" }];
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Subir el comprobante" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    expect(pedidos(api, "POST", SUBIDA)).toHaveLength(1);
    expect(pedidos(api, "POST", `${BASE}/pagos-por-aclarar/${O1}/comprobante`)).toHaveLength(0);
  });

  it("sin pagos para aclarar lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/egresos/por-aclarar");
    expect(await screen.findByText("No tenés pagos para aclarar.")).toBeTruthy();
  });
});
