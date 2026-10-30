import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { aCentavos, cuentaEnmascarada, renovarClave } from "./comun";
import { AVISO_MODIFICAR, contratosAplicables, vencida } from "./OrdenesPago";
import { AVISO_CUENTA } from "./Proveedores";

// Pantallas de egresos (H3, aprobadas el 30-10-2026). Todos los datos son ficticios: CUIT y CBU
// inventados, sin ningún proveedor ni cuenta real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const P1 = "22222222-2222-4222-8222-222222222222";
const O1 = "33333333-3333-4333-8333-333333333333";
const K1 = "44444444-4444-4444-8444-444444444444";
const K2 = "55555555-5555-4555-8555-555555555555";
const PERSONA = "66666666-6666-4666-8666-666666666666";
const DOC = "77777777-7777-4777-8777-777777777777";
const CUENTA = "0000003100000000001234";
const CUENTA_NUEVA = "0000003100000000009876";
const H1 = "a".repeat(64);
const H2 = "b".repeat(64);
const BASE = `/api/v1/consorcios/${C1}`;

type Proveedor = components["schemas"]["Proveedor"];
type Orden = components["schemas"]["OrdenPago"];
type Version = components["schemas"]["VersionOrdenPago"];
type Contrato = components["schemas"]["Contrato"];

function proveedor(extra: Partial<Proveedor> = {}): Proveedor {
  return {
    id: P1,
    cuit: "20000000001",
    razon_social: "Limpieza Ficticia SRL",
    rubro: "limpieza",
    contacto: null,
    cuenta_destino: CUENTA,
    documento_id: null,
    estado: "activo",
    version: 1,
    huella: H1,
    alta_por: PERSONA,
    creado_en: "2026-10-20T15:00:00Z",
    actualizado_en: "2026-10-20T15:00:00Z",
    ...extra,
  };
}

function orden(extra: Partial<Orden> = {}): Orden {
  return {
    id: O1,
    proveedor_id: P1,
    razon_social: "Limpieza Ficticia SRL",
    importe_centavos: 1_500_000,
    moneda: "ARS",
    concepto: "Limpieza de octubre",
    usa_fondo_reserva: false,
    estado: "borrador",
    version_vigente: 1,
    franja: "bajo_umbral",
    alerta_fraccionamiento: false,
    condicion_beneficiario: "conocido",
    aprobaciones: 0,
    aprobaciones_requeridas: 0,
    aprobada_en: null,
    aprobacion_vence_en: null,
    motivo_rechazo: null,
    preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z",
    actualizada_en: "2026-10-30T12:00:00Z",
    ...extra,
  };
}

function version(extra: Partial<Version> = {}): Version {
  return {
    numero: 1,
    proveedor_id: P1,
    proveedor_version: 1,
    razon_social: "Limpieza Ficticia SRL",
    cuit: "20000000001",
    cuenta_destino: CUENTA,
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
    aprobada_por: [],
    huella: H1,
    preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z",
    ...extra,
  };
}

function contrato(id: string, extra: Partial<Contrato> = {}): Contrato {
  return {
    id,
    proveedor_id: P1,
    razon_social: "Limpieza Ficticia SRL",
    tipo: "abono",
    alcance: "Limpieza mensual",
    importe_maximo_centavos: 2_000_000,
    moneda: "ARS",
    vigente_desde: "2026-01-01",
    vigente_hasta: "2026-12-31",
    documento_id: DOC,
    decision_id: null,
    huella: H1,
    registrado_por: PERSONA,
    creado_en: "2026-10-01T12:00:00Z",
    confirmado_por: PERSONA,
    confirmado_en: "2026-10-02T12:00:00Z",
    baja_en: null,
    estado: "vigente",
    ...extra,
  };
}

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response | Promise<Response>>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

/** El único pedido de la lista: si hay otro número, la prueba falla. */
function unico(lista: Request[]): Request {
  expect(lista).toHaveLength(1);
  return lista[0] as Request;
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);

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

/** Verifica el código TOTP en el formulario que aparece ante `segundo_factor_requerido`. */
async function verificarCodigo() {
  fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
}

