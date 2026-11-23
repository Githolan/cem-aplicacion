import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { nombreDeFacultad } from "../facultades";
import { TEXTO_SUBIENDO } from "../CargaConLectura";
import { SUBIR_FACTURA, TEXTO_ACEPTADA, TEXTO_LISTO, TEXTO_RECHAZADA } from "../pagos/SubirFactura";
import type { components } from "../api/tipos";
import {
  gastoLiquidadoPorPeriodo,
  gastoPorMes,
  gastoPorRubro,
  mesesConPagos,
  ordenesPorEstado,
  type Panel,
  periodosConRubros,
  rubrosDelPeriodo,
  SIN_RUBRO,
} from "./datos";
import {
  SEGUN_LAS_OFICIALES,
  SIN_DATOS,
  TEXTO_LA_SUBE_LA_ADMINISTRACION,
  textoNoCierra,
  textoSolicitudesAbiertas,
  TITULO_GASTO_POR_PERIODO,
  TITULO_GASTO_POR_RUBRO,
  VER_LIQUIDACIONES,
} from "./Panel";
import {
  NO_FIGURA,
  NO_SE_CALCULA,
  REEMPLAZAR,
  REGISTRADO,
  REGISTRAR,
  SEGUN_EL_PRESUPUESTO,
  SIN_PRESUPUESTO,
  SIN_PRESUPUESTO_PARA_LOS_PERIODOS,
  textoDesvio,
  textoVencimientos,
  TITULO_DESVIOS,
  TITULO_REEMPLAZAR,
} from "./Presupuesto";

// Resumen de pagos del edificio (app 0094; antes «Panel del Consejo»). Datos ficticios: ningún
// proveedor ni importe real.

type Presupuesto = components["schemas"]["Presupuesto"];

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const O1 = "33333333-3333-4333-8333-333333333333";
const O2 = "44444444-4444-4444-8444-444444444444";
const PANEL = `GET /api/v1/consorcios/${C1}/panel`;
const ACCIONES = `GET /api/v1/consorcios/${C1}/mis-acciones`;
const PRESUPUESTOS = `GET /api/v1/consorcios/${C1}/presupuestos`;

/** `GET …/presupuestos` (app 0128), con los límites del contrato. */
const listaPresupuestos = (elementos: Presupuesto[], puede_registrar: boolean) => ({
  elementos, puede_registrar, largo_aprobacion: 200, largo_motivo: 500, largo_nombre_rubro: 120, maximo_rubros: 60,
});

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
  solicitudes_abiertas: 2,
  gasto_liquidado: [],
  rubros_liquidados: [],
  liquidacion_vigente: null,
  desvios_presupuesto: [],
};
const VACIO: Panel = {
  ordenes_por_estado: [], gasto_pagado: [], comprobantes_faltantes: [], solicitudes_abiertas: 0, gasto_liquidado: [], rubros_liquidados: [],
  liquidacion_vigente: null, desvios_presupuesto: [],
};  // prettier-ignore

/** Modo Consejo (app 0120): sin órdenes, dos liquidaciones oficiales. SINTETICO. */
const L1 = "55555555-5555-4555-8555-555555555555";
const GRUPOS = [{ nombre: "Gastos Comunes", total: "2000.00" }, { nombre: "AySA", total: "500.50" }];
const CONSEJO: Panel = {
  ...VACIO,
  gasto_liquidado: [
    { periodo: "2026-09-01", confirmada_en: "2026-10-05T15:00:00Z", total: "2400.49", grupos: GRUPOS },
    { periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z", total: "2500.50", grupos: GRUPOS },
  ],
  rubros_liquidados: [
    { periodo: "2026-09-01", numero: 1, nombre: "Servicios públicos SINTETICOS", total: "1500.50" },
    { periodo: "2026-09-01", numero: 2, nombre: "Mantenimiento SINTETICO", total: "899.99" },
    { periodo: "2026-10-01", numero: 1, nombre: "Servicios públicos SINTETICOS", total: "1500.50" },
    { periodo: "2026-10-01", numero: 2, nombre: "Mantenimiento SINTETICO", total: "1000.00" },
  ],
  liquidacion_vigente: { id: L1, periodo: "2026-10-01", diferencias: 2, enlace_documentacion: null, vencimientos: [] },
};  // prettier-ignore

type Respuestas = Record<string, () => Response | Promise<Response>>;

const INICIO: Respuestas = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () =>
    Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
  [PRESUPUESTOS]: () => Response.json(listaPresupuestos([], false)),
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

function Lugar() {
  return <output data-testid="lugar">{useLocation().pathname}</output>;
}

const lugar = () => screen.getByTestId("lugar").textContent;

function abrir(ruta = "/panel") {
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

async function sinViolaciones(contenedor: HTMLElement) {
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations.map((v) => v.id)).toEqual([]);
}

/** Desde qué ancho «Facturas faltantes» es una tabla (FL-PAN-02); más angosto, filas compactas. */
const TABLA_DE_FALTANTES = "(min-width: 48.0625em)";

/** El ancho de la pantalla: con tabla (tablet y PC) o de teléfono. */
function ancho(conTabla: boolean) {
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: conTabla && consulta === TABLA_DE_FALTANTES, media: consulta, addEventListener: () => undefined, removeEventListener: () => undefined,
  }));
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
  ancho(true);
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

