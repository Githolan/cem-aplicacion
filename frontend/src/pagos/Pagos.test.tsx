import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import type { components } from "../api/tipos";
import { App } from "../App";
import { TEXTO_ALERTA_FRACCIONAMIENTO, VOLVIO_LA_RED } from "../egresos/comun";
import { ContextoVersion } from "../version";
import { cambiosDeVersion } from "./Ficha";
import { contratosAplicables } from "./FormularioOrden";
import { AVISO_ANTIVIRUS_TARDA, AVISO_ARCHIVO_RECHAZADO, AVISO_MODIFICAR, AVISO_RETOMAR, AVISO_SIN_CUENTA, AVISO_SIN_SALIDA, AVISO_VER_LA_CUENTA, type Paso, VERBO } from "./textos";

// La bandeja y la ficha del circuito de pagos. Todos los datos son ficticios: CUIT y CBU inventados,
// banco 999, y nada se transfiere. La API está simulada: que el servidor mande el `paso` correcto lo
// prueban `backend/tests/test_circuito_pagos*.py`; acá, que cada `paso` tenga su acción y que sin el
// `puede_*` no aparezca.

// jsdom no tiene trabajadores ni canvas: el dibujo real lo prueba `e2e/egresos.e2e.ts`.
const pdf = vi.hoisted(() => ({ abrirPdf: vi.fn() }));
vi.mock("./pdf", async (original) => ({ ...(await original<typeof import("./pdf")>()), ...pdf }));
const PAGINA = { getViewport: () => ({ width: 100, height: 100 }), render: () => ({ promise: Promise.resolve() }) };

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "abababab-abab-4bab-8bab-abababababab";
const P1 = "22222222-2222-4222-8222-222222222222";
const O1 = "33333333-3333-4333-8333-333333333333";
const O2 = "34343434-3434-4434-8434-343434343434";
const X1 = "44444444-4444-4444-8444-444444444444";
const PAGO = "55555555-5555-4555-8555-555555555555";
const PERSONA = "66666666-6666-4666-8666-666666666666";
const DOC = "77777777-7777-4777-8777-777777777777";
const ARCHIVO = "88888888-8888-4888-8888-888888888888";
const DECL = "99999999-9999-4999-8999-999999999999";
const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const K1 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const DIF = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const CUENTA = "9990003100000000001234";
const H1 = "a".repeat(64);
const H2 = "b".repeat(64);
const BASE = `/api/v1/consorcios/${C1}`;
const CONSULTA_AL_LADO = "(min-width: 64.0625em)";
const CONSULTA_TELEFONO = "(max-width: 30em)";
const CONTENIDO = `${BASE}/documentos/${DOC}/versiones/${ARCHIVO}/contenido`;
const RUTA_REVISION = `${BASE}/ordenes-pago/${O1}/revision`;
const REVISION = `GET ${RUTA_REVISION}`;

type Ficha = components["schemas"]["FichaPago"];
type Orden = components["schemas"]["OrdenPago"];
type Version = components["schemas"]["VersionOrdenPago"];
type Pago = components["schemas"]["PagoManual"];
type Diferencia = components["schemas"]["DiferenciaPagoManual"];
type Elemento = components["schemas"]["ElementoBandejaPagos"];
type Proveedor = components["schemas"]["Proveedor"];
type Contrato = components["schemas"]["Contrato"];
type Declaracion = components["schemas"]["PagoNoRealizado"];

function orden(extra: Partial<Orden> = {}): Orden {
  return {
    id: O1, proveedor_id: P1, razon_social: "Limpieza Ficticia SRL", importe_centavos: 1_500_000, moneda: "ARS",
    concepto: "Limpieza de octubre", usa_fondo_reserva: false, estado: "aprobada", version_vigente: 1,
    franja: "cuenta_cambiada", alerta_fraccionamiento: false, condicion_beneficiario: "cuenta_cambiada",
    aprobaciones: 1, aprobaciones_requeridas: 1, aprobada_en: "2026-10-31T12:00:00Z",
    aprobacion_vence_en: "2099-11-07T12:00:00Z", aprobada_por_vos: false, motivo_rechazo: null, preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z", actualizada_en: "2026-10-31T12:00:00Z", ...extra,
  };
}

function version(extra: Partial<Version> = {}): Version {
  return {
    numero: 1, proveedor_id: P1, proveedor_version: 1, razon_social: "Limpieza Ficticia SRL", cuit: "20000000001",
    cuenta_ultimos_digitos: CUENTA.slice(-4), importe_centavos: 1_500_000, moneda: "ARS", concepto: "Limpieza de octubre",
    comprobante: "Factura B 0001-00000001", usa_fondo_reserva: false, condicion_beneficiario: "cuenta_cambiada",
    franja: "cuenta_cambiada", alerta_fraccionamiento: false, umbral_importe_centavos: null, umbral_moneda: null,
    contrato_id: null, aprobaciones_requeridas: 1, aprobada_por: [PERSONA], huella: H1, preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z", ...extra,
  };
}

function pago(extra: Partial<Pago> = {}): Pago {
  return {
    id: PAGO, corrige_a: null, numero_operacion: "0001234567", fecha_operacion: "2026-10-31", importe_centavos: 1_500_000,
    moneda: "ARS", documento_id: DOC, version_documento_id: ARCHIVO, registrado_por: PERSONA, registrado_por_nombre: "Marta Ficticia",
    registrado_en: "2026-10-31T13:00:00Z", conciliacion: null, motivo_conciliacion: null, conciliado_por: null,
    conciliado_en: null, fuera_de_termino: false, ...extra,
  };
}

const DECLARACION: Declaracion = {
  id: DECL, motivo: "No había saldo", declarado_por: PERSONA, declarado_en: "2026-10-31T13:00:00Z", confirmado_por: null,
  confirmado_en: null, confirmable: true, puede_confirmar: true,
};

function ejecucion(pagos: Pago[] = [], declaraciones: Declaracion[] = []): NonNullable<Ficha["ejecucion"]> {
  return { id: X1, version: 1, creada_en: "2026-10-31T12:30:00Z", resultado: null, referencia: null, pagos, declaraciones };
}

function ficha(extra: Partial<Ficha> = {}): Ficha {
  return {
    orden: orden(), versiones: [version()], situacion: "para_pagar", paso: null, le_toca_a: null, puede_enviar: false,
    puede_aprobar: false, puede_rechazar: false, puede_renovar: false, puede_pagar: false, puede_reabrir_cuenta: false,
    puede_modificar: false, puede_subir_comprobante: false, puede_registrar: false, puede_declarar: false,
    puede_corregir: false, puede_conciliar: false, puede_confirmar: false, puede_resolver_diferencia: false,
    puede_recuperar: false, puede_preparar_faltante: false, ejecucion: null, comprobante_actual: null,
    comprobante_subido: null, observacion_vigente: null, declaracion_vigente: null, diferencia_vigente: null, ...extra,
  };
}

function diferencia(extra: Partial<Diferencia> = {}): Diferencia {
  return {
    id: DIF, tipo: "importe", importe_real: "12500.00", moneda: "ARS", cuenta_ultimos_digitos: CUENTA.slice(-4),
    importe_orden_centavos: 1_500_000, moneda_orden: "ARS", numero_operacion: "0001234567", fecha_operacion: "2026-10-31",
    documento_id: DOC, version_documento_id: ARCHIVO, explicacion: "Me equivoqué al escribirlo", fuera_de_termino: false,
    registrada_por: PERSONA, registrada_por_nombre: "Marta Ficticia", registrada_en: "2026-10-31T13:00:00Z", resolucion: null,
    resuelta_por: null, resuelta_en: null, recuperada_en: null, falta_centavos: 250_000, sobra_centavos: null,
    se_puede_dar_por_pagada: true, ...extra,
  };
}

const RESOLVER = {
  orden: orden({ estado: "en_verificacion" }), situacion: "con_diferencia", paso: "resolver_diferencia", le_toca_a: "vos",
  puede_resolver_diferencia: true, ejecucion: ejecucion(), diferencia_vigente: diferencia(),
} as const;

const PAGAR = ficha({ paso: "pagar", le_toca_a: "vos", puede_pagar: true });
const SUBIR = ficha({
  orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", paso: "subir_comprobante", le_toca_a: "vos",
  puede_subir_comprobante: true, puede_registrar: true, puede_declarar: true, puede_reabrir_cuenta: true, ejecucion: ejecucion(),
});
const EL_COMPROBANTE = { documento_id: DOC, version_documento_id: ARCHIVO };
/** Con la cuenta vista y el comprobante ya revisado: el formulario está a la vista. */
const LISTO = { ...SUBIR, comprobante_actual: EL_COMPROBANTE };
const OBSERVACION = { pago_id: PAGO, motivo: "No coincide el número", observado_por: PERSONA, observado_en: "2026-11-01T12:00:00Z" };
const CORREGIR = ficha({
  orden: orden({ estado: "en_verificacion" }), situacion: "observado", paso: "corregir", le_toca_a: "vos", puede_corregir: true,
  puede_registrar: true, puede_declarar: true, ejecucion: ejecucion([pago({ conciliacion: "observado", motivo_conciliacion: "No coincide el número" })]),
  observacion_vigente: OBSERVACION, comprobante_actual: EL_COMPROBANTE,
});
const CONCILIAR = ficha({
  orden: orden({ estado: "en_verificacion" }), situacion: "falta_conciliar", paso: "conciliar", le_toca_a: "vos", puede_conciliar: true,
  ejecucion: ejecucion([pago()]), comprobante_actual: EL_COMPROBANTE,
});
const APROBAR = ficha({
  orden: orden({ estado: "en_revision", aprobaciones: 0 }), versiones: [version({ aprobada_por: [] })], situacion: "en_revision",
  paso: "aprobar", le_toca_a: "vos", puede_aprobar: true, puede_rechazar: true,
});
const BORRADOR = ficha({
  orden: orden({ estado: "borrador" }), versiones: [version({ aprobada_por: [] })], situacion: "borrador", paso: "enviar",
  le_toca_a: "vos", puede_enviar: true, puede_modificar: true,
});

const CUENTA_DEL_ACTO = {
  ejecucion_id: X1, version: 1, huella: H1, proveedor_id: P1, proveedor_version: 1, razon_social: "Limpieza Ficticia SRL",
  cuit: "20000000001", cuenta_destino: CUENTA, importe_centavos: 1_500_000, moneda: "ARS", concepto: "Limpieza de octubre",
  comprobante: "Factura B 0001-00000001", aprobada_en: "2026-10-31T12:00:00Z", aprobacion_vence_en: "2026-11-07T12:00:00Z",
};

function elemento(paso: Paso, grupo: Elemento["grupo"], extra: Partial<Elemento> = {}): Elemento {
  return {
    orden_id: O1, proveedor: "Limpieza Ficticia SRL", concepto: "Limpieza de octubre", importe_centavos: 1_500_000, moneda: "ARS",
    paso, grupo, le_toca_a: "vos", ...extra,
  };
}

function proveedor(extra: Partial<Proveedor> = {}): Proveedor {
  return {
    id: P1, cuit: "20000000001", razon_social: "Limpieza Ficticia SRL", rubro: "limpieza", contacto: null,
    cuenta_ultimos_digitos: CUENTA.slice(-4), documento_id: null, estado: "activo", version: 1, huella: H1, alta_por: PERSONA,
    creado_en: "2026-10-20T15:00:00Z", actualizado_en: "2026-10-20T15:00:00Z", ...extra,
  };
}

function contrato(id: string, extra: Partial<Contrato> = {}): Contrato {
  return {
    id, proveedor_id: P1, razon_social: "Limpieza Ficticia SRL", tipo: "abono", alcance: "Limpieza mensual",
    importe_maximo_centavos: 2_000_000, moneda: "ARS", vigente_desde: "2026-01-01", vigente_hasta: "2026-12-31", documento_id: DOC,
    decision_id: null, huella: H1, registrado_por: PERSONA, creado_en: "2026-10-01T12:00:00Z", confirmado_por: PERSONA,
    confirmado_en: "2026-10-02T12:00:00Z", baja_en: null, estado: "vigente", proveedor_version: 1, cuenta_ultimos_digitos: "0012", ...extra,
  };
}

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response | Promise<Response>>;

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);
const bandejaCon = (...elementos: Elemento[]) => () => Response.json({ elementos });
const VERIFICADO = () =>
  Response.json({ verificado_en: "2026-10-31T12:00:00Z", vale_hasta: "2026-10-31T12:10:00Z", codigos_recuperacion_restantes: 9 });

/** Responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  const base: Respuestas = {
    "GET /api/v1/me/consorcios": CONSORCIOS,
    "GET /api/v1/auth/totp": () =>
      Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
    [`GET ${BASE}/pagos/bandeja`]: bandejaCon(),
  };
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? base[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

const escrituras = (simulado: ReturnType<typeof vi.fn>) =>
  simulado.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => p.method === "POST").map((p) => new URL(p.url).pathname);

const claves = (lista: Request[]) => lista.map((p) => p.headers.get("Idempotency-Key"));
const cuerpo = (p: Request | undefined) => (p as Request).clone().json() as Promise<Record<string, unknown>>;

function Lugar() {
  return <output data-testid="lugar">{useLocation().pathname}</output>;
}

function abrir(ruta: string) {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[ruta]}>
            <App />
            <Lugar />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

/** El ancho: en PC la ficha va al lado de la bandeja. */
function ancho(alLado: boolean, telefono = false) {
  vi.stubGlobal("matchMedia", (consulta: string) => {
    const matches = (consulta === CONSULTA_AL_LADO && alLado) || (consulta === CONSULTA_TELEFONO && telefono);
    return { matches, addEventListener: () => undefined, removeEventListener: () => undefined };
  });
}

const lugar = () => screen.getByTestId("lugar").textContent;
const boton = (nombre: string | RegExp) => screen.findByRole("button", { name: nombre });
const sinBoton = (nombre: string | RegExp) => expect(screen.queryByRole("button", { name: nombre })).toBeNull();
const volvioLaRed = () => fireEvent(window, new Event("online"));
const conEsperaManual = () => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
const ESPERA = 3000;
/** Un PNG: el archivo que `…/contenido` devuelve si la prueba no dice otra cosa. */
const IMAGEN = () => new Response(new Blob(["\x89PNG SINTETICO"], { type: "image/png" }), { headers: { "Content-Type": "image/png" } });

