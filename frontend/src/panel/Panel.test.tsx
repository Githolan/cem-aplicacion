import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { gastoPorMes, gastoPorRubro, mesesConPagos, type Panel, SIN_RUBRO } from "./datos";

// Panel del Consejo (app 0094). Datos ficticios: ningún proveedor ni importe real.

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
      importe_centavos: 100000, moneda: "ARS", actualizada_en: "2026-10-31T12:00:00Z" },
    { orden_id: O2, estado: "aprobada", proveedor: "Ascensores Ficticios SA", concepto: "Abono de noviembre",
      importe_centavos: 80000, moneda: "ARS", actualizada_en: "2026-11-05T12:00:00Z" },
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

describe("panel del Consejo", () => {
  it("muestra cada gráfico con su base, su tabla y los enlaces a los registros", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    const { container } = abrir();

    const estados = await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    expect(within(estados).getByText(/No incluye borradores/)).toBeTruthy();
    // En el orden del circuito y con el valor escrito: no depende del color.
    const filas = within(estados).getAllByRole("row").slice(1);
    expect(filas.map((f) => f.textContent)).toEqual([
      expect.stringMatching(/^En revisión1 orden, \$\s?500,00$/),
      expect.stringMatching(/^Pagada3 órdenes, \$\s?3\.000,00$/),
    ]);
    expect(within(estados).getByRole("link", { name: "Consultar los pagos" }).getAttribute("href")).toBe("/pagos/consulta");

    const porMes = screen.getByRole("region", { name: "Gasto pagado por mes" });
    expect(within(porMes).getByRole("rowheader", { name: "noviembre de 2026" })).toBeTruthy();
    const porRubro = screen.getByRole("region", { name: "Gasto pagado por rubro" });
    expect(within(porRubro).getAllByRole("rowheader").map((c) => c.textContent)).toEqual(["Limpieza", SIN_RUBRO]);
    fireEvent.change(within(porRubro).getByLabelText("Mes"), { target: { value: "2026-10-01" } });
    expect(within(porRubro).getAllByRole("rowheader").map((c) => c.textContent)).toEqual(["Limpieza"]);
    const fondo = screen.getByRole("region", { name: "Pagos con el fondo de reserva" });
    expect(within(fondo).getByText(/No es el saldo del fondo/)).toBeTruthy();

    const faltantes = screen.getByRole("region", { name: "Comprobantes faltantes" });
    const lista = within(faltantes).getByRole("table", { name: "Órdenes sin factura" });
    expect(within(lista).getByRole("link", { name: "Limpieza Ficticia SRL" }).getAttribute("href")).toBe(`/pagos/${O1}`);
    expect(within(lista).getByRole("link", { name: "Ascensores Ficticios SA" }).getAttribute("href")).toBe(`/pagos/${O2}`);

    for (const detalle of container.querySelectorAll("details")) detalle.open = true;
    await sinViolaciones(container);
  });

  it("sin acceso a los pagos no ofrece los enlaces a los registros", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS), [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir();
    const faltantes = await screen.findByRole("region", { name: "Comprobantes faltantes" });
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
    expect(await screen.findByText("No pudimos cargar los datos de esta pantalla.")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).not.toBe("");
    await sinViolaciones(container);
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("region", { name: "Comprobantes faltantes" })).toBeTruthy();
    expect(screen.queryByText("No pudimos cargar los datos de esta pantalla.")).toBeNull();
  });

  it("sin la acción del panel no aparece en el inicio y la dirección lleva al inicio", async () => {
    const api = backend({ [ACCIONES]: alcanza("documentos.ver"), [PANEL]: () => Response.json(DATOS) });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    await screen.findByRole("link", { name: "Documentos del edificio" });
    expect(screen.queryByRole("link", { name: "Panel del Consejo" })).toBeNull();
    expect(api.mock.calls.some((c) => new URL((c as unknown as [Request])[0].url).pathname.endsWith("/panel"))).toBe(false);
  });

  it("con la acción aparece en el inicio", async () => {
    vi.stubGlobal("fetch", backend({ [ACCIONES]: alcanza("fiscalizacion.ver_panel") }));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Panel del Consejo" })).getAttribute("href")).toBe("/panel");
  });

  it("si la API lo niega lleva al inicio: decide el servidor", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => error("sin_permiso", 403) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Panel del Consejo" })).toBeNull());
  });
});

describe("informe de fricción, tanda 7", () => {
  it("AC-09: en /panel sigue la navegación de la aplicación, con el Panel en ella", async () => {
    vi.stubGlobal("fetch", backend({ [PANEL]: () => Response.json(DATOS) }));
    abrir();

    await screen.findByRole("region", { name: "Órdenes de pago por estado" });
    const navegacion = await screen.findByRole("navigation", { name: "Secciones" });
    expect(within(navegacion).getByRole("link", { name: "Panel del Consejo" }).getAttribute("href")).toBe("/panel");
  });
});
