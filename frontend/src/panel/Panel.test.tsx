import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { nombreDeFacultad } from "../facultades";
import { SUBIR_FACTURA, TEXTO_ACEPTADA, TEXTO_LISTO, TEXTO_RECHAZADA } from "../pagos/SubirFactura";
import { faltantesPorEstado, gastoPorMes, gastoPorRubro, mesesConPagos, ordenesPorEstado, type Panel, SIN_RUBRO } from "./datos";
import { TEXTO_LA_SUBE_LA_ADMINISTRACION } from "./Panel";

// Resumen de pagos del edificio (app 0094; antes «Panel del Consejo»). Datos ficticios: ningún
// proveedor ni importe real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const O1 = "33333333-3333-4333-8333-333333333333";
const O2 = "44444444-4444-4444-8444-444444444444";
const PANEL = `GET /api/v1/consorcios/${C1}/panel`;
const ACCIONES = `GET /api/v1/consorcios/${C1}/mis-acciones`;

const DATOS: Panel = {
  ordenes_por_estado: [
    { estado: "pagada", moneda: "ARS", cantidad: 3, importe_centavos: 300000 },
    { estado: "en_revision", moneda: "ARS", cantidad: 1, importe_centavos: 50000 },
  ],
  gasto_pagado: [
    { periodo: "2026-10-01", rubro: "Limpieza", fondo_reserva: true, moneda: "ARS", cantidad: 1, importe_centavos: 100000 },
    { periodo: "2026-11-01", rubro: null, fondo_reserva: false, moneda: "ARS", cantidad: 1, importe_centavos: 150000 },
    { periodo: "2026-11-01", rubro: "Limpieza", fondo_reserva: false, moneda: "ARS", cantidad: 1, importe_centavos: 50000 },
  ],
  comprobantes_faltantes: [
    { orden_id: O1, estado: "pagada", proveedor: "Limpieza Ficticia SRL", concepto: "Limpieza de octubre",
      importe_centavos: 100000, moneda: "ARS", actualizada_en: "2026-10-31T12:00:00Z", version: 1, factura_subida: null },
    { orden_id: O2, estado: "aprobada", proveedor: "Ascensores Ficticios SA", concepto: "Abono de noviembre",
      importe_centavos: 80000, moneda: "ARS", actualizada_en: "2026-11-05T12:00:00Z", version: 2, factura_subida: null },
  ],
};
const VACIO: Panel = { ordenes_por_estado: [], gasto_pagado: [], comprobantes_faltantes: [] };

type Respuestas = Record<string, () => Response | Promise<Response>>;