describe("Modo Consejo: el gasto según las liquidaciones oficiales (app 0120)", () => {
  it("dibuja el gasto por período y por rubro, cada uno con su tabla, y lo que no cierra lleva a las liquidaciones", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(CONSEJO) }));
    const { container } = abrir();
    const porPeriodo = await screen.findByRole("region", { name: TITULO_GASTO_POR_PERIODO });
    expect(within(porPeriodo).getByText(SEGUN_LAS_OFICIALES)).toBeTruthy();
    // La tabla equivalente: un período por fila, del más viejo al más nuevo, con el total y los grupos.
    const tabla = within(porPeriodo).getByRole("table", { name: TITULO_GASTO_POR_PERIODO });
    expect(within(tabla).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Período", "Gasto", "Por grupo"]);
    expect(within(tabla).getAllByRole("row").slice(1).map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^septiembre de 2026\$\s?2\.400,49Gastos Comunes: \$\s?2\.000,00 · AySA: \$\s?500,50$/),
      expect.stringMatching(/^octubre de 2026\$\s?2\.500,50Gastos Comunes/),
    ]);
    // Por rubro: el último período primero; el desplegable cambia de período.
    const porRubro = screen.getByRole("region", { name: TITULO_GASTO_POR_RUBRO });
    expect(within(porRubro).getAllByRole("row").slice(1).map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^Servicios públicos SINTETICOS\$\s?1\.500,50$/),
      expect.stringMatching(/^Mantenimiento SINTETICO\$\s?1\.000,00$/),
    ]);
    fireEvent.change(within(porRubro).getByLabelText("Período"), { target: { value: "2026-09-01" } });
    expect(within(porRubro).getByRole("row", { name: /Mantenimiento SINTETICO/ }).textContent).toMatch(/899,99$/);
    // RN33: lo que no cierra, neutro, como el único enlace a las liquidaciones.
    const noCierra = screen.getByRole("link", { name: textoNoCierra("2026-10-01", 2) });
    expect(noCierra.getAttribute("href")).toBe("/liquidacion-oficial");
    expect(textoNoCierra("2026-10-01", 2)).toBe("Lo que no cierra en el documento de octubre de 2026: 2 diferencias");
    expect(screen.queryByRole("link", { name: VER_LIQUIDACIONES })).toBeNull();
    // Sin órdenes, sus bloques no se dibujan y no se dice que falten (PAN-03).
    expect(screen.queryByRole("region", { name: /Órdenes de pago por estado/ })).toBeNull();
    expect(screen.queryByText(SIN_DATOS)).toBeNull();
    for (const detalle of container.querySelectorAll("details")) detalle.open = true;
    await sinViolaciones(container);
  });

  it("sin diferencias ofrece las liquidaciones; quien no las abre no recibe enlace", async () => {
    const sinDiferencias: Panel = { ...CONSEJO, liquidacion_vigente: { id: L1, periodo: "2026-10-01", diferencias: 0, enlace_documentacion: null, vencimientos: [] } };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(sinDiferencias) }));
    abrir();
    expect((await screen.findByRole("link", { name: VER_LIQUIDACIONES })).getAttribute("href")).toBe("/liquidacion-oficial");
    expect(screen.queryByText(/Lo que no cierra/)).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(CONSEJO), [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir();
    expect(await screen.findByText(textoNoCierra("2026-10-01", 2))).toBeTruthy();
    expect(screen.queryByRole("link", { name: textoNoCierra("2026-10-01", 2) })).toBeNull();
    expect(screen.queryByRole("link", { name: VER_LIQUIDACIONES })).toBeNull();
  });

  it("RF47: la vigente sin el enlace a la documentación lo avisa sobre los gráficos, sin botones; con el enlace, nada", async () => {
    const enlace = { jurisdiccion: "caba" as const, version: 1, norma: "Disposición 1146/DGDYPC/24", fuente: "https://boletinoficial.buenosaires.gob.ar/normativaba/norma/714586" };
    const sinEnlace: Panel = { ...CONSEJO, liquidacion_vigente: { ...CONSEJO.liquidacion_vigente!, enlace_documentacion: { ...enlace, estado: "no_encontrado" } } };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(sinEnlace) }));
    const { container } = abrir();
    const aviso = await screen.findByRole("note");
    expect(aviso.textContent).toContain("La liquidación oficial de octubre de 2026 no trae el enlace ni el QR a la documentación del período");
    expect(aviso.textContent).toContain("En Modo Consejo CEM no publica la liquidación");
    expect(within(aviso).queryByRole("button")).toBeNull();
    // Arriba de los gráficos, y el único enlace a las liquidaciones sigue siendo el de lo que no cierra.
    const porPeriodo = screen.getByRole("region", { name: TITULO_GASTO_POR_PERIODO });
    expect(aviso.compareDocumentPosition(porPeriodo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const internos = within(container.querySelector<HTMLElement>(".panel-consejo")!).getAllByRole("link").filter((a) => a.getAttribute("href") === "/liquidacion-oficial");
    expect(internos.map((a) => a.textContent)).toEqual([textoNoCierra("2026-10-01", 2)]);
    await sinViolaciones(container);
    cleanup();

    const conEnlace: Panel = { ...CONSEJO, liquidacion_vigente: { ...CONSEJO.liquidacion_vigente!, enlace_documentacion: { ...enlace, estado: "encontrado" } } };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(conEnlace) }));
    abrir();
    expect(await screen.findByRole("region", { name: TITULO_GASTO_POR_PERIODO })).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("con un solo período no hay desplegable y el período va en el título (PAN-07)", async () => {
    const unPeriodo: Panel = {
      ...CONSEJO,
      gasto_liquidado: CONSEJO.gasto_liquidado.slice(1),
      rubros_liquidados: CONSEJO.rubros_liquidados.filter((r) => r.periodo === "2026-10-01"),
    };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(unPeriodo) }));
    abrir();
    const porRubro = await screen.findByRole("region", { name: `${TITULO_GASTO_POR_RUBRO}, octubre de 2026` });
    expect(within(porRubro).queryByLabelText("Período")).toBeNull();
  });

  it("los datos: un período sin total no lleva barra y los rubros siguen el orden de la liquidación", () => {
    const sinTotal: Panel = { ...CONSEJO, gasto_liquidado: [{ ...CONSEJO.gasto_liquidado[0]!, total: null }, CONSEJO.gasto_liquidado[1]!] };
    expect(gastoLiquidadoPorPeriodo(sinTotal).map((b) => b.clave)).toEqual(["2026-10-01"]);
    expect(periodosConRubros(CONSEJO)).toEqual(["2026-10-01", "2026-09-01"]);
    // El período más nuevo con todos sus rubros sin total no se ofrece: escondería el selector.
    const octubreSinTotales: Panel = {
      ...CONSEJO,
      rubros_liquidados: CONSEJO.rubros_liquidados.map((f) => (f.periodo === "2026-10-01" ? { ...f, total: null } : f)),
    };
    expect(periodosConRubros(octubreSinTotales)).toEqual(["2026-09-01"]);
    expect(rubrosDelPeriodo(CONSEJO, "2026-09-01").map((b) => [b.etiqueta, b.valor])).toEqual([
      ["Servicios públicos SINTETICOS", 1500.5],
      ["Mantenimiento SINTETICO", 899.99],
    ]);
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

    const faltantes = screen.getByRole("region", { name: /^Facturas faltantes/ });
    const lista = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    expect(within(lista).getByRole("link", { name: "Limpieza Ficticia SRL" }).getAttribute("href")).toBe(`/pagos/${O1}`);
    expect(within(lista).getByRole("link", { name: "Ascensores Ficticios SA" }).getAttribute("href")).toBe(`/pagos/${O2}`);

    for (const detalle of container.querySelectorAll("details")) detalle.open = true;
    await sinViolaciones(container);
  });

  it("PAN-02 y PAN-05: sin la tabla repetida a la vista; la base, plegada salvo lo que evita leer mal", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    const { container } = abrir();
    const estados = await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    expect(screen.queryByText("Ver los datos en una tabla")).toBeNull();
    // La tabla sigue para el lector de pantalla, que no lee las barras.
    expect(within(estados).getByRole("table", { name: "Órdenes de pago por estado" }).closest(".solo-lector")).not.toBeNull();
    expect(container.querySelector(".grafico__barras")?.getAttribute("aria-hidden")).toBe("true");
    const como = within(estados).getByText("Cómo se calcula").closest("details");
    expect(como?.open).toBe(false);
    expect(within(como as HTMLElement).getByText(/Cuántas órdenes hay en cada paso/)).toBeTruthy();
  });

  it("PAN-01: «Solicitudes abiertas» solo si hay alguna", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    abrir();
    expect((await screen.findByRole("link", { name: textoSolicitudesAbiertas(2) })).getAttribute("href")).toBe("/solicitudes");
    cleanup();

    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json({ ...DATOS, solicitudes_abiertas: 0 }) }));
    abrir();
    await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    expect(screen.queryByText(/Solicitudes abiertas/)).toBeNull();
  });

  it("PAN-04 y PAN-06: las facturas faltantes, con la cantidad en el título y sin datos repetidos", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: alcanza("fiscalizacion.ver_panel", "egresos.revisar_orden") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: "Facturas faltantes (2)" });
    expect(faltantes.querySelector(".grafico__barras")).toBeNull();
    const tabla = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    expect(within(tabla).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Proveedor", "Estado", "Importe"]);
    expect(within(faltantes).queryByText("Falta")).toBeNull();
    expect(within(faltantes).getAllByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toHaveLength(1);
    expect(screen.queryByText(/Conviene pedirlo/)).toBeNull();
  });

  it("PAN-07: con un solo mes no hay desplegable y el mes va en el título", async () => {
    const unMes: Panel = { ...DATOS, gasto_pagado: DATOS.gasto_pagado.filter((g) => g.periodo === "2026-11-01") };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(unMes) }));
    abrir();
    const porRubro = await screen.findByRole("region", { name: "Gasto pagado por rubro, noviembre de 2026" });
    expect(within(porRubro).queryByLabelText("Mes")).toBeNull();
    expect(within(porRubro).getAllByRole("rowheader").map((c) => c.textContent)).toEqual(["Limpieza", SIN_RUBRO]);
  });

  it("sin acceso a los pagos no ofrece los enlaces a los registros", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    expect(within(faltantes).getByText("Limpieza Ficticia SRL")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Limpieza Ficticia SRL" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Consultar los pagos" })).toBeNull();
  });

  it("sin órdenes ni liquidaciones oficiales lo dice y no dibuja nada", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(VACIO) }));
    const { container } = abrir();
    expect(await screen.findByText(SIN_DATOS)).toBeTruthy();
    expect(container.querySelector("svg.grafico__dibujo")).toBeNull();
    expect(screen.queryByRole("region", { name: TITULO_GASTO_POR_PERIODO })).toBeNull();
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
    expect(await screen.findByRole("region", { name: /^Facturas faltantes/ })).toBeTruthy();
    expect(screen.queryByText("No se pudieron cargar los datos de esta pantalla.")).toBeNull();
  });

  it("sin la acción del panel no aparece en el inicio y la dirección lleva al inicio", async () => {
    const api = backend({ [ACCIONES]: alcanza("documentos.ver"), [PANEL]: () => Response.json(DATOS) });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    // Decisión 5: el inicio no repite el menú; la entrada de Documentos está en la navegación.
    await within(await screen.findByRole("navigation", { name: "Secciones" })).findByRole("link", { name: /^Documentos/ });
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
    };
    const barras = ordenesPorEstado(conConciliadas, "ARS");
    expect(barras.map((b) => [b.etiqueta, b.valor])).toEqual([
      ["Esperando aprobación", 1],
      ["Pagada", 5],
    ]);
    expect(barras[1]!.texto).toMatch(/^5 órdenes, \$\s?5\.000,00$/);
  });

  it("AC-45 y PAN-03: sin pagos no se dibujan sus bloques, y sin edificios igual que las demás pantallas", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json({ ...DATOS, gasto_pagado: [] }) }));
    abrir();
    // Control: el bloque con datos sí está.
    expect(await screen.findByRole("region", { name: "Órdenes de pago por estado" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: /^Gasto pagado/ })).toBeNull();
    expect(screen.queryByRole("region", { name: /^Pagos con el fondo/ })).toBeNull();
    expect(screen.queryByText(/pagos con fecha|Todavía no se pagó/)).toBeNull();
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
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
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
    const faltantes = screen.getByRole("region", { name: /^Facturas faltantes/ });
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
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    await within(faltantes).findByRole("link", { name: "Limpieza Ficticia SRL" });
    expect(within(faltantes).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: PREPARA }));
    abrir();
    const otra = await screen.findByRole("region", { name: /^Facturas faltantes/ });
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

  it("mientras la factura se envía, la hoja no se cierra ni con Escape, y el navegador avisa al salir", async () => {
    let responder: (r: Response) => void = () => undefined;
    const api = servidor(() => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }), {
      [`POST ${SUBIDA}`]: () => new Promise<Response>((r) => (responder = r)),
    });
    abrir();
    const hoja = await abrirLaHoja();
    const salir = () => {
      const evento = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(evento);
      return evento.defaultPrevented;
    };
    expect(salir()).toBe(false); // control: sin envío, no avisa
    fireEvent.change(within(hoja).getByLabelText("Archivo de la factura"), { target: { files: [PDF] } });
    expect(await within(hoja).findByText(TEXTO_SUBIENDO)).toBeTruthy();
    const cerrar = within(hoja).getByRole("button", { name: "Cerrar" }) as HTMLButtonElement;
    expect(cerrar.disabled).toBe(true);
    fireEvent.keyDown(hoja, { key: "Escape" });
    fireEvent.click(cerrar);
    expect(screen.getByRole("dialog", { name: SUBIR_FACTURA })).toBeTruthy();
    expect(salir()).toBe(true);

    // Confirmada la subida, se cierra y la pantalla de atrás se vuelve a leer.
    responder(SUBIDA_OK());
    expect(await within(hoja).findByText(TEXTO_LISTO)).toBeTruthy();
    expect(salir()).toBe(false);
    const lecturas = api.mock.calls.filter((c) => new URL((c as unknown as [Request])[0].url).pathname === PANEL.split(" ")[1]).length;
    fireEvent.click(within(hoja).getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: SUBIR_FACTURA })).toBeNull());
    await waitFor(() =>
      expect(api.mock.calls.filter((c) => new URL((c as unknown as [Request])[0].url).pathname === PANEL.split(" ")[1]).length).toBeGreaterThan(lecturas),
    );
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
    const faltantes = screen.getByRole("region", { name: /^Facturas faltantes/ });
    const fila = () => within(faltantes).getByRole("row", { name: /Ascensores Ficticios SA/ });
    expect(await within(faltantes).findByText("El antivirus la está revisando")).toBeTruthy();
    expect(within(fila()).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();

    // El antivirus termina después de cerrar la hoja: la fila lo dice sola y vuelve a ofrecerla.
    factura = "rechazado";
    await act(() => vi.advanceTimersByTimeAsync(ESPERA));
    expect(await within(faltantes).findByText("La subida no pasó el antivirus")).toBeTruthy();
    expect(within(fila()).getByRole("button", { name: SUBIR_FACTURA })).toBeTruthy();
  });

  it("al Consejo, que no prepara, se le dice una vez que la sube la administración", async () => {
    const enRevision: Panel = {
      ...DATOS,
      comprobantes_faltantes: [DATOS.comprobantes_faltantes[0]!, { ...DATOS.comprobantes_faltantes[1]!, factura_subida: "en_cuarentena" }],
    };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: alcanza("fiscalizacion.ver_panel", "egresos.revisar_orden") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    const tabla = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    // Una sola vez, sobre la lista, y no en cada fila (PAN-06).
    expect(await within(faltantes).findByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeTruthy();
    expect(within(tabla).queryByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeNull();
    const [limpieza, ascensores] = within(tabla).getAllByRole("row").slice(1);
    expect(within(limpieza!).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
    // Lo que cambia sí se dice en la fila: la subida está en el antivirus.
    expect(within(ascensores!).getByText("El antivirus la está revisando")).toBeTruthy();
    cleanup();

    // Control: quien prepara y carga ve el botón, no el aviso.
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(enRevision), [ACCIONES]: PREPARA }));
    abrir();
    const otra = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    const [fila] = within(within(otra).getByRole("table", { name: "Órdenes sin factura" })).getAllByRole("row").slice(1);
    expect(await within(fila!).findByRole("button", { name: SUBIR_FACTURA })).toBeTruthy();
    expect(within(otra).queryByText(TEXTO_LA_SUBE_LA_ADMINISTRACION)).toBeNull();
  });
});