const VERIFICADO = () =>
  Response.json({
    verificado_en: "2026-10-30T12:00:00Z",
    vale_hasta: "2026-10-30T12:10:00Z",
    codigos_recuperacion_restantes: 9,
  });

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("lo común de egresos", () => {
  it("en las listas la cuenta va enmascarada: solo los últimos 4 dígitos", () => {
    expect(cuentaEnmascarada(CUENTA)).toBe("…1234");
    expect(cuentaEnmascarada(null)).toBe("Sin cuenta");
  });

  it("el importe escrito pasa a centavos y rechaza lo que no es un importe", () => {
    expect(aCentavos("12.345,50")).toBe(1_234_550);
    expect(aCentavos("1234.5")).toBe(123_450);
    expect(aCentavos("0")).toBeNull();
    expect(aCentavos("12,345,6")).toBeNull();
  });

  it("ante R10 se conserva la clave de idempotencia; ante datos cambiados se renueva", () => {
    expect(renovarClave(403, "segundo_factor_requerido")).toBe(false);
    expect(renovarClave(403, "sesion_reciente_requerida")).toBe(false);
    expect(renovarClave(409, "idempotencia_en_curso")).toBe(false);
    expect(renovarClave(422, "idempotencia_reutilizada")).toBe(true);
    expect(renovarClave(503, undefined)).toBe(false);
  });

  it("solo se declaran contratos vigentes del proveedor y la moneda elegidos", () => {
    const lista = [
      contrato(K1),
      contrato(K2, { estado: "pendiente_confirmacion" }),
      contrato("88888888-8888-4888-8888-888888888888", { moneda: "USD" }),
      contrato("99999999-9999-4999-8999-999999999999", { proveedor_id: PERSONA }),
    ];
    expect(contratosAplicables(lista, P1, "ARS").map((k) => k.id)).toEqual([K1]);
  });

  it("una aprobación vence a los 7 días: la marca la API y la pantalla la respeta", () => {
    const aprobada = orden({ estado: "aprobada", aprobacion_vence_en: "2026-11-06T12:00:00Z" });
    expect(vencida(aprobada, Date.parse("2026-11-06T11:59:59Z"))).toBe(false);
    expect(vencida(aprobada, Date.parse("2026-11-06T12:00:00Z"))).toBe(true);
    expect(vencida(orden({ estado: "borrador" }), Date.parse("2030-01-01T00:00:00Z"))).toBe(false);
  });
});

describe("inicio", () => {
  it("tiene los enlaces a las órdenes y a la revisión", async () => {
    vi.stubGlobal("fetch", backend({ "GET /health": () => Response.json({ estado: "ok" }) }));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Órdenes de pago" })).getAttribute("href")).toBe("/egresos/ordenes");
    expect(screen.getByRole("link", { name: "Revisar órdenes de pago (Consejo)" }).getAttribute("href")).toBe(
      "/egresos/revision",
    );
  });
});