const INICIO: Respuestas = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () =>
    Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
};

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? INICIO[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

function abrir(ruta = "/panel") {
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

async function sinViolaciones(contenedor: HTMLElement) {
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

describe("datos del panel", () => {
  it("suma por mes y por rubro, con lo que no tiene rubro al final", () => {
    expect(gastoPorMes(DATOS, "ARS").map((b) => [b.clave, b.valor])).toEqual([
      ["2026-10-01", 100000],
      ["2026-11-01", 200000],
    ]);
    expect(gastoPorMes(DATOS, "ARS", true).map((b) => b.clave)).toEqual(["2026-10-01"]);
    expect(mesesConPagos(DATOS, "ARS")).toEqual(["2026-11-01", "2026-10-01"]);
    // El de mayor importe es el que no tiene rubro, y aun así va último.
    expect(gastoPorRubro(DATOS, "ARS", "2026-11-01").map((b) => b.etiqueta)).toEqual(["Limpieza", SIN_RUBRO]);
  });
});

describe("resumen de pagos del edificio", () => {
  it("muestra cada gráfico con su base, su tabla y los enlaces a los registros", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    const { container } = abrir();

    const estados = await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    expect(within(estados).getByText(/No incluye borradores/)).toBeTruthy();
    // En el orden del circuito y con el valor escrito: no depende del color.
    const filas = within(estados).getAllByRole("row").slice(1);
    expect(filas.map((f) => f.textContent)).toEqual([
      // Una sola lista de nombres de estado (decisión 11 del 12-11-2026).
      expect.stringMatching(/^Esperando aprobación1 orden, \$\s?500,00$/),
      expect.stringMatching(/^Pagada3 órdenes, \$\s?3\.000,00$/),
    ]);
    // Un solo enlace a los pagos, arriba, y no uno al pie de cada gráfico (AC-44).
    const enlaces = screen.getAllByRole("link", { name: "Consultar los pagos" });
    expect(enlaces).toHaveLength(1);
    expect(enlaces[0]!.getAttribute("href")).toBe("/pagos/consulta");

    const porMes = screen.getByRole("region", { name: "Gasto pagado por mes" });
    expect(within(porMes).getByRole("rowheader", { name: "noviembre de 2026" })).toBeTruthy();
    const porRubro = screen.getByRole("region", { name: "Gasto pagado por rubro" });
    expect(within(porRubro).getAllByRole("rowheader").map((c) => c.textContent)).toEqual(["Limpieza", SIN_RUBRO]);
    fireEvent.change(within(porRubro).getByLabelText("Mes"), { target: { value: "2026-10-01" } });
    expect(within(porRubro).getAllByRole("rowheader").map((c) => c.textContent)).toEqual(["Limpieza"]);
    const fondo = screen.getByRole("region", { name: "Pagos con el fondo de reserva" });
    expect(within(fondo).getByText(/No es el saldo del fondo/)).toBeTruthy();

    const faltantes = screen.getByRole("region", { name: "Facturas faltantes" });
    const lista = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    expect(within(lista).getByRole("link", { name: "Limpieza Ficticia SRL" }).getAttribute("href")).toBe(`/pagos/${O1}`);
    expect(within(lista).getByRole("link", { name: "Ascensores Ficticios SA" }).getAttribute("href")).toBe(`/pagos/${O2}`);

    for (const detalle of container.querySelectorAll("details")) detalle.open = true;
    await sinViolaciones(container);
  });

  it("sin acceso a los pagos no ofrece los enlaces a los registros", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: "Facturas faltantes" });
    expect(within(faltantes).getByText("Limpieza Ficticia SRL")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Limpieza Ficticia SRL" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Consultar los pagos" })).toBeNull();
  });

  it("sin órdenes lo dice y no dibuja nada", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(VACIO) }));
    const { container } = abrir();
    expect(await screen.findByText(/Todavía no hay órdenes de pago en este edificio/)).toBeTruthy();
    expect(container.querySelector("svg.grafico__dibujo")).toBeNull();
    await sinViolaciones(container);
  });

  it("sin conexión avisa en la misma pantalla y se puede reintentar", async () => {
    let caida = true;
    vi.stubGlobal(
      "fetch",
      backend({
        [PANEL]: () => {
          if (caida) throw new TypeError("Failed to fetch");
          return Response.json(DATOS);
        },
      }),
    );
    const { container } = abrir();
    expect(await screen.findByText("No se pudieron cargar los datos de esta pantalla.")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).not.toBe("");
    await sinViolaciones(container);
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("region", { name: "Facturas faltantes" })).toBeTruthy();
    expect(screen.queryByText("No se pudieron cargar los datos de esta pantalla.")).toBeNull();
  });

  it("sin la acción del panel no aparece en el inicio y la dirección lleva al inicio", async () => {
    const api = backend({ [ACCIONES]: alcanza("documentos.ver"), [PANEL]: () => Response.json(DATOS) });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    await screen.findByRole("link", { name: "Documentos del edificio" });
    expect(screen.queryByRole("link", { name: "Resumen de pagos del edificio" })).toBeNull();
    expect(api.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/panel"))).toBe(false);
  });

  it("con la acción aparece en el inicio", async () => {
    vi.stubGlobal("fetch", backend({ [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Resumen de pagos del edificio" })).getAttribute("href")).toBe("/panel");
  });

  it("si la API lo niega lleva al inicio: decide el servidor", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => error("sin_permiso", 403) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Resumen de pagos del edificio" })).toBeNull());
  });
});

describe("informe de fricción, tanda 7", () => {
  it("AC-09: en /panel sigue la navegación de la aplicación, con el Panel en ella", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    abrir();

    await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    const navegacion = await screen.findByRole("navigation", { name: "Secciones" });
    expect(within(navegacion).getByRole("link", { name: "Resumen de pagos del edificio" }).getAttribute("href")).toBe("/panel");
  });
});

describe("informe de fricción, tanda 9", () => {
  it("AC-42: el mismo nombre en el inicio, la barra, la cabecera y la facultad", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Resumen de pagos del edificio" })).toBeTruthy();
    await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    const navegacion = await screen.findByRole("navigation", { name: "Secciones" });
    expect(await within(navegacion).findByRole("link", { name: "Resumen de pagos del edificio" })).toBeTruthy();
    expect(screen.queryByText(/Panel del Consejo|panel de fiscalización/)).toBeNull();
    expect(nombreDeFacultad("fiscalizacion.ver_panel")).toBe("Ver el resumen de pagos del edificio");
  });

  it("AC-43: «Pagada» es una sola barra aunque el servidor separe las pagadas de las conciliadas", () => {
    const conConciliadas: Panel = {
      ...DATOS,
      ordenes_por_estado: [
        { estado: "conciliada", moneda: "ARS", cantidad: 2, importe_centavos: 200000 },
        { estado: "pagada", moneda: "ARS", cantidad: 3, importe_centavos: 300000 },
        { estado: "en_revision", moneda: "ARS", cantidad: 1, importe_centavos: 50000 },
      ],
      comprobantes_faltantes: [
        ...DATOS.comprobantes_faltantes,
        { ...DATOS.comprobantes_faltantes[0]!, orden_id: O2.replace("4444-4444", "4445-4444"), estado: "conciliada" },
      ],
    };
    const barras = ordenesPorEstado(conConciliadas, "ARS");
    expect(barras.map((b) => [b.etiqueta, b.valor])).toEqual([
      ["Esperando aprobación", 1],
      ["Pagada", 5],
    ]);
    expect(barras[1]!.texto).toMatch(/^5 órdenes, \$\s?5\.000,00$/);
    expect(faltantesPorEstado(conConciliadas).map((b) => [b.etiqueta, b.valor])).toEqual([
      ["Lista para pagar", 1],
      ["Pagada", 2],
    ]);
  });

  it("AC-45: sin pagos lo dice en palabras, y sin edificios igual que las demás pantallas", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json({ ...DATOS, gasto_pagado: [] }) }));
    abrir();
    const porMes = await screen.findByRole("region", { name: "Gasto pagado por mes" });
    expect(within(porMes).getByText("Todavía no se pagó ninguna orden.")).toBeTruthy();
    expect(screen.queryByText(/pagos con fecha/)).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => Response.json([]) }));
    abrir();
    expect(await screen.findByText("Esta cuenta no tiene edificios en Consorcio en Mano.")).toBeTruthy();
  });
});