/** Abre la ficha de esa orden con esas respuestas. */
function abrirFicha(f: Ficha | (() => Ficha), extra: Respuestas = {}) {
  const leer = () => (typeof f === "function" ? f() : f);
  const api = backend({
    [`GET ${BASE}/pagos/${O1}`]: () => Response.json(leer()),
    [REVISION]: () => {
      const { orden: o, versiones } = leer();
      return Response.json({ orden: o, versiones: versiones.map((x) => ({ ...x, cuenta_destino: CUENTA })), puede_aprobar: true, puede_rechazar: true });
    },
    [`GET ${CONTENIDO}`]: IMAGEN,
    ...extra,
  });
  vi.stubGlobal("fetch", api);
  abrir(`/pagos/${O1}`);
  return api;
}

async function verificarCodigo() {
  fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
}

function escribirRegistro() {
  fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
  fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
  fireEvent.change(screen.getByLabelText(/^Importe transferido/), { target: { value: "15000" } });
  fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
}

async function sinFaltasDeAccesibilidad(contenedor: HTMLElement) {
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations.map((v) => v.id)).toEqual([]);
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
  ancho(false);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:sintetico", revokeObjectURL: () => undefined }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("bandeja", () => {
  it("agrupa por el próximo paso, en el orden del servidor, sin secciones vacías ni cuenta", async () => {
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(
        elemento("pagar", "para_pagar"),
        elemento("aprobar", "para_aprobar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" }),
      ),
    }));
    const { container } = abrir("/pagos");
    const pagar = await screen.findByRole("region", { name: /^Para pagar/ });
    expect(within(pagar).getByRole("link").getAttribute("href")).toBe(`/pagos/${O1}`);
    expect(within(pagar).getByRole("link").textContent).toContain("Pagar");
    expect(screen.getByRole("region", { name: /^Para aprobar/ }).textContent).toContain("Ascensores Ficticios SA");
    expect(screen.queryByRole("region", { name: /^Para conciliar/ })).toBeNull();
    expect(screen.queryByText("No tenés nada para hacer.")).toBeNull();
    expect(container.textContent).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
  });

  it.each(Object.entries(VERBO) as [Paso, string][])("%s dice «%s»", async (paso, verbo) => {
    const grupo = paso.startsWith("espera_") ? "esperando" : "para_pagar";
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento(paso, grupo)) }));
    abrir("/pagos");
    expect((await screen.findByRole("link", { name: new RegExp(verbo) })).getAttribute("href")).toBe(`/pagos/${O1}`);
  });

  it("sin nada pendiente lo dice, y lo que espera a otra persona no cuenta como pendiente", async () => {
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("espera_conciliar", "esperando", { le_toca_a: "consejo" })),
    }));
    abrir("/pagos");
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
    const plegado = screen.getByText("Esperando a otra persona (1)").closest("details") as HTMLElement;
    expect(plegado.textContent).toContain("Falta conciliar");
    expect(plegado.textContent).toContain("Le toca al Consejo.");
  });

  it("control: sin nada, tampoco aparece «Esperando a otra persona»", async () => {
    vi.stubGlobal("fetch", backend({}));
    abrir("/pagos");
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
    expect(screen.queryByText(/Esperando a otra persona/)).toBeNull();
  });

  it("«Preparar una orden» y «Proveedores» se ven con `egresos.preparar_orden`", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden") }));
    abrir("/pagos");
    expect((await screen.findByRole("link", { name: "Preparar una orden" })).getAttribute("href")).toBe("/pagos/nueva");
    expect(screen.getByRole("link", { name: "Proveedores" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Consultar pagos" }).getAttribute("href")).toBe("/pagos/consulta");
  });

  it("control: el Consejo, sin preparar, consulta y no prepara", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden") }));
    abrir("/pagos");
    await screen.findByRole("link", { name: "Consultar pagos" });
    await waitFor(() => expect(screen.queryByRole("link", { name: "Preparar una orden" })).toBeNull());
    expect(screen.queryByRole("link", { name: "Proveedores" })).toBeNull();
  });

  it("si la lectura falla, ofrece Reintentar y vuelve a pedir", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/bandeja`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [] })),
    }));
    abrir("/pagos");
    const reintentar = await boton("Reintentar");
    expect(screen.getByRole("alert").textContent).toContain("El servicio no está disponible");
    caido = false;
    fireEvent.click(reintentar);
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("una lectura fallida se repite sola al volver la red", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/bandeja`]: () => {
        if (caido) throw new TypeError("sin red");
        return Response.json({ elementos: [] });
      },
    }));
    abrir("/pagos");
    await boton("Reintentar");
    caido = false;
    volvioLaRed();
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
    expect(screen.getByText(VOLVIO_LA_RED)).toBeTruthy();
  });

  it("control: si sigue caída no dice que actualizó", async () => {
    const api = backend({ [`GET ${BASE}/pagos/bandeja`]: () => error("error_interno", 500) });
    vi.stubGlobal("fetch", api);
    abrir("/pagos");
    await boton("Reintentar");
    const antes = pedidos(api, "GET", `${BASE}/pagos/bandeja`).length;
    volvioLaRed();
    await waitFor(() => expect(pedidos(api, "GET", `${BASE}/pagos/bandeja`).length).toBeGreaterThan(antes));
    expect(await boton("Reintentar")).toBeTruthy();
    expect(screen.queryByText(VOLVIO_LA_RED)).toBeNull();
  });

  it("un 403 al leer lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/auth/sesion": () => Response.json({}),
      "GET /health": () => Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
      "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
      [`GET ${BASE}/pagos/bandeja`]: () => error("sin_permiso", 403),
    }));
    abrir("/pagos");
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
  });

  it("con varios edificios se elige cuál, y cambiar sale de la ficha de otro", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Azul", vinculos: [] }, { id: C2, nombre: "Bordó", vinculos: [] }]),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(PAGAR),
      [`GET /api/v1/consorcios/${C2}/pagos/bandeja`]: bandejaCon(),
      [`GET /api/v1/consorcios/${C2}/pagos/${O1}`]: () => error("no_encontrado", 404),
    });
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    await screen.findByRole("heading", { level: 1, name: "Limpieza Ficticia SRL" });
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(screen.queryByRole("heading", { name: "Limpieza Ficticia SRL" })).toBeNull();
  });
});

