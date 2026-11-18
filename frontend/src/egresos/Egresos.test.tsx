import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import {
  aCentavos,
  conLaRedDeVuelta,
  cuentaEnmascarada,
  mensajeDeActo,
  mensajeDeError,
  renovarClave,
  sinElAvisoDeLaRed,
  sinPisarLoHecho,
  sobreLoNuevo,
  soltarClaveSiCambio,
  vencida,
  VOLVIO_LA_RED,
  TEXTO_SESION_VENCIDA,
} from "./comun";
import { AVISO_CUENTA, AVISO_PROVEEDOR_CAMBIO, FALTA_CUIT, FALTA_RAZON, MARCA_FALTANTE, MARCA_VENCIDA, NO_CAMBIAR_CUENTA, SIN_CAMBIOS_PARA_GUARDAR } from "./Proveedores";
import { CARGAR, FALTA_VENCIMIENTO, SIN_DOCUMENTACION as SIN_DOCUMENTACION_TEXTO, SIN_REQUISITO, TITULO_SECCION } from "./DocumentacionProveedor";
import { RUTA_CONSULTA_PROVEEDORES, TITULO_CONSULTA_PROVEEDORES } from "./ConsultaProveedores";
import {
  ACLARACION_RETIRO,
  AUTORIZAR,
  CONFIRMAR_AUTORIZAR,
  CONFIRMAR_RETIRAR,
  FALTA_MOTIVO,
  MOTIVO,
  RETIRAR,
  RUTA_AUTORIZACIONES_PROVEEDORES,
  textoAutorizado,
  textoRetirado,
  TITULO_AUTORIZACIONES_PROVEEDORES,
} from "./AutorizacionesProveedores";
import { AGREGAR_RUBRO, AGREGAR_UN_RUBRO, AVISO_FALTA_AVISA, AVISO_FALTA_BLOQUEA, FALTA_RUBRO, TITULO_EXIGENCIA } from "./ExigenciaLegal";
import {
  CONFIRMAR_SALTEO,
  EXPLICACION_SALTEO,
  FALTA_MOTIVO_SALTEO,
  MOTIVO_SALTEO,
  SALTEAR,
  SALTEO_REGISTRADO,
  textoSalteo,
  textoSalteoDeLaVersion,
  TITULO_SALTEO,
} from "./SalteoDocumentacion";
import { CUIT_INVALIDO } from "../CampoCuit";
import { CUENTA_INVALIDA } from "../cuentaBancaria";

// Pantallas de egresos (H3, aprobadas el 30-10-2026). Todos los datos son ficticios: CUIT y CBU
// inventados, sin ningún proveedor ni cuenta real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const P1 = "22222222-2222-4222-8222-222222222222";
const O1 = "33333333-3333-4333-8333-333333333333";
const PERSONA = "66666666-6666-4666-8666-666666666666";
const DOC = "77777777-7777-4777-8777-777777777777";
// Con los dos dígitos verificadores bien: la pantalla los comprueba al escribir (tanda 3, P-26).
const CUENTA = "0000003160000000001234";
const CUENTA_NUEVA = "0000003140000000009876";
/** 22 números con el segundo verificador mal. */
const CUENTA_MALA = "0000003160000000001235";
const H1 = "a".repeat(64);
const BASE = `/api/v1/consorcios/${C1}`;
const C2 = "abababab-abab-4bab-8bab-abababababab";

type Proveedor = components["schemas"]["Proveedor"];
type Orden = components["schemas"]["OrdenPago"];

/** La ficha sin seguros ni habilitaciones (RF50, app 0103), de quien puede cargarlos. */
// RF50 configurable (app 0104): la ficha trae los rubros legales, lo que falta, la matriz y la configuración.
const EXIGENCIA = {
  rubros_legales: [] as string[],
  faltantes: [] as components["schemas"]["RequisitoFaltante"][],
  requisitos: [] as components["schemas"]["RequisitoLegal"][],
  configuracion: { dias_por_vencer: 30, modo_exigencia: "bloquear" as const, admite_no_vence: true },
  // Salteo por urgencia (app 0106).
  salteos: [] as components["schemas"]["SalteoDocumentacion"][],
};
const SIN_DOCUMENTACION = { documentacion: [], puede_cargar_documentacion: true, ...EXIGENCIA };

function proveedor(extra: Partial<Proveedor> = {}): Proveedor {
  return {
    id: P1,
    cuit: "20000000001",
    razon_social: "Limpieza Ficticia SRL",
    rubro: "limpieza",
    contacto: null,
    telefono: null,
    whatsapp: null,
    correo: null,
    domicilio: null,
    atiende_emergencias: false,
    cuenta_ultimos_digitos: CUENTA.slice(-4),
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
    aprobada_por_vos: false,
    motivo_rechazo: null,
    preparada_por: PERSONA,
    creada_en: "2026-10-30T12:00:00Z",
    actualizada_en: "2026-10-30T12:00:00Z",
    ...extra,
  };
}

function error(codigo: string, status: number, detalle: Record<string, unknown> | null = null) {
  return Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response | Promise<Response>>;

/** La pantalla del código pregunta si hay segundo factor; salvo que la prueba diga otra cosa, lo hay. */
const CON_SEGUNDO_FACTOR: Respuestas = {
  "GET /api/v1/auth/totp": () =>
    Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
};

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? CON_SEGUNDO_FACTOR[clave] ?? alcanzaTodo(clave);
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

const INICIO: Respuestas = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () => Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": CONSORCIOS,
  // Con todas las acciones, el inicio cuenta los vínculos sin confirmar (G-26).
  [`GET /api/v1/consorcios/${C1}/padron`]: () => Response.json({ elementos: [] }),
  // Lo demás que lee el inicio como bandeja (decisión 9 del 18-11-2026), sin pendientes.
  [`GET ${BASE}/pagos/bandeja`]: () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 }),
  [`GET ${BASE}/solicitudes-formales`]: () => Response.json({ elementos: [] }),
  [`GET ${BASE}/mandatos`]: () => Response.json({ elementos: [] }),
  [`GET ${BASE}/contactos/gestion`]: () => Response.json({ elementos: [] }),
};

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

const volvioLaRed = () => fireEvent(window, new Event("online"));

/** Las claves de reintento de esos pedidos, en orden. */

/** Verifica el código TOTP en el formulario que aparece ante `segundo_factor_requerido`. */
async function verificarCodigo() {
  fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
}