describe("subir la factura que falta (app 0099, AC-44)", () => {
  const DOC = "55555555-5555-4555-8555-555555555555";
  const ARCHIVO = "66666666-6666-4666-8666-666666666666";
  const SUBIDA = `/api/v1/consorcios/${C1}/ordenes-pago/${O2}/factura`;
  const ESTADO_ARCHIVO = `GET /api/v1/consorcios/${C1}/documentos/${DOC}/versiones/${ARCHIVO}/estado`;
  const PDF = new File(["%PDF-1.4 SINTETICO"], "Factura 0001.pdf", { type: "application/pdf" });
  const SUBIDA_OK = () =>
    Response.json(
      { orden_id: O2, version: 2, documento_id: DOC,
        archivo: { id: ARCHIVO, estado: "en_cuarentena", tipo: "application/pdf", tamano: 18, sha256: "a".repeat(64) } },
      { status: 201 },
    );
  const PREPARA = alcanza("fiscalizacion.ver_panel", "egresos.preparar_orden", "documentos.cargar", "egresos.revisar_orden");
  const ESPERA = 3000;
  const conEsperaManual = () => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** El resumen como queda: sin O2 una vez que el antivirus aceptó su factura. */
  function servidor(estado: () => Response, extra: Respuestas = {}) {
    let aceptada = false;
    const api = backend({
      [ACCIONES]: PREPARA,
      [PANEL]: () =>
        Response.json(aceptada ? { ...DATOS, comprobantes_faltantes: DATOS.comprobantes_faltantes.slice(0, 1) } : DATOS),
      [`POST ${SUBIDA}`]: SUBIDA_OK,
      [ESTADO_ARCHIVO]: () => {
        const r = estado();
        aceptada = r.status === 200;
        return r;
      },
      ...extra,
    });
    vi.stubGlobal("fetch", api);
    return api;
  }

  async function abrirLaHoja() {
    const faltantes = await screen.findByRole("region", { name: "Facturas faltantes" });
    const fila = within(faltantes).getByRole("row", { name: /Ascensores Ficticios SA/ });
    fireEvent.click(await within(fila).findByRole("button", { name: SUBIR_FACTURA }));
    return screen.findByRole("dialog", { name: SUBIR_FACTURA });
  }

  it("sube, dice «Listo», y cuando el antivirus la acepta la orden sale de la lista", async () => {
    conEsperaManual();
    const api = servidor(() => Response.json({ estado: "disponible", motivo_rechazo: null }));
    const { container } = abrir();
    const hoja = await abrirLaHoja();
    expect(within(hoja).getByText(/Ascensores Ficticios SA/)).toBeTruthy();
    expect(within(hoja).getByText(/no vuelve a aprobación/)).toBeTruthy();
    await sinViolaciones(container.ownerDocument.body);

    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    expect(await within(hoja).findByText(TEXTO_LISTO)).toBeTruthy();
    // La versión que se vio y el nombre del archivo como título; el archivo crudo, con su tipo.
    const [pedido] = api.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((r) => r.method === "POST");
    const url = new URL(pedido!.url);
    expect(url.pathname).toBe(SUBIDA);
    expect([url.searchParams.get("version_esperada"), url.searchParams.get("titulo")]).toEqual(["2", "Factura 0001"]);
    expect(pedido!.headers.get("Content-Type")).toBe("application/pdf");
    expect(pedido!.headers.get("Idempotency-Key")).toBeTruthy();
    // Mientras el antivirus revisa, no se elige otro archivo.
    expect(within(hoja).queryByLabelText("Archivo de la factura")).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await within(hoja).findByText(TEXTO_ACEPTADA)).toBeTruthy();
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    const faltantes = screen.getByRole("region", { name: "Facturas faltantes" });
    await waitFor(() => expect(within(faltantes).queryByText("Ascensores Ficticios SA")).toBeNull());
    expect(within(faltantes).getByText("Limpieza Ficticia SRL")).toBeTruthy(); // control: la otra sigue
  });

  it("si el antivirus la rechaza lo dice y deja elegir otro archivo", async () => {
    conEsperaManual();
    servidor(() => Response.json({ estado: "rechazado", motivo_rechazo: "virus" }));
    abrir();
    const hoja = await abrirLaHoja();
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    await within(hoja).findByText(TEXTO_LISTO);
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect((await within(hoja).findByRole("alert")).textContent).toBe(TEXTO_RECHAZADA);
    expect(within(hoja).getByLabelText("Archivo de la factura")).toBeTruthy();
    expect(within(hoja).queryByText(TEXTO_ACEPTADA)).toBeNull();
  });

  it("un error del servidor se dice dentro de la hoja y se puede elegir otra vez", async () => {
    servidor(() => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }), {
      [`POST ${SUBIDA}`]: () => error("factura_ya_adjunta", 409),
    });
    abrir();
    const hoja = await abrirLaHoja();
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    const aviso = await within(hoja).findByRole("alert");
    expect(aviso.textContent).toMatch(/^La factura no se subió\. La orden ya tiene su factura/);
    expect(within(hoja).queryByText(TEXTO_LISTO)).toBeNull();
    expect(within(hoja).getByLabelText("Archivo de la factura")).toBeTruthy();
  });

  it("sin cargar documentos no se ofrece, y una factura en el antivirus tampoco ofrece otra", async () => {
    const enRevision: Panel = {
      ...DATOS,
      comprobantes_faltantes: [DATOS.comprobantes_faltantes[0]!, { ...DATOS.comprobantes_faltantes[1]!, factura_subida: "en_cuarentena" }],
    };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: alcanza("fiscalizacion.ver_panel", "egresos.preparar_orden") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: "Facturas faltantes" });
    await within(faltantes).findByRole("link", { name: "Limpieza Ficticia SRL" });
    expect(within(faltantes).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: PREPARA }));
    abrir();
    const otra = await screen.findByRole("region", { name: "Facturas faltantes" });
    const [limpieza, ascensores] = within(within(otra).getByRole("table", { name: "Órdenes sin factura" })).getAllByRole("row").slice(1);
    // Control: la que falta sí la ofrece.
    expect(await within(limpieza!).findByRole("button", { name: SUBIR_FACTURA })).toBeTruthy();
    expect(within(ascensores!).getByText("El antivirus la está revisando")).toBeTruthy();
    expect(within(ascensores!).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
  });

  it("si se perdió la respuesta, elegir el mismo archivo reintenta con la misma clave; otro archivo, con otra", async () => {
    let intentos = 0;
    const api = servidor(() => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }), {
      [`POST ${SUBIDA}`]: () => {
        intentos += 1;
        // La primera vez el servidor guardó la subida pero la respuesta no llegó.
        if (intentos === 1) throw new TypeError("Failed to fetch");
        return intentos === 2 ? SUBIDA_OK() : error("factura_ya_adjunta", 409);
      },
    });
    abrir();
    const hoja = await abrirLaHoja();
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    expect((await within(hoja).findByRole("alert")).textContent).toMatch(/^La factura no se subió\. No se pudo completar/);
    const mismo = new File(["%PDF-1.4 SINTETICO"], PDF.name, { type: PDF.type, lastModified: PDF.lastModified });
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [mismo] } });
    expect(await within(hoja).findByText(TEXTO_LISTO)).toBeTruthy();
    const claves = () =>
      api.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((r) => r.method === "POST").map((r) => r.headers.get("Idempotency-Key"));
    const [primera, segunda] = claves();
    expect(primera).toBeTruthy();
    expect(segunda).toBe(primera);
    cleanup();

    // Control: después de una falla, otro archivo es otro pedido y lleva otra clave.
    intentos = 0;
    const otraApi = servidor(() => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }), {
      [`POST ${SUBIDA}`]: () => {
        intentos += 1;
        if (intentos === 1) throw new TypeError("Failed to fetch");
        return SUBIDA_OK();
      },
    });
    abrir();
    const otraHoja = await abrirLaHoja();
    fireEvent.change(within(otraHoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    await within(otraHoja).findByRole("alert");
    const otro = new File(["%PDF-1.4 OTRO SINTETICO"], "Factura 0002.pdf", { type: "application/pdf" });
    fireEvent.change(within(otraHoja).getByLabelText("Archivo de la factura"), { target: { files: [otro] } });
    await within(otraHoja).findByText(TEXTO_LISTO);
    const [a, z] = otraApi.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((r) => r.method === "POST").map((r) => r.headers.get("Idempotency-Key"));
    expect(z).not.toBe(a);
  });

  it("cerrada la hoja mientras el antivirus revisa, la fila sigue y dice el rechazo", async () => {
    conEsperaManual();
    let factura: "en_cuarentena" | "rechazado" | null = null;
    const datos = (): Panel => ({
      ...DATOS,
      comprobantes_faltantes: DATOS.comprobantes_faltantes.map((f) => (f.orden_id === O2 ? { ...f, factura_subida: factura } : f)),
    });
    vi.stubGlobal(
      "fetch",
      backend({
        [ACCIONES]: PREPARA,
        [PANEL]: () => Response.json(datos()),
        [`POST ${SUBIDA}`]: () => {
          factura = "en_cuarentena";
          return SUBIDA_OK();
        },
        [ESTADO_ARCHIVO]: () => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }),
      }),
    );
    abrir();
    const hoja = await abrirLaHoja();
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    await within(hoja).findByText(TEXTO_LISTO);
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    const faltantes = screen.getByRole("region", { name: "Facturas faltantes" });
    const fila = () => within(faltantes).getByRole("row", { name: /Ascensores Ficticios SA/ });
    expect(await within(faltantes).findByText("El antivirus la está revisando")).toBeTruthy();
    expect(within(fila()).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();

    // El antivirus termina después de cerrar la hoja: la fila lo dice sola y vuelve a ofrecerla.
    factura = "rechazado";
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await within(faltantes).findByText("La subida no pasó el antivirus")).toBeTruthy();
    expect(within(fila()).getByRole("button", { name: SUBIR_FACTURA })).toBeTruthy();
  });

  it("al Consejo, que no prepara, la fila le dice que la sube la administración", async () => {
    const enRevision: Panel = {
      ...DATOS,
      comprobantes_faltantes: [DATOS.comprobantes_faltantes[0]!, { ...DATOS.comprobantes_faltantes[1]!, factura_subida: "en_cuarentena" }],
    };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: alcanza("fiscalizacion.ver_panel", "egresos.revisar_orden") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: "Facturas faltantes" });
    const tabla = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    const [limpieza, ascensores] = within(tabla).getAllByRole("row").slice(1);
    expect(await within(limpieza!).findByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeTruthy();
    expect(within(limpieza!).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
    // Mientras el antivirus revisa la subida, la fila dice eso y no quién la sube.
    expect(within(ascensores!).getByText("El antivirus la está revisando")).toBeTruthy();
    expect(within(ascensores!).queryByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeNull();
    cleanup();

    // Control: quien prepara y carga ve el botón, no el aviso.
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: PREPARA }));
    abrir();
    const otra = await screen.findByRole("region", { name: "Facturas faltantes" });
    const [fila] = within(within(otra).getByRole("table", { name: "Órdenes sin factura" })).getAllByRole("row").slice(1);
    expect(await within(fila!).findByRole("button", { name: SUBIR_FACTURA })).toBeTruthy();
    expect(within(fila!).queryByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeNull();
  });
});