describe("el ancho de la pantalla", () => {
  const CON_UNA = { [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("pagar", "para_pagar")), [`GET ${BASE}/pagos/${O1}`]: () => Response.json(PAGAR) };

  it("en PC la primera orden se abre al lado de la bandeja, como título de segundo nivel", async () => {
    ancho(true);
    vi.stubGlobal("fetch", backend(CON_UNA));
    const { container } = abrir("/pagos");
    expect(await screen.findByRole("heading", { level: 2, name: "Limpieza Ficticia SRL" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Pagos" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Pagar/ }).getAttribute("aria-current")).toBe("true");
    expect(container.querySelector(".circuito-pagos--al-lado")).not.toBeNull();
  });

  it("en PC la orden abierta sola queda fija aunque la bandeja se reordene, y la cuenta sigue a la vista", async () => {
    ancho(true);
    let actual = PAGAR;
    let reordenada = false;
    const OTRA = elemento("pagar", "para_pagar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" });
    const api = backend({
      [`GET ${BASE}/pagos/bandeja`]: () =>
        reordenada ? bandejaCon(OTRA, elemento("subir_comprobante", "para_pagar"))() : bandejaCon(elemento("pagar", "para_pagar"), OTRA)(),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(actual),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => {
        actual = { ...PAGAR, orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", ejecucion: ejecucion() };
        reordenada = true;
        return Response.json({ id: O1, version: 1 });
      },
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => {
        actual = SUBIR;
        return Response.json(CUENTA_DEL_ACTO);
      },
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos");
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Pagar|Subir|comprobante/i })[0]?.textContent).toContain("Ascensores"));
    expect(screen.getByText(CUENTA)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Limpieza Ficticia SRL" })).toBeTruthy();
    expect(lugar()).toBe("/pagos");
  });

  it("control: en PC, al terminar el acto se suelta y se abre la primera de la bandeja nueva", async () => {
    ancho(true);
    let terminado = false;
    const OTRA: Ficha = { ...BORRADOR, orden: orden({ id: O2, estado: "borrador" }), versiones: [version({ razon_social: "Ascensores Ficticios SA" })] };
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/bandeja`]: () =>
        (terminado ? bandejaCon(elemento("enviar", "para_preparar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" })) : bandejaCon(elemento("aprobar", "para_aprobar")))(),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(APROBAR),
      [`GET ${BASE}/pagos/${O2}`]: () => Response.json(OTRA),
      [REVISION]: () => Response.json({ orden: APROBAR.orden, versiones: APROBAR.versiones.map((x) => ({ ...x, cuenta_destino: CUENTA })), puede_aprobar: true, puede_rechazar: true }),
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => {
        terminado = true;
        return Response.json({ id: O1, version: 1 }, { status: 201 });
      },
    }));
    abrir("/pagos");
    const aprobar = (await boton("Aprobar esta versión")) as HTMLButtonElement;
    await waitFor(() => expect(aprobar.disabled).toBe(false));
    fireEvent.click(aprobar);
    expect(await screen.findByRole("heading", { level: 2, name: "Ascensores Ficticios SA" })).toBeTruthy();
  });

  it("si mientras se busca el pago siguiente se abrió otra orden, esa queda y la bandeja se relee", async () => {
    ancho(true);
    let colgar = false;
    const colgadas: ((r: Response) => void)[] = [];
    const OTRA: Ficha = { ...BORRADOR, orden: orden({ id: O2, estado: "borrador" }), versiones: [version({ razon_social: "Ascensores Ficticios SA" })] };
    const lista = bandejaCon(elemento("conciliar", "para_conciliar"), elemento("enviar", "para_preparar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" }));
    const api = backend({
      [`GET ${BASE}/pagos/bandeja`]: () => (colgar ? new Promise<Response>((r) => colgadas.push(r)) : lista()),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(CONCILIAR),
      [`GET ${BASE}/pagos/${O2}`]: () => Response.json(OTRA),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`]: () => {
        colgar = true;
        return Response.json({ id: PAGO, orden_id: O1 }, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    fireEvent.click(await boton("Conforme"));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`)).toHaveLength(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    fireEvent.click(screen.getByRole("link", { name: /Ascensores Ficticios SA/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Ascensores Ficticios SA" })).toBeTruthy();
    const leidas = pedidos(api, "GET", `${BASE}/pagos/bandeja`).length;
    colgar = false;
    await act(async () => {
      for (const soltar of colgadas) soltar(lista());
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(lugar()).toBe(`/pagos/${O2}`);
    expect(screen.queryByText("Listo: conciliaste el pago. La orden quedó pagada.")).toBeNull();
    expect(pedidos(api, "GET", `${BASE}/pagos/bandeja`).length).toBeGreaterThan(leidas);
  });

  it("la subida que termina con la ficha ya cerrada no mueve la que se abrió después", async () => {
    ancho(true);
    let soltar: (r: Response) => void = () => undefined;
    const OTRA: Ficha = { ...BORRADOR, orden: orden({ id: O2, estado: "borrador" }), versiones: [version({ razon_social: "Ascensores Ficticios SA" })] };
    const ARCHIVO_NUEVO = `${BASE}/documentos/${NUEVO}/versiones`;
    const api = backend({
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("subir_comprobante", "para_pagar"), elemento("enviar", "para_preparar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" })),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(SUBIR),
      [`GET ${BASE}/pagos/${O2}`]: () => Response.json(OTRA),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`POST ${ARCHIVO_NUEVO}`]: () => new Promise<Response>((r) => (soltar = r)),
    });
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    fireEvent.change(await screen.findByLabelText("Archivo del comprobante"), {
      target: { files: [new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(pedidos(api, "POST", ARCHIVO_NUEVO)).toHaveLength(1));
    fireEvent.click(screen.getByRole("link", { name: /Ascensores Ficticios SA/ }));
    await screen.findByRole("heading", { level: 2, name: "Ascensores Ficticios SA" });
    const lecturas = pedidos(api, "GET", `${BASE}/pagos/${O1}`).length;
    await act(async () => {
      soltar(Response.json({ id: ARCHIVO, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 202 }));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(lugar()).toBe(`/pagos/${O2}`);
    expect(screen.getByRole("heading", { level: 2, name: "Ascensores Ficticios SA" })).toBeTruthy();
    expect(screen.queryByText("Estamos revisando el comprobante")).toBeNull();
    expect(pedidos(api, "GET", `${BASE}/pagos/${O1}`)).toHaveLength(lecturas);
  });

  it("control: en tablet y teléfono la bandeja no abre ninguna ficha", async () => {
    const api = backend(CON_UNA);
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/pagos");
    await screen.findByRole("link", { name: /Pagar/ });
    expect(pedidos(api, "GET", `${BASE}/pagos/${O1}`)).toHaveLength(0);
    expect(container.querySelector(".circuito-pagos--al-lado")).toBeNull();
  });

  it("en tablet y teléfono la ficha va aparte, con la salida a la bandeja y sin leerla", async () => {
    const api = backend(CON_UNA);
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Limpieza Ficticia SRL" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Volver a Pagos" })).toHaveLength(1);
    expect(pedidos(api, "GET", `${BASE}/pagos/bandeja`)).toHaveLength(0);
  });
});

describe("ficha: cada paso tiene su botón, y sin su `puede_*` no aparece", () => {
  const CASOS: [string, Ficha, string, keyof Ficha][] = [
    ["enviar", BORRADOR, "Enviar a aprobar", "puede_enviar"],
    ["renovar", ficha({ paso: "renovar", puede_renovar: true }), "Renovar la aprobación", "puede_renovar"],
    ["volver_a_pagar", ficha({ situacion: "no_se_pago", paso: "volver_a_pagar", puede_modificar: true }), "Volver a pagar", "puede_modificar"],
    ["aprobar", APROBAR, "Aprobar esta versión", "puede_aprobar"],
    ["pagar", PAGAR, "Ver la cuenta para pagar", "puede_pagar"],
    ["subir_comprobante", SUBIR, "Elegir el archivo", "puede_subir_comprobante"],
    ["corregir", CORREGIR, "Guardar la corrección", "puede_corregir"],
    ["declarar_no_pago", ficha({ situacion: "pagando", paso: "declarar_no_pago", puede_declarar: true }), "No se pudo pagar", "puede_declarar"],
    ["conciliar", CONCILIAR, "Conforme", "puede_conciliar"],
    [
      "confirmar_no_pago",
      ficha({ situacion: "no_pago_declarado", paso: "confirmar_no_pago", puede_confirmar: true, declaracion_vigente: DECLARACION }),
      "Confirmar que no se pagó",
      "puede_confirmar",
    ],
  ];

  it.each(CASOS)("%s", async (_, f, nombre, puede) => {
    abrirFicha(f);
    expect(await boton(nombre)).toBeTruthy();
    cleanup();
    abrirFicha({ ...f, [puede]: false });
    await screen.findByRole("heading", { level: 1, name: "Limpieza Ficticia SRL" });
    sinBoton(nombre);
  });

  it.each([
    ["espera_aprobar", "en_revision", "consejo", "Le toca al Consejo."],
    ["espera_conciliar", "falta_conciliar", "consejo", "Le toca al Consejo."],
    ["espera_corregir", "observado", "quien_registro", "Le toca a quien registró el pago."],
    ["espera_declarar", "pagando", "quien_ejecuta", "Le toca a quien ejecuta los pagos."],
    ["espera_comprobante", "pagando", "quien_vio_la_cuenta", "Le toca a quien vio la cuenta."],
    ["espera_confirmar", "no_pago_declarado", "quien_vio_la_cuenta", "Le toca a quien vio la cuenta."],
  ] as [Paso, Ficha["situacion"], Ficha["le_toca_a"], string][])("%s dice a quién le toca y no ofrece nada", async (paso, situacion, leToca, texto) => {
    const api = abrirFicha(ficha({ paso, situacion, le_toca_a: leToca }));
    expect(await screen.findByText(texto)).toBeTruthy();
    expect(within(document.querySelector(".ficha-pago") as HTMLElement).queryAllByRole("button")).toHaveLength(0);
    expect(escrituras(api)).toEqual([]);
  });

  it("una orden cerrada se ve de solo lectura, con lo pagado y sin acciones", async () => {
    abrirFicha(ficha({ orden: orden({ estado: "pagada" }), situacion: "pagada", ejecucion: ejecucion([pago({ conciliacion: "conforme" })]) }));
    expect(await screen.findByText("Pagada")).toBeTruthy();
    expect(screen.getByText("0001234567")).toBeTruthy();
    expect(within(document.querySelector(".ficha-pago") as HTMLElement).queryAllByRole("button")).toHaveLength(0);
  });

  it("la orden rechazada muestra el motivo y lleva a preparar otra", async () => {
    abrirFicha(ficha({ orden: orden({ estado: "rechazada", motivo_rechazo: "Falta el presupuesto" }), situacion: "rechazada", paso: "rechazada" }));
    expect(await screen.findByText("Falta el presupuesto")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Preparar otra orden" }).getAttribute("href")).toBe("/pagos/nueva");
  });

  it("una orden que no está en el edificio lo dice y deja volver", async () => {
    abrirFicha(PAGAR, { [`GET ${BASE}/pagos/${O1}`]: () => error("no_encontrado", 404) });
    expect(await screen.findByText("Esa orden no está en este edificio.")).toBeTruthy();
  });

  it("no muestra datos que no hacen falta para decidir, salvo en «Más datos»", async () => {
    abrirFicha(APROBAR);
    await boton("Aprobar esta versión");
    const mas = screen.getByText("Más datos").closest("details") as HTMLElement;
    expect(mas.hasAttribute("open")).toBe(false);
    expect(mas.textContent).toContain("20000000001");
    expect(mas.textContent).toContain("Cuenta cambiada");
  });

  it("la alerta de fraccionamiento se ve al decidir", async () => {
    abrirFicha({ ...APROBAR, versiones: [version({ alerta_fraccionamiento: true, aprobada_por: [] })] });
    expect(await screen.findByText(TEXTO_ALERTA_FRACCIONAMIENTO)).toBeTruthy();
  });

  it("control: decidida, la alerta ya no se ve y la franja dice lo que pasó", async () => {
    abrirFicha({ ...PAGAR, versiones: [version({ alerta_fraccionamiento: true })] });
    await boton("Ver la cuenta para pagar");
    expect(screen.queryByText(TEXTO_ALERTA_FRACCIONAMIENTO)).toBeNull();
    expect(screen.getByText("Más datos").closest("details")?.textContent).toContain("Cuenta cambiada: pidió aprobación");
  });

  it("con una versión nueva se ve lo que cambió, sin repetir lo demás", () => {
    expect(cambiosDeVersion(version({ importe_centavos: 200_000 }), version({ importe_centavos: 100_000 }))).toEqual([
      expect.stringContaining("Cambió el importe"),
    ]);
    expect(cambiosDeVersion(version(), version())).toEqual([]);
  });

  it("sin faltas de accesibilidad con el formulario a la vista", async () => {
    abrirFicha(LISTO);
    await boton("Registrar el pago");
    await sinFaltasDeAccesibilidad(document.body);
  });
});

describe("pagar", () => {
  /** La ficha cambia con la orden, como en el servidor. */
  function circuito(extra: Respuestas = {}) {
    let actual = PAGAR;
    const api = abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => {
        actual = { ...PAGAR, orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", ejecucion: ejecucion() };
        return Response.json({ id: O1, version: 1 });
      },
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => {
        actual = SUBIR;
        return Response.json(CUENTA_DEL_ACTO);
      },
      ...extra,
    });
    return api;
  }

  it("si emitir pasa y la cuenta no, la bandeja igual se relee: la orden ya cambió", async () => {
    ancho(true);
    let actual = PAGAR;
    const api = backend({
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("pagar", "para_pagar")),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(actual),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => {
        actual = { ...PAGAR, orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", ejecucion: ejecucion() };
        return Response.json({ id: O1, version: 1 });
      },
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => error("error_interno", 500),
    });
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    const verla = await boton("Ver la cuenta para pagar");
    const leidas = pedidos(api, "GET", `${BASE}/pagos/bandeja`).length;
    fireEvent.click(verla);
    await screen.findByRole("alert");
    expect(pedidos(api, "GET", `${BASE}/pagos/bandeja`).length).toBeGreaterThan(leidas);
  });

  it("la cuenta no está antes del acto y el botón avisa qué deja registrado", async () => {
    circuito();
    await boton("Ver la cuenta para pagar");
    expect(screen.getByText(AVISO_VER_LA_CUENTA)).toBeTruthy();
    expect(document.body.textContent).not.toContain(CUENTA);
  });

  it("aprobada, encadena emitir y ver la cuenta con un toque, con la versión y la huella vistas", async () => {
    const api = circuito();
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(screen.getByText(/mirá que el titular sea/).textContent).toContain("Limpieza Ficticia SRL");
    expect(escrituras(api)).toEqual([`${BASE}/ordenes-pago/${O1}/emitir`, `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]);
    const [emision] = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`);
    expect(await cuerpo(emision)).toEqual({ version: 1, huella: H1 });
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`)[0])).toEqual({ version: 1, huella: H1 });
    expect(await boton("Elegir el archivo")).toBeTruthy();
    sinBoton("Ver la cuenta para pagar");
  });

  it("control: ya en ejecución no emite otra vez: solo abre la cuenta", async () => {
    const api = abrirFicha({ ...PAGAR, orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", ejecucion: ejecucion() }, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
    });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(escrituras(api)).toEqual([`${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]);
  });

  it("el encadenado pide un solo gesto: el código vale para emitir y para la cuenta, con la misma clave", async () => {
    let conFactor = false;
    let actual = PAGAR;
    const api = abrirFicha(() => actual, {
      "POST /api/v1/auth/totp/verificar": () => {
        conFactor = true;
        return VERIFICADO();
      },
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => {
        if (!conFactor) return error("segundo_factor_requerido", 403);
        actual = { ...PAGAR, orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", ejecucion: ejecucion() };
        return Response.json({ id: O1, version: 1 });
      },
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => (conFactor ? Response.json(CUENTA_DEL_ACTO) : error("segundo_factor_requerido", 403)),
    });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    await verificarCodigo();
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    const emisiones = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`);
    expect(emisiones).toHaveLength(2);
    expect(new Set(claves(emisiones)).size).toBe(1);
    expect(pedidos(api, "POST", "/api/v1/auth/totp/verificar")).toHaveLength(1);
  });

  it("si emitió y la cuenta no llegó, el reintento no vuelve a emitir", async () => {
    let caido = true;
    const api = circuito({
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => {
        if (caido) throw new TypeError("sin red");
        return Response.json(CUENTA_DEL_ACTO);
      },
    });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect((await screen.findByRole("alert")).textContent).toContain("conexión");
    caido = false;
    await waitFor(() => expect(pedidos(api, "GET", `${BASE}/pagos/${O1}`).length).toBeGreaterThan(2));
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`)).toHaveLength(1);
  });

  it("quien no puede ver la cuenta recibe el error en la ficha y no hay cuenta", async () => {
    abrirFicha(PAGAR, { [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => error("sin_permiso", 403) });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect((await screen.findByRole("alert")).textContent).toContain("personas distintas");
    expect(document.body.textContent).not.toContain(CUENTA);
  });

  it("si el contrato dejó de regir, la ficha vuelve a leer y ofrece renovar", async () => {
    let actual = PAGAR;
    abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => {
        actual = ficha({ paso: "renovar", puede_renovar: true, puede_modificar: true });
        return error("contrato_no_vigente", 409);
      },
    });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    expect(await boton("Renovar la aprobación")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("ya no rige");
    sinBoton("Ver la cuenta para pagar");
  });

  it("emitir de nuevo tras un 500 lleva la misma clave", async () => {
    const api = abrirFicha(PAGAR, { [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => error("error_interno", 500) });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    await screen.findByRole("alert");
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`)).toHaveLength(2));
    expect(new Set(claves(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`))).size).toBe(1);
  });

  it("volver la red nunca repite un pedido que escribe", async () => {
    const api = abrirFicha(PAGAR, { [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () => error("error_interno", 500) });
    fireEvent.click(await boton("Ver la cuenta para pagar"));
    await screen.findByText(/El servicio no está disponible/);
    volvioLaRed();
    await new Promise((r) => setTimeout(r, 0));
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`)).toHaveLength(1);
  });

  it("quien ya la vio la vuelve a pedir con un botón aparte", async () => {
    const api = abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
    });
    await boton("Registrar el pago");
    expect(document.body.textContent).not.toContain(CUENTA);
    fireEvent.click(await boton("Ver la cuenta de nuevo"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/emitir`)).toHaveLength(0);
  });

  it("sin poder reabrirla, lo dice y deja registrar o declarar", async () => {
    abrirFicha({ ...LISTO, puede_reabrir_cuenta: false });
    expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
    sinBoton("Ver la cuenta de nuevo");
    expect(await boton("Registrar el pago")).toBeTruthy();
    expect(await boton("No se pudo pagar")).toBeTruthy();
  });

  it("si la orden cambió de versión, la cuenta de la anterior se va y lo escrito queda", async () => {
    let actual = LISTO;
    abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => {
        actual = {
          ...LISTO, orden: orden({ estado: "en_ejecucion", version_vigente: 2 }), puede_reabrir_cuenta: false,
          versiones: [version(), version({ numero: 2, huella: H2 })],
        };
        return error("version_desactualizada", 409);
      },
    });
    fireEvent.click(await boton("Ver la cuenta de nuevo"));
    await screen.findByText(CUENTA);
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    expect(await screen.findByText(AVISO_SIN_CUENTA)).toBeTruthy();
    expect(screen.queryByText(CUENTA)).toBeNull();
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
  });
});

describe("registrar y corregir", () => {
  it("registra lo escrito por la persona, no lo de la orden, y vuelve a la bandeja con el aviso", async () => {
    const api = abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    await screen.findByLabelText("Número de operación");
    expect((screen.getByLabelText(/^Importe transferido/) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(/^CBU\/CVU de destino/) as HTMLInputElement).value).toBe("");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`)[0])).toEqual({
      version: 1, huella: H1, numero_operacion: "0001-234567", fecha_operacion: "2026-10-31", importe_centavos: 1_500_000, moneda: "ARS",
      cuenta_destino: CUENTA, documento_id: DOC,
    });
  });

  it("si falla la recarga después de registrar, el «Listo» queda y se puede reintentar", async () => {
    let caido = false;
    abrirFicha(LISTO, {
      [`GET ${BASE}/pagos/bandeja`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [] })),
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => {
        caido = true;
        return Response.json({ id: PAGO, orden_id: O1 }, { status: 201 });
      },
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    expect(await boton("Reintentar")).toBeTruthy();
    expect(screen.getByText(/Listo: registraste el pago/)).toBeTruthy();
  });

  it("un error queda en la ficha con lo escrito", async () => {
    abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => error("no_coincide_con_la_orden", 422),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    expect((await screen.findByRole("alert")).textContent).toContain("no son los de la orden");
    expect(lugar()).toBe(`/pagos/${O1}`);
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
  });

  it("pagué otra cosa: lleva lo que salió del banco, y no dice que quedó pagada", async () => {
    const api = abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/diferencia`]: () => Response.json({ id: DIF, orden_id: O1 }, { status: 201 }),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Pagué otra cosa"));
    fireEvent.change(screen.getByLabelText("Importe que salió del banco"), { target: { value: "12.500,50" } });
    fireEvent.change(screen.getByLabelText("Qué pasó"), { target: { value: " Me equivoqué " } });
    fireEvent.click(await boton("Registrar lo que pasó"));
    expect(await screen.findByText(/La orden no queda pagada/)).toBeTruthy();
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/diferencia`)[0])).toMatchObject({
      version: 1, huella: H1, importe_real: "12500.50", moneda: "ARS", cuenta_real: CUENTA, documento_id: DOC, explicacion: "Me equivoqué",
    });
  });

  it("pagué otra cosa en otra moneda lleva la moneda que se escribió, no la de la orden", async () => {
    const api = abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/diferencia`]: () => Response.json({ id: DIF, orden_id: O1 }, { status: 201 }),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Pagué otra cosa"));
    const moneda = screen.getByLabelText("Moneda que salió del banco") as HTMLInputElement;
    expect(moneda.value).toBe("ARS");
    fireEvent.change(moneda, { target: { value: "usd" } });
    expect(moneda.value).toBe("USD");
    fireEvent.change(screen.getByLabelText("Qué pasó"), { target: { value: "Salió en dólares" } });
    fireEvent.click(await boton("Registrar lo que pasó"));
    await screen.findByText(/La orden no queda pagada/);
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/diferencia`)[0])).toMatchObject({ moneda: "USD" });
  });

  it("pagué otra cosa con los mismos datos lo dice y deja lo escrito", async () => {
    abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/diferencia`]: () => error("sin_diferencia", 422),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Pagué otra cosa"));
    fireEvent.change(screen.getByLabelText("Qué pasó"), { target: { value: "Nada" } });
    fireEvent.click(await boton("Registrar lo que pasó"));
    expect((await screen.findByRole("alert")).textContent).toContain("es lo mismo que la orden");
    expect((screen.getByLabelText("Qué pasó") as HTMLTextAreaElement).value).toBe("Nada");
  });

  it("sin poder registrar no ofrece pagué otra cosa", async () => {
    abrirFicha({ ...LISTO, puede_registrar: false });
    await screen.findByLabelText("Número de operación");
    expect(screen.queryByRole("button", { name: "Pagué otra cosa" })).toBeNull();
  });

  it("al corregir, pagué otra cosa pide la cuenta del comprobante", async () => {
    abrirFicha(CORREGIR);
    await screen.findByLabelText("Número de operación");
    expect(screen.queryByLabelText("CBU/CVU de destino (como figura en el comprobante)")).toBeNull();
    fireEvent.click(await boton("Pagué otra cosa"));
    expect(screen.getByLabelText("CBU/CVU de destino (como figura en el comprobante)")).toBeTruthy();
  });

  describe("resolver lo que se pagó", () => {
    const CAMINO = `${BASE}/ordenes-pago/${O1}/pago-manual/diferencia/${DIF}`;

    it("de menos ofrece dar por pagado lo real y dice lo que falta", async () => {
      const api = abrirFicha(ficha(RESOLVER), {
        [`POST ${CAMINO}/resolucion`]: () => Response.json({ orden_id: O1, estado: "pagada" }),
      });
      await screen.findByText("La orden contra lo que salió del banco");
      expect(screen.getByRole("radio", { name: /Dar por pagados .*12\.500,00 y preparar otra orden por .*2\.500,00/ })).toBeTruthy();
      fireEvent.click(await boton("Resolver"));
      expect(await screen.findByText(/quedó pagada por lo que salió del banco/)).toBeTruthy();
      expect(await cuerpo(pedidos(api, "POST", `${CAMINO}/resolucion`)[0])).toEqual({ resolucion: "pagada_por_lo_real" });
    });

    it("otra cuenta solo ofrece reclamar, enmascarada", async () => {
      const api = abrirFicha(ficha({
        ...RESOLVER,
        diferencia_vigente: diferencia({ tipo: "cuenta", cuenta_ultimos_digitos: "9044", importe_real: "15000.00", falta_centavos: null, se_puede_dar_por_pagada: false }),
      }), { [`POST ${CAMINO}/resolucion`]: () => Response.json({ orden_id: O1, estado: "en_verificacion" }) });
      expect(await screen.findByText("…9044")).toBeTruthy();
      expect(screen.queryByRole("radio", { name: /Dar por pagados/ })).toBeNull();
      fireEvent.click(await boton("Resolver"));
      expect(await screen.findByText(/queda abierta hasta recuperar/)).toBeTruthy();
      expect(await cuerpo(pedidos(api, "POST", `${CAMINO}/resolucion`)[0])).toEqual({ resolucion: "reclamo" });
    });

    it("a quien espera le dice que no transfiera, sin botones de resolver", async () => {
      abrirFicha(ficha({ ...RESOLVER, paso: "espera_resolver", le_toca_a: "consejo", puede_resolver_diferencia: false }));
      expect(await screen.findByText(/No transfieras de nuevo/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Resolver" })).toBeNull();
    });

    it("con el reclamo abierto se dice que volvió la plata", async () => {
      const api = abrirFicha(ficha({
        ...RESOLVER, situacion: "reclamo", puede_resolver_diferencia: false, puede_recuperar: true,
        diferencia_vigente: diferencia({ resolucion: "reclamo", resuelta_en: "2026-11-01T12:00:00Z" }),
      }), { [`POST ${CAMINO}/recuperada`]: () => Response.json({ orden_id: O1, estado: "fallida" }) });
      expect(await screen.findByText("Reclamo abierto")).toBeTruthy();
      fireEvent.click(await boton("Ya recuperamos la plata"));
      expect(await screen.findByText(/se puede volver a pagar/)).toBeTruthy();
      expect(pedidos(api, "POST", `${CAMINO}/recuperada`)).toHaveLength(1);
    });

    it("pagada de menos lleva a preparar lo que falta, sin crear nada", async () => {
      abrirFicha(ficha({
        orden: orden({ estado: "pagada" }), situacion: "pagada", paso: "preparar_faltante", le_toca_a: "vos", puede_preparar_faltante: true,
        ejecucion: ejecucion(), diferencia_vigente: diferencia({ resolucion: "pagada_por_lo_real", resuelta_en: "2026-11-01T12:00:00Z" }),
      }));
      expect(await screen.findByRole("link", { name: "Preparar la orden por lo que falta" })).toBeTruthy();
      expect(screen.getByText("Falta pagar")).toBeTruthy();
    });
  });

  it("con otra operación ya usada sigue en la ficha", async () => {
    abrirFicha(LISTO, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => error("operacion_de_otra_orden", 409),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    expect((await screen.findByRole("alert")).textContent).toContain("ya respalda otra orden");
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
  });

  it("el segundo factor no se lleva lo escrito y reintenta con la misma clave", async () => {
    let conFactor = false;
    const api = abrirFicha(LISTO, {
      "POST /api/v1/auth/totp/verificar": () => {
        conFactor = true;
        return VERIFICADO();
      },
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () =>
        conFactor ? Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }) : error("segundo_factor_requerido", 403),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    await verificarCodigo();
    expect(await screen.findByText(/Listo: registraste el pago/)).toBeTruthy();
    const registros = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`);
    expect(registros).toHaveLength(2);
    expect(new Set(claves(registros)).size).toBe(1);
  });

  it("corregir abre con lo registrado y el comprobante al lado, y no vuelve a pedir la cuenta", async () => {
    const api = abrirFicha(CORREGIR, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    expect((await screen.findByText(/no transfieras de nuevo/)).textContent).toContain("No coincide el número");
    expect((await screen.findByRole("img", { name: "Comprobante de la transferencia" })).getAttribute("src")).toBe("blob:sintetico");
    expect((await screen.findByLabelText<HTMLInputElement>("Número de operación")).value).toBe("0001234567");
    expect(screen.getByLabelText<HTMLInputElement>("Fecha de la transferencia").value).toBe("2026-10-31");
    expect(screen.getByLabelText<HTMLInputElement>(/^Importe transferido/).value).toBe("15.000,00");
    expect(screen.queryByLabelText(/^CBU\/CVU de destino/)).toBeNull();
    expect(screen.getByText(/la que ya registraste/).textContent).toContain(CUENTA.slice(-4));
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-999" } });
    fireEvent.click(await boton("Guardar la corrección"));
    await screen.findByText(/Listo: registraste el pago/);
    // Una sola escritura: ni `pago-manual/cuenta`, ni otra subida.
    expect(escrituras(api)).toEqual([`${BASE}/ordenes-pago/${O1}/pago-manual`]);
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`)[0])).toEqual({
      version: 1, huella: H1, numero_operacion: "0001-999", fecha_operacion: "2026-10-31", importe_centavos: 1_500_000, moneda: "ARS",
      documento_id: DOC,
    });
  });

  it("control: un registro nuevo no se llena con nada de la orden y pide la cuenta", async () => {
    abrirFicha(LISTO);
    expect((await screen.findByLabelText<HTMLInputElement>("Número de operación")).value).toBe("");
    expect(screen.getByLabelText<HTMLInputElement>(/^Importe transferido/).value).toBe("");
    expect(screen.getByLabelText<HTMLInputElement>(/^CBU\/CVU de destino/).value).toBe("");
  });

  it("observado, sin ser quien registró: no corrige y sabe a quién le toca", async () => {
    abrirFicha({ ...CORREGIR, paso: "espera_corregir", le_toca_a: "quien_registro", puede_corregir: false, puede_declarar: false });
    expect(await screen.findByText("Le toca a quien registró el pago.")).toBeTruthy();
    expect(screen.getByText("Observado")).toBeTruthy();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
  });
});

describe("el comprobante en un paso", () => {
  const CREAR = `${BASE}/ordenes-pago/${O1}/pago-manual/comprobante`;
  const SUBIDA = `${BASE}/documentos/${NUEVO}/versiones`;
  const COMPROBANTE = {
    [`POST ${CREAR}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
    [`POST ${SUBIDA}`]: () => Response.json({ id: ARCHIVO, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 202 }),
  };
  const EN_REVISION = { ...SUBIR, comprobante_subido: { documento_id: NUEVO, version_documento_id: K1, estado: "en_cuarentena" as const } };
  const RECHAZADO = { ...SUBIR, comprobante_subido: { documento_id: NUEVO, version_documento_id: K1, estado: "rechazado" as const } };
  const APROBADO = { ...SUBIR, comprobante_actual: { documento_id: NUEVO, version_documento_id: K1 } };
  const PDF = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
  const FOTO = new File(["\xff\xd8\xff SINTETICO"], "foto.jpg", { type: "image/jpeg" });

  async function elegir(archivo = PDF) {
    fireEvent.change(await screen.findByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
  }

  /** La ficha cambia cuando se sube el archivo, como en el servidor. */
  function conSubida(extra: Respuestas = {}) {
    let actual: Ficha = SUBIR;
    const cambiar = (f: Ficha) => {
      actual = f;
    };
    const api = abrirFicha(() => actual, {
      ...COMPROBANTE,
      [`POST ${SUBIDA}`]: () => {
        actual = EN_REVISION;
        return Response.json({ id: K1, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 201 });
      },
      [`GET ${BASE}/documentos/${NUEVO}/versiones/${K1}/contenido`]: IMAGEN,
      ...extra,
    });
    return { api, cambiar };
  }

  it("elegir el archivo dispara todo, sin un botón de subir ni una lista", async () => {
    const api = abrirFicha(SUBIR, COMPROBANTE);
    expect(await boton("Elegir el archivo")).toBeTruthy();
    expect(screen.getByText("Soltá acá el comprobante")).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    sinBoton("Subir");
    sinBoton("Registrar el pago");
    await elegir();
    await waitFor(() => expect(escrituras(api)).toEqual([CREAR, SUBIDA]));
    const [archivo] = pedidos(api, "POST", SUBIDA);
    expect(archivo?.headers.get("Content-Type")).toBe("application/pdf");
    expect(await cuerpo(pedidos(api, "POST", CREAR)[0])).toMatchObject({ titulo: "Transferencia a Limpieza Ficticia SRL" });
    expect(pedidos(api, "GET", `${BASE}/documentos`)).toHaveLength(0);
  });

  it("soltar un archivo en la zona también lo sube", async () => {
    const api = abrirFicha(SUBIR, COMPROBANTE);
    const zona = (await screen.findByText("Soltá acá el comprobante")).parentElement as HTMLElement;
    fireEvent.drop(zona, { dataTransfer: { files: [PDF] } });
    await waitFor(() => expect(escrituras(api)).toEqual([CREAR, SUBIDA]));
  });

  it("en el teléfono ofrece la cámara y la galería, y no la zona para soltar", async () => {
    ancho(false, true);
    abrirFicha(SUBIR);
    expect(await boton("Sacar una foto")).toBeTruthy();
    expect(await boton("Elegir de la galería")).toBeTruthy();
    expect(screen.getByLabelText("Foto del comprobante").getAttribute("capture")).toBe("environment");
    expect(screen.queryByText("Soltá acá el comprobante")).toBeNull();
  });

  it("control: fuera del teléfono no hay cámara", async () => {
    abrirFicha(SUBIR);
    await boton("Elegir el archivo");
    sinBoton("Sacar una foto");
    expect(screen.queryByLabelText("Foto del comprobante")).toBeNull();
  });

  it("una imagen se ve enseguida desde el archivo local y un PDF dice su nombre", async () => {
    const { cambiar } = conSubida();
    await elegir(FOTO);
    expect((await screen.findByRole("img", { name: "Vista previa de foto.jpg" })).getAttribute("src")).toBe("blob:sintetico");
    cambiar(SUBIR);
  });

  it("control: de un PDF se ve el nombre, porque se muestra recién cuando pasa el antivirus", async () => {
    conSubida();
    await elegir();
    expect(await screen.findByText("comprobante.pdf")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("la ficha dice en qué va, consulta sola y al pasar abre el formulario con el comprobante", async () => {
    conEsperaManual();
    const { api, cambiar } = conSubida();
    await elegir();
    expect(await screen.findByText("Estamos revisando el comprobante")).toBeTruthy();
    expect(await screen.findByText("El antivirus lo está revisando…")).toBeTruthy();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
    sinBoton("Actualizar comprobantes");
    cambiar(APROBADO);
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await screen.findByLabelText("Número de operación")).toBeTruthy();
    expect(await screen.findByRole("img", { name: "Comprobante de la transferencia" })).toBeTruthy();
    expect(screen.queryByText("Estamos revisando el comprobante")).toBeNull();
    // Dejó de consultar.
    const lecturas = pedidos(api, "GET", `${BASE}/pagos/${O1}`).length;
    await act(() => vi.advanceTimersByTimeAsync(ESPERA * 3));
    expect(pedidos(api, "GET", `${BASE}/pagos/${O1}`)).toHaveLength(lecturas);
  });

  it("una consulta vieja del antivirus no pisa la nueva", async () => {
    conEsperaManual();
    const colgadas: ((r: Response) => void)[] = [];
    let responder = (): Response | Promise<Response> => Response.json(EN_REVISION);
    abrirFicha(EN_REVISION, { [`GET ${BASE}/pagos/${O1}`]: () => responder() });
    expect(await screen.findByText("Estamos revisando el comprobante")).toBeTruthy();
    responder = () => new Promise<Response>((r) => colgadas.push(r));
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    await waitFor(() => expect(colgadas).toHaveLength(1));
    responder = () => Response.json(APROBADO);
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await screen.findByLabelText("Número de operación")).toBeTruthy();
    await act(async () => {
      (colgadas[0] as (r: Response) => void)(Response.json(EN_REVISION));
      await Promise.resolve();
    });
    expect(screen.getByLabelText("Número de operación")).toBeTruthy();
    expect(screen.queryByText("Estamos revisando el comprobante")).toBeNull();
  });

  it("registra con el comprobante actual: el documento no se elige", async () => {
    const api = abrirFicha(APROBADO, {
      [`GET ${BASE}/documentos/${NUEVO}/versiones/${K1}/contenido`]: IMAGEN,
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("Registrar el pago"));
    await screen.findByText(/Listo: registraste el pago/);
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual`)[0])).toMatchObject({ documento_id: NUEVO });
  });

  it("tras un minuto avisa que se puede salir y sigue consultando", async () => {
    conEsperaManual();
    const api = abrirFicha(EN_REVISION);
    await screen.findByText("Estamos revisando el comprobante");
    await act(() => vi.advanceTimersByTimeAsync(ESPERA * 19));
    expect(screen.queryByText(AVISO_ANTIVIRUS_TARDA)).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await screen.findByText(AVISO_ANTIVIRUS_TARDA)).toBeTruthy();
    const lecturas = pedidos(api, "GET", `${BASE}/pagos/${O1}`).length;
    await act(() => vi.advanceTimersByTimeAsync(ESPERA * 2));
    expect(pedidos(api, "GET", `${BASE}/pagos/${O1}`).length).toBeGreaterThan(lecturas);
  });

  it("al volver a la ficha con un archivo en revisión sigue la consulta, sin subir nada", async () => {
    const api = abrirFicha(EN_REVISION);
    expect(await screen.findByText("Estamos revisando el comprobante")).toBeTruthy();
    expect(escrituras(api)).toEqual([]);
    expect(screen.queryByLabelText("Archivo del comprobante")).toBeNull();
  });

  it("rechazado, lo dice en la ficha y deja subir otro", async () => {
    conEsperaManual();
    const { cambiar } = conSubida();
    await elegir();
    await screen.findByText("Estamos revisando el comprobante");
    cambiar(RECHAZADO);
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect((await screen.findByRole("alert")).textContent).toBe(AVISO_ARCHIVO_RECHAZADO);
    expect(await boton("Elegir el archivo")).toBeTruthy();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
  });

  it("volver a subir reemplaza al actual y lo escrito queda", async () => {
    conEsperaManual();
    let actual: Ficha = LISTO;
    abrirFicha(() => actual, {
      ...COMPROBANTE,
      [`POST ${SUBIDA}`]: () => {
        actual = { ...LISTO, comprobante_subido: { documento_id: NUEVO, version_documento_id: K1, estado: "en_cuarentena" } };
        return Response.json({ id: K1, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 201 });
      },
      [`GET ${BASE}/documentos/${NUEVO}/versiones/${K1}/contenido`]: IMAGEN,
    });
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    await boton("Elegir otro archivo");
    await elegir();
    await screen.findByText("Estamos revisando el comprobante");
    // Mientras se revisa el nuevo, no se registra con el anterior.
    sinBoton("Registrar el pago");
    actual = APROBADO;
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect((await screen.findByLabelText<HTMLInputElement>("Número de operación")).value).toBe("0001-234567");
  });

  it("si el archivo no entra, reintentar sube solo el archivo, al mismo comprobante y con la misma clave", async () => {
    let caido = true;
    const { api } = conSubida({
      [`POST ${SUBIDA}`]: () => (caido ? error("error_interno", 500) : Response.json({ id: K1, documento_id: NUEVO, estado: "en_cuarentena" }, { status: 201 })),
    });
    await elegir();
    const reintentar = await boton("Reintentar");
    expect(screen.getByRole("alert").textContent).toContain("El archivo no se subió");
    expect(screen.getByText("comprobante.pdf")).toBeTruthy();
    caido = false;
    fireEvent.click(reintentar);
    await waitFor(() => expect(pedidos(api, "POST", SUBIDA)).toHaveLength(2));
    expect(pedidos(api, "POST", CREAR)).toHaveLength(1);
    expect(new Set(claves(pedidos(api, "POST", SUBIDA))).size).toBe(1);
  });

  it("sin red queda el archivo a la vista y se reintenta con un toque", async () => {
    const api = abrirFicha(SUBIR, {
      [`POST ${CREAR}`]: () => {
        throw new TypeError("sin red");
      },
    });
    await elegir();
    expect(await boton("Reintentar")).toBeTruthy();
    expect(screen.getByText("comprobante.pdf")).toBeTruthy();
    expect(escrituras(api)).toEqual([CREAR]);
  });

  it("subir no pide el código: el gesto se pide recién al registrar", async () => {
    const { api } = conSubida();
    await elegir();
    await waitFor(() => expect(escrituras(api)).toEqual([CREAR, SUBIDA]));
    expect(screen.queryByLabelText("Código")).toBeNull();
  });

  it("si no vio la cuenta, la carga se niega y no sube el archivo", async () => {
    const api = abrirFicha(SUBIR, { [`POST ${CREAR}`]: () => error("no_es_quien_ejecuto", 403) });
    await elegir();
    expect((await screen.findByRole("alert")).textContent).toContain("quien vio la cuenta");
    expect(pedidos(api, "POST", SUBIDA)).toHaveLength(0);
  });

  it("un archivo que no es PDF, JPEG ni PNG no se manda y deja elegir otro", async () => {
    const api = abrirFicha(SUBIR);
    await elegir(new File(["x"], "a.exe", { type: "application/x-msdownload" }));
    expect((await screen.findByRole("alert")).textContent).toContain("PDF, JPEG o PNG");
    expect(escrituras(api)).toEqual([]);
    expect(await boton("Elegir el archivo")).toBeTruthy();
  });

  it("una lectura que falla mientras se espera se dice y se va con la siguiente", async () => {
    conEsperaManual();
    let caido = false;
    const lectura = () => (caido ? error("error_interno", 500) : Response.json(EN_REVISION));
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/${O1}`]: lectura,
    }));
    abrir(`/pagos/${O1}`);
    await screen.findByText("Estamos revisando el comprobante");
    caido = true;
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await screen.findByRole("alert")).toBeTruthy();
    caido = false;
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});

