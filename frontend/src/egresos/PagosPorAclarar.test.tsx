import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { FALTA_CODIGO_MONEDA } from "../pagos/CampoMonedaSalida";
import { CAMBIAR_ARCHIVO } from "../SubirArchivo";
import { ContextoVersion } from "../version";
import { VOLVIO_LA_RED } from "./comun";
import { CORREGIR_LO_LEIDO, NO_SE_TRANSFIRIO, OTRA_COSA, REGISTRAR_TRANSFERENCIA, resumenDe, SI_SE_LEYO_MAL, SI_SE_TRANSFIRIO, SIN_PAGOS, textoLeido, textosDistintos } from "./PagosPorAclarar";

// Pagos para aclarar (app 0067). Todos los datos son ficticios: CUIT y CBU inventados, sin
// ningún proveedor ni cuenta real, y nada se transfiere.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const O1 = "33333333-3333-4333-8333-333333333333";
const X1 = "44444444-4444-4444-8444-444444444444";
const PAGO = "55555555-5555-4555-8555-555555555555";
const DOC = "77777777-7777-4777-8777-777777777777";
const ARCHIVO = "88888888-8888-4888-8888-888888888888";
const DECL = "99999999-9999-4999-8999-999999999999";
const CUENTA = "0000003100000000001234";
const H1 = "a".repeat(64);
const BASE = `/api/v1/consorcios/${C1}`;


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

const POR_ACLARAR = {
  consorcio_id: C1,
  consorcio: "Edificio Ficticio",
  orden_id: O1,
  version: 1,
  concepto: "Limpieza de octubre",
  razon_social: "Limpieza Ficticia SRL",
  importe_centavos: 1_500_000,
  moneda: "ARS",
  cuenta_vista_en: "2026-10-31T13:00:00Z",
  huella: H1,
  comprobante_id: null,
  comprobante_estado: null,
  lectura_comprobante: null,
};

/** Abre la ficha del pago tocando su fila. */
async function abrirFicha() {
  fireEvent.click(await screen.findByRole("button", { name: /^Edificio Ficticio: / }));
  return screen.findByRole("dialog", { name: /^Edificio Ficticio: / });
}

/** Abre la ficha y, sobre ella, la ventana de la acción. */
async function abrirAccion(boton: string, ventana: string | RegExp) {
  const ficha = await abrirFicha();
  fireEvent.click(within(ficha).getByRole("button", { name: boton }));
  return screen.findByRole("dialog", { name: ventana });
}

const transferencia = () => abrirAccion(SI_SE_TRANSFIRIO, REGISTRAR_TRANSFERENCIA);