describe("las facturas faltantes, de vuelta al resumen y en el teléfono (flujos 18-11)", () => {
  const PREPARA = alcanza("fiscalizacion.ver_panel", "egresos.preparar_orden", "documentos.cargar", "egresos.revisar_orden");
  const CONSEJO = alcanza("fiscalizacion.ver_panel", "egresos.revisar_orden");
  const NO_ESTA = () => error("no_encontrado", 404);

  it("FL-PAN-01: la orden abierta desde «Facturas faltantes» vuelve al resumen, no a Pagos", async () => {
    vi.stubGlobal("fetch", backend({
      [PANEL]: () => Response.json(DATOS), [ACCIONES]: CONSEJO, [`GET /api/v1/consorcios/${C1}/pagos/${O1}`]: NO_ESTA,
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS"] }]),
    }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    fireEvent.click(within(faltantes).getByRole("link", { name: "Limpieza Ficticia SRL" }));
    await waitFor(() => expect(lugar()).toBe(`/pagos/${O1}`));
    expect(screen.queryByRole("link", { name: "Volver a Pagos" })).toBeNull();
    fireEvent.click(await screen.findByRole("link", { name: "Volver a Resumen de pagos" }));
    await waitFor(() => expect(lugar()).toBe("/panel"));
  });

  it("FL-PAN-02: en el teléfono, una fila compacta por orden, que abre la orden, y sin tabla", async () => {
    ancho(false);
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: CONSEJO }));
    const { container } = abrir();
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    expect(within(faltantes).queryByRole("table")).toBeNull();
    const filas = within(within(faltantes).getByRole("list", { name: "Órdenes sin factura" })).getAllByRole("link");
    expect(filas.map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^Limpieza Ficticia SRL · \$\s?1\.000,00 Limpieza de octubre · Pagada$/),
      expect.stringMatching(/^Ascensores Ficticios SA · \$\s?800,00 Abono de noviembre · /),
    ]);
    expect(filas[0]!.getAttribute("href")).toBe(`/pagos/${O1}`);
    await sinViolaciones(container);
  });

  it("FL-PAN-02: en el teléfono, quien sube la factura lo hace desde la ficha de la fila", async () => {
    ancho(false);
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: PREPARA }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: /^Facturas faltantes/ });
    expect(within(faltantes).queryByRole("button", { name: SUBIR_FACTURA })).toBeNull();
    fireEvent.click(await within(faltantes).findByRole("button", { name: /Ascensores Ficticios SA/ }));
    const ficha = within(await screen.findByRole("dialog", { name: "Ascensores Ficticios SA" }));
    expect(ficha.getByText("Abono de noviembre")).toBeTruthy();
    expect(ficha.getByRole("link", { name: "Ver la orden" }).getAttribute("href")).toBe(`/pagos/${O2}`);
    fireEvent.click(ficha.getByRole("button", { name: SUBIR_FACTURA }));
    expect(await screen.findByRole("dialog", { name: SUBIR_FACTURA })).toBeTruthy();
  });
});