describe("copiar", () => {
  const conLaCuenta = async () => {
    abrirFicha(LISTO, { [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json({ ...CUENTA_DEL_ACTO, cuenta_destino: `${CUENTA.slice(0, 8)} ${CUENTA.slice(8)}` }) });
    fireEvent.click(await boton("Ver la cuenta de nuevo"));
    await boton("Copiar el CBU");
  };

  it("el CBU sin espacios y el importe sin miles y con coma decimal", async () => {
    const escribir = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator) as Navigator, { clipboard: { writeText: escribir } }));
    await conLaCuenta();
    fireEvent.click(await boton("Copiar el CBU"));
    expect(await boton("Copiado el CBU")).toBeTruthy();
    fireEvent.click(await boton("Copiar el importe"));
    expect(await boton("Copiado el importe")).toBeTruthy();
    expect(escribir.mock.calls).toEqual([[CUENTA], ["15000,00"]]);
  });

  it("si el navegador no deja, el texto queda seleccionado", async () => {
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator) as Navigator, { clipboard: { writeText: () => Promise.reject(new Error("no")) } }));
    await conLaCuenta();
    fireEvent.click(await boton("Copiar el CBU"));
    const campo = await screen.findByLabelText<HTMLInputElement>("Copiá el CBU");
    expect(campo.value).toBe(CUENTA);
    expect([campo.selectionStart, campo.selectionEnd]).toEqual([0, CUENTA.length]);
  });

  it("sin el portapapeles, también", async () => {
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator) as Navigator, { clipboard: undefined }));
    await conLaCuenta();
    fireEvent.click(await boton("Copiar el importe"));
    expect((await screen.findByLabelText<HTMLInputElement>("Copiá el importe")).value).toBe("15000,00");
  });

  it("el titular a la vista, con el texto del diseño", async () => {
    await conLaCuenta();
    expect(screen.getByText(/Antes de confirmar en tu banco, mirá que el titular sea/).textContent).toContain("Limpieza Ficticia SRL");
  });

  it("control: antes de ver la cuenta no hay nada para copiar", async () => {
    abrirFicha(LISTO);
    await boton("Registrar el pago");
    sinBoton(/^Copiar/);
  });
});