describe("pagos para aclarar (app 0067)", () => {
  const DECLARAR = `${BASE}/pagos-por-aclarar/${O1}/no-realizado`;

  it("ACL-01 y ACL-03: cada pago es una fila que abre su ficha, sin formularios ni explicación en la lista", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [POR_ACLARAR] }) }));
    abrir("/pagos/por-aclarar");
    const lista = await screen.findByRole("list", { name: "Pagos para aclarar" });
    expect(within(lista).getAllByRole("button")).toHaveLength(1);
    expect(within(lista).getByRole("button").textContent).toMatch(/Edificio Ficticio: .*Limpieza de octubre/);
    expect(screen.queryByLabelText("Por qué no se transfirió")).toBeNull();
    expect(screen.queryByText(/Esta cuenta vio la cuenta/)).toBeNull();
    const ficha = await abrirFicha();
    expect(within(ficha).getByText("Limpieza de octubre")).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: NO_SE_TRANSFIRIO })).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: SI_SE_TRANSFIRIO })).toBeTruthy();
    await sinFaltasDeAccesibilidad(document.body);
  });

  it("FL-ACL-03 (tanda 9): la fila y la ficha dicen a qué proveedor era el pago", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [POR_ACLARAR] }) }));
    abrir("/pagos/por-aclarar");
    const lista = await screen.findByRole("list", { name: "Pagos para aclarar" });
    expect(within(lista).getByRole("button").textContent).toContain(resumenDe(POR_ACLARAR));
    expect(resumenDe(POR_ACLARAR)).toContain("Limpieza Ficticia SRL");
    const ficha = await abrirFicha();
    expect(within(ficha).getByText("Proveedor")).toBeTruthy();
    expect(within(ficha).getByText("Limpieza Ficticia SRL")).toBeTruthy();
  });

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
    const { container } = abrir("/pagos/por-aclarar");
    const ventana = await abrirAccion(NO_SE_TRANSFIRIO, "Declarar que no se transfirió");
    expect(container.textContent).not.toContain(CUENTA);
    // C8P-18: el botón está activo; sin el motivo, marca el campo y no manda nada.
    const boton = within(ventana).getByRole("button", { name: NO_SE_TRANSFIRIO }) as HTMLButtonElement;
    expect(boton.disabled).toBe(false);
    fireEvent.click(boton);
    expect(await within(ventana).findByText("Falta decir por qué no se transfirió.")).toBeTruthy();
    expect(within(ventana).getByLabelText("Por qué no se transfirió").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(api, "POST", DECLARAR)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Por qué no se transfirió"), { target: { value: "Renuncié antes de pagar" } });
    fireEvent.click(boton);
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    expect(await screen.findByText(/Ahora lo confirma otro miembro del Consejo/)).toBeTruthy();
    // ACL-06: aclarado el último, el «Listo» y la vuelta al inicio, sin la tarjeta de vacío.
    expect((await screen.findByRole("link", { name: "Volver al inicio" })).getAttribute("href")).toBe("/");
    expect(screen.queryByText(SIN_PAGOS)).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    const [uno, dos] = pedidos(api, "POST", DECLARAR);
    expect(await uno?.json()).toEqual({ version: 1, motivo: "Renuncié antes de pagar" });
    expect(dos?.headers.get("Idempotency-Key")).toBe(uno?.headers.get("Idempotency-Key"));
  });

  it("un rechazo de la declaración queda en su ventana, con lo escrito", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [POR_ACLARAR] }),
      [`POST ${DECLARAR}`]: () => error("error_interno", 500),
    }));  // prettier-ignore
    abrir("/pagos/por-aclarar");
    const ventana = await abrirAccion(NO_SE_TRANSFIRIO, "Declarar que no se transfirió");
    fireEvent.change(within(ventana).getByLabelText("Por qué no se transfirió"), { target: { value: "Renuncié" } });
    fireEvent.click(within(ventana).getByRole("button", { name: NO_SE_TRANSFIRIO }));
    expect((await within(ventana).findByRole("alert")).textContent).not.toBe("");
    expect((within(ventana).getByLabelText("Por qué no se transfirió") as HTMLInputElement).value).toBe("Renuncié");
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
    abrir("/pagos/por-aclarar");
    const hoja = await transferencia();
    expect(within(hoja).getByText("Primero hay que subir el comprobante de la transferencia.")).toBeTruthy();
    // C8P-10: sin el comprobante aceptado, ni los datos del pago ni el botón de registrar.
    expect(within(hoja).queryByRole("button", { name: "Registrar el pago" })).toBeNull();
    expect(within(hoja).queryByLabelText("Número de operación")).toBeNull();
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(within(hoja).getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
    expect(await within(hoja).findByText(/El antivirus revisa el comprobante/)).toBeTruthy();
    // DOC-16: subido, el nombre y un solo «Cambiar el archivo», sin las formas de elegir a la vista.
    expect(within(hoja).getByText("Elegido: comprobante.pdf")).toBeTruthy();
    expect(within(hoja).getByRole("button", { name: CAMBIAR_ARCHIVO })).toBeTruthy();
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"])
      expect(within(hoja).queryByRole("button", { name: nombre })).toBeNull();
    // ACL-05: la línea de estado alcanza, sin otro aviso que quede viejo.
    expect(screen.queryByText(/Listo: el comprobante se subió/)).toBeNull();
    const [carga] = pedidos(api, "POST", CARGA);
    expect(await carga?.json()).toEqual({ titulo: "Comprobante de la transferencia", fecha: new Date().toLocaleDateString("en-CA") });
    const [subida] = pedidos(api, "POST", SUBIDA);
    expect(subida?.headers.get("Content-Type")).toBe("application/pdf");
    expect(subida?.headers.get("Idempotency-Key")).toBeTruthy();
    // El antivirus lo aprueba: la pantalla lo ve sola, sin un botón, y se registra.
    expect(screen.queryByRole("button", { name: "Actualizar" })).toBeNull();
    lista = [{ ...POR_ACLARAR, comprobante_id: NUEVO, comprobante_estado: "disponible" }];
    await vi.advanceTimersByTimeAsync(ESPERA);
    const ventana = await screen.findByRole("dialog", { name: REGISTRAR_TRANSFERENCIA });
    expect(await within(ventana).findByText(/ya se puede registrar el pago/)).toBeTruthy();
    // Decisión 4: el botón está activo y marca lo que falta.
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar el pago" }));
    expect(await within(ventana).findByText("Falta escribir el número de operación.")).toBeTruthy();
    expect(within(ventana).getByText("Falta escribir el importe.")).toBeTruthy();
    expect(pedidos(api, "POST", REGISTRO)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(within(ventana).getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(within(ventana).getByLabelText(/^Importe transferido/), { target: { value: "15000" } });
    fireEvent.change(within(ventana).getByLabelText(/^CBU\/CVU de destino/), { target: { value: `${CUENTA.slice(0, 8)} ${CUENTA.slice(8)}` } });
    await sinFaltasDeAccesibilidad(document.body);
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar el pago" }));
    expect(await screen.findByText(/Listo: el pago quedó registrado/)).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Volver al inicio" })).toBeTruthy();
    const [registro] = pedidos(api, "POST", REGISTRO);
    expect(await registro?.json()).toEqual({
      version: 1,
      lectura_mal: false,
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
    abrir("/pagos/por-aclarar");
    const hoja = await transferencia();
    expect(within(hoja).getByText(/El antivirus rechazó el archivo/)).toBeTruthy();
    // DOC-16, control: rechazado, hay que subir otro: las formas de elegir a la vista, sin «Cambiar el archivo».
    expect(within(hoja).getByRole("button", { name: "Elegir un archivo" })).toBeTruthy();
    expect(within(hoja).queryByRole("button", { name: CAMBIAR_ARCHIVO })).toBeNull();
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(within(hoja).getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
    await waitFor(() => expect(pedidos(api, "POST", SUBIDA)).toHaveLength(1));
    expect(pedidos(api, "POST", `${BASE}/pagos-por-aclarar/${O1}/comprobante`)).toHaveLength(0);
  });

  it("DOC-16: con un comprobante subido antes, un solo «Cambiar el archivo» que abre las formas de elegir", async () => {
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () =>
        Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const hoja = await transferencia();
    const cambiar = within(hoja).getByRole("button", { name: CAMBIAR_ARCHIVO });
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"])
      expect(within(hoja).queryByRole("button", { name: nombre })).toBeNull();
    // La API no informa el nombre del archivo subido antes: no se inventa uno.
    expect(within(hoja).queryByText(/^Elegido:/)).toBeNull();
    fireEvent.click(cambiar);
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"])
      expect(within(hoja).getByRole("button", { name: nombre })).toBeTruthy();
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
    abrir("/pagos/por-aclarar");
    const hoja = await transferencia();
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(within(hoja).getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
    expect(await within(hoja).findByText(/El archivo no se subió/)).toBeTruthy();
    fireEvent.click(await within(hoja).findByRole("button", { name: "Reintentar la subida" }));
    await waitFor(() => expect(within(hoja).queryByRole("button", { name: "Reintentar la subida" })).toBeNull());
    expect(within(hoja).queryByText(/El archivo no se subió/)).toBeNull();
    const [primera, segunda] = pedidos(api, "POST", SUBIDA).map((q) => q.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("quien perdió la facultad y pagó otra cosa lo registra con su explicación, por su ruta", async () => {
    const DIFERENCIA = `${BASE}/pagos-por-aclarar/${O1}/diferencia`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
      [`POST ${DIFERENCIA}`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    expect(within(ventana).queryByLabelText("Qué pasó")).toBeNull();
    fireEvent.click(within(ventana).getByRole("button", { name: OTRA_COSA }));
    fireEvent.change(within(ventana).getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(within(ventana).getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(within(ventana).getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    fireEvent.change(within(ventana).getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    // ACL-03: la ayuda dice solo lo que hace falta para escribir.
    expect(within(ventana).getByText("Sin números de cuenta.")).toBeTruthy();
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar lo que pasó" }));
    expect(await within(ventana).findByText("Falta escribir qué pasó.")).toBeTruthy();
    expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(0); // sin explicación no se manda
    fireEvent.change(within(ventana).getByLabelText("Qué pasó"), { target: { value: "SINTETICO: transferí de menos" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar lo que pasó" }));
    await waitFor(() => expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(1));
    expect(await pedidos(api, "POST", DIFERENCIA)[0]?.json()).toMatchObject({ moneda: "ARS", documento_id: DOC, explicacion: "SINTETICO: transferí de menos", cuenta_real: CUENTA });
    expect(await screen.findByText(/lo resuelve el Consejo/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/pagos-por-aclarar/${O1}/pago`)).toHaveLength(0);
  });

  it("FL-ACL-02: «Se transfirió otro importe o a otra cuenta» trae la moneda de la orden, por su nombre, y se puede cambiar", async () => {
    const DIFERENCIA = `${BASE}/pagos-por-aclarar/${O1}/diferencia`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
      [`POST ${DIFERENCIA}`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    expect(within(ventana).queryByLabelText("Moneda que salió del banco")).toBeNull(); // el registro común no la pide
    fireEvent.click(within(ventana).getByRole("button", { name: OTRA_COSA }));
    const moneda = within(ventana).getByLabelText("Moneda que salió del banco") as HTMLSelectElement;
    expect(moneda.tagName).toBe("SELECT");
    expect(moneda.value).toBe("ARS");
    expect([...moneda.options].map((o) => o.text)).toEqual(["Pesos", "Dólares", "Otra"]);
    // El código se escribe solo si fue otra moneda.
    expect(within(ventana).queryByLabelText("Código de la moneda")).toBeNull();
    fireEvent.change(moneda, { target: { value: "USD" } });
    expect(moneda.value).toBe("USD");
    fireEvent.change(within(ventana).getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(within(ventana).getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(within(ventana).getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    fireEvent.change(within(ventana).getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.change(within(ventana).getByLabelText("Qué pasó"), { target: { value: "SINTETICO: salió en dólares" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar lo que pasó" }));
    await waitFor(() => expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(1));
    expect(await pedidos(api, "POST", DIFERENCIA)[0]?.json()).toMatchObject({ moneda: "USD" });
  });

  it("FL-ACL-02: con «Otra», el código se escribe aparte y, si falta, se marca junto al campo", async () => {
    const DIFERENCIA = `${BASE}/pagos-por-aclarar/${O1}/diferencia`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
      [`POST ${DIFERENCIA}`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    fireEvent.click(within(ventana).getByRole("button", { name: OTRA_COSA }));
    fireEvent.change(within(ventana).getByLabelText("Moneda que salió del banco"), { target: { value: "otra" } });
    fireEvent.change(within(ventana).getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(within(ventana).getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(within(ventana).getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    fireEvent.change(within(ventana).getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.change(within(ventana).getByLabelText("Qué pasó"), { target: { value: "SINTETICO: salió en euros" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar lo que pasó" }));
    expect(await within(ventana).findByText(FALTA_CODIGO_MONEDA)).toBeTruthy();
    expect(within(ventana).getByLabelText("Código de la moneda").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Código de la moneda"), { target: { value: "eur" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar lo que pasó" }));
    await waitFor(() => expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(1));
    expect(await pedidos(api, "POST", DIFERENCIA)[0]?.json()).toMatchObject({ moneda: "EUR" });
  });

  it("sin pagos para aclarar lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/pagos/por-aclarar");
    expect(await screen.findByText(SIN_PAGOS)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Volver al inicio" })).toBeNull();
  });
});

describe("fricción: las lecturas y el comprobante", () => {
  const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

  const LECTURAS = [
    ["/pagos/por-aclarar", "GET /api/v1/pagos-por-aclarar", SIN_PAGOS],
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
    const EN_REVISION = { ...CON_ARCHIVO, comprobante_estado: "en_cuarentena" };
    const actualizar = () => screen.queryByRole("button", { name: "Actualizar" });

    it("el comprobante en revisión se actualiza solo y recién entonces ofrece registrar", async () => {
      conEsperaManual();
      let estado = "en_cuarentena";
      vi.stubGlobal("fetch", backend({
        "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...CON_ARCHIVO, comprobante_estado: estado }] }),
      }));
      abrir("/pagos/por-aclarar");
      await transferencia();
      expect(screen.queryByRole("button", { name: "Registrar el pago" })).toBeNull();
      estado = "disponible";
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect(await screen.findByText(/ya se puede registrar el pago/)).toBeTruthy();
      expect((screen.getByRole("button", { name: "Registrar el pago" }) as HTMLButtonElement).disabled).toBe(false);
    });

    it("sin la ventana abierta no mira nada por su cuenta", async () => {
      conEsperaManual();
      const api = backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [EN_REVISION] }) });
      vi.stubGlobal("fetch", api);
      abrir("/pagos/por-aclarar");
      await abrirFicha();
      const lecturas = pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length;
      await vi.advanceTimersByTimeAsync(ESPERA * 2);
      expect(pedidos(api, "GET", "/api/v1/pagos-por-aclarar")).toHaveLength(lecturas);
    });

    it("ACL-04: si falla la actualización lo dice en la ventana y la vuelve a pedir sola, sin «Actualizar»", async () => {
      conEsperaManual();
      let caido = false;
      vi.stubGlobal("fetch", backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          caido ? error("error_interno", 500) : Response.json({ elementos: [EN_REVISION] }),
      }));
      const hoja = await (async () => {
        abrir("/pagos/por-aclarar");
        return transferencia();
      })();
      caido = true;
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect((await within(hoja).findByRole("alert")).textContent).toContain("No se pudo actualizar la lista");
      expect(actualizar()).toBeNull();
      caido = false;
      await vi.advanceTimersByTimeAsync(ESPERA);
      await waitFor(() => expect(within(hoja).queryByRole("alert")).toBeNull());
    });

    it("la lista que falló con la ventana abierta se repite sola al volver la red", async () => {
      conEsperaManual();
      let caido = false;
      const api = backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          caido ? error("error_interno", 500) : Response.json({ elementos: [EN_REVISION] }),
      });
      vi.stubGlobal("fetch", api);
      abrir("/pagos/por-aclarar");
      const hoja = await transferencia();
      caido = true;
      await vi.advanceTimersByTimeAsync(ESPERA);
      await within(hoja).findByRole("alert");
      caido = false;
      volvioLaRed();
      await waitFor(() => expect(within(hoja).queryByRole("alert")).toBeNull());
    });

    it("ACL-04: si el antivirus tarda, sigue mirando solo y no ofrece «Actualizar»", async () => {
      conEsperaManual();
      const api = backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [EN_REVISION] }) });
      vi.stubGlobal("fetch", api);
      abrir("/pagos/por-aclarar");
      await transferencia();
      await vi.advanceTimersByTimeAsync(ESPERA * 21);
      expect(actualizar()).toBeNull();
      expect(screen.queryByText(/El antivirus sigue revisando el comprobante/)).toBeNull();
      const lecturas = pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length;
      await vi.advanceTimersByTimeAsync(ESPERA * 2);
      expect(pedidos(api, "GET", "/api/v1/pagos-por-aclarar").length).toBeGreaterThan(lecturas);
    });
  });
});

describe("informe de fricción, tanda 7 (AC-11)", () => {
  it("la cabecera tiene cómo volver al inicio, como el Panel", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/pagos/por-aclarar");

    expect(await screen.findByRole("heading", { level: 1, name: "Pagos para aclarar" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Volver a Inicio" }).getAttribute("href")).toBe("/");
  });
});

describe("tanda 10 de flujos: «Sí se transfirió» con lo leído del comprobante (FL-ACL-01)", () => {
  const PAGO_RUTA = `${BASE}/pagos-por-aclarar/${O1}/pago`;
  /** El párrafo con ese texto exacto: los importes llevan espacios duros que el buscador de texto cambia. */
  const parrafo = (texto: string) => (_t: string, el: Element | null) => el?.tagName === "P" && el.textContent === texto;
  const LEIDA = {
    version_documento_id: ARCHIVO, estado: "leida", importe_centavos: 1_500_000, moneda: "ARS", cuenta_ultimos_digitos: "1234",
    cuenta_coincide: true, titular_destino: "Limpieza Ficticia SRL", numero_operacion: "0001-234567", fecha: "2026-10-31",
    distinto_de_la_orden: [], titular_distinto: false, distinto_de_lo_registrado: [], coincide_con_lo_registrado: false,
  };  // prettier-ignore
  const conLectura = (lectura: object | null) => ({ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible", lectura_comprobante: lectura });
  const servidor = (lectura: () => object | null) =>
    backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [conLectura(lectura())] }),
      [`POST ${PAGO_RUTA}`]: () => Response.json({ id: PAGO, orden_id: O1 }, { status: 201 }),
    });

  it("con lo leído que coincide, no pide el importe ni la cuenta: se confirma con un toque", async () => {
    const api = servidor(() => LEIDA);
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    expect(await within(ventana).findByText(parrafo(textoLeido(LEIDA as never, POR_ACLARAR)))).toBeTruthy();
    expect((within(ventana).getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567");
    expect((within(ventana).getByLabelText("Fecha de la transferencia") as HTMLInputElement).value).toBe("2026-10-31");
    expect(within(ventana).queryByLabelText(/^Importe transferido/)).toBeNull();
    expect(within(ventana).queryByLabelText(/^CBU\/CVU de destino/)).toBeNull();
    // La cuenta completa no está en ningún lado: solo sus últimos dígitos.
    expect(ventana.textContent).not.toContain(CUENTA);
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar el pago" }));
    await waitFor(() => expect(pedidos(api, "POST", PAGO_RUTA)).toHaveLength(1));
    const cuerpo = await pedidos(api, "POST", PAGO_RUTA)[0]?.json();
    expect(cuerpo).toMatchObject({ numero_operacion: "0001-234567", fecha_operacion: "2026-10-31", importe_centavos: 1_500_000, lectura_mal: false, documento_id: DOC });
    expect(cuerpo).not.toHaveProperty("cuenta_destino");
  });

  it("«Corregir lo que se leyó» pide también el importe y la cuenta", async () => {
    vi.stubGlobal("fetch", servidor(() => LEIDA));
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    fireEvent.click(await within(ventana).findByRole("button", { name: CORREGIR_LO_LEIDO }));
    expect((within(ventana).getByLabelText(/^Importe transferido/) as HTMLInputElement).value).toBe("15.000,00");
    expect(within(ventana).getByLabelText(/^CBU\/CVU de destino/)).toBeTruthy();
  });

  it("lo leído que no coincide se dice; registrar igual va como «Se leyó mal», con la cuenta escrita", async () => {
    const distinta = { ...LEIDA, importe_centavos: 1_400_000, distinto_de_la_orden: ["importe"] };
    const api = servidor(() => distinta);
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    const [texto] = textosDistintos(distinta as never, POR_ACLARAR);
    expect(await within(ventana).findByText(parrafo(texto!))).toBeTruthy();
    expect(within(ventana).getByText(SI_SE_LEYO_MAL)).toBeTruthy();
    expect((within(ventana).getByLabelText(/^Importe transferido/) as HTMLInputElement).value).toBe("14.000,00");
    fireEvent.change(within(ventana).getByLabelText(/^Importe transferido/), { target: { value: "15000" } });
    fireEvent.change(within(ventana).getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar el pago" }));
    await waitFor(() => expect(pedidos(api, "POST", PAGO_RUTA)).toHaveLength(1));
    expect(await pedidos(api, "POST", PAGO_RUTA)[0]?.json()).toMatchObject({ importe_centavos: 1_500_000, lectura_mal: true, cuenta_destino: CUENTA });
  });

  it("control: sin lectura, los cuatro datos a mano y sin «Se leyó mal»", async () => {
    const api = servidor(() => null);
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    expect((within(ventana).getByLabelText("Número de operación") as HTMLInputElement).value).toBe("");
    expect(within(ventana).getByLabelText(/^Importe transferido/)).toBeTruthy();
    expect(within(ventana).getByLabelText(/^CBU\/CVU de destino/)).toBeTruthy();
    expect(within(ventana).queryByRole("button", { name: CORREGIR_LO_LEIDO })).toBeNull();
  });

  it("mientras se lee, lo dice; cuando llega la lectura, llena los datos", async () => {
    let lectura: object = { ...LEIDA, estado: "leyendo", importe_centavos: null, numero_operacion: null, fecha: null, cuenta_ultimos_digitos: null, cuenta_coincide: null };
    vi.stubGlobal("fetch", servidor(() => lectura));
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    expect(within(ventana).getByText("Leyendo el comprobante: los datos se completan solos.")).toBeTruthy();
    lectura = LEIDA;
    await waitFor(() => expect((within(ventana).getByLabelText("Número de operación") as HTMLInputElement).value).toBe("0001-234567"), { timeout: 6000 });
    expect(within(ventana).queryByLabelText(/^CBU\/CVU de destino/)).toBeNull();
  });
});

describe("Codex en el PR #262: lo leído que llega tarde no pisa lo escrito (FL-ACL-01)", () => {
  const LEYENDO = {
    version_documento_id: ARCHIVO, estado: "leyendo", importe_centavos: null, moneda: null, cuenta_ultimos_digitos: null,
    cuenta_coincide: null, titular_destino: null, numero_operacion: null, fecha: null,
    distinto_de_la_orden: [], titular_distinto: false, distinto_de_lo_registrado: [], coincide_con_lo_registrado: false,
  };  // prettier-ignore
  const LEIDA = { ...LEYENDO, estado: "leida", importe_centavos: 1_500_000, moneda: "ARS", cuenta_ultimos_digitos: "1234", cuenta_coincide: true, numero_operacion: "0001-234567", fecha: "2026-10-31" };
  const numero = (v: HTMLElement) => (within(v).getByLabelText("Número de operación") as HTMLInputElement).value;

  async function conLecturaTardia(escribir: (ventana: HTMLElement) => void) {
    let lectura: object = LEYENDO;
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/pagos-por-aclarar": () =>
        Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible", lectura_comprobante: lectura }] }),
    }));  // prettier-ignore
    abrir("/pagos/por-aclarar");
    const ventana = await transferencia();
    await within(ventana).findByText("Leyendo el comprobante: los datos se completan solos.");
    escribir(ventana);
    lectura = LEIDA;
    // Control de que la lectura llegó: completa lo que no se escribió.
    await waitFor(() => expect(numero(ventana)).toBe("0001-234567"), { timeout: 6000 });
    return ventana;
  }

  it("el importe escrito en «otro importe» durante la lectura se mantiene", async () => {
    const ventana = await conLecturaTardia((v) => {
      fireEvent.click(within(v).getByRole("button", { name: OTRA_COSA }));
      fireEvent.change(within(v).getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    });
    expect((within(ventana).getByLabelText("Importe que salió del banco") as HTMLInputElement).value).toBe("14.000,00");
  });

  it("en el registro, el importe escrito se mantiene y se sigue pidiendo con la cuenta", async () => {
    const ventana = await conLecturaTardia((v) => {
      fireEvent.change(within(v).getByLabelText(/^Importe transferido/), { target: { value: "14000" } });
    });
    expect((within(ventana).getByLabelText(/^Importe transferido/) as HTMLInputElement).value).toBe("14.000,00");
    expect(within(ventana).getByLabelText(/^CBU\/CVU de destino/)).toBeTruthy();
  });
});
