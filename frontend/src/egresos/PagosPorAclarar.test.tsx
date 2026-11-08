import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { VOLVIO_LA_RED } from "./comun";

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
    const { container } = abrir("/pagos/por-aclarar");
    const tarjetas = await screen.findByRole("list", { name: "Pagos para aclarar" });
    expect(within(tarjetas).getByText(/Edificio Ficticio/)).toBeTruthy();
    expect(container.textContent).not.toContain(CUENTA);
    await sinFaltasDeAccesibilidad(container);
    const boton = screen.getByRole("button", { name: "No transferí" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Por qué no se transfirió"), { target: { value: "Renuncié antes de pagar" } });
    fireEvent.click(boton);
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
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
    const { container } = abrir("/pagos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.getByText("Primero hay que subir el comprobante de la transferencia.")).toBeTruthy();
    const registrar = screen.getByRole("button", { name: "Registrar el pago" }) as HTMLButtonElement;
    expect(registrar.disabled).toBe(true);
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
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
    expect(await screen.findByText(/ya se puede registrar el pago/)).toBeTruthy();
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
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.getByText(/El antivirus rechazó el archivo/)).toBeTruthy();
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
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
    abrir("/pagos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    const archivo = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [archivo] } });
    expect(await screen.findByText(/El archivo no se subió/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar la subida" }));
    expect(await screen.findByText(/Listo: subimos el comprobante/)).toBeTruthy();
    const [primera, segunda] = pedidos(api, "POST", SUBIDA).map((q) => q.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(screen.queryByRole("button", { name: "Reintentar la subida" })).toBeNull();
  });

  it("quien perdió la facultad y pagó otra cosa lo registra con su explicación, por su ruta", async () => {
    const DIFERENCIA = `${BASE}/pagos-por-aclarar/${O1}/diferencia`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
      [`POST ${DIFERENCIA}`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.queryByLabelText("Qué pasó")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Pagué otra cosa" }));
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar lo que pasó" }));
    expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(0); // sin explicación no se manda
    fireEvent.change(screen.getByLabelText("Qué pasó"), { target: { value: "SINTETICO: transferí de menos" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar lo que pasó" }));
    await waitFor(() => expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(1));
    expect(await pedidos(api, "POST", DIFERENCIA)[0]?.json()).toMatchObject({ moneda: "ARS", documento_id: DOC, explicacion: "SINTETICO: transferí de menos", cuenta_real: CUENTA });
    expect(await screen.findByText(/lo resuelve el Consejo/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/pagos-por-aclarar/${O1}/pago`)).toHaveLength(0);
  });

  it("«Pagué otra cosa» trae la moneda de la orden y se puede cambiar", async () => {
    const DIFERENCIA = `${BASE}/pagos-por-aclarar/${O1}/diferencia`;
    const api = backend({
      "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [{ ...POR_ACLARAR, comprobante_id: DOC, comprobante_estado: "disponible" }] }),
      [`POST ${DIFERENCIA}`]: () => Response.json({ id: DECL, orden_id: O1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/por-aclarar");
    fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
    expect(screen.queryByLabelText("Moneda que salió del banco")).toBeNull(); // el registro común no la pide
    fireEvent.click(screen.getByRole("button", { name: "Pagué otra cosa" }));
    const moneda = screen.getByLabelText("Moneda que salió del banco") as HTMLInputElement;
    expect(moneda.value).toBe("ARS");
    fireEvent.change(moneda, { target: { value: "usd" } });
    expect(moneda.value).toBe("USD");
    fireEvent.change(screen.getByLabelText("Fecha de la transferencia"), { target: { value: "2026-10-31" } });
    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "0001-234567" } });
    fireEvent.change(screen.getByLabelText("Importe que salió del banco"), { target: { value: "14000" } });
    fireEvent.change(screen.getByLabelText(/^CBU\/CVU de destino/), { target: { value: CUENTA } });
    fireEvent.change(screen.getByLabelText("Qué pasó"), { target: { value: "SINTETICO: salió en dólares" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrar lo que pasó" }));
    await waitFor(() => expect(pedidos(api, "POST", DIFERENCIA)).toHaveLength(1));
    expect(await pedidos(api, "POST", DIFERENCIA)[0]?.json()).toMatchObject({ moneda: "USD" });
  });

  it("sin pagos para aclarar lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }) }));
    abrir("/pagos/por-aclarar");
    expect(await screen.findByText("No tenés pagos para aclarar.")).toBeTruthy();
  });
});

describe("fricción: las lecturas y el comprobante", () => {
  const NUEVO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const POR_ACLARAR = {
    consorcio_id: C1, consorcio: "Edificio Ficticio", orden_id: O1, version: 1, concepto: "Limpieza de octubre",
    importe_centavos: 1_500_000, moneda: "ARS", cuenta_vista_en: "2026-10-31T13:00:00Z", huella: H1,
    comprobante_id: null, comprobante_estado: null,
  };

  const LECTURAS = [
    ["/pagos/por-aclarar", "GET /api/v1/pagos-por-aclarar", "No tenés pagos para aclarar."],
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
      abrir("/pagos/por-aclarar");
      fireEvent.click(await screen.findByRole("button", { name: "Sí transferí" }));
      const registrar = screen.getByRole("button", { name: "Registrar el pago" }) as HTMLButtonElement;
      expect(registrar.disabled).toBe(true);
      estado = "disponible";
      await vi.advanceTimersByTimeAsync(ESPERA);
      expect(await screen.findByText(/ya se puede registrar el pago/)).toBeTruthy();
      expect(registrar.disabled).toBe(false);
    });

    it("sin el formulario abierto no mira nada por su cuenta", async () => {
      conEsperaManual();
      const api = backend({
        "GET /api/v1/pagos-por-aclarar": () =>
          Response.json({ elementos: [{ ...CON_ARCHIVO, comprobante_estado: "en_cuarentena" }] }),
      });
      vi.stubGlobal("fetch", api);
      abrir("/pagos/por-aclarar");
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
      abrir("/pagos/por-aclarar");
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
      abrir("/pagos/por-aclarar");
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
      abrir("/pagos/por-aclarar");
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
});