describe("página /egresos/proveedores", () => {
  const conLista = (extra: Respuestas = {}) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }),
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        Response.json({
          proveedor: proveedor(),
          versiones: [
            {
              version: 1,
              razon_social: "Limpieza Ficticia SRL",
              rubro: "limpieza",
              contacto: null,
              cuenta_destino: CUENTA,
              documento_id: null,
              estado: "activo",
              huella: H1,
              preparada_por: PERSONA,
              creada_en: "2026-10-20T15:00:00Z",
            },
          ],
        }),
      ...extra,
    });

  it("la lista y la ficha muestran la cuenta enmascarada y nunca la completa", async () => {
    vi.stubGlobal("fetch", conLista());
    const { container } = abrir("/egresos/proveedores");
    const lista = await screen.findByRole("list", { name: "Proveedores" });
    expect(within(lista).getByText("…1234")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ver la ficha de Limpieza Ficticia SRL" }));
    await screen.findByRole("list", { name: "Versiones del proveedor" });
    fireEvent.click(screen.getByRole("button", { name: "Cambiar los datos" }));
    await screen.findByLabelText(/Cuenta destino nueva/);
    // Control: el número completo no aparece en ningún lugar del documento, ni en un campo.
    expect(container.innerHTML).not.toContain(CUENTA);
    expect((screen.getByLabelText(/Cuenta destino nueva/) as HTMLInputElement).value).toBe("");
    await sinFaltasDeAccesibilidad(container);
  });

  it("cambiar la cuenta muestra el aviso antes de mandar y crea una versión nueva", async () => {
    const simulado = conLista({
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Ver la ficha de Limpieza Ficticia SRL" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText(/Cuenta destino nueva/), { target: { value: CUENTA_NUEVA } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect((await screen.findByRole("alert")).textContent).toBe(AVISO_CUENTA);
    expect(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sí, cambiar la cuenta" }));
    expect(await screen.findByText("Listo: el proveedor quedó en la versión 2.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`));
    expect(await enviado.json()).toMatchObject({ cuenta_destino: CUENTA_NUEVA, version_esperada: 1 });
  });

  it("con el campo de cuenta vacío se conserva la cuenta y no hay aviso", async () => {
    const simulado = conLista({
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Ver la ficha de Limpieza Ficticia SRL" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText("Rubro (opcional)"), { target: { value: "limpieza y jardín" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Listo: el proveedor quedó en la versión 2.")).toBeTruthy();
    expect(screen.queryByText(AVISO_CUENTA)).toBeNull();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`));
    expect(await enviado.json()).toMatchObject({ cuenta_destino: CUENTA, rubro: "limpieza y jardín" });
  });
});

describe("página /egresos/ordenes", () => {
  const conLista = (ordenes: Orden[], extra: Respuestas = {}) =>
    backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago`]: () => Response.json({ elementos: ordenes }),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }),
      [`GET ${BASE}/contratos`]: () =>
        Response.json({ elementos: [contrato(K1), contrato(K2, { estado: "vencido", alcance: "Abono vencido" })] }),
      ...extra,
    });

  it("lista estado, importe, proveedor, franja y la alerta de fraccionamiento, sin la cuenta", async () => {
    vi.stubGlobal("fetch", conLista([orden({ alerta_fraccionamiento: true })]));
    const { container } = abrir("/egresos/ordenes");
    const lista = await screen.findByRole("list", { name: "Órdenes de pago" });
    expect(within(lista).getByRole("heading", { name: /Limpieza Ficticia SRL/ })).toBeTruthy();
    expect(within(lista).getByText("Borrador")).toBeTruthy();
    expect(within(lista).getByText("No supera el umbral")).toBeTruthy();
    expect(within(lista).getByText(/revisá si se fraccionó un gasto/)).toBeTruthy();
    expect(container.innerHTML).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
  });

  it("preparar ofrece solo los contratos aplicables y manda el elegido", async () => {
    const simulado = conLista([], {
      [`POST ${BASE}/ordenes-pago`]: () => Response.json({ id: O1, version: 1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/ordenes");
    fireEvent.click(await screen.findByRole("button", { name: "Preparar una orden" }));
    const selector = screen.getByLabelText("Contrato o abono (opcional)") as HTMLSelectElement;
    expect([...selector.options].map((o) => o.value)).toEqual(["", K1]);
    fireEvent.change(screen.getByLabelText("Importe"), { target: { value: "15000" } });
    fireEvent.change(screen.getByLabelText("Concepto"), { target: { value: "Limpieza de octubre" } });
    fireEvent.change(selector, { target: { value: K1 } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Listo: la orden quedó en borrador.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/ordenes-pago`));
    expect(await enviado.json()).toMatchObject({ importe_centavos: 1_500_000, proveedor_id: P1, contrato_id: K1 });
    expect(enviado.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("cambiar la moneda descarta el contrato elegido", async () => {
    vi.stubGlobal("fetch", conLista([]));
    abrir("/egresos/ordenes");
    fireEvent.click(await screen.findByRole("button", { name: "Preparar una orden" }));
    const selector = screen.getByLabelText("Contrato o abono (opcional)") as HTMLSelectElement;
    fireEvent.change(selector, { target: { value: K1 } });
    expect(selector.value).toBe(K1);
    fireEvent.change(screen.getByLabelText("Moneda"), { target: { value: "USD" } });
    expect(selector.value).toBe("");
    expect([...selector.options].map((o) => o.value)).toEqual([""]);
  });

  it("modificar una orden en revisión avisa que las aprobaciones anteriores dejan de contar", async () => {
    const simulado = conLista([orden({ estado: "en_revision" })], {
      [`GET ${BASE}/ordenes-pago/${O1}`]: () =>
        Response.json({ orden: orden({ estado: "en_revision" }), versiones: [version()] }),
      [`POST ${BASE}/ordenes-pago/${O1}/versiones`]: () => Response.json({ id: O1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/ordenes");
    fireEvent.click(await screen.findByRole("button", { name: "Modificar" }));
    fireEvent.change(await screen.findByLabelText("Importe"), { target: { value: "16000" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect((await screen.findByRole("alert")).textContent).toBe(AVISO_MODIFICAR);
    expect(pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/versiones`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sí, crear la versión nueva" }));
    expect(await screen.findByText("Listo: guardamos la versión nueva.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/versiones`));
    expect(await enviado.json()).toMatchObject({ importe_centavos: 1_600_000, version_esperada: 1 });
  });

  it("emitir manda la huella vista y, ante R10, reintenta con la misma clave", async () => {
    const aprobada = orden({
      estado: "aprobada",
      aprobaciones: 1,
      aprobaciones_requeridas: 1,
      aprobada_en: "2026-10-30T12:00:00Z",
      aprobacion_vence_en: "2999-01-01T00:00:00Z",
    });
    let intentos = 0;
    const simulado = conLista([aprobada], {
      [`GET ${BASE}/ordenes-pago/${O1}`]: () => Response.json({ orden: aprobada, versiones: [version({ huella: H2 })] }),
      [`POST ${BASE}/ordenes-pago/${O1}/emitir`]: () =>
        ++intentos === 1 ? error("segundo_factor_requerido", 403) : Response.json({ id: O1, version: 1 }),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/ordenes");
    fireEvent.click(await screen.findByRole("button", { name: "Emitir" }));
    await verificarCodigo();
    expect(await screen.findByText("Listo: la orden quedó en ejecución. No se movió dinero.")).toBeTruthy();
    const emisiones = pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/emitir`);
    expect(emisiones).toHaveLength(2);
    expect(await (emisiones[0] as Request).json()).toEqual({ version: 1, huella: H2 });
    expect(await (emisiones[1] as Request).json()).toEqual({ version: 1, huella: H2 });
    const [primera, segunda] = emisiones.map((p) => p.headers.get("Idempotency-Key"));
    expect(primera).toMatch(/^[0-9a-f-]{36}$/);
    expect(segunda).toBe(primera);
  });

  it("una aprobación vencida no ofrece emitir: ofrece renovar", async () => {
    const vieja = orden({
      estado: "aprobada",
      aprobada_en: "2026-10-01T12:00:00Z",
      aprobacion_vence_en: "2026-10-08T12:00:00Z",
    });
    vi.stubGlobal("fetch", conLista([vieja]));
    abrir("/egresos/ordenes");
    expect(await screen.findByRole("button", { name: "Renovar" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Emitir" })).toBeNull();
    expect(screen.getByText("La aprobación venció: renovala")).toBeTruthy();
  });
});

describe("página /egresos/revision", () => {
  const enRevision = orden({ estado: "en_revision", franja: "cuenta_cambiada", aprobaciones_requeridas: 1 });
  const ficha = (v: Version) => () => Response.json({ orden: { ...enRevision, version_vigente: v.numero }, versiones: [v] });

  it("la ficha muestra la cuenta completa, el CUIT, la condición y las aprobaciones", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/ordenes-pago/revision`]: () => Response.json({ elementos: [enRevision] }),
        [`GET ${BASE}/ordenes-pago/${O1}/revision`]: ficha(version()),
      }),
    );
    const { container } = abrir("/egresos/revision");
    // Control: en la lista la cuenta no aparece.
    await screen.findByRole("list", { name: "Órdenes enviadas" });
    expect(container.innerHTML).not.toContain(CUENTA);
    fireEvent.click(screen.getByRole("button", { name: "Revisar la orden de Limpieza Ficticia SRL" }));
    expect(await screen.findByText(CUENTA)).toBeTruthy();
    expect(screen.getByText("20000000001")).toBeTruthy();
    expect(screen.getByText("Cuenta cambiada")).toBeTruthy();
    expect(screen.getByText("0 de 1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Aprobar esta versión" })).toBeTruthy();
    await sinFaltasDeAccesibilidad(container);
  });

  it("aprobar manda versión y huella; ante R10 reintenta con la misma clave", async () => {
    let intentos = 0;
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago/revision`]: () => Response.json({ elementos: [enRevision] }),
      [`GET ${BASE}/ordenes-pago/${O1}/revision`]: ficha(version()),
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () =>
        ++intentos === 1
          ? error("segundo_factor_requerido", 403)
          : Response.json({ orden_id: O1, version: 1, estado: "aprobada" }, { status: 201 }),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/revision");
    fireEvent.click(await screen.findByRole("button", { name: "Revisar la orden de Limpieza Ficticia SRL" }));
    fireEvent.click(await screen.findByRole("button", { name: "Aprobar esta versión" }));
    await verificarCodigo();
    expect(await screen.findByText("Listo: aprobaste la versión 1.")).toBeTruthy();
    const aprobaciones = pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`);
    expect(aprobaciones).toHaveLength(2);
    expect(await (aprobaciones[0] as Request).json()).toEqual({ version: 1, huella: H1 });
    const [primera, segunda] = aprobaciones.map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("si la orden cambió (409), recarga la ficha, lo dice y no traslada la aprobación", async () => {
    let fichas = 0;
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago/revision`]: () => Response.json({ elementos: [enRevision] }),
      [`GET ${BASE}/ordenes-pago/${O1}/revision`]: () =>
        ++fichas === 1
          ? ficha(version())()
          : ficha(version({ numero: 2, huella: H2, importe_centavos: 9_900_000, cuenta_destino: CUENTA_NUEVA }))(),
      [`POST ${BASE}/ordenes-pago/${O1}/aprobaciones`]: () => error("huella_distinta", 409),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/revision");
    fireEvent.click(await screen.findByRole("button", { name: "Revisar la orden de Limpieza Ficticia SRL" }));
    fireEvent.click(await screen.findByRole("button", { name: "Aprobar esta versión" }));
    expect(await screen.findByText(/La orden cambió mientras la mirabas/)).toBeTruthy();
    expect(await screen.findByRole("heading", { name: /Versión 2/ })).toBeTruthy();
    expect(screen.getByText(CUENTA_NUEVA)).toBeTruthy();
    // Una sola aprobación mandada, por la versión 1: la versión 2 no recibió ninguna.
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`)).toHaveLength(1));
    expect(await unico(pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/aprobaciones`)).json()).toEqual({
      version: 1,
      huella: H1,
    });
  });

  it("rechazar pide un motivo", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/ordenes-pago/revision`]: () => Response.json({ elementos: [enRevision] }),
      [`GET ${BASE}/ordenes-pago/${O1}/revision`]: ficha(version()),
      [`POST ${BASE}/ordenes-pago/${O1}/rechazar`]: () => Response.json({ id: O1, version: 1 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/revision");
    fireEvent.click(await screen.findByRole("button", { name: "Revisar la orden de Limpieza Ficticia SRL" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rechazar" }));
    const enviar = screen.getByRole("button", { name: "Rechazar la orden" }) as HTMLButtonElement;
    expect(enviar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Motivo del rechazo"), { target: { value: "Falta la factura" } });
    fireEvent.click(enviar);
    expect(await screen.findByText("Listo: rechazaste la orden.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/ordenes-pago/${O1}/rechazar`));
    expect(await enviado.json()).toEqual({ version: 1, motivo: "Falta la factura" });
  });
});