describe("presupuesto, desvíos y vencimientos en Modo Consejo (app 0128)", () => {
  const P1 = "66666666-6666-4666-8666-666666666666";
  const P2 = "77777777-7777-4777-8777-777777777777";
  const VIGENTE: Presupuesto = {
    id: P1, desde: "2026-09-01", hasta: "2026-10-01", aprobacion: "Asamblea ordinaria del 12-03-2026 (SINTETICA)",
    rubros: [{ numero: 1, nombre: "Servicios públicos SINTETICOS", importe: "1500.00" }, { numero: 3, nombre: "Fondo SINTETICO", importe: "300.00" }],
    reemplaza_a: null, motivo: null, reemplazado_por: null, registrado_por_nombre: "Consejera SINTETICA", registrado_en: "2026-11-20T15:00:00Z",
  };  // prettier-ignore
  type Desvio = Panel["desvios_presupuesto"][number];
  const fila = (periodo: string, numero: number, nombre: string, presupuestado: string | null, liquidado: string | null, desvio: string | null, porcentaje: string | null): Desvio =>
    ({ periodo, numero, nombre, presupuesto_id: P1, presupuestado, liquidado, desvio, porcentaje });  // prettier-ignore
  const CON_PRESUPUESTO: Panel = {
    ...CONSEJO,
    liquidacion_vigente: { ...CONSEJO.liquidacion_vigente!, vencimientos: ["2026-11-10", "2026-11-20"] },
    desvios_presupuesto: [
      fila("2026-09-01", 1, "Servicios públicos SINTETICOS", "1500.00", "1500.50", "0.50", "0.03"),
      fila("2026-09-01", 2, "Mantenimiento SINTETICO", null, "899.99", null, null),
      fila("2026-09-01", 3, "Fondo SINTETICO", "300.00", null, null, null),
      fila("2026-10-01", 1, "Servicios públicos SINTETICOS", "1500.00", "1500.50", "0.50", "0.03"),
      fila("2026-10-01", 2, "Mantenimiento SINTETICO", "0.00", "1000.00", "1000.00", null),
    ],
  };
  const pedidos = (api: ReturnType<typeof backend>, metodo: string, camino: string) =>
    api.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
  const RUTA = `/api/v1/consorcios/${C1}/presupuestos`;
  const RUTA_PANEL = `/api/v1/consorcios/${C1}/panel`;

  it("los textos: el desvío con signo y porcentaje, y los vencimientos como se leen", () => {
    expect(textoDesvio(CON_PRESUPUESTO.desvios_presupuesto[0]!)).toMatch(/^\+\$\s?0,50 \(\+0,03 %\)$/);
    expect(textoDesvio({ ...CON_PRESUPUESTO.desvios_presupuesto[0]!, desvio: "-100.00", porcentaje: "-9.09" })).toMatch(/^-\$\s?100,00 \(-9,09 %\)$/);
    // Con lo presupuestado en cero, el desvío sin porcentaje; sin uno de los dos importes, no se calcula.
    expect(textoDesvio(CON_PRESUPUESTO.desvios_presupuesto[4]!)).toMatch(/^\+\$\s?1\.000,00$/);
    expect(textoDesvio(CON_PRESUPUESTO.desvios_presupuesto[1]!)).toBe(NO_SE_CALCULA);
    expect(textoVencimientos("2026-10-01", ["2026-11-10", "2026-11-20"])).toBe("Vencimientos de la liquidación de octubre de 2026: 10/11/2026 y 20/11/2026");
    expect(textoVencimientos("2026-10-01", ["2026-11-10"])).toBe("Vencimiento de la liquidación de octubre de 2026: 10/11/2026");
  });

  it("muestra los vencimientos y, por período, lo presupuestado frente a lo liquidado sin inventar ningún desvío", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(CON_PRESUPUESTO), [PRESUPUESTOS]: () => Response.json(listaPresupuestos([VIGENTE], false)) }));
    const { container } = abrir();
    expect(await screen.findByText("Vencimientos de la liquidación de octubre de 2026: 10/11/2026 y 20/11/2026")).toBeTruthy();
    const region = screen.getByRole("region", { name: TITULO_DESVIOS });
    expect(within(region).getByText(SEGUN_EL_PRESUPUESTO)).toBeTruthy();
    const tabla = () => within(region).getByRole("table");
    expect(within(tabla()).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Rubro", "Presupuestado", "Liquidado", "Desvío"]);
    // El período más nuevo primero: el rubro presupuestado en cero tiene desvío y no porcentaje.
    expect(within(tabla()).getAllByRole("row").slice(1).map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^Servicios públicos SINTETICOS\$\s?1\.500,00\$\s?1\.500,50\+\$\s?0,50 \(\+0,03 %\)$/),
      expect.stringMatching(/^Mantenimiento SINTETICO\$\s?0,00\$\s?1\.000,00\+\$\s?1\.000,00$/),
    ]);
    fireEvent.change(within(region).getByLabelText("Período"), { target: { value: "2026-09-01" } });
    // Sin presupuesto para el rubro: «Sin presupuesto», nunca un 100 %; el que la liquidación no trae, tampoco.
    expect(within(tabla()).getAllByRole("row").slice(1).map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^Servicios públicos SINTETICOS/),
      expect.stringMatching(new RegExp(`^Mantenimiento SINTETICO${SIN_PRESUPUESTO}\\$\\s?899,99${NO_SE_CALCULA}$`)),
      expect.stringMatching(new RegExp(`^Fondo SINTETICO\\$\\s?300,00${NO_FIGURA}${NO_SE_CALCULA}$`)),
    ]);
    expect(region.textContent).not.toMatch(/100 %/);
    // Sin la facultad, ni registrar ni reemplazar; la historia sí se lee.
    expect(await within(region).findByText("Presupuestos registrados (1)")).toBeTruthy();
    expect(within(region).queryByRole("button", { name: REGISTRAR })).toBeNull();
    expect(within(region).queryByRole("button", { name: REEMPLAZAR })).toBeNull();
    for (const detalle of container.querySelectorAll("details")) detalle.open = true;
    await sinViolaciones(container);
  });

  it("sin ningún presupuesto que cubra los períodos lo dice, sin tabla", async () => {
    const sinCubrir: Panel = { ...CONSEJO, desvios_presupuesto: [{ ...fila("2026-10-01", 2, "Mantenimiento SINTETICO", null, "1000.00", null, null), presupuesto_id: null }] };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(sinCubrir) }));
    abrir();
    const region = await screen.findByRole("region", { name: `${TITULO_DESVIOS}, octubre de 2026` });
    expect(within(region).getByText(SIN_PRESUPUESTO_PARA_LOS_PERIODOS)).toBeTruthy();
    expect(within(region).queryByRole("table")).toBeNull();
  });

  it("registra con los rubros de la liquidación: importe mensual, meses AAAA-MM-01, y vuelve a leer el resumen", async () => {
    const api = backend({
      [PANEL]: () => Response.json(CONSEJO),
      [PRESUPUESTOS]: () => Response.json(listaPresupuestos([], true)),
      [`POST ${RUTA}`]: () => Response.json({ ...VIGENTE, id: P2 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: REGISTRAR }));
    const ventana = await screen.findByRole("dialog", { name: REGISTRAR });
    // Los rubros son los de la liquidación, por número; sin motivo (no reemplaza).
    expect(within(ventana).getByLabelText("1. Servicios públicos SINTETICOS")).toBeTruthy();
    expect(within(ventana).getByLabelText("2. Mantenimiento SINTETICO")).toBeTruthy();
    expect(within(ventana).queryByLabelText("Motivo del reemplazo")).toBeNull();
    // Vacío no viaja: dice qué falta junto a cada campo.
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar" }));
    expect(await within(ventana).findByText("Falta el primer mes.")).toBeTruthy();
    expect(within(ventana).getByText("Falta de dónde sale el presupuesto.")).toBeTruthy();
    expect(within(ventana).getByText("Falta el importe de al menos un rubro.")).toBeTruthy();
    expect(pedidos(api, "POST", RUTA)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Primer mes (AAAA-MM)"), { target: { value: "2026-09" } });
    fireEvent.change(within(ventana).getByLabelText("Último mes (AAAA-MM)"), { target: { value: "2026-08" } });
    fireEvent.change(within(ventana).getByLabelText("De dónde sale"), { target: { value: "Asamblea ordinaria del 12-03-2026 (SINTETICA)" } });
    fireEvent.change(within(ventana).getByLabelText("1. Servicios públicos SINTETICOS"), { target: { value: "1.500,5" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar" }));
    expect(await within(ventana).findByText("El último mes no puede ser anterior al primero.")).toBeTruthy();
    expect(pedidos(api, "POST", RUTA)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Último mes (AAAA-MM)"), { target: { value: "2026-12" } });
    fireEvent.change(within(ventana).getByLabelText("2. Mantenimiento SINTETICO"), { target: { value: "0" } });
    const lecturas = pedidos(api, "GET", RUTA_PANEL).length;
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar" }));
    expect(await screen.findByText(REGISTRADO)).toBeTruthy();
    const [pedido] = pedidos(api, "POST", RUTA);
    expect(await pedido!.json()).toEqual({
      desde: "2026-09-01", hasta: "2026-12-01", aprobacion: "Asamblea ordinaria del 12-03-2026 (SINTETICA)",
      rubros: [{ numero: 1, nombre: "Servicios públicos SINTETICOS", importe: "1500.50" }, { numero: 2, nombre: "Mantenimiento SINTETICO", importe: "0.00" }],
      reemplaza_a: null, motivo: null,
    });  // prettier-ignore
    expect(pedido!.headers.get("Idempotency-Key")).toBeTruthy();
    await waitFor(() => expect(pedidos(api, "GET", RUTA_PANEL).length).toBeGreaterThan(lecturas));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reemplazar empieza con lo del vigente, suma su rubro que la liquidación no trae y pide el motivo", async () => {
    const api = backend({
      [PANEL]: () => Response.json(CON_PRESUPUESTO),
      [PRESUPUESTOS]: () => Response.json(listaPresupuestos([VIGENTE], true)),
      [`POST ${RUTA}`]: () => error("presupuesto_superpuesto", 409),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByText("Presupuestos registrados (1)"));
    fireEvent.click(await screen.findByRole("button", { name: REEMPLAZAR }));
    const ventana = await screen.findByRole("dialog", { name: TITULO_REEMPLAZAR });
    expect((within(ventana).getByLabelText("Primer mes (AAAA-MM)") as HTMLInputElement).value).toBe("2026-09");
    expect((within(ventana).getByLabelText("1. Servicios públicos SINTETICOS") as HTMLInputElement).value).toBe("1500");
    expect((within(ventana).getByLabelText("3. Fondo SINTETICO") as HTMLInputElement).value).toBe("300");
    expect((within(ventana).getByLabelText("2. Mantenimiento SINTETICO") as HTMLInputElement).value).toBe("");
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar" }));
    expect(await within(ventana).findByText("Falta el motivo del reemplazo.")).toBeTruthy();
    expect(pedidos(api, "POST", RUTA)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Motivo del reemplazo"), { target: { value: "El acta decía otra cosa" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Registrar" }));
    // El rechazo se dice adentro, con qué hacer; la ventana queda abierta.
    expect(await within(ventana).findByText(/Otro presupuesto vigente ya cubre alguno de esos meses/)).toBeTruthy();
    const [pedido] = pedidos(api, "POST", RUTA);
    expect(await pedido!.json()).toMatchObject({
      reemplaza_a: P1, motivo: "El acta decía otra cosa",
      rubros: [{ numero: 1, importe: "1500.00" }, { numero: 3, importe: "300.00" }],
    });  // prettier-ignore
  });

  it("sin rubros numerados en las liquidaciones no ofrece registrar aunque tenga la facultad", async () => {
    const sinNumeros: Panel = { ...CONSEJO, rubros_liquidados: CONSEJO.rubros_liquidados.map((r) => ({ ...r, numero: null })) };
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(sinNumeros), [PRESUPUESTOS]: () => Response.json(listaPresupuestos([], true)) }));
    abrir();
    await screen.findByRole("region", { name: TITULO_DESVIOS });
    await waitFor(() => expect(screen.queryByText("Cargando los presupuestos…")).toBeNull());
    expect(screen.queryByRole("button", { name: REGISTRAR })).toBeNull();
  });
});