describe("no se pagó", () => {
  it("con la cuenta vista, declara sobre la ficha y vuelve a la bandeja", async () => {
    const api = abrirFicha(SUBIR, {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    fireEvent.click(await boton("No se pudo pagar"));
    fireEvent.change(await screen.findByLabelText("Por qué no se pagó"), { target: { value: "No había saldo" } });
    fireEvent.click(await boton("Declarar que no se pagó"));
    expect(await screen.findByText(/Listo: declaraste que no se pagó/)).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado`)[0])).toEqual({ version: 1, motivo: "No había saldo" });
  });

  it("cancelar la declaración no pierde lo escrito en el registro", async () => {
    abrirFicha(LISTO);
    await screen.findByLabelText("Número de operación");
    escribirRegistro();
    fireEvent.click(await boton("No se pudo pagar"));
    fireEvent.click(await boton("Cancelar"));
    expect((screen.getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
  });

  it("sin salida, declara sin ver la cuenta", async () => {
    const api = abrirFicha(ficha({ orden: orden({ estado: "en_ejecucion" }), situacion: "pagando", paso: "declarar_no_pago", puede_declarar: true, ejecucion: ejecucion() }), {
      [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    expect(await screen.findByText(AVISO_SIN_SALIDA)).toBeTruthy();
    fireEvent.click(await boton("No se pudo pagar"));
    fireEvent.change(await screen.findByLabelText("Por qué no se pagó"), { target: { value: "Venció" } });
    fireEvent.click(await boton("Declarar que no se pagó"));
    await screen.findByText(/Listo: declaraste/);
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`)).toHaveLength(0);
  });

  it("sin `documentos.cargar` no hay un registro que no se podría terminar: queda declarar", async () => {
    abrirFicha({ ...SUBIR, paso: "espera_comprobante", puede_subir_comprobante: false, puede_reabrir_cuenta: false });
    expect(await boton("No se pudo pagar")).toBeTruthy();
    expect(screen.queryByLabelText("Número de operación")).toBeNull();
  });

  it("con una declaración vigente no ofrece pagar y dice en qué quedó", async () => {
    abrirFicha(ficha({
      orden: orden({ estado: "en_ejecucion" }), situacion: "no_pago_declarado", paso: "espera_confirmar", le_toca_a: "consejo",
      declaracion_vigente: DECLARACION, ejecucion: ejecucion([], [DECLARACION]),
    }));
    expect(await screen.findByText(/No había saldo/)).toBeTruthy();
    expect(screen.getByText("Falta confirmar")).toBeTruthy();
    sinBoton("Ver la cuenta para pagar");
    sinBoton("Retomar el pago");
  });

  it("«Retomar el pago» avisa antes y abre la cuenta", async () => {
    const api = abrirFicha(ficha({
      orden: orden({ estado: "en_ejecucion" }), situacion: "no_pago_declarado", paso: "espera_confirmar", puede_reabrir_cuenta: true,
      declaracion_vigente: DECLARACION, ejecucion: ejecucion([], [DECLARACION]),
    }), { [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`]: () => Response.json(CUENTA_DEL_ACTO) });
    expect(await screen.findByText(AVISO_RETOMAR)).toBeTruthy();
    fireEvent.click(await boton("Retomar el pago"));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/pago-manual/cuenta`)).toHaveLength(1);
  });

  it("el Consejo confirma y vuelve a la bandeja", async () => {
    const api = abrirFicha(ficha({
      orden: orden({ estado: "en_ejecucion" }), situacion: "no_pago_declarado", paso: "confirmar_no_pago", le_toca_a: "vos", puede_confirmar: true,
      declaracion_vigente: DECLARACION, ejecucion: ejecucion([], [DECLARACION]),
    }), { [`POST ${BASE}/ordenes-pago/${O1}/pago-manual/no-realizado/${DECL}/confirmacion`]: () => Response.json({ id: DECL, orden_id: O1 }) });
    fireEvent.click(await boton("Confirmar que no se pagó"));
    expect(await screen.findByText(/Listo: confirmaste que no se pagó/)).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(escrituras(api)).toHaveLength(1);
  });
});

describe("preparar, enviar y renovar", () => {
  const LECTURAS = {
    [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }),
    [`GET ${BASE}/contratos`]: () => Response.json({ elementos: [contrato(K1)] }),
  };

  it("solo se declaran contratos vigentes del proveedor y la moneda elegidos", () => {
    const vencido = contrato(X1, { estado: "vencido" });
    expect(contratosAplicables([contrato(K1), vencido], P1, "ARS").map((k) => k.id)).toEqual([K1]);
    expect(contratosAplicables([contrato(K1)], P1, "USD")).toEqual([]);
  });

  it("preparar manda lo escrito y sigue a la ficha, donde está el paso que falta", async () => {
    const api = backend({
      ...LECTURAS,
      [`POST ${BASE}/ordenes-pago`]: () => Response.json({ id: O1, version: 1 }, { status: 201 }),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(BORRADOR),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/nueva");
    fireEvent.change(await screen.findByLabelText("Importe"), { target: { value: "15000" } });
    fireEvent.change(screen.getByLabelText("Concepto"), { target: { value: "Limpieza" } });
    fireEvent.change(screen.getByLabelText("Contrato o abono (opcional)"), { target: { value: K1 } });
    fireEvent.click(await boton("Guardar"));
    expect(await boton("Enviar a aprobar")).toBeTruthy();
    expect(lugar()).toBe(`/pagos/${O1}`);
    expect(await screen.findByText("Listo: la orden quedó en borrador.")).toBeTruthy();
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago`)[0])).toMatchObject({ importe_centavos: 1_500_000, contrato_id: K1, proveedor_id: P1 });
  });

  it("cambiar la moneda descarta el contrato elegido", async () => {
    vi.stubGlobal("fetch", backend(LECTURAS));
    abrir("/pagos/nueva");
    const elegir = (await screen.findByLabelText("Contrato o abono (opcional)")) as HTMLSelectElement;
    fireEvent.change(elegir, { target: { value: K1 } });
    fireEvent.change(screen.getByLabelText("Moneda"), { target: { value: "USD" } });
    expect(elegir.value).toBe("");
  });

  it("guardar de nuevo tras un 500 lleva la misma clave; cambiar un dato es otro intento", async () => {
    const api = backend({ ...LECTURAS, [`POST ${BASE}/ordenes-pago`]: () => error("error_interno", 500) });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/nueva");
    fireEvent.change(await screen.findByLabelText("Importe"), { target: { value: "15000" } });
    fireEvent.change(screen.getByLabelText("Concepto"), { target: { value: "Limpieza" } });
    const envios = () => pedidos(api, "POST", `${BASE}/ordenes-pago`);
    fireEvent.click(await boton("Guardar"));
    await screen.findByRole("alert");
    fireEvent.click(await boton("Guardar"));
    await waitFor(() => expect(envios()).toHaveLength(2));
    await waitFor(() => expect((screen.getByRole("button", { name: "Guardar" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Concepto"), { target: { value: "Limpieza de tanques" } });
    fireEvent.click(await boton("Guardar"));
    await waitFor(() => expect(envios()).toHaveLength(3));
    const [a, b, c] = claves(envios());
    expect(a).toBe(b);
    expect(c).not.toBe(a);
  });

  it("un proveedor inactivo no se lleva el formulario: lo escrito queda para elegir otro", async () => {
    vi.stubGlobal("fetch", backend({ ...LECTURAS, [`POST ${BASE}/ordenes-pago`]: () => error("proveedor_inactivo", 409) }));
    abrir("/pagos/nueva");
    fireEvent.change(await screen.findByLabelText("Importe"), { target: { value: "1234,50" } });
    fireEvent.change(screen.getByLabelText("Concepto"), { target: { value: "Arreglo del portón" } });
    fireEvent.click(await boton("Guardar"));
    expect(await screen.findByText(/está suspendido o de baja/)).toBeTruthy();
    expect((screen.getByLabelText("Concepto") as HTMLInputElement).value).toBe("Arreglo del portón");
    expect(lugar()).toBe("/pagos/nueva");
  });

  it("sin proveedor activo lleva a cargarlo, y el alta vuelve a preparar con él elegido", async () => {
    let conProveedor = false;
    const api = backend({
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: conProveedor ? [proveedor()] : [] }),
      [`GET ${BASE}/contratos`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => {
        conProveedor = true;
        return Response.json({ id: P1, version: 1 }, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/nueva");
    fireEvent.click(await screen.findByRole("link", { name: "cargalo en Proveedores" }));
    fireEvent.click(await boton("Dar de alta un proveedor"));
    fireEvent.change(await screen.findByLabelText(/CUIT/), { target: { value: "20000000001" } });
    fireEvent.change(screen.getByLabelText(/Razón social/), { target: { value: "Limpieza Ficticia SRL" } });
    fireEvent.change(screen.getByLabelText(/CBU|Cuenta/), { target: { value: CUENTA } });
    fireEvent.submit(document.querySelector("form") as HTMLFormElement);
    expect(await screen.findByRole("heading", { level: 1, name: "Preparar una orden" })).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("Proveedor") as HTMLSelectElement).value).toBe(P1));
    expect(screen.getByText("Listo: dimos de alta el proveedor.")).toBeTruthy();
  });

  it("enviar lleva a la bandeja con el resultado", async () => {
    const api = abrirFicha(BORRADOR, { [`POST ${BASE}/ordenes-pago/${O1}/enviar-a-revision`]: () => Response.json({ id: O1, version: 1 }) });
    fireEvent.click(await boton("Enviar a aprobar"));
    expect(await screen.findByText(/Listo: la orden se envió/)).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/enviar-a-revision`)[0])).toEqual({ version_esperada: 1 });
  });

  it("renovar lleva a la bandeja con el resultado", async () => {
    abrirFicha(ficha({ paso: "renovar", le_toca_a: "vos", puede_renovar: true }), {
      [`POST ${BASE}/ordenes-pago/${O1}/renovar`]: () => Response.json({ id: O1, version: 2 }),
    });
    fireEvent.click(await boton("Renovar la aprobación"));
    expect(await screen.findByText(/volvió a revisión/)).toBeTruthy();
    expect(lugar()).toBe("/pagos");
  });

  describe("modificar", () => {
    const EN_REVISION = { ...APROBAR, paso: "espera_aprobar" as const, le_toca_a: "consejo" as const, puede_aprobar: false, puede_rechazar: false, puede_modificar: true };

    it("con aprobaciones en juego, avisa antes de crear la versión nueva", async () => {
      const api = abrirFicha(EN_REVISION, {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => Response.json({ id: O1, version: 2 }, { status: 201 }),
      });
      fireEvent.click(await boton("Modificar"));
      fireEvent.change(await screen.findByLabelText("Concepto"), { target: { value: "Otro concepto" } });
      fireEvent.click(await boton("Guardar"));
      expect(await screen.findByText(AVISO_MODIFICAR)).toBeTruthy();
      expect(escrituras(api)).toEqual([]);
      fireEvent.click(await boton("Sí, crear la versión nueva"));
      expect(await screen.findByText("Listo: guardamos la versión nueva.")).toBeTruthy();
      expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/versiones`)[0])).toMatchObject({ version_esperada: 1, concepto: "Otro concepto" });
      expect(screen.queryByLabelText("Concepto")).toBeNull();
    });

    it("control: en borrador no hay aprobaciones que perder y guarda de una", async () => {
      const api = abrirFicha(BORRADOR, {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => Response.json({ id: O1, version: 2 }, { status: 201 }),
      });
      fireEvent.click(await boton("Modificar"));
      await screen.findByLabelText("Concepto");
      fireEvent.click(await boton("Guardar"));
      await screen.findByText("Listo: guardamos la versión nueva.");
      expect(escrituras(api)).toHaveLength(1);
      expect(screen.queryByText(AVISO_MODIFICAR)).toBeNull();
    });

    it("sin cambios o con el proveedor inactivo, el formulario sigue abierto con lo escrito", async () => {
      abrirFicha(BORRADOR, { ...LECTURAS, [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => error("sin_cambios", 409) });
      fireEvent.click(await boton("Modificar"));
      await screen.findByLabelText("Concepto");
      fireEvent.click(await boton("Guardar"));
      expect((await screen.findByRole("alert")).textContent).toContain("ningún cambio");
      expect(screen.getByLabelText("Concepto")).toBeTruthy();
    });

    it("volver a pagar abre el formulario con los datos de la orden", async () => {
      abrirFicha(ficha({ orden: orden({ estado: "fallida" }), situacion: "no_se_pago", paso: "volver_a_pagar", le_toca_a: "vos", puede_modificar: true }), LECTURAS);
      fireEvent.click(await boton("Modificar"));
      expect(((await screen.findByLabelText("Concepto")) as HTMLInputElement).value).toBe("Limpieza de octubre");
    });

    it("volver a pagar manda los mismos datos, sin abrir el formulario", async () => {
      const api = abrirFicha(ficha({ orden: orden({ estado: "fallida" }), situacion: "no_se_pago", paso: "volver_a_pagar", le_toca_a: "vos", puede_modificar: true }), {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => Response.json({ id: O1, version: 2 }),
      });
      fireEvent.click(await boton("Volver a pagar"));
      expect(await screen.findByText(/pasa otra vez por aprobación/)).toBeTruthy();
      expect(screen.queryByLabelText("Concepto")).toBeNull();
      expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/versiones`)[0])).toEqual({
        version_esperada: 1, importe_centavos: 1_500_000, moneda: "ARS", concepto: "Limpieza de octubre", proveedor_id: P1,
        comprobante: "Factura B 0001-00000001", usa_fondo_reserva: false, contrato_id: null,
      });
    });

    it("si el contrato ya no rige, volver a pagar lo dice y queda Modificar", async () => {
      abrirFicha(ficha({ orden: orden({ estado: "fallida" }), situacion: "no_se_pago", paso: "volver_a_pagar", le_toca_a: "vos", puede_modificar: true }), {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => error("contrato_no_aplica", 422),
      });
      fireEvent.click(await boton("Volver a pagar"));
      expect((await screen.findByRole("alert")).textContent).toContain("no cubre esta orden");
      expect(await boton("Modificar")).toBeTruthy();
    });

    it("un contrato que ya no rige se quita del formulario y se avisa", async () => {
      abrirFicha({ ...BORRADOR, versiones: [version({ contrato_id: X1 })] }, LECTURAS);
      fireEvent.click(await boton("Modificar"));
      expect(((await screen.findByLabelText("Contrato o abono (opcional)")) as HTMLSelectElement).value).toBe("");
      expect(screen.getByRole("alert").textContent).toContain("ya no rige");
    });

    it("si la orden cambió, queda lo escrito sobre la versión nueva, con lo ajeno, y se vuelve a confirmar", async () => {
      let actual: Ficha = EN_REVISION;
      const api = abrirFicha(() => actual, {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => {
          actual = {
            ...EN_REVISION, orden: orden({ estado: "en_revision", version_vigente: 2 }),
            versiones: [version(), version({ numero: 2, huella: H2, aprobada_por: [], importe_centavos: 2_000_000 })],
          };
          return error("version_desactualizada", 409);
        },
      });
      fireEvent.click(await boton("Modificar"));
      fireEvent.change(await screen.findByLabelText("Concepto"), { target: { value: "Mío" } });
      fireEvent.click(await boton("Guardar"));
      fireEvent.click(await boton("Sí, crear la versión nueva"));
      await waitFor(() => expect((screen.getByLabelText("Importe") as HTMLInputElement).value).toContain("20"));
      expect((screen.getByLabelText("Concepto") as HTMLInputElement).value).toBe("Mío");
      sinBoton("Sí, crear la versión nueva");
      fireEvent.click(await boton("Guardar"));
      fireEvent.click(await boton("Sí, crear la versión nueva"));
      await waitFor(() => expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/versiones`)).toHaveLength(2));
      expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/versiones`)[1])).toMatchObject({ version_esperada: 2, importe_centavos: 2_000_000 });
    });

    it("control: si ya no se puede modificar, el formulario se cierra", async () => {
      let actual: Ficha = EN_REVISION;
      abrirFicha(() => actual, {
        ...LECTURAS,
        [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => {
          actual = ficha({ orden: orden({ estado: "en_ejecucion" }), situacion: "pagando" });
          return error("version_desactualizada", 409);
        },
      });
      fireEvent.click(await boton("Modificar"));
      await screen.findByLabelText("Concepto");
      fireEvent.click(await boton("Guardar"));
      fireEvent.click(await boton("Sí, crear la versión nueva"));
      await waitFor(() => expect(screen.queryByLabelText("Concepto")).toBeNull());
    });

    it("otro conflicto de la orden cierra el formulario y vuelve a la ficha", async () => {
      abrirFicha(BORRADOR, { ...LECTURAS, [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => error("orden_no_admite", 409) });
      fireEvent.click(await boton("Modificar"));
      await screen.findByLabelText("Concepto");
      fireEvent.click(await boton("Guardar"));
      expect((await screen.findByRole("alert")).textContent).toContain("ya no admite");
      await waitFor(() => expect(screen.queryByLabelText("Concepto")).toBeNull());
    });
  });
});