const VERIFICADO = () =>
  Response.json({
    verificado_en: "2026-10-30T12:00:00Z",
    vale_hasta: "2026-10-30T12:10:00Z",
    codigos_recuperacion_restantes: 9,
  });

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
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
    expect(cuentaEnmascarada("1234")).toBe("…1234");
    expect(cuentaEnmascarada(null)).toBe("Sin cuenta");
  });

  it("el importe escrito pasa a centavos y rechaza lo que no es un importe", () => {
    expect(aCentavos("12.345,50")).toBe(1_234_550);
    expect(aCentavos("1234.5")).toBe(123_450);
    expect(aCentavos("1.234")).toBe(123_400);
    expect(aCentavos("1.234.567")).toBe(123_456_700);
    expect(aCentavos("1.234,5")).toBe(123_450);
    expect(aCentavos("1.23")).toBe(123);
    expect(aCentavos("1234,05")).toBe(123_405);
    expect(aCentavos("120.000,00")).toBe(12_000_000);
    expect(aCentavos("0,07")).toBe(7);
    expect(aCentavos("4.35")).toBe(435);
    expect(aCentavos("1.2345")).toBeNull();
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


  it("una aprobación vence a los 7 días: la marca la API y la pantalla la respeta", () => {
    const aprobada = orden({ estado: "aprobada", aprobacion_vence_en: "2026-11-06T12:00:00Z" });
    expect(vencida(aprobada, Date.parse("2026-11-06T11:59:59Z"))).toBe(false);
    expect(vencida(aprobada, Date.parse("2026-11-06T12:00:00Z"))).toBe(true);
    expect(vencida(orden({ estado: "borrador" }), Date.parse("2030-01-01T00:00:00Z"))).toBe(false);
  });

  it("la clave sigue mientras se repite el mismo acto y se suelta si el acto cambió", () => {
    const clave: { current: string | null } = { current: null };
    const ultimo: { current: string | null } = { current: null };
    soltarClaveSiCambio(clave, ultimo, "aprobar");
    clave.current = "k";
    soltarClaveSiCambio(clave, ultimo, "aprobar");
    expect(clave.current).toBe("k");
    soltarClaveSiCambio(clave, ultimo, "rechazar");
    expect(clave.current).toBeNull();
  });

  it("sobre una versión nueva queda lo que la persona cambió y lo demás toma el valor nuevo", () => {
    const visto = { importe: "100", concepto: "Limpieza" };
    const escrito = { importe: "200", concepto: "Limpieza" };
    const nuevo = { importe: "100", concepto: "Pintura" };
    expect(sobreLoNuevo(escrito, visto, nuevo)).toEqual({ importe: "200", concepto: "Pintura" });
    // Control: sin nada cambiado queda lo nuevo entero.
    expect(sobreLoNuevo(visto, visto, nuevo)).toEqual(nuevo);
  });

  it("la separación de funciones dice el motivo concreto que manda el servidor (pregunta 10)", () => {
    const separacion = { codigo: "ya_participo", mensaje: "La preparó esta cuenta: la aprueba otra persona del Consejo." };
    expect(mensajeDeActo(403, separacion)).toBe("La preparó esta cuenta: la aprueba otra persona del Consejo.");
    // Control: sin_permiso es la facultad que falta, y no el párrafo de las dos causas posibles.
    expect(mensajeDeActo(403, { codigo: "sin_permiso", mensaje: "otro" })).toBe("Esta cuenta no tiene la facultad para esto en este edificio.");
    expect(mensajeDeActo(403, { codigo: "sin_permiso", mensaje: "otro" })).not.toMatch(/Puede ser que/);
    expect(mensajeDeActo(403, { codigo: "no_es_quien_aprobo", mensaje: "otro" })).toBe(mensajeDeError(403, "no_es_quien_aprobo"));
  });

  it("el aviso de la red no pisa un «Listo», y una falla sí lo pisa a él", () => {
    const listo = { texto: "Listo: hecho.", ok: true };
    const red = { texto: VOLVIO_LA_RED, ok: true };
    expect(conLaRedDeVuelta(null)).toEqual(red);
    expect(conLaRedDeVuelta({ texto: "falló", ok: false })).toEqual(red);
    expect(conLaRedDeVuelta(listo)).toBe(listo);
    expect(sinPisarLoHecho("falló")(red)).toEqual({ texto: "falló", ok: false });
    expect(sinPisarLoHecho("falló")(listo)).toBe(listo);
    expect(sinElAvisoDeLaRed(red)).toBeNull();
    expect(sinElAvisoDeLaRed(listo)).toBe(listo);
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
              cuenta_ultimos_digitos: CUENTA.slice(-4),
              documento_id: null,
              estado: "activo",
              huella: H1,
              preparada_por: PERSONA,
              creada_en: "2026-10-20T15:00:00Z",
            },
          ],
          ...SIN_DOCUMENTACION,
        }),
      ...extra,
    });

  // RF50 (app 0103): seguros y habilitaciones con vigencia. Vencido marca y no excluye.
  const D1 = "88888888-8888-4888-8888-888888888888";
  const DOCUMENTACION = `${BASE}/proveedores/${P1}/documentacion`;
  function documentacion(extra: Partial<components["schemas"]["DocumentacionProveedor"]> = {}) {
    return {
      id: D1,
      proveedor_id: P1,
      tipo: "seguro" as const,
      descripcion: "ART del personal SINTETICA",
      vence_el: "2026-11-01",
      documento_id: DOC,
      version_documento_id: DOC,
      estado: "vencida" as const,
      cargada_por_nombre: "Persona Ficticia",
      cargada_en: "2026-10-20T15:00:00Z",
      reemplaza_a: null,
      documento_vigente: true,
      no_vence: false,
      requisito_codigo: null,
      ...extra,
    };
  }

  it("RF50: la lista marca al proveedor con documentación vencida y la ficha lo dice una vez, sin frenar nada (SEG-08)", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}/proveedores`]: () =>
          Response.json({ elementos: [proveedor()], documentacion_vencida: [P1], documentacion_por_vencer: [] }),
        [`GET ${BASE}/proveedores/${P1}`]: () =>
          Response.json({
            proveedor: proveedor(),
            versiones: [],
            documentacion: [
              documentacion(),
              documentacion({ id: O1, tipo: "habilitacion", descripcion: "Matricula SINTETICA", estado: "vigente", vence_el: "2027-03-31" }),
              documentacion({ id: PERSONA, descripcion: "Poliza vieja SINTETICA", estado: "reemplazada" }),
            ],
            puede_cargar_documentacion: true,
            ...EXIGENCIA,
          }),
      }),
    );
    const { container } = abrir("/egresos/proveedores");
    const fila = await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    expect(fila.textContent).toContain(MARCA_VENCIDA);
    fireEvent.click(fila);
    const lista = await screen.findByRole("list", { name: "Seguros y habilitaciones" });
    // El vencimiento lo dice la etiqueta del documento; sin otra banda que lo repita (SEG-08).
    expect(screen.queryByText(/Tiene documentación vencida/)).toBeNull();
    expect(within(lista).getByText("Vencido el 01/11/2026")).toBeTruthy();
    expect(within(lista).getByText("Vigente hasta el 31/03/2027")).toBeTruthy();
    // Lo reemplazado va a la historia, no a la lista; cada una que vale se puede reemplazar.
    expect(within(lista).queryByText(/Poliza vieja/)).toBeNull();
    expect(within(lista).getAllByRole("button", { name: "Reemplazar" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Agregar un seguro o una habilitación" })).toBeTruthy();
    // No frena: cambiar los datos sigue a mano.
    expect(screen.getByRole("button", { name: "Cambiar los datos" })).toBeTruthy();
    await sinFaltasDeAccesibilidad(container);
  });

  it("RF50: quien no carga documentos ve el estado pero no los botones", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}/proveedores/${P1}`]: () =>
          Response.json({ proveedor: proveedor(), versiones: [], documentacion: [documentacion()], puede_cargar_documentacion: false, ...EXIGENCIA }),
      }),
    );
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    expect(await screen.findByText("Vencido el 01/11/2026")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reemplazar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar un seguro o una habilitación" })).toBeNull();
  });

  it("RF50: sin constancias no marca ni avisa; quien carga ve el título y su botón, sin la frase (SEG-03)", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir("/egresos/proveedores");
    const fila = await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    expect(fila.textContent).not.toContain(MARCA_VENCIDA);
    fireEvent.click(fila);
    expect(await screen.findByRole("heading", { name: TITULO_SECCION })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Agregar un seguro o una habilitación" })).toBeTruthy();
    expect(screen.queryByText(SIN_DOCUMENTACION_TEXTO)).toBeNull();
    // Sin rubros ni nada que agregar, «Lo que exige la ley» no se dibuja.
    expect(screen.queryByRole("heading", { name: TITULO_EXIGENCIA })).toBeNull();
  });

  it("RF50: reemplazar manda el tipo, la descripción, el vencimiento y la constancia; sin vencimiento no manda nada", async () => {
    const api = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        Response.json({ proveedor: proveedor(), versiones: [], documentacion: [documentacion()], puede_cargar_documentacion: true, ...EXIGENCIA }),
      [`POST ${DOCUMENTACION}`]: () =>
        Response.json(
          {
            proveedor_id: P1,
            constancia_id: O1,
            documento_id: DOC,
            archivo: { id: DOC, estado: "en_cuarentena", tipo: "application/pdf", tamano: 18, sha256: H1 },
          },
          { status: 201 },
        ),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Reemplazar" }));
    const hoja = await screen.findByRole("dialog", { name: /^Reemplazar/ });
    const poliza = new File(["%PDF-1.4 SINTETICO"], "poliza.pdf", { type: "application/pdf" });
    const subidas = () => api.mock.calls.map(([q]) => q as Request).filter((q) => q.method === "POST");
    // Control: sin la fecha no se manda nada y se dice qué falta.
    fireEvent.change(within(hoja).getByLabelText("Archivo del documento"), { target: { files: [poliza] } });
    expect(await within(hoja).findByText("Falta la fecha de vencimiento.")).toBeTruthy();
    expect(subidas()).toHaveLength(0);

    fireEvent.change(within(hoja).getByLabelText("Vence el"), { target: { value: "2027-11-01" } });
    fireEvent.change(within(hoja).getByLabelText("Archivo del documento"), { target: { files: [poliza] } });
    expect(await within(hoja).findByText(/Listo: quedó cargado/)).toBeTruthy();
    expect(subidas()).toHaveLength(1);
    const pedido = subidas()[0] as Request;
    const url = new URL(pedido.url);
    expect(url.pathname).toBe(DOCUMENTACION);
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      tipo: "seguro",
      descripcion: "ART del personal SINTETICA",
      vence_el: "2027-11-01",
      reemplaza: D1,
    });
    expect(pedido.headers.get("Content-Type")).toBe("application/pdf");
    expect(pedido.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("SEG-10: el archivo elegido antes de completar los datos queda, y «Cargar» lo manda sin elegirlo de nuevo", async () => {
    const api = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        Response.json({ proveedor: proveedor(), versiones: [], documentacion: [documentacion()], puede_cargar_documentacion: true, ...EXIGENCIA }),
      [`POST ${DOCUMENTACION}`]: () =>
        Response.json({ proveedor_id: P1, constancia_id: O1, documento_id: DOC,
          archivo: { id: DOC, estado: "en_cuarentena", tipo: "application/pdf", tamano: 18, sha256: H1 } }, { status: 201 }),  // prettier-ignore
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Reemplazar" }));
    const hoja = await screen.findByRole("dialog", { name: /^Reemplazar/ });
    const poliza = new File(["%PDF-1.4 SINTETICO"], "poliza.pdf", { type: "application/pdf" });
    fireEvent.change(within(hoja).getByLabelText("Archivo del documento"), { target: { files: [poliza] } });
    // Lo que falta se marca junto a su campo, y el archivo queda elegido.
    const fecha = within(hoja).getByLabelText("Vence el");
    expect(fecha.getAttribute("aria-invalid")).toBe("true");
    expect(within(hoja).getByText(FALTA_VENCIMIENTO)).toBeTruthy();
    expect(within(hoja).getByText("Elegido: poliza.pdf")).toBeTruthy();
    expect(pedidos(api, "POST", DOCUMENTACION)).toHaveLength(0);
    // Control: «Cargar» sin la fecha tampoco manda nada.
    fireEvent.click(within(hoja).getByRole("button", { name: CARGAR }));
    expect(pedidos(api, "POST", DOCUMENTACION)).toHaveLength(0);
    fireEvent.change(fecha, { target: { value: "2027-11-01" } });
    expect(fecha.getAttribute("aria-invalid")).toBeNull();
    fireEvent.click(within(hoja).getByRole("button", { name: CARGAR }));
    expect(await within(hoja).findByText(/Listo: quedó cargado/)).toBeTruthy();
    expect(new URL(unico(pedidos(api, "POST", DOCUMENTACION)).url).searchParams.get("vence_el")).toBe("2027-11-01");
  });

  it("SEG-09: cuando el antivirus acepta el archivo, la hoja se cierra sola y la ficha se vuelve a leer", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const ESTADO_VERSION = `${BASE}/documentos/${DOC}/versiones/${DOC}/estado`;
    let revisado = false;
    const api = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        Response.json({ proveedor: proveedor(), versiones: [], puede_cargar_documentacion: true, ...EXIGENCIA,
          documentacion: revisado ? [documentacion({ estado: "vigente", vence_el: "2027-11-01" })] : [] }),  // prettier-ignore
      [`POST ${DOCUMENTACION}`]: () =>
        Response.json({ proveedor_id: P1, constancia_id: O1, documento_id: DOC,
          archivo: { id: DOC, estado: "en_cuarentena", tipo: "application/pdf", tamano: 18, sha256: H1 } }, { status: 201 }),  // prettier-ignore
      [`GET ${ESTADO_VERSION}`]: () => {
        revisado = true;
        return Response.json({ id: DOC, estado: "disponible" });
      },
    });
    vi.stubGlobal("fetch", api);
    try {
      abrir("/egresos/proveedores");
      fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
      fireEvent.click(await screen.findByRole("button", { name: "Agregar un seguro o una habilitación" }));
      const hoja = await screen.findByRole("dialog", { name: "Agregar un seguro o una habilitación" });
      fireEvent.change(within(hoja).getByLabelText("Qué cubre"), { target: { value: "ART SINTETICA" } });
      fireEvent.change(within(hoja).getByLabelText("Vence el"), { target: { value: "2027-11-01" } });
      const archivo = new File(["%PDF-1.4 SINTETICO"], "art.pdf", { type: "application/pdf" });
      fireEvent.change(within(hoja).getByLabelText("Archivo del documento"), { target: { files: [archivo] } });
      expect(await within(hoja).findByText(/Listo: quedó cargado/)).toBeTruthy();
      const lecturas = pedidos(api, "GET", `${BASE}/proveedores/${P1}`).length;
      await act(() => vi.advanceTimersByTimeAsync(3000));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Agregar un seguro o una habilitación" })).toBeNull());
      expect(pedidos(api, "GET", `${BASE}/proveedores/${P1}`).length).toBeGreaterThan(lecturas);
      expect(await screen.findByText("Vigente hasta el 01/11/2027")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  // RF50 configurable (app 0104): rubros legales, lo que falta, «No vence» y el requisito.
  const ASCENSOR_SEGURO = {
    codigo: "ascensores.seguro_rc",
    rubro_legal: "ascensores",
    tipo: "seguro" as const,
    descripcion: "Responsabilidad civil de la empresa conservadora SINTETICA",
    fuente: "Fuente SINTETICA",
    verificado: true,
    exigido: true,
  };
  const ASCENSOR_PERMISO = { ...ASCENSOR_SEGURO, codigo: "ascensores.permiso_conservador", tipo: "habilitacion" as const,
    descripcion: "Permiso de conservador SINTETICO" };  // prettier-ignore
  const GAS = { ...ASCENSOR_SEGURO, codigo: "gas.matricula_gasista", rubro_legal: "gas", tipo: "habilitacion" as const,
    descripcion: "Matrícula de gasista SINTETICA" };  // prettier-ignore
  const REQUISITOS_DE_PRUEBA = [ASCENSOR_PERMISO, ASCENSOR_SEGURO, GAS];
  const RUBROS = `${BASE}/proveedores/${P1}/rubros-legales`;
  type Exigencia = Omit<components["schemas"]["ProveedorConVersiones"], "proveedor" | "versiones" | "configuracion">;
  function fichaConExigencia(extra: Partial<Exigencia> = {}, configuracion: Partial<components["schemas"]["ConfiguracionProveedores"]> = {}) {
    return () =>
      Response.json({
        proveedor: proveedor(),
        versiones: [],
        documentacion: [],
        puede_cargar_documentacion: true,
        ...EXIGENCIA,
        requisitos: REQUISITOS_DE_PRUEBA,
        ...extra,
        configuracion: { ...EXIGENCIA.configuracion, ...configuracion },
      });
  }
  const FALTA_SEGURO = { proveedor_id: P1, codigo: ASCENSOR_SEGURO.codigo, rubro_legal: "ascensores", tipo: "seguro" as const,
    descripcion: ASCENSOR_SEGURO.descripcion };  // prettier-ignore

  it("RF50c: la ficha dice los rubros legales y lo que falta; en «bloquear» avisa que no se preparan órdenes nuevas", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}/proveedores`]: () =>
          Response.json({ elementos: [proveedor()], documentacion_vencida: [], documentacion_por_vencer: [],
            documentacion_faltante: [P1], modo_exigencia: "bloquear" }),  // prettier-ignore
        [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia({ rubros_legales: ["ascensores"], faltantes: [FALTA_SEGURO] }),
      }),
    );
    const { container } = abrir("/egresos/proveedores");
    const fila = await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    expect(fila.textContent).toContain(MARCA_FALTANTE);
    fireEvent.click(fila);
    expect(await screen.findByText(AVISO_FALTA_BLOQUEA)).toBeTruthy();
    expect(screen.queryByText(AVISO_FALTA_AVISA)).toBeNull();
    const falta = screen.getByRole("list", { name: "Lo que falta" });
    expect(within(falta).getByText(/Responsabilidad civil de la empresa conservadora SINTETICA/)).toBeTruthy();
    // «Quitar» no repite el rubro que tiene al lado; el nombre completo es para el lector (SEG-02).
    const quitar = screen.getByRole("button", { name: "Quitar Ascensores y montacargas" });
    expect(quitar.textContent).toBe("Quitar");
    // El rubro se agrega en su ventana (SEG-01), y solo se ofrecen los que no tiene.
    expect(screen.queryByLabelText("Rubro legal")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: AGREGAR_UN_RUBRO }));
    const ventana = await screen.findByRole("dialog", { name: AGREGAR_UN_RUBRO });
    const opciones = within(within(ventana).getByLabelText("Rubro legal")).getAllByRole("option").map((o) => o.textContent);
    expect(opciones).toEqual(["Elegir un rubro", "Gas"]);
    await sinFaltasDeAccesibilidad(container);
  });

  it("RF50c: en «avisar» solo marca; sin rubros no avisa nada", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia({ rubros_legales: ["ascensores"],
      faltantes: [FALTA_SEGURO] }, { modo_exigencia: "avisar" }) }));  // prettier-ignore
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    expect(await screen.findByText(AVISO_FALTA_AVISA)).toBeTruthy();
    expect(screen.queryByText(AVISO_FALTA_BLOQUEA)).toBeNull();
    cleanup();
    // Control: sin rubros ni faltantes, el título y el botón de agregar, sin una frase que lo diga (SEG-03).
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia() }));
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    expect(await screen.findByRole("button", { name: AGREGAR_UN_RUBRO })).toBeTruthy();
    expect(screen.getByRole("heading", { name: TITULO_EXIGENCIA })).toBeTruthy();
    expect(screen.queryByText(/No tiene rubros legales/)).toBeNull();
    expect(screen.queryByText(AVISO_FALTA_AVISA)).toBeNull();
    expect(screen.queryByText(AVISO_FALTA_BLOQUEA)).toBeNull();
  });

  it("RF50c: agregar y quitar un rubro mandan el pedido con su clave y vuelven a leer la ficha", async () => {
    const respuesta = () => Response.json({ proveedor_id: P1, rubros_legales: [], faltantes: [] });
    const api = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia({ rubros_legales: ["ascensores"] }),
      [`POST ${RUBROS}/gas`]: respuesta,
      [`DELETE ${RUBROS}/ascensores`]: respuesta,
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: AGREGAR_UN_RUBRO }));
    const ventana = await screen.findByRole("dialog", { name: AGREGAR_UN_RUBRO });
    const agregar = within(ventana).getByRole("button", { name: AGREGAR_RUBRO });
    // Control: el botón está activo (C8P-21); sin elegir un rubro, lo marca y no manda nada.
    expect((agregar as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(agregar);
    expect(await within(ventana).findByText(FALTA_RUBRO)).toBeTruthy();
    expect(within(ventana).getByLabelText("Rubro legal").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(api, "POST", `${RUBROS}/gas`)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Rubro legal"), { target: { value: "gas" } });
    fireEvent.click(agregar);
    await waitFor(() => expect(pedidos(api, "POST", `${RUBROS}/gas`)).toHaveLength(1));
    // Terminado, la ventana se cierra sola.
    await waitFor(() => expect(screen.queryByRole("dialog", { name: AGREGAR_UN_RUBRO })).toBeNull());
    expect(unico(pedidos(api, "POST", `${RUBROS}/gas`)).headers.get("Idempotency-Key")).toBeTruthy();
    await waitFor(() => expect(pedidos(api, "GET", `${BASE}/proveedores/${P1}`).length).toBeGreaterThan(1));
    fireEvent.click(await screen.findByRole("button", { name: "Quitar Ascensores y montacargas" }));
    await waitFor(() => expect(pedidos(api, "DELETE", `${RUBROS}/ascensores`)).toHaveLength(1));
  });

  it("RF50c: «No vence» deja la fecha en blanco y manda el requisito elegido del mismo tipo", async () => {
    const api = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia(),
      [`POST ${BASE}/proveedores/${P1}/documentacion`]: () =>
        Response.json({ proveedor_id: P1, constancia_id: O1, documento_id: DOC,
          archivo: { id: DOC, estado: "en_cuarentena", tipo: "application/pdf", tamano: 18, sha256: H1 } }, { status: 201 }),  // prettier-ignore
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un seguro o una habilitación" }));
    const hoja = await screen.findByRole("dialog", { name: "Agregar un seguro o una habilitación" });
    fireEvent.click(within(hoja).getByLabelText("Habilitación"));
    // Solo los requisitos de habilitaciones.
    const opciones = within(within(hoja).getByLabelText("Qué requisito de la ley cubre")).getAllByRole("option").map((o) => o.textContent);
    expect(opciones).toEqual([SIN_REQUISITO, ASCENSOR_PERMISO.descripcion, GAS.descripcion]);
    fireEvent.change(within(hoja).getByLabelText("Qué requisito de la ley cubre"), { target: { value: GAS.codigo } });
    fireEvent.change(within(hoja).getByLabelText("Qué cubre"), { target: { value: "Matricula SINTETICA" } });
    fireEvent.click(within(hoja).getByLabelText(/No vence/));
    expect((within(hoja).getByLabelText("Vence el") as HTMLInputElement).disabled).toBe(true);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "matricula.pdf", { type: "application/pdf" });
    fireEvent.change(within(hoja).getByLabelText("Archivo del documento"), { target: { files: [archivo] } });
    expect(await within(hoja).findByText(/Listo: quedó cargado/)).toBeTruthy();
    const url = new URL(unico(pedidos(api, "POST", `${BASE}/proveedores/${P1}/documentacion`)).url);
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ tipo: "habilitacion", no_vence: "true", requisito: GAS.codigo });
    expect(url.searchParams.has("vence_el")).toBe(false);
  });

  it("RF50c: si el edificio no admite «No vence», no se ofrece", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia({}, { admite_no_vence: false }) }));
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un seguro o una habilitación" }));
    const hoja = await screen.findByRole("dialog", { name: "Agregar un seguro o una habilitación" });
    expect(within(hoja).getByLabelText("Vence el")).toBeTruthy();  // control: la hoja está
    expect(within(hoja).queryByLabelText(/No vence/)).toBeNull();
  });

  it("RF50c: una constancia que no vence se dice así", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${BASE}/proveedores/${P1}`]: fichaConExigencia({
      documentacion: [documentacion({ estado: "vigente", vence_el: null, no_vence: true, requisito_codigo: GAS.codigo })] }) }));  // prettier-ignore
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    expect(await screen.findByText("Vigente: no vence")).toBeTruthy();
    expect(screen.getByText(`Cubre: ${GAS.descripcion}`)).toBeTruthy();
  });

  it("un proveedor sin CUIT lo carga por su ruta, con la versión que se vio", async () => {
    const CARGA = `${BASE}/proveedores/${P1}/cuit`;
    const sinCuit = proveedor({ cuit: null });
    const api = conLista({
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [sinCuit] }),
      [`GET ${BASE}/proveedores/${P1}`]: () => Response.json({ proveedor: sinCuit, versiones: [], ...SIN_DOCUMENTACION }),
      [`POST ${CARGA}`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cargar el CUIT" }));
    // En su ventana, sobre la ficha, con el foco en el campo (PRV-02).
    const ventana = await screen.findByRole("dialog", { name: "Cargar el CUIT de Limpieza Ficticia SRL" });
    await waitFor(() => expect(document.activeElement).toBe(within(ventana).getByLabelText(/^CUIT/)));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "20000000001" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar el CUIT" }));
    // Solo lo que se hizo: lo de la orden aprobada lo dice su ficha (PRV-06).
    expect((await screen.findByText(/Listo: el CUIT de/)).textContent).toBe("Listo: el CUIT de Limpieza Ficticia SRL quedó cargado.");
    const pedido = api.mock.calls.map(([q]) => q as Request).find((q) => q.method === "POST" && new URL(q.url).pathname === CARGA);
    expect(await pedido?.clone().json()).toEqual({ cuit: "20000000001", version_esperada: 1 });
  });

  it("Codex en #236: si otra sesión ya cargó el CUIT (409 con campo), cierra y recarga en vez de marcar el campo", async () => {
    const CARGA = `${BASE}/proveedores/${P1}/cuit`;
    const sinCuit = proveedor({ cuit: null });
    const api = conLista({
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [sinCuit] }),
      [`GET ${BASE}/proveedores/${P1}`]: () => Response.json({ proveedor: sinCuit, versiones: [], ...SIN_DOCUMENTACION }),
      [`POST ${CARGA}`]: () => error("cuit_ya_cargado", 409, { campos: ["body.cuit"] }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cargar el CUIT" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "20000000001" } });
    const listasAntes = pedidos(api, "GET", `${BASE}/proveedores`).length;
    fireEvent.click(screen.getByRole("button", { name: "Guardar el CUIT" }));
    expect(await screen.findByText("Ese proveedor ya tiene CUIT, y un CUIT no cambia.")).toBeTruthy();
    // Lo que se veía cambió: el formulario se cierra y la lista se vuelve a pedir.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Guardar el CUIT" })).toBeNull());
    expect(screen.queryByText(/^Hay que corregir el CUIT/)).toBeNull();
    await waitFor(() => expect(pedidos(api, "GET", `${BASE}/proveedores`).length).toBeGreaterThan(listasAntes));
  });

  it("la fila entera abre la ficha, sin un botón aparte, y al volver el foco queda en la fila", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir("/egresos/proveedores");
    const fila = await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    expect(fila.textContent).toContain("Cuenta …1234");
    expect(screen.queryByRole("button", { name: /Ver la ficha/ })).toBeNull();
    fireEvent.click(fila);
    expect(await screen.findByRole("heading", { level: 2, name: "Limpieza Ficticia SRL" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(document.activeElement?.textContent).toMatch(/^Limpieza Ficticia SRL /));
  });

  it("PRV-04: la fila no dice «Activo» ni «Cuenta Sin cuenta», y la ficha omite el rubro que no hay", async () => {
    const sinCuenta = proveedor({ cuenta_ultimos_digitos: null, rubro: null });
    const suspendido = proveedor({ id: O1, razon_social: "Pintura Ficticia SA", estado: "suspendido" });
    vi.stubGlobal("fetch", conLista({
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [sinCuenta, suspendido] }),
      [`GET ${BASE}/proveedores/${P1}`]: () => Response.json({ proveedor: sinCuenta, versiones: [], ...SIN_DOCUMENTACION }),
    }));
    abrir("/egresos/proveedores");
    const fila = await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    expect(fila.textContent).toBe("Limpieza Ficticia SRL 20000000001 · Sin cuenta");
    // Control: un estado que no es el de siempre sí se dice.
    expect(screen.getByRole("button", { name: /^Pintura Ficticia SA / }).textContent).toContain("· Suspendido ·");
    fireEvent.click(fila);
    const ficha = await screen.findByRole("dialog", { name: "Limpieza Ficticia SRL" });
    expect(within(ficha).queryByText("Rubro")).toBeNull();
    expect(within(ficha).queryByText("Estado")).toBeNull();
    // La cuenta que falta sí: hace falta para pagarle.
    expect(within(ficha).getByText("Sin cuenta")).toBeTruthy();
    // Las acciones van enseguida de los datos, antes de las secciones (PRV-01).
    const cambiar = within(ficha).getByRole("button", { name: "Cambiar los datos" });
    const seguros = within(ficha).getByRole("heading", { name: TITULO_SECCION });
    expect(cambiar.compareDocumentPosition(seguros) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("control: un proveedor con CUIT no ofrece cargarlo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    await screen.findByRole("button", { name: "Cambiar los datos" });
    expect(screen.queryByRole("button", { name: "Cargar el CUIT" })).toBeNull();
  });

  it("con una sola versión, la ficha no repite los datos que ya muestra", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    await screen.findByRole("button", { name: "Cambiar los datos" });
    expect(screen.queryByText("Versiones anteriores")).toBeNull();
    expect(screen.queryByText(/^Versión 1/)).toBeNull();
  });

  it("control: con dos versiones, la ficha muestra solo la anterior", async () => {
    const anterior = {
      version: 1,
      razon_social: "Limpieza Vieja SRL",
      rubro: "limpieza",
      contacto: null,
      cuenta_ultimos_digitos: "9876",
      documento_id: null,
      estado: "activo",
      huella: H1,
      preparada_por: PERSONA,
      creada_en: "2026-10-20T15:00:00Z",
    };
    const vigente = { ...anterior, version: 2, razon_social: "Limpieza Ficticia SRL", creada_en: "2026-10-25T15:00:00Z" };
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}/proveedores/${P1}`]: () =>
          Response.json({ proveedor: proveedor({ version: 2 }), versiones: [vigente, anterior], ...SIN_DOCUMENTACION }),
      }),
    );
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    expect(await screen.findByText("Versiones anteriores")).toBeTruthy();
    // Solo la fecha y lo que era distinto, sin el número de versión (PRV-03).
    expect(screen.getByText(/: Limpieza Vieja SRL$/)).toBeTruthy();
    expect(screen.queryByText(/Versión [0-9]/)).toBeNull();
  });

  it("la lista y la ficha muestran la cuenta enmascarada y nunca la completa", async () => {
    vi.stubGlobal("fetch", conLista());
    const { container } = abrir("/egresos/proveedores");
    const lista = await screen.findByRole("list", { name: "Proveedores" });
    expect(within(lista).getByText(/· Cuenta …1234$/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    await screen.findByLabelText(/^Cuenta destino/);
    // Control: el número completo no aparece en ningún lugar del documento, ni en un campo.
    expect(document.body.innerHTML).not.toContain(CUENTA);
    const cuenta = screen.getByLabelText(/^Cuenta destino/) as HTMLInputElement;
    expect(cuenta.value).toBe("");
    // La cuenta que tiene va dentro del campo (comentario 27), y una sola vez en el formulario.
    expect(cuenta.placeholder).toBe("…1234");
    expect(screen.getByRole("dialog", { name: /^Cambiar/ }).textContent).not.toContain("…1234");
    await sinFaltasDeAccesibilidad(container);
  });

  it("con varios edificios se elige cuál y se leen sus proveedores", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () =>
        Response.json([
          { id: C1, nombre: "Edificio Ficticio", vinculos: [] },
          { id: C2, nombre: "Otro Edificio Ficticio", vinculos: [] },
        ]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }),
      [`GET /api/v1/consorcios/${C2}/proveedores`]: () =>
        Response.json({ elementos: [proveedor({ razon_social: "Pintura Ficticia SA" })] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / });
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByRole("button", { name: /^Pintura Ficticia SA / })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Limpieza Ficticia SRL / })).toBeNull();
  });

  it("una respuesta tardía del edificio anterior no aparece en el nuevo", async () => {
    let soltar: (r: Response) => void = () => undefined;
    const tardia = new Promise<Response>((r) => (soltar = r));
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () =>
          Response.json([
            { id: C1, nombre: "Edificio Ficticio", vinculos: [] },
            { id: C2, nombre: "Otro Edificio Ficticio", vinculos: [] },
          ]),
        [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }),
        [`GET ${BASE}/proveedores/${P1}`]: () => tardia,
        [`GET /api/v1/consorcios/${C2}/proveedores`]: () => Response.json({ elementos: [] }),
      }),
    );
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByText("El edificio no tiene proveedores cargados.")).toBeTruthy();
    soltar(Response.json({ proveedor: proveedor(), versiones: [], ...SIN_DOCUMENTACION }));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: "Cambiar los datos" })).toBeNull();
    expect(screen.queryByText("Limpieza Ficticia SRL")).toBeNull();
  });

  it("cambiar de edificio descarta el pedido de código pendiente del anterior", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () =>
          Response.json([
            { id: C1, nombre: "Edificio Ficticio", vinculos: [] },
            { id: C2, nombre: "Otro Edificio Ficticio", vinculos: [] },
          ]),
        [`GET ${BASE}/proveedores`]: () => error("segundo_factor_requerido", 403),
        [`GET /api/v1/consorcios/${C2}/proveedores`]: () =>
          Response.json({ elementos: [proveedor({ razon_social: "Pintura Ficticia SA" })] }),
      }),
    );
    abrir("/egresos/proveedores");
    expect(await screen.findByLabelText("Código")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByRole("button", { name: /^Pintura Ficticia SA / })).toBeTruthy();
    expect(screen.queryByLabelText("Código")).toBeNull();
  });

  it("un código ya mandado en el edificio anterior no repite el alta después de cambiar", async () => {
    let soltar: (r: Response) => void = () => undefined;
    const verificacion = new Promise<Response>((r) => (soltar = r));
    const simulado = backend({
      "GET /api/v1/me/consorcios": () =>
        Response.json([
          { id: C1, nombre: "Edificio Ficticio", vinculos: [] },
          { id: C2, nombre: "Otro Edificio Ficticio", vinculos: [] },
        ]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => error("segundo_factor_requerido", 403),
      [`GET /api/v1/consorcios/${C2}/proveedores`]: () => Response.json({ elementos: [] }),
      "POST /api/v1/auth/totp/verificar": () => verificacion,
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    await verificarCodigo();
    expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByText("El edificio no tiene proveedores cargados.")).toBeTruthy();
    soltar(VERIFICADO());
    await new Promise((r) => setTimeout(r, 50));
    // El alta pendiente del primer edificio no se repite al llegar la verificación.
    expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(1);
  });

  it("un CUIT con el verificador mal se marca en el campo y el alta no sale; corregido, sale", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => Response.json({ id: P1, version: 1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000008" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    expect(screen.getByRole("alert").textContent).toBe(CUIT_INVALIDO);
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(0);
    // Control: con el verificador bien, el mismo formulario sí sale.
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    expect(screen.queryByText(CUIT_INVALIDO)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(1));
  });

  it("P-25: «Cancelar» se lleva el error del formulario (tanda 1, 12-11-2026)", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => error("validacion", 422),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    // Control: el error se ve mientras el formulario está abierto.
    expect(await screen.findByText("Hay que revisar los datos del formulario.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("button", { name: "Dar de alta el proveedor" })).toBeNull();
    expect(screen.queryByText("Hay que revisar los datos del formulario.")).toBeNull();
  });

  it("P-26: la cuenta con el dígito verificador mal se marca al escribirla y no se manda", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => Response.json({ id: P1, version: 1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    const cuenta = screen.getByLabelText(/^Cuenta destino/);
    fireEvent.change(cuenta, { target: { value: CUENTA_MALA } });
    expect(cuenta.getAttribute("aria-invalid")).toBe("true");
    const mensaje = document.getElementById(cuenta.getAttribute("aria-describedby") ?? "");
    expect(mensaje?.textContent).toBe(CUENTA_INVALIDA);
    // El mensaje nunca repite la cuenta escrita.
    expect(mensaje?.textContent).not.toContain(CUENTA_MALA.slice(-4));
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(0);
    // Control: con los verificadores bien, la marca se va y el alta sale.
    fireEvent.change(cuenta, { target: { value: CUENTA } });
    expect(cuenta.getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText(CUENTA_INVALIDA)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(1));
  });

  it("P-26: el 422 marca el campo que nombra, con su mensaje al lado, y se va al corregirlo", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () =>
        error("peticion_invalida", 422, {
          campos: ["body.cuenta_destino"],
          mensajes: { "body.cuenta_destino": "La cuenta no es un CBU o CVU válido." },
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    const cuenta = screen.getByLabelText(/^Cuenta destino/);
    fireEvent.change(cuenta, { target: { value: CUENTA } });
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    expect(await screen.findByText("Hay que corregir la cuenta destino: el motivo está junto a cada campo.")).toBeTruthy();
    expect(screen.queryByText("Hay que revisar los datos del formulario.")).toBeNull();
    expect(cuenta.getAttribute("aria-invalid")).toBe("true");
    expect(document.getElementById(cuenta.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "La cuenta no es un CBU o CVU válido.",
    );
    // El resumen, que toma el foco, nombra el campo; el campo dice qué hacer.
    // Control: los otros campos no quedan marcados.
    expect(screen.getByLabelText("Razón social").getAttribute("aria-invalid")).toBeNull();
    // Al corregir la cuenta, su marca se va.
    fireEvent.change(cuenta, { target: { value: CUENTA_NUEVA } });
    expect(cuenta.getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText("La cuenta no es un CBU o CVU válido.")).toBeNull();
  });

  it("P-26: el CUIT repetido se dice junto al CUIT", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => error("cuit_repetido", 409, { campos: ["body.cuit"] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    const cuit = screen.getByLabelText(/^CUIT/);
    fireEvent.change(cuit, { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    fireEvent.click(screen.getByRole("button", { name: "Dar de alta el proveedor" }));
    expect(await screen.findByText("Hay que corregir el CUIT: el motivo está junto a cada campo.")).toBeTruthy();
    expect(cuit.getAttribute("aria-invalid")).toBe("true");
    expect(document.getElementById(cuit.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "Ya hay un proveedor con ese CUIT en este edificio.",
    );
    // El formulario sigue abierto para corregirlo; «Cancelar» se lleva la marca y el aviso.
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByText(/Hay que corregir el CUIT/)).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    expect(screen.getByLabelText(/^CUIT/).getAttribute("aria-invalid")).not.toBe("true");
  });

  it("cambiar los datos conserva el documento de respaldo del proveedor", async () => {
    const conDocumento = proveedor({ documento_id: DOC });
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [conDocumento] }),
      [`GET ${BASE}/proveedores/${P1}`]: () => Response.json({ proveedor: conDocumento, versiones: [], ...SIN_DOCUMENTACION }),
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText("Rubro (opcional)"), { target: { value: "limpieza integral" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    expect(await screen.findByText("Listo: los cambios de Limpieza Ficticia SRL quedaron guardados.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`));
    expect(await enviado.json()).toMatchObject({ documento_id: DOC });
  });

  it("cambiar la cuenta muestra el aviso antes de mandar y crea una versión nueva", async () => {
    const simulado = conLista({
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText(/^Cuenta destino/), { target: { value: CUENTA_NUEVA } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    // Una pregunta corta, con el color de lo pendiente y no de un error (PRV-05).
    const pregunta = await screen.findByText((_, el) => el?.classList.contains("pregunta-cuenta") === true);
    expect(pregunta.textContent).toContain(AVISO_CUENTA);
    expect(pregunta.className).toContain("aviso-pendiente");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sí, cambiar la cuenta" }));
    expect(await screen.findByText("Listo: los cambios de Limpieza Ficticia SRL quedaron guardados.")).toBeTruthy();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`));
    expect(await enviado.json()).toMatchObject({ cuenta_destino: CUENTA_NUEVA, version_esperada: 1, conservar_cuenta: false });
    // Terminado, la ventana y la ficha se cierran: queda la lista con el resultado.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("PRV-05: «No» a la pregunta deja la cuenta que tiene y no manda nada", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    const cuenta = (await screen.findByLabelText(/^Cuenta destino/)) as HTMLInputElement;
    fireEvent.change(cuenta, { target: { value: CUENTA_NUEVA } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    fireEvent.click(await screen.findByRole("button", { name: NO_CAMBIAR_CUENTA }));
    expect(cuenta.value).toBe("");
    expect(screen.queryByRole("button", { name: NO_CAMBIAR_CUENTA })).toBeNull();
    expect(screen.getByRole("button", { name: "Guardar los cambios" })).toBeTruthy();
    expect(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`)).toHaveLength(0);
  });

  it("C8P-07: guardar sin cambiar nada no le pregunta al servidor y dice que no hay cambios", async () => {
    const simulado = conLista({
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    const guardar = await screen.findByRole("button", { name: "Guardar los cambios" });
    expect((guardar as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(guardar);
    expect((await screen.findByRole("alert")).textContent).toBe(SIN_CAMBIOS_PARA_GUARDAR);
    expect(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`)).toHaveLength(0);
    // Control: con un cambio, sale.
    fireEvent.change(screen.getByLabelText("Rubro (opcional)"), { target: { value: "otro rubro" } });
    fireEvent.click(guardar);
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`)).toHaveLength(1));
  });

  it("decisión 4: el alta sin CUIT ni razón social marca los dos campos y no manda nada", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [] }),
      [`POST ${BASE}/proveedores`]: () => Response.json({ id: P1, version: 1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    const ventana = await screen.findByRole("dialog", { name: "Alta de un proveedor" });
    fireEvent.click(within(ventana).getByRole("button", { name: "Dar de alta el proveedor" }));
    expect(await within(ventana).findByText(FALTA_CUIT)).toBeTruthy();
    expect(within(ventana).getByText(FALTA_RAZON)).toBeTruthy();
    expect(within(ventana).getByLabelText("Razón social").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(simulado, "POST", `${BASE}/proveedores`)).toHaveLength(0);
    // Al escribir, la marca de ese campo se va.
    fireEvent.change(within(ventana).getByLabelText("Razón social"), { target: { value: "Plomería Ficticia SRL" } });
    expect(within(ventana).queryByText(FALTA_RAZON)).toBeNull();
    expect(within(ventana).getByText(FALTA_CUIT)).toBeTruthy();
  });

  it("MEN-05: «Quién ve los proveedores» se abre desde la pantalla de gestión", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir("/egresos/proveedores");
    const enlace = await screen.findByRole("link", { name: "Quién ve los proveedores" });
    expect(enlace.getAttribute("href")).toBe("/proveedores/quien-los-ve");
  });

  it("con el campo de cuenta vacío se conserva la cuenta y no hay aviso", async () => {
    const simulado = conLista({
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => Response.json({ id: P1, version: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText("Rubro (opcional)"), { target: { value: "limpieza y jardín" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    expect(await screen.findByText("Listo: los cambios de Limpieza Ficticia SRL quedaron guardados.")).toBeTruthy();
    expect(screen.queryByText(AVISO_CUENTA)).toBeNull();
    const enviado = unico(pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`));
    // La pantalla no tiene la cuenta completa: pide conservarla, sin mandar ninguna.
    expect(await enviado.json()).toMatchObject({ cuenta_destino: null, conservar_cuenta: true, rubro: "limpieza y jardín" });
  });

  it("si el proveedor cambió mientras se editaba, queda lo escrito y la cuenta se vuelve a confirmar", async () => {
    let cambio = false;
    const simulado = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        Response.json({ proveedor: cambio ? proveedor({ version: 2, rubro: "limpieza integral" }) : proveedor(), versiones: [], ...SIN_DOCUMENTACION }),
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => {
        if (cambio) return Response.json({ id: P1, version: 3 });
        cambio = true;
        return error("version_desactualizada", 409);
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText(/^Cuenta destino/), { target: { value: CUENTA_NUEVA } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, cambiar la cuenta" }));
    expect(await screen.findByText(AVISO_PROVEEDOR_CAMBIO)).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("Rubro (opcional)") as HTMLInputElement).value).toBe("limpieza integral"));
    expect((screen.getByLabelText(/^Cuenta destino/) as HTMLInputElement).value).toBe(CUENTA_NUEVA);
    expect(screen.queryByRole("button", { name: "Sí, cambiar la cuenta" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Guardar los cambios" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, cambiar la cuenta" }));
    expect(await screen.findByText("Listo: los cambios de Limpieza Ficticia SRL quedaron guardados.")).toBeTruthy();
    const intentos = pedidos(simulado, "POST", `${BASE}/proveedores/${P1}/versiones`);
    expect(intentos).toHaveLength(2);
    expect(await (intentos[1] as Request).json()).toMatchObject({
      version_esperada: 2,
      cuenta_destino: CUENTA_NUEVA,
      rubro: "limpieza integral",
    });
  });

  it("control: si el proveedor ya no está, el formulario se cierra y vuelve la lista", async () => {
    let cambio = false;
    const simulado = conLista({
      [`GET ${BASE}/proveedores/${P1}`]: () =>
        cambio ? error("no_encontrado", 404) : Response.json({ proveedor: proveedor(), versiones: [], ...SIN_DOCUMENTACION }),
      [`POST ${BASE}/proveedores/${P1}/versiones`]: () => {
        cambio = true;
        return error("version_desactualizada", 409);
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: /^Limpieza Ficticia SRL / }));
    fireEvent.click(await screen.findByRole("button", { name: "Cambiar los datos" }));
    fireEvent.change(await screen.findByLabelText("Rubro (opcional)"), { target: { value: "otro" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar los cambios" }));
    await waitFor(() => expect(screen.queryByLabelText("Rubro (opcional)")).toBeNull());
    expect(await screen.findByRole("list", { name: "Proveedores" })).toBeTruthy();
  });
});

describe("fricción: el resultado y el error en el lugar justo", () => {
  const PROVEEDORES_LISTA = { [`GET ${BASE}/proveedores`]: () => Response.json({ elementos: [proveedor()] }) };

  it.each([
    ["/egresos/proveedores", `GET ${BASE}/proveedores`, PROVEEDORES_LISTA, "Dar de alta un proveedor"],
  ] as [string, string, Respuestas, string][])("%s: si la lectura falla, ofrece Reintentar y vuelve a pedir", async (ruta, clave, sanas, boton) => {
    let caido = true;
    const sana = sanas[clave] as () => Response;
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      ...sanas,
      [clave]: () => (caido ? error("error_interno", 500) : sana()),
    }));
    abrir(ruta);
    const reintentar = await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.getByRole("alert").textContent).toContain("El servicio no está disponible");
    caido = false;
    fireEvent.click(reintentar);
    expect(await screen.findByRole("button", { name: boton })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("una lectura fallida se repite sola al volver la red, y lo dice", async () => {
    let caido = true;
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [proveedor()] })),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    await screen.findByRole("button", { name: "Reintentar" });
    caido = false;
    volvioLaRed();
    expect(await screen.findByRole("button", { name: "Dar de alta un proveedor" })).toBeTruthy();
    expect(screen.getByText(VOLVIO_LA_RED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("control: si sigue caída no dice que actualizó", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => error("error_interno", 500),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/egresos/proveedores");
    await screen.findByRole("button", { name: "Reintentar" });
    const antes = pedidos(simulado, "GET", `${BASE}/proveedores`).length;
    volvioLaRed();
    await waitFor(() => expect(pedidos(simulado, "GET", `${BASE}/proveedores`).length).toBeGreaterThan(antes));
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.queryByText(VOLVIO_LA_RED)).toBeNull();
  });

  it("si no llegaron los edificios, se repiten solos al volver la red", async () => {
    let caido = true;
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": () => (caido ? error("error_interno", 500) : Response.json([])),
    }));
    abrir("/egresos/proveedores");
    await screen.findByRole("button", { name: "Reintentar" });
    caido = false;
    volvioLaRed();
    expect(await screen.findByText("Esta cuenta no tiene edificios en Consorcio en Mano.")).toBeTruthy();
  });

  it("un 403 al leer lleva al inicio: volver a pedir no lo arregla", async () => {
    vi.stubGlobal("fetch", backend({ ...INICIO, [`GET ${BASE}/proveedores`]: () => error("sin_permiso", 403) }));
    abrir("/egresos/proveedores");
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    expect(screen.queryByText(/no tiene la facultad/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it.each([
    "/egresos/proveedores", "/pagos", "/pagos/nueva", "/pagos/consulta", "/liquidacion",
  ])("%s: si no llegan los edificios, Reintentar vuelve a pedirlos", async (ruta) => {
    let caido = true;
    const api = backend({
      "GET /api/v1/me/consorcios": () => (caido ? error("error_interno", 500) : Response.json([])),
    });
    vi.stubGlobal("fetch", api);
    abrir(ruta);
    const boton = await screen.findByRole("button", { name: "Reintentar" });
    // Una vez la pantalla y otra permisos.tsx, aun con StrictMode.
    expect(pedidos(api, "GET", "/api/v1/me/consorcios")).toHaveLength(2);
    caido = false;
    fireEvent.click(boton);
    expect(await screen.findByText("Esta cuenta no tiene edificios en Consorcio en Mano.")).toBeTruthy();
    expect(pedidos(api, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("si falla la recarga después de un alta, el «Listo» queda y se puede reintentar", async () => {
    let caido = false;
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => (caido ? error("error_interno", 500) : Response.json({ elementos: [] })),
      [`POST ${BASE}/proveedores`]: () => {
        caido = true;
        return Response.json(proveedor(), { status: 201 });
      },
    }));
    abrir("/egresos/proveedores");
    fireEvent.click(await screen.findByRole("button", { name: "Dar de alta un proveedor" }));
    fireEvent.change(screen.getByLabelText(/^CUIT/), { target: { value: "30000000007" } });
    fireEvent.change(screen.getByLabelText(/Razón social/), { target: { value: "Nuevo SRL" } });
    fireEvent.submit(screen.getByLabelText(/Razón social/).closest("form") as HTMLFormElement);
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.getByText("Listo: el proveedor quedó dado de alta.")).toBeTruthy();
    expect(screen.queryByText(/El servicio no está disponible/)).toBeNull();
  });

  it("la sesión que ya no sirve no lleva a otra pantalla ni deja un aviso viejo: la pide la hoja", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/proveedores`]: () => error("sesion_reciente_requerida", 403),
    });
    vi.stubGlobal("fetch", api);
    abrir("/egresos/proveedores");
    await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.getByRole("heading", { name: "Proveedores" })).toBeTruthy();
    expect(screen.queryByText(TEXTO_SESION_VENCIDA)).toBeNull();
  });
});

// RF50 configurable (app 0104): la consulta de proveedores para el Consejo, solo lectura.
describe("página /proveedores (consulta)", () => {
  const CONSULTA = `${BASE}/consulta-proveedores`;
  const MIS_ACCIONES = `GET ${BASE}/mis-acciones`;
  const enConsulta = {
    id: P1,
    cuit: "20000000001",
    razon_social: "Ascensores Ficticios SA",
    rubro: "ascensores",
    contacto: null,
    telefono: "SINTETICO",
    whatsapp: null,
    correo: null,
    domicilio: null,
    atiende_emergencias: false,
    estado: "activo" as const,
  };
  const configuracion = { dias_por_vencer: 30, modo_exigencia: "bloquear" as const, admite_no_vence: true };
  const fichaConsulta = (salteos: components["schemas"]["SalteoDocumentacion"][] = []) =>
    Response.json({
      proveedor: enConsulta,
      documentacion: [
        {
          id: O1, proveedor_id: P1, tipo: "seguro", descripcion: "Poliza SINTETICA", vence_el: "2026-11-01", no_vence: false,
          requisito_codigo: "ascensores.seguro_rc", documento_id: DOC, version_documento_id: DOC, estado: "vencida",
          cargada_por_nombre: "Persona Ficticia", cargada_en: "2026-10-20T15:00:00Z", reemplaza_a: null, documento_vigente: true,
        },
      ],
      rubros_legales: ["ascensores"],
      faltantes: [{ proveedor_id: P1, codigo: "ascensores.seguro_rc", rubro_legal: "ascensores", tipo: "seguro", descripcion: "RC SINTETICA" }],
      requisitos: [{ codigo: "ascensores.seguro_rc", rubro_legal: "ascensores", tipo: "seguro", descripcion: "RC SINTETICA",
        fuente: "Fuente SINTETICA", verificado: true, exigido: true }],
      configuracion,
      salteos,
    });
  const consulta = (extra: Respuestas = {}) =>
    backend({
      ...INICIO,
      [MIS_ACCIONES]: alcanza("proveedores.ver"),
      [`GET ${CONSULTA}`]: () =>
        Response.json({
          elementos: [enConsulta],
          documentacion_vencida: [P1],
          documentacion_por_vencer: [],
          documentacion_faltante: [P1],
          modo_exigencia: "bloquear",
        }),
      [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta(),
      ...extra,
    });

  it("lista sin la cuenta, con las marcas, y la ficha solo se lee", async () => {
    vi.stubGlobal("fetch", consulta());
    const { container } = abrir(RUTA_CONSULTA_PROVEEDORES);
    const fila = await screen.findByRole("button", { name: /^Ascensores Ficticios SA / });
    expect(fila.textContent).toContain(MARCA_VENCIDA);
    expect(fila.textContent).toContain(MARCA_FALTANTE);
    expect(fila.textContent).not.toContain("Cuenta");
    // «Activo» es lo esperado: no se dice (CPR-02).
    expect(fila.textContent).not.toContain("Activo");
    fireEvent.click(fila);
    const ficha = await screen.findByRole("dialog", { name: "Ascensores Ficticios SA" });
    expect(within(ficha).getByText("Vencido el 01/11/2026")).toBeTruthy();
    expect(within(ficha).getByText("Cubre: RC SINTETICA")).toBeTruthy();
    expect(within(ficha).getByText(AVISO_FALTA_BLOQUEA)).toBeTruthy();
    // Solo lo que tiene valor: sin «Estado: Activo» ni datos vacíos (CPR-02).
    expect(within(ficha).queryByText("Estado")).toBeNull();
    expect(within(ficha).queryByText("Correo")).toBeNull();
    expect(within(ficha).getByText("SINTETICO")).toBeTruthy(); // control: el teléfono, que sí está
    // Solo lee: ni cargar, ni reemplazar, ni cambiar los rubros.
    for (const nombre of [/Agregar/, /Reemplazar/, /Quitar/, /Cambiar los datos/])
      expect(within(ficha).queryByRole("button", { name: nombre })).toBeNull();
    fireEvent.click(within(ficha).getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(document.activeElement?.textContent).toMatch(/^Ascensores Ficticios SA /));
    await sinFaltasDeAccesibilidad(container);
  });

  it("CPR-02: sin CUIT ni rubro, quien consulta no ve «Sin CUIT» ni «Sin rubro»; un estado que no es activo sí se dice", async () => {
    const suspendido = { ...enConsulta, cuit: null, rubro: null, estado: "suspendido" as const };
    vi.stubGlobal("fetch", consulta({
      [`GET ${CONSULTA}`]: () => Response.json({ elementos: [suspendido], documentacion_vencida: [], documentacion_por_vencer: [],
        documentacion_faltante: [], modo_exigencia: "bloquear" }),  // prettier-ignore
      [`GET ${CONSULTA}/${P1}`]: () => Response.json({ proveedor: suspendido, documentacion: [], rubros_legales: [], faltantes: [],
        requisitos: [], configuracion, salteos: [] }),  // prettier-ignore
    }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    const fila = await screen.findByRole("button", { name: /^Ascensores Ficticios SA / });
    expect(fila.textContent).toContain("Suspendido");
    fireEvent.click(fila);
    const ficha = await screen.findByRole("dialog", { name: "Ascensores Ficticios SA" });
    expect(within(ficha).getByText("Suspendido")).toBeTruthy();
    expect(within(ficha).queryByText(/Sin CUIT|Sin rubro/)).toBeNull();
    // Sin nada cargado ni nada que hacer: una línea para los seguros y ninguna sección vacía (SEG-03).
    expect(within(ficha).getByText(SIN_DOCUMENTACION_TEXTO)).toBeTruthy();
    expect(within(ficha).queryByRole("heading", { name: TITULO_EXIGENCIA })).toBeNull();
    expect(within(ficha).queryByRole("heading", { name: TITULO_SECCION })).toBeNull();
  });

  it("MEN-05: una sola entrada «Proveedores», que lleva a la consulta a quien solo consulta y a la gestión a quien gestiona", async () => {
    vi.stubGlobal("fetch", consulta());
    abrir(RUTA_CONSULTA_PROVEEDORES);
    const menu = await screen.findByRole("navigation", { name: "Secciones" });
    const entrada = await within(menu).findByRole("link", { name: "Proveedores" });
    expect(entrada.getAttribute("href")).toBe(RUTA_CONSULTA_PROVEEDORES);
    expect(entrada.getAttribute("aria-current")).toBe("page");
    expect(within(menu).queryByRole("link", { name: TITULO_CONSULTA_PROVEEDORES })).toBeNull();
    // Sin la facultad de autorizar, la pantalla no ofrece «Quién ve los proveedores».
    await screen.findByRole("button", { name: /^Ascensores Ficticios SA / });
    expect(screen.queryByRole("link", { name: "Quién ve los proveedores" })).toBeNull();
    cleanup();
    // Control: con todas las acciones, la misma entrada lleva a la gestión.
    vi.stubGlobal("fetch", consulta({ [MIS_ACCIONES]: alcanzaTodo(MIS_ACCIONES) ?? (() => Response.json({})) }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    const otro = await screen.findByRole("navigation", { name: "Secciones" });
    expect((await within(otro).findByRole("link", { name: "Proveedores" })).getAttribute("href")).toBe("/egresos/proveedores");
    expect(within(otro).getAllByRole("link", { name: /proveedores/i })).toHaveLength(1);
  });

  it("MEN-05: quien autoriza abre «Quién ve los proveedores» desde la consulta", async () => {
    vi.stubGlobal("fetch", consulta({ [MIS_ACCIONES]: alcanza("proveedores.ver", "proveedores.autorizar") }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    const enlace = await screen.findByRole("link", { name: "Quién ve los proveedores" });
    expect(enlace.getAttribute("href")).toBe(RUTA_AUTORIZACIONES_PROVEEDORES);
  });

  // Salteo por urgencia (app 0106; propietario, 16-11-2026: «si con motivo»).
  const SALTEO = {
    numero: 7,
    proveedor_id: P1,
    motivo: "SINTETICO: pérdida de gas",
    registrado_en: "2026-11-16T15:00:00Z",
    registrado_por_nombre: "Consejera Ficticia",
    vence_en: "2026-11-17T20:52:00Z",
    estado: "vigente" as const,
    orden_id: null,
    version: null,
    usado_en: null,
    propio: false,
  };
  const PROPIO = { ...SALTEO, propio: true };
  const SALTEOS = `${BASE}/proveedores/${P1}/salteos-documentacion`;

  it("en una urgencia, el Consejo saltea el bloqueo con el motivo, en su ventana, para una orden (SEG-04, SEG-06, C8P-22)", async () => {
    let registrado = false;
    const api = consulta({
      [MIS_ACCIONES]: alcanza("proveedores.ver", "egresos.saltear_documentacion"),
      [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta(registrado ? [PROPIO] : []),
      [`POST ${SALTEOS}`]: () => {
        registrado = true;
        return Response.json(PROPIO, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    expect(await screen.findByRole("heading", { name: TITULO_SALTEO })).toBeTruthy();
    // La explicación no está fija en la ficha: va en la ventana (SEG-06).
    expect(screen.queryByText(EXPLICACION_SALTEO)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: SALTEAR }));
    const ventana = await screen.findByRole("dialog", { name: SALTEAR });
    expect(within(ventana).getByText(EXPLICACION_SALTEO)).toBeTruthy();
    const confirmar = within(ventana).getByRole("button", { name: CONFIRMAR_SALTEO });
    // Siempre activo: sin motivo, lo marca y no manda nada (C8P-22).
    expect((confirmar as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirmar);
    expect(await within(ventana).findByText(FALTA_MOTIVO_SALTEO)).toBeTruthy();
    expect(pedidos(api, "POST", SALTEOS)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText(MOTIVO_SALTEO), { target: { value: "  SINTETICO: pérdida de gas  " } });
    fireEvent.click(confirmar);
    // El resultado queda en la sección, con la ventana cerrada.
    expect(await screen.findByText(SALTEO_REGISTRADO)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: SALTEAR })).toBeNull();
    const pedido = unico(pedidos(api, "POST", SALTEOS));
    expect(await pedido.json()).toEqual({ motivo: "SINTETICO: pérdida de gas" });
    expect(pedido.headers.get("Idempotency-Key")).toBeTruthy();
    // Comentario 40: el suyo espera su orden, así que no se le ofrece registrar otro.
    expect(await screen.findByRole("list", { name: "Salteos" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: SALTEAR })).toBeNull();
    await sinFaltasDeAccesibilidad(container);
  });

  it("C8P-03: al volver a abrir la ficha, un salteo propio que espera su orden no ofrece registrar otro (lo dice la API)", async () => {
    vi.stubGlobal("fetch", consulta({
      [MIS_ACCIONES]: alcanza("proveedores.ver", "egresos.saltear_documentacion"),
      [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta([PROPIO]),
    }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    expect(await screen.findByRole("list", { name: "Salteos" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: SALTEAR })).toBeNull();
    cleanup();
    // Control: el mismo salteo, ya usado, no impide registrar otro.
    vi.stubGlobal("fetch", consulta({
      [MIS_ACCIONES]: alcanza("proveedores.ver", "egresos.saltear_documentacion"),
      [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta([{ ...PROPIO, estado: "usado", orden_id: O1, version: 1, usado_en: "2026-11-16T16:00:00Z" }]),
    }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    expect(await screen.findByRole("button", { name: SALTEAR })).toBeTruthy();
  });

  it("comentario 40: el salteo de otra persona que espera su orden no esconde el botón; la fecha lleva un solo punto", async () => {
    const deOtro = { ...SALTEO, numero: 8, registrado_por_nombre: "Otro Consejero Ficticio" };
    vi.stubGlobal("fetch", consulta({
      [MIS_ACCIONES]: alcanza("proveedores.ver", "egresos.saltear_documentacion"),
      [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta([deOtro]),
    }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    // La base deja a cada miembro un salteo propio en espera: el de otro no le impide registrar el suyo.
    expect(await screen.findByRole("button", { name: SALTEAR })).toBeTruthy();
    const renglon = (await screen.findByRole("list", { name: "Salteos" })).textContent ?? "";
    expect(renglon).toMatch(/^Espera su orden hasta el .*\. Registrado el /);
    expect(renglon).not.toMatch(/\.\./);
  });

  it("sin la acción no se ofrece saltear, y la ficha muestra los salteos que hubo, plegados (SEG-07)", async () => {
    const usado = { ...SALTEO, estado: "usado" as const, orden_id: O1, version: 1, usado_en: "2026-11-16T16:00:00Z" };
    vi.stubGlobal("fetch", consulta());
    abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    expect(await screen.findByText(AVISO_FALTA_BLOQUEA)).toBeTruthy();
    expect(screen.queryByRole("button", { name: SALTEAR })).toBeNull();
    expect(screen.queryByRole("heading", { name: TITULO_SALTEO })).toBeNull();
    cleanup();
    // Control: con un salteo ya usado, se ve aunque no se pueda saltear: en «Anteriores», sin la versión.
    vi.stubGlobal("fetch", consulta({ [`GET ${CONSULTA}/${P1}`]: () => fichaConsulta([usado]) }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    fireEvent.click(await screen.findByRole("button", { name: /^Ascensores Ficticios SA / }));
    // El momento lleva espacios finos (Intl): se compara el texto entero de la fila.
    const anteriores = await screen.findByRole("list", { name: "Salteos anteriores" });
    expect(anteriores.closest("details")?.open).toBe(false);
    expect(anteriores.textContent).toBe(textoSalteo(usado));
    expect(textoSalteo(usado)).toMatch(/^Usado por una orden\. Registrado el .*Consejera Ficticia: SINTETICO: pérdida de gas$/);
    expect(screen.queryByRole("list", { name: "Salteos" })).toBeNull();
    expect(screen.queryByRole("button", { name: SALTEAR })).toBeNull();
    expect(textoSalteoDeLaVersion(usado)).toContain("SINTETICO: pérdida de gas");
  });

  it("sin proveedores.ver la dirección lleva al inicio", async () => {
    vi.stubGlobal("fetch", consulta({ [MIS_ACCIONES]: alcanza("contactos.ver") }));
    abrir(RUTA_CONSULTA_PROVEEDORES);
    await waitFor(() => expect(screen.queryByRole("heading", { name: TITULO_CONSULTA_PROVEEDORES })).toBeNull());
    expect(await screen.findByRole("link", { name: /Contactos de emergencia/ })).toBeTruthy();
  });
});

// Quién ve los proveedores (app 0105): el Consejo autoriza a un habitante, con el motivo.
describe("página /proveedores/quien-los-ve", () => {
  const LISTA = `${BASE}/autorizaciones-proveedores`;
  const MIS_ACCIONES = `GET ${BASE}/mis-acciones`;
  const sinAutorizar = {
    persona_id: PERSONA,
    nombre: "Habitante Ficticio",
    unidades: ["3° A"],
    autorizada: false,
    motivo: null,
    registrado_en: null,
    registrado_por_nombre: null,
  };
  const autorizado = {
    ...sinAutorizar,
    autorizada: true,
    motivo: "SINTETICO: comisión de obras",
    registrado_en: "2026-11-15T15:00:00Z",
    registrado_por_nombre: "Consejera Ficticia",
  };
  const pantalla = (extra: Respuestas = {}) =>
    backend({
      ...INICIO,
      [MIS_ACCIONES]: alcanza("proveedores.autorizar"),
      [`GET ${LISTA}`]: () => Response.json({ elementos: [sinAutorizar] }),
      [`POST ${LISTA}/${PERSONA}`]: () => Response.json(autorizado),
      ...extra,
    });

  /** Abre la ficha del habitante y la ventana de su acción. */
  async function abrirVentana(boton: string) {
    fireEvent.click(await screen.findByRole("button", { name: /^Habitante Ficticio / }));
    const ficha = await screen.findByRole("dialog", { name: "Habitante Ficticio" });
    fireEvent.click(within(ficha).getByRole("button", { name: boton }));
    return screen.findByRole("dialog", { name: boton === AUTORIZAR ? "Autorizar a Habitante Ficticio" : "Retirar la autorización de Habitante Ficticio" });
  }

  it("cada habitante es una fila compacta que abre su ficha (comentario 38, AUT-01), sin el párrafo de arriba (AUT-03)", async () => {
    vi.stubGlobal("fetch", pantalla({ [`GET ${LISTA}`]: () => Response.json({ elementos: [sinAutorizar, { ...autorizado, persona_id: P1, nombre: "Otra Habitante" }] }) }));
    const { container } = abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const fila = await screen.findByRole("button", { name: /^Habitante Ficticio / });
    expect(fila.className).toBe("fila-registro");
    expect(fila.textContent).toBe("Habitante Ficticio 3° A · Sin autorización");
    expect(screen.getByRole("button", { name: /^Otra Habitante / }).textContent).toBe("Otra Habitante 3° A · Autorizado");
    // Ni tarjetas ni el botón en la lista: la acción está en la ficha.
    expect(container.querySelector("article.tarjeta")).toBeNull();
    expect(screen.queryByRole("button", { name: AUTORIZAR })).toBeNull();
    expect(screen.queryByText(/El Consejo y la administración ven los proveedores/)).toBeNull();
    // La ficha de quien está autorizado dice desde cuándo, quién y por qué.
    fireEvent.click(screen.getByRole("button", { name: /^Otra Habitante / }));
    const ficha = await screen.findByRole("dialog", { name: "Otra Habitante" });
    expect(within(ficha).getByText("Consejera Ficticia")).toBeTruthy();
    expect(within(ficha).getByText("SINTETICO: comisión de obras")).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: RETIRAR }).className).toBe("secundario peligro");
    await sinFaltasDeAccesibilidad(container);
  });

  it("autoriza con el motivo en su ventana (AUT-02): botón siempre activo, y al terminar queda la lista con el resultado", async () => {
    const api = pantalla();
    vi.stubGlobal("fetch", api);
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const ventana = await abrirVentana(AUTORIZAR);
    const confirmar = within(ventana).getByRole("button", { name: CONFIRMAR_AUTORIZAR });
    // Sin motivo, lo marca y no manda nada (C8P-23).
    expect((confirmar as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirmar);
    expect(await within(ventana).findByText(FALTA_MOTIVO)).toBeTruthy();
    expect(within(ventana).getByLabelText(MOTIVO).getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(api, "POST", `${LISTA}/${PERSONA}`)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText(MOTIVO), { target: { value: "  SINTETICO: comisión de obras  " } });
    fireEvent.click(confirmar);
    expect(await screen.findByText(textoAutorizado("Habitante Ficticio"))).toBeTruthy();
    const pedido = unico(pedidos(api, "POST", `${LISTA}/${PERSONA}`));
    expect(await pedido.json()).toEqual({ autorizada: true, motivo: "SINTETICO: comisión de obras" });
    expect(pedido.headers.get("Idempotency-Key")).toBeTruthy();
    // La ventana y la ficha se cierran; la fila ya dice lo nuevo.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: /^Habitante Ficticio / }).textContent).toContain("Autorizado");
  });

  it("retirar pide el motivo, es un acto negativo y aclara lo que ve por su rol", async () => {
    const api = pantalla({
      [`GET ${LISTA}`]: () => Response.json({ elementos: [autorizado] }),
      [`POST ${LISTA}/${PERSONA}`]: () => Response.json({ ...autorizado, autorizada: false, motivo: "SINTETICO: se mudó" }),
    });
    vi.stubGlobal("fetch", api);
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const ventana = await abrirVentana(RETIRAR);
    expect(within(ventana).getByText(ACLARACION_RETIRO)).toBeTruthy();
    fireEvent.change(within(ventana).getByLabelText(MOTIVO), { target: { value: "SINTETICO: se mudó" } });
    const confirmar = within(ventana).getByRole("button", { name: CONFIRMAR_RETIRAR });
    expect(confirmar.className).toBe("peligro");
    fireEvent.click(confirmar);
    // Solo dice lo que cambió: el rol puede seguir dándole acceso (Codex en #250).
    expect(await screen.findByText(textoRetirado("Habitante Ficticio"))).toBeTruthy();
    expect(await unico(pedidos(api, "POST", `${LISTA}/${PERSONA}`)).json()).toEqual({ autorizada: false, motivo: "SINTETICO: se mudó" });
    expect(screen.queryByText(/ya no ve/)).toBeNull();
    // Control: la ficha vuelve a ofrecer autorizar, y dice que se retiró.
    fireEvent.click(screen.getByRole("button", { name: /^Habitante Ficticio / }));
    const ficha = await screen.findByRole("dialog", { name: "Habitante Ficticio" });
    expect(within(ficha).getByText("Autorización retirada")).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: AUTORIZAR })).toBeTruthy();
  });

  it("un rechazo se dice en la ventana, que queda abierta con lo escrito", async () => {
    vi.stubGlobal("fetch", pantalla({ [`POST ${LISTA}/${PERSONA}`]: () => error("motivo_invalido", 422) }));
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const ventana = await abrirVentana(AUTORIZAR);
    fireEvent.change(within(ventana).getByLabelText(MOTIVO), { target: { value: "SINTETICO" } });
    fireEvent.click(within(ventana).getByRole("button", { name: CONFIRMAR_AUTORIZAR }));
    expect((await within(ventana).findByRole("alert")).textContent).toBe(mensajeDeError(422, "motivo_invalido"));
    expect((within(ventana).getByLabelText(MOTIVO) as HTMLTextAreaElement).value).toBe("SINTETICO");
  });

  it("si otro miembro del Consejo ya la cambió, cierra la ventana y la ficha y vuelve a leer la lista (Codex en #250)", async () => {
    // Lo registrado cambió en el medio: la base ya la tiene autorizada.
    let rechazado = false;
    const api = pantalla({
      [`GET ${LISTA}`]: () => Response.json({ elementos: [rechazado ? autorizado : sinAutorizar] }),
      [`POST ${LISTA}/${PERSONA}`]: () => {
        rechazado = true;
        return error("autorizacion_sin_cambios", 409);
      },
    });
    vi.stubGlobal("fetch", api);
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const ventana = await abrirVentana(AUTORIZAR);
    fireEvent.change(within(ventana).getByLabelText(MOTIVO), { target: { value: "SINTETICO" } });
    fireEvent.click(within(ventana).getByRole("button", { name: CONFIRMAR_AUTORIZAR }));
    // La fila muestra lo registrado; el aviso queda.
    expect(await screen.findByText(mensajeDeError(409, "autorizacion_sin_cambios"))).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("button", { name: /^Habitante Ficticio / }).textContent).toContain("Autorizado"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(pedidos(api, "GET", LISTA).length).toBeGreaterThanOrEqual(2); // control: se volvió a leer después del rechazo
  });

  it("con la autorización por persona apagada lo dice una sola vez y no lista (AUT-04)", async () => {
    vi.stubGlobal("fetch", pantalla({ [`GET ${LISTA}`]: () => error("autorizacion_por_persona_apagada", 409) }));
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    expect(await screen.findByText(mensajeDeError(409, "autorizacion_por_persona_apagada"))).toBeTruthy();
    expect(screen.queryByText(/puede autorizar a un habitante/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Habitante/ })).toBeNull();
  });

  it("MEN-05: la entrada «Proveedores» la marca y lleva acá a quien solo autoriza; la dirección lleva al inicio a quien no", async () => {
    vi.stubGlobal("fetch", pantalla());
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    const menu = await screen.findByRole("navigation", { name: "Secciones" });
    const entrada = await within(menu).findByRole("link", { name: "Proveedores" });
    expect(entrada.getAttribute("href")).toBe(RUTA_AUTORIZACIONES_PROVEEDORES);
    expect(entrada.getAttribute("aria-current")).toBe("page");
    expect(within(menu).queryByRole("link", { name: TITULO_AUTORIZACIONES_PROVEEDORES })).toBeNull();
    cleanup();
    vi.stubGlobal("fetch", pantalla({ [MIS_ACCIONES]: alcanza("contactos.ver") }));
    abrir(RUTA_AUTORIZACIONES_PROVEEDORES);
    await waitFor(() => expect(screen.queryByRole("heading", { name: TITULO_AUTORIZACIONES_PROVEEDORES })).toBeNull());
    expect(await screen.findByRole("link", { name: /Contactos de emergencia/ })).toBeTruthy();
  });
});