describe("aprobar y rechazar", () => {
  /** El botón ya se puede tocar: llegó la cuenta que se aprueba. */
  async function listoParaAprobar() {
    await waitFor(async () => expect(((await boton("Aprobar esta versión")) as HTMLButtonElement).disabled).toBe(false));
  }

  it("quien decide ve la cuenta completa, porque es lo que aprueba", async () => {
    const api = abrirFicha(APROBAR);
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(screen.queryByText(`…${CUENTA.slice(-4)}`)).toBeNull();
    expect(screen.getByText("0 de 1")).toBeTruthy();
    expect(pedidos(api, "GET", RUTA_REVISION).length).toBeGreaterThan(0);
    await listoParaAprobar();
  });

  it("quien solo puede rechazar también la ve", async () => {
    abrirFicha({ ...APROBAR, paso: "espera_aprobar", puede_aprobar: false });
    expect(await screen.findByText(CUENTA)).toBeTruthy();
  });

  it.each([
    ["quien prepara, en borrador", BORRADOR],
    ["quien prepara, con la orden en revisión", { ...APROBAR, paso: "espera_aprobar" as const, le_toca_a: "consejo" as const, puede_aprobar: false, puede_rechazar: false, puede_modificar: true }],
    ["quien espera, sin decidir", { ...APROBAR, paso: "espera_aprobar" as const, le_toca_a: "consejo" as const, puede_aprobar: false, puede_rechazar: false }],
  ] as [string, Ficha][])("control: %s no la ve y no se hace el pedido", async (_, f) => {
    const api = abrirFicha(f);
    expect(await screen.findByText(`…${CUENTA.slice(-4)}`)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0));
    expect(document.body.textContent).not.toContain(CUENTA);
    expect(pedidos(api, "GET", RUTA_REVISION)).toHaveLength(0);
  });

  it("control: si la revisión no manda la cuenta (preparó la orden), quedan los últimos dígitos", async () => {
    abrirFicha(APROBAR, {
      [REVISION]: () => Response.json({ orden: APROBAR.orden, versiones: APROBAR.versiones.map((x) => ({ ...x, cuenta_destino: null })), puede_aprobar: false, puede_rechazar: false }),
    });
    expect(await screen.findByText(`…${CUENTA.slice(-4)}`)).toBeTruthy();
    expect(document.body.textContent).not.toContain(CUENTA);
  });

  it("sin la cuenta no se aprueba: lo dice, deja volver a pedirla y rechazar sigue", async () => {
    let caido = true;
    abrirFicha(APROBAR, {
      [REVISION]: () =>
        caido
          ? error("error_interno", 500)
          : Response.json({ orden: APROBAR.orden, versiones: APROBAR.versiones.map((x) => ({ ...x, cuenta_destino: CUENTA })), puede_aprobar: true, puede_rechazar: true }),
    });
    const volver = await boton("Volver a pedir la cuenta");
    expect(screen.getByRole("alert").textContent).toContain("El servicio no está disponible");
    expect(((await boton("Aprobar esta versión")) as HTMLButtonElement).disabled).toBe(true);
    expect(((await boton("Rechazar")) as HTMLButtonElement).disabled).toBe(false);
    caido = false;
    fireEvent.click(volver);
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    await listoParaAprobar();
    sinBoton("Volver a pedir la cuenta");
  });

  it("con una versión nueva, la cuenta de la anterior se va y se pide la de la nueva", async () => {
    const OTRA = "9990003100000000009876";
    let actual = APROBAR;
    abrirFicha(() => actual, {
      [REVISION]: () =>
        Response.json({
          orden: actual.orden, puede_aprobar: true, puede_rechazar: true,
          versiones: actual.versiones.map((x) => ({ ...x, cuenta_destino: x.numero === 2 ? OTRA : CUENTA })),
        }),
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => {
        actual = {
          ...APROBAR, orden: orden({ estado: "en_revision", version_vigente: 2 }),
          versiones: [version({ aprobada_por: [] }), version({ numero: 2, huella: H2, aprobada_por: [], cuenta_ultimos_digitos: OTRA.slice(-4) })],
        };
        return error("huella_distinta", 409);
      },
    });
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    expect(await screen.findByText(OTRA)).toBeTruthy();
    expect(screen.queryByText(CUENTA)).toBeNull();
  });

  it("aprobar manda versión y huella y vuelve a la bandeja; ante R10 reintenta con la misma clave", async () => {
    let conFactor = false;
    const api = abrirFicha(APROBAR, {
      "POST /api/v1/auth/totp/verificar": () => {
        conFactor = true;
        return VERIFICADO();
      },
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () =>
        conFactor ? Response.json({ id: O1, version: 1 }, { status: 201 }) : error("segundo_factor_requerido", 403),
    });
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    await verificarCodigo();
    expect(await screen.findByText("Listo: aprobaste la versión 1.")).toBeTruthy();
    const aprobaciones = pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`);
    expect(new Set(claves(aprobaciones)).size).toBe(1);
    expect(await cuerpo(aprobaciones[0])).toEqual({ version: 1, huella: H1 });
    expect(lugar()).toBe("/pagos");
  });

  it("la respuesta de un acto de una ficha ya cerrada no mueve la que se abrió después", async () => {
    ancho(true);
    let soltar: (r: Response) => void = () => undefined;
    const OTRA: Ficha = { ...BORRADOR, orden: orden({ id: O2, estado: "borrador" }), versiones: [version({ razon_social: "Ascensores Ficticios SA" })] };
    const api = backend({
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("aprobar", "para_aprobar"), elemento("enviar", "para_preparar", { orden_id: O2, proveedor: "Ascensores Ficticios SA" })),
      [`GET ${BASE}/pagos/${O1}`]: () => Response.json(APROBAR),
      [`GET ${BASE}/pagos/${O2}`]: () => Response.json(OTRA),
      [REVISION]: () => Response.json({ orden: APROBAR.orden, versiones: APROBAR.versiones.map((x) => ({ ...x, cuenta_destino: CUENTA })), puede_aprobar: true, puede_rechazar: true }),
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => new Promise<Response>((r) => (soltar = r)),
    });
    vi.stubGlobal("fetch", api);
    abrir(`/pagos/${O1}`);
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`)).toHaveLength(1));
    fireEvent.click(screen.getByRole("link", { name: /Ascensores Ficticios SA/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Ascensores Ficticios SA" })).toBeTruthy();
    const leidas = pedidos(api, "GET", `${BASE}/pagos/bandeja`).length;
    await act(async () => {
      soltar(Response.json({ id: O1, version: 1 }, { status: 201 }));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(lugar()).toBe(`/pagos/${O2}`);
    expect(screen.queryByText("Listo: aprobaste la versión 1.")).toBeNull();
    expect(pedidos(api, "GET", `${BASE}/pagos/bandeja`).length).toBeGreaterThan(leidas);
  });

  it("si la orden cambió entre la ficha y la revisión, la ficha se relee y deja aprobar", async () => {
    let lecturas = 0;
    const V2 = { ...APROBAR, orden: orden({ estado: "en_revision", version_vigente: 2 }), versiones: [version({ aprobada_por: [] }), version({ numero: 2, huella: H2, aprobada_por: [] })] };
    abrirFicha(() => (lecturas++ < 2 ? APROBAR : V2), {
      [REVISION]: () => Response.json({ orden: V2.orden, versiones: V2.versiones.map((x) => ({ ...x, cuenta_destino: CUENTA })), puede_aprobar: true, puede_rechazar: true }),
    });
    await listoParaAprobar();
    expect(lecturas).toBeGreaterThan(2);
  });

  it("si la orden cambió, la ficha muestra la versión nueva, lo dice y no traslada la aprobación", async () => {
    let actual = APROBAR;
    const api = abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => {
        actual = {
          ...APROBAR, orden: orden({ estado: "en_revision", version_vigente: 2 }),
          versiones: [version({ aprobada_por: [] }), version({ numero: 2, huella: H2, aprobada_por: [], importe_centavos: 3_000_000 })],
        };
        return error("huella_distinta", 409);
      },
    });
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    expect(await screen.findByText(/Cambió el importe/)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Revisá la versión nueva");
    expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`)).toHaveLength(1);
  });

  it("si ya no admite aprobar, relee y no deja el botón viejo", async () => {
    let actual = APROBAR;
    abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => {
        actual = ficha();
        return error("orden_no_admite", 409);
      },
    });
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    await waitFor(() => sinBoton("Aprobar esta versión"));
  });

  it("rechazar pide un motivo sobre la misma ficha", async () => {
    const api = abrirFicha(APROBAR, { [`POST ${BASE}/ordenes-pago/${O1}/rechazar`]: () => Response.json({ id: O1, version: 1 }) });
    fireEvent.click(await boton("Rechazar"));
    const enviar = (await boton("Rechazar la orden")) as HTMLButtonElement;
    expect(enviar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Motivo del rechazo"), { target: { value: "Falta el presupuesto" } });
    fireEvent.click(enviar);
    expect(await screen.findByText("Listo: rechazaste la orden.")).toBeTruthy();
    expect(await cuerpo(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/rechazar`)[0])).toEqual({ version: 1, motivo: "Falta el presupuesto" });
  });

  it("rechazar de nuevo tras un 500 lleva la misma clave; otro motivo es otro intento", async () => {
    const api = abrirFicha(APROBAR, { [`POST ${BASE}/ordenes-pago/${O1}/rechazar`]: () => error("error_interno", 500) });
    fireEvent.click(await boton("Rechazar"));
    fireEvent.change(await screen.findByLabelText("Motivo del rechazo"), { target: { value: "Uno" } });
    const envios = () => pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/rechazar`);
    fireEvent.click(await boton("Rechazar la orden"));
    await waitFor(() => expect(envios()).toHaveLength(1));
    await screen.findByRole("alert");
    fireEvent.click(await boton("Rechazar la orden"));
    await waitFor(() => expect(envios()).toHaveLength(2));
    await waitFor(() => expect((screen.getByRole("button", { name: "Cancelar" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Motivo del rechazo"), { target: { value: "Dos" } });
    fireEvent.click(await boton("Rechazar la orden"));
    await waitFor(() => expect(envios()).toHaveLength(3));
    const [a, b, c] = claves(envios());
    expect(a).toBe(b);
    expect(c).not.toBe(a);
  });

  it("control: después de un éxito, el mismo acto lleva otra clave", async () => {
    let fallar = true;
    const api = abrirFicha(APROBAR, {
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => {
        if (fallar) {
          fallar = false;
          return error("sin_permiso", 403);
        }
        return error("error_interno", 500);
      },
    });
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    await screen.findByRole("alert");
    await listoParaAprobar();
    fireEvent.click(await boton("Aprobar esta versión"));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`)).toHaveLength(2));
    const [a, b] = claves(pedidos(api, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`));
    expect(a).not.toBe(b);
  });

  it("si la orden cambió mientras se rechazaba, el motivo queda para la versión nueva", async () => {
    let actual = APROBAR;
    abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/rechazar`]: () => {
        actual = { ...APROBAR, orden: orden({ estado: "en_revision", version_vigente: 2 }), versiones: [version(), version({ numero: 2, huella: H2, aprobada_por: [] })] };
        return error("version_desactualizada", 409);
      },
    });
    fireEvent.click(await boton("Rechazar"));
    fireEvent.change(await screen.findByLabelText("Motivo del rechazo"), { target: { value: "Falta" } });
    fireEvent.click(await boton("Rechazar la orden"));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("Motivo del rechazo") as HTMLInputElement).value).toBe("Falta");
  });

  it("control: si en la versión nueva ya no puede rechazar, el rechazo se va", async () => {
    let actual = APROBAR;
    abrirFicha(() => actual, {
      [`POST ${BASE}/ordenes-pago/${O1}/rechazar`]: () => {
        actual = { ...APROBAR, puede_aprobar: false, puede_rechazar: false };
        return error("version_desactualizada", 409);
      },
    });
    fireEvent.click(await boton("Rechazar"));
    fireEvent.change(await screen.findByLabelText("Motivo del rechazo"), { target: { value: "Falta" } });
    fireEvent.click(await boton("Rechazar la orden"));
    await waitFor(() => expect(screen.queryByLabelText("Motivo del rechazo")).toBeNull());
  });

  it("quien ya aprobó rechaza y no aprueba: cada botón sigue a su campo", async () => {
    abrirFicha({ ...APROBAR, puede_aprobar: false });
    expect(await boton("Rechazar")).toBeTruthy();
    sinBoton("Aprobar esta versión");
  });
});

describe("conciliar", () => {
  const RUTA_CONCILIACION = `${BASE}/ordenes-pago/${O1}/pago-manual/${PAGO}/conciliacion`;

  it("conforme abre el pago siguiente, con el aviso", async () => {
    const api = abrirFicha(CONCILIAR, {
      [`GET ${BASE}/pagos/${O2}`]: () => Response.json({ ...CONCILIAR, orden: orden({ id: O2, estado: "en_verificacion" }), versiones: [version({ razon_social: "Ascensores Ficticios SA" })] }),
      [`GET ${BASE}/ordenes-pago/${O2}/revision`]: () => Response.json({ orden: CONCILIAR.orden, versiones: CONCILIAR.versiones, puede_aprobar: false, puede_rechazar: false }),
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("conciliar", "para_conciliar", { orden_id: O2 })),
      [`POST ${RUTA_CONCILIACION}`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    fireEvent.click(await boton("Conforme"));
    expect(await screen.findByRole("heading", { name: "Ascensores Ficticios SA" })).toBeTruthy();
    expect(lugar()).toBe(`/pagos/${O2}`);
    expect(await screen.findByText("Listo: conciliaste el pago. La orden quedó pagada.")).toBeTruthy();
    expect(await cuerpo(pedidos(api, "POST", RUTA_CONCILIACION)[0])).toEqual({ resultado: "conforme" });
  });

  it("control: sin otro pago para conciliar vuelve a la bandeja", async () => {
    abrirFicha(CONCILIAR, {
      [`GET ${BASE}/pagos/bandeja`]: bandejaCon(elemento("aprobar", "para_aprobar", { orden_id: O2 })),
      [`POST ${RUTA_CONCILIACION}`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    fireEvent.click(await boton("Conforme"));
    await screen.findByText(/Listo: conciliaste/);
    expect(lugar()).toBe("/pagos");
  });

  it("observar exige el motivo", async () => {
    const api = abrirFicha(CONCILIAR, { [`POST ${RUTA_CONCILIACION}`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }) });
    fireEvent.click(await boton("Observar"));
    const enviar = (await boton("Observar el pago")) as HTMLButtonElement;
    expect(enviar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Qué no coincide"), { target: { value: "El número" } });
    fireEvent.click(enviar);
    expect(await screen.findByText(/Listo: observaste el pago/)).toBeTruthy();
    expect(await cuerpo(pedidos(api, "POST", RUTA_CONCILIACION)[0])).toEqual({ resultado: "observado", motivo: "El número" });
  });

  it("fuera de término, conforme pide el motivo en la misma ficha", async () => {
    const api = abrirFicha({ ...CONCILIAR, ejecucion: ejecucion([pago({ fuera_de_termino: true })]) }, {
      [`POST ${RUTA_CONCILIACION}`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });
    expect(await screen.findByText(/Para darlo por conforme, escribí por qué lo aceptás/)).toBeTruthy();
    fireEvent.click(await boton("Conforme"));
    expect(escrituras(api)).toEqual([]);
    fireEvent.change(await screen.findByLabelText("Por qué lo aceptás fuera de término"), { target: { value: "Lo autorizó el Consejo" } });
    fireEvent.click(await boton("Dar el pago por conforme"));
    await screen.findByText(/Listo: conciliaste/);
    expect(await cuerpo(pedidos(api, "POST", RUTA_CONCILIACION)[0])).toEqual({ resultado: "conforme", motivo: "Lo autorizó el Consejo" });
  });

  it("muestra el comprobante y, al lado, la orden contra lo registrado, con la cuenta y quién registró", async () => {
    abrirFicha(CONCILIAR);
    await boton("Conforme");
    expect((await screen.findByRole("img", { name: "Comprobante de la transferencia" })).getAttribute("src")).toBe("blob:sintetico");
    const tabla = screen.getByRole("table", { name: "La orden contra lo registrado" });
    expect(within(tabla).getAllByText(/15\.000,00/)).toHaveLength(2);
    expect(await within(tabla).findByText(CUENTA)).toBeTruthy();
    expect(screen.getByText("0001234567")).toBeTruthy();
    expect(screen.getByText(/Marta Ficticia/)).toBeTruthy();
  });

  it("control: si la revisión no manda la cuenta, quedan los últimos dígitos", async () => {
    abrirFicha(CONCILIAR, {
      [REVISION]: () => Response.json({ orden: CONCILIAR.orden, versiones: CONCILIAR.versiones.map((x) => ({ ...x, cuenta_destino: null })), puede_aprobar: false, puede_rechazar: false }),
    });
    await boton("Conforme");
    const tabla = screen.getByRole("table", { name: "La orden contra lo registrado" });
    expect(within(tabla).getByText(`…${CUENTA.slice(-4)}`)).toBeTruthy();
    await waitFor(() => expect(document.body.textContent).not.toContain(CUENTA));
  });

  it("control: quien mira sin conciliar no pide la cuenta", async () => {
    const api = abrirFicha({ ...CONCILIAR, paso: "espera_conciliar", le_toca_a: "consejo", puede_conciliar: false });
    await screen.findByText("Le toca al Consejo.");
    expect(pedidos(api, "GET", RUTA_REVISION)).toHaveLength(0);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("quien vio la cuenta no recibe los botones: decide `puede_conciliar`", async () => {
    abrirFicha({ ...CONCILIAR, paso: "espera_conciliar", le_toca_a: "consejo", puede_conciliar: false });
    expect(await screen.findByText("Le toca al Consejo.")).toBeTruthy();
    sinBoton("Conforme");
    sinBoton("Observar");
  });

  it("si igual llega un 403, se dice en la ficha", async () => {
    abrirFicha(CONCILIAR, { [`POST ${RUTA_CONCILIACION}`]: () => error("sin_permiso", 403) });
    fireEvent.click(await boton("Conforme"));
    expect((await screen.findByRole("alert")).textContent).toContain("personas distintas");
    expect(lugar()).toBe(`/pagos/${O1}`);
  });

  describe("ver el comprobante", () => {
    it("un error se dice en la pantalla y deja reintentar", async () => {
      let caido = true;
      abrirFicha(CONCILIAR, { [`GET ${CONTENIDO}`]: () => (caido ? error("archivo_vencido", 410) : IMAGEN()) });
      expect((await screen.findByRole("alert")).textContent).toContain("ya no está disponible");
      caido = false;
      fireEvent.click(await boton("Reintentar"));
      expect(await screen.findByRole("img", { name: "Comprobante de la transferencia" })).toBeTruthy();
    });

    it("sin comprobante en la ficha no lo pide", async () => {
      const api = abrirFicha({ ...CONCILIAR, comprobante_actual: null });
      await boton("Conforme");
      expect(pedidos(api, "GET", CONTENIDO)).toHaveLength(0);
    });

    it("abrir un PDF no evalúa código y el trabajador sale del mismo origen", async () => {
      const { abrirPdf } = await vi.importActual<typeof import("./pdf")>("./pdf");
      const getDocument = vi.fn(() => "tarea");
      const GlobalWorkerOptions = { workerSrc: "" };
      const cargar = () => Promise.resolve({ getDocument, GlobalWorkerOptions }) as never;
      const datos = new Uint8Array([1]);
      expect(await abrirPdf(datos, cargar)).toBe("tarea");
      expect(getDocument).toHaveBeenCalledWith({ data: datos, isEvalSupported: false });
      expect(GlobalWorkerOptions.workerSrc).toMatch(/^\/.*pdf\.worker/);
    });

    it("un PDF se dibuja en un canvas, sin volverse una página", async () => {
      vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as never);
      pdf.abrirPdf.mockImplementation(() => Promise.resolve({
        promise: Promise.resolve({ numPages: 1, getPage: () => Promise.resolve(PAGINA) }),
        destroy: () => undefined,
      }));
      abrirFicha(CONCILIAR, {
        [`GET ${CONTENIDO}`]: () => new Response(new Blob(["%PDF-1.4 SINTETICO"], { type: "application/pdf" }), { headers: { "Content-Type": "application/pdf" } }),
      });
      const lienzo = await screen.findByRole("img", { name: "Comprobante de la transferencia, página 1" });
      expect(lienzo.tagName).toBe("CANVAS");
      expect(document.querySelector("iframe, embed, object")).toBeNull();
    });

    it("un PDF que no se puede dibujar lo dice", async () => {
      pdf.abrirPdf.mockImplementation(() => Promise.resolve({ promise: Promise.reject(new Error("roto")), destroy: () => undefined }));
      abrirFicha(CONCILIAR, {
        [`GET ${CONTENIDO}`]: () => new Response(new Blob(["%PDF roto"], { type: "application/pdf" }), { headers: { "Content-Type": "application/pdf" } }),
      });
      expect((await screen.findByRole("alert")).textContent).toContain("No pudimos mostrar este PDF");
    });
  });
});

describe("consultar pagos: el comprobante en pantalla", () => {
  it("una orden cerrada muestra su comprobante, de solo lectura", async () => {
    abrirFicha(ficha({
      orden: orden({ estado: "pagada" }), situacion: "pagada", ejecucion: ejecucion([pago({ conciliacion: "conforme" })]),
      comprobante_actual: EL_COMPROBANTE,
    }));
    expect(await screen.findByRole("img", { name: "Comprobante de la transferencia" })).toBeTruthy();
    expect(screen.queryByLabelText("Archivo del comprobante")).toBeNull();
    sinBoton("Conforme");
  });

  it("control: quien no puede abrirlo no recibe el comprobante y no se pide", async () => {
    const api = abrirFicha(ficha({ orden: orden({ estado: "pagada" }), situacion: "pagada", ejecucion: ejecucion([pago({ conciliacion: "conforme" })]) }));
    await screen.findByText("0001234567");
    expect(pedidos(api, "GET", CONTENIDO)).toHaveLength(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("consultar pagos", () => {
  const FILA = {
    orden_id: O1, proveedor: "Limpieza Ficticia SRL", concepto: "Limpieza de octubre", importe_centavos: 1_500_000, moneda: "ARS", estado: "pagada",
    situacion: "pagada", numero_operacion: "0001234567", fecha_operacion: "2026-10-31", actualizada_en: "2026-11-01T12:00:00Z",
  };
  const parametros = (p: Request | undefined) => new URL((p as Request).url).searchParams;

  it("busca, filtra y abre la misma ficha", async () => {
    const api = backend({ [`GET ${BASE}/pagos/consulta`]: () => Response.json({ elementos: [FILA], siguiente: null }) });
    vi.stubGlobal("fetch", api);
    const { container } = abrir("/pagos/consulta");
    const fila = await screen.findByRole("link", { name: /Limpieza Ficticia SRL/ });
    expect(fila.getAttribute("href")).toBe(`/pagos/${O1}`);
    expect(fila.textContent).toContain("Operación 0001234567");
    await sinFaltasDeAccesibilidad(container);

    fireEvent.change(screen.getByLabelText(/número de operación/), { target: { value: "Limpieza" } });
    fireEvent.click(await boton("Buscar"));
    await waitFor(() => expect(parametros(pedidos(api, "GET", `${BASE}/pagos/consulta`).at(-1)).get("busqueda")).toBe("Limpieza"));
    fireEvent.click(await boton("No se pagaron"));
    await waitFor(() => expect(parametros(pedidos(api, "GET", `${BASE}/pagos/consulta`).at(-1)).get("filtro")).toBe("no_se_pagaron"));
    expect(parametros(pedidos(api, "GET", `${BASE}/pagos/consulta`).at(-1)).get("busqueda")).toBe("Limpieza");
    expect(screen.getByRole("button", { name: "No se pagaron" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("«Ver más» suma la página siguiente y, sin más, desaparece", async () => {
    const api = backend({
      [`GET ${BASE}/pagos/consulta`]: () => Response.json({ elementos: [FILA], siguiente: 1 }),
    });
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/consulta`]: () => Response.json({ elementos: [FILA], siguiente: null }),
    }));
    abrir("/pagos/consulta");
    await screen.findByRole("link", { name: /Limpieza Ficticia SRL/ });
    sinBoton("Ver más");
    cleanup();

    let pagina = 0;
    const paginado = vi.fn(async (pedido: Request) => {
      if (new URL(pedido.url).pathname === `${BASE}/pagos/consulta`) {
        const saltar = new URL(pedido.url).searchParams.get("saltar");
        pagina = Number(saltar);
        return saltar === "0"
          ? Response.json({ elementos: [FILA], siguiente: 1 })
          : Response.json({ elementos: [{ ...FILA, orden_id: O2, proveedor: "Ascensores Ficticios SA" }], siguiente: null });
      }
      return api(pedido);
    });
    vi.stubGlobal("fetch", paginado);
    abrir("/pagos/consulta");
    fireEvent.click(await boton("Ver más"));
    expect(await screen.findByRole("link", { name: /Ascensores Ficticios SA/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeTruthy();
    expect(pagina).toBe(1);
    sinBoton("Ver más");
  });

  it("si falla «Ver más», lo cargado queda y el reintento suma esa página", async () => {
    let caido = true;
    const api = backend({});
    const saltos: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (pedido: Request) => {
      const url = new URL(pedido.url);
      if (url.pathname !== `${BASE}/pagos/consulta`) return api(pedido);
      const saltar = url.searchParams.get("saltar") as string;
      saltos.push(saltar);
      if (saltar === "0") return Response.json({ elementos: [FILA], siguiente: 1 });
      if (caido) return error("error_interno", 500);
      return Response.json({ elementos: [{ ...FILA, orden_id: O2, proveedor: "Ascensores Ficticios SA" }], siguiente: null });
    }));
    abrir("/pagos/consulta");
    fireEvent.click(await boton("Ver más"));
    expect((await screen.findByRole("alert")).textContent).toContain("El servicio no está disponible");
    expect(screen.getByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeTruthy();
    sinBoton("Reintentar");
    caido = false;
    fireEvent.click(await boton("Ver más"));
    expect(await screen.findByRole("link", { name: /Ascensores Ficticios SA/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(saltos.slice(-2)).toEqual(["1", "1"]);
    sinBoton("Ver más");
  });

  it("otra consulta saca lo cargado y su «Ver más»: no se mezclan resultados", async () => {
    let soltar: (r: Response) => void = () => undefined;
    const api = backend({});
    vi.stubGlobal("fetch", vi.fn(async (pedido: Request) => {
      const url = new URL(pedido.url);
      if (url.pathname !== `${BASE}/pagos/consulta`) return api(pedido);
      if (url.searchParams.get("filtro") === "todos") return Response.json({ elementos: [FILA], siguiente: 1 });
      return new Promise<Response>((r) => (soltar = r));
    }));
    abrir("/pagos/consulta");
    await boton("Ver más");
    fireEvent.click(await boton("No se pagaron"));
    expect(await screen.findByText("Cargando los pagos…")).toBeTruthy();
    sinBoton("Ver más");
    expect(screen.queryByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeNull();
    await act(async () => {
      soltar(Response.json({ elementos: [{ ...FILA, orden_id: O2, proveedor: "Ascensores Ficticios SA" }], siguiente: null }));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.getByRole("link", { name: /Ascensores Ficticios SA/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeNull();
  });

  it("si la red cae en «Ver más», también queda lo cargado", async () => {
    const api = backend({});
    vi.stubGlobal("fetch", vi.fn(async (pedido: Request) => {
      const url = new URL(pedido.url);
      if (url.pathname !== `${BASE}/pagos/consulta`) return api(pedido);
      if (url.searchParams.get("saltar") === "0") return Response.json({ elementos: [FILA], siguiente: 1 });
      throw new TypeError("sin red");
    }));
    abrir("/pagos/consulta");
    fireEvent.click(await boton("Ver más"));
    await screen.findByRole("alert");
    expect(screen.getByRole("link", { name: /Limpieza Ficticia SRL/ })).toBeTruthy();
    sinBoton("Reintentar");
    expect(await boton("Ver más")).toBeTruthy();
  });

  it("sin resultados lo dice", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/pagos/consulta`]: () => Response.json({ elementos: [], siguiente: null }) }));
    abrir("/pagos/consulta");
    expect(await screen.findByText("No encontramos pagos.")).toBeTruthy();
  });

  it("si la lectura falla, ofrece Reintentar", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      [`GET ${BASE}/pagos/consulta`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [], siguiente: null })),
    }));
    abrir("/pagos/consulta");
    const reintentar = await boton("Reintentar");
    caido = false;
    fireEvent.click(reintentar);
    expect(await screen.findByText("No encontramos pagos.")).toBeTruthy();
  });
});

describe("rutas y navegación", () => {
  const INICIO: Respuestas = {
    "GET /api/v1/auth/sesion": () => Response.json({}),
    "GET /health": () => Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
    "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
    "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
  };

  it.each(["/egresos/ordenes", "/egresos/revision", "/egresos/pagos", "/egresos/conciliacion"])("%s redirige a la bandeja", async (vieja) => {
    vi.stubGlobal("fetch", backend({}));
    abrir(vieja);
    expect(await screen.findByRole("heading", { level: 1, name: "Pagos" })).toBeTruthy();
    expect(lugar()).toBe("/pagos");
  });

  it("/egresos/por-aclarar redirige a su ruta nueva", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/egresos/por-aclarar");
    await waitFor(() => expect(lugar()).toBe("/pagos/por-aclarar"));
  });

  it("el inicio tiene una sola entrada «Pagos» en la lista y la pestaña", async () => {
    vi.stubGlobal("fetch", backend(INICIO));
    abrir("/");
    const pestanas = await screen.findByRole("navigation", { name: "Secciones" });
    expect(within(pestanas).getByRole("link", { name: "Pagos" }).getAttribute("href")).toBe("/pagos");
    expect(screen.getAllByRole("link", { name: "Pagos" })).toHaveLength(2);
    for (const vieja of ["Órdenes de pago", "Revisar órdenes de pago", "Pagos a realizar", "Conciliar pagos"])
      expect(screen.queryByRole("link", { name: vieja })).toBeNull();
  });

  it("control: quien no tiene que ver con pagos no recibe la entrada ni la pestaña", async () => {
    vi.stubGlobal("fetch", backend({ ...INICIO, [`GET ${BASE}/mis-acciones`]: alcanza("contactos.ver") }));
    abrir("/");
    await screen.findByRole("link", { name: "Contactos de emergencia" });
    expect(screen.queryByRole("link", { name: "Pagos" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Secciones" })).toBeNull();
  });

  it("una dirección abierta a mano sin permiso lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({ ...INICIO, [`GET ${BASE}/mis-acciones`]: alcanza("contactos.ver") }));
    abrir("/pagos");
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    await waitFor(() => expect(lugar()).toBe("/"));
  });

  it("quien revisa y no prepara entra a Pagos pero no a preparar", async () => {
    vi.stubGlobal("fetch", backend({ ...INICIO, [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden") }));
    abrir("/pagos/nueva");
    await waitFor(() => expect(lugar()).toBe("/"));
    cleanup();
    abrir("/pagos");
    expect(await screen.findByText("No tenés nada para hacer.")).toBeTruthy();
  });
});
