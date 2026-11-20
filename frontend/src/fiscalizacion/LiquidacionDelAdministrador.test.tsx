import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { SIN_CONEXION } from "../fallas";
import { ContextoVersion } from "../version";
import {
  aImporte,
  CONFIRMAR_DATO,
  CONFIRMAR_LIQUIDACION,
  CORREGIR_DATO,
  DESCARTAR_LIQUIDACION,
  EN_ANTIVIRUS,
  enPesos,
  FALTA_MOTIVO,
  hojasDe,
  RUTA_LIQUIDACION_ADMINISTRADOR,
  SUBIR,
} from "./LiquidacionDelAdministrador";

// Liquidación del administrador (H5-3, app 0115). Datos SINTETICOS: ningún edificio ni importe real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const I1 = "22222222-2222-4222-8222-222222222222";
const D1 = "33333333-3333-4333-8333-333333333333";
const V1 = "44444444-4444-4444-8444-444444444444";
const F1 = "55555555-5555-4555-8555-555555555555";
const F2 = "66666666-6666-4666-8666-666666666666";
const PERSONA = "88888888-8888-4888-8888-888888888888";
const BASE = `/api/v1/consorcios/${C1}`;

type Importacion = components["schemas"]["ImportacionLiquidacion"];
type Campo = components["schemas"]["CampoPreliminar"];

const campo = (id: string, orden: number, extra: Partial<Campo>): Campo => ({
  id, orden, tipo: "gasto", clave: `gasto:1:${orden}`, valor: {}, confianza: "1.00", origen: "determinista",
  pagina: 1, renglon: "269.0", unidad_id: null, estado: "pendiente", valor_corregido: null,
  fragmento: null, confirmado_por: null, confirmado_en: null, ...extra,
});

const LUZ = campo(F1, 0, {
  valor: { rubro: 1, concepto: "Luz SINTETICA", importes: { "Gastos Comunes": "1000.00" }, total: "1000.00" },
  fragmento: "Luz SINTETICA 1.000,00 1.000,00",
});
const UNIDAD = campo(F2, 1, {
  tipo: "unidad", clave: "unidad:1", valor: { uf: "1", depto: "PB-A" }, origen: "ia", confianza: "0.90",
  fragmento: "UF 1 PB-A 750,25",
});
const IMPORTACION: Importacion = {
  id: I1, documento_id: D1, version_documento_id: V1, subida_por: PERSONA, creada_en: "2026-11-20T12:00:00Z",
  estado: "para_confirmar", formato: "mis_expensas", periodo: "2026-09-01", motivo: null, diferencias: [],
  leida_en: "2026-11-20T12:00:10Z", decidida_por: null, decidida_en: null, motivo_descarte: null,
  campos: [LUZ, UNIDAD],
};
const RESUMEN = {
  id: I1, documento_id: D1, version_documento_id: V1, subida_por: PERSONA, creada_en: "2026-11-20T12:00:00Z",
  estado: "para_confirmar", formato: "mis_expensas", periodo: "2026-09-01", motivo: null, diferencias: 0,
  campos: 2, pendientes: 2, decidida_por: null, decidida_en: null, motivo_descarte: null,
};

type Respuestas = Record<string, () => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const LISTA = () => Response.json([RESUMEN]);
const DETALLE = (i: Importacion = IMPORTACION) => () => Response.json(i);
const DECISION = `POST ${BASE}/liquidaciones-importadas/${I1}/campos/${F1}/decision`;

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_LIQUIDACION_ADMINISTRADOR]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
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

async function abrirLaImportacion() {
  fireEvent.click(await screen.findByRole("button", { name: /Período 2026-09/ }));
  return screen.findByRole("list", { name: "Datos leídos" });
}

describe("importes", () => {
  it("se leen en pesos y lo escrito vuelve a texto decimal", () => {
    expect(enPesos("1234567.50")).toBe("$ 1.234.567,50");
    expect(enPesos("-10.00")).toBe("-$ 10,00");
    expect(aImporte("1.234,5")).toBe("1234.50");
    expect(aImporte("1234.50")).toBe("1234.50");
    expect(aImporte("-10")).toBe("-10.00");
    expect(aImporte("12,345")).toBeNull();
    expect(aImporte("doce")).toBeNull();
  });

  it("se corrigen los textos y los importes del dato, no los números de orden", () => {
    expect(hojasDe(LUZ.valor).map((h) => [h.camino.join("."), h.importe])).toEqual([
      ["concepto", false],
      ["importes.Gastos Comunes", true],
      ["total", true],
    ]);
  });
});

describe("pantalla /importar-liquidacion", () => {
  it("lista las importaciones, abre una y muestra cada dato con el renglón del documento", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
    }));  // prettier-ignore
    const { container } = abrir();
    expect(await screen.findByText("2 de 2 datos sin decidir")).toBeTruthy();
    const datos = await abrirLaImportacion();
    expect(within(datos).getByRole("button", { name: /Gasto · Luz SINTETICA: \$ 1\.000,00/ })).toBeTruthy();
    expect(within(datos).getByRole("button", { name: /Leído con IA/ })).toBeTruthy();
    // Con datos pendientes no se ofrece confirmar la liquidación entera (C9: nada que se sabe que falla).
    expect(screen.queryByRole("button", { name: CONFIRMAR_LIQUIDACION })).toBeNull();
    expect(screen.getByText("Quedan 2 datos sin decidir.")).toBeTruthy();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).getByText("Página 1: «Luz SINTETICA 1.000,00 1.000,00»")).toBeTruthy();
    expect(within(ficha).getByText("100 %")).toBeTruthy();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("confirmar un dato lo manda sin valor corregido y muestra la importación como queda", async () => {
    const confirmado = { ...IMPORTACION, campos: [{ ...LUZ, estado: "confirmado" as const }, UNIDAD] };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [DECISION]: DETALLE(confirmado),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Gasto" })).getByRole("button", { name: CONFIRMAR_DATO }));
    await waitFor(() => expect(pedidos(api, "POST", DECISION.split(" ")[1] ?? "")).toHaveLength(1));
    const [pedido] = pedidos(api, "POST", DECISION.split(" ")[1] ?? "");
    expect(await pedido?.json()).toEqual({ decision: "confirmado", valor_corregido: null });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByText("Gasto: confirmado.")).toBeTruthy();
    expect(screen.getByText("Quedan 1 datos sin decidir.")).toBeTruthy();
  });

  it("la ficha muestra cada valor del dato y, corregido, cada cambio al lado de lo leído", async () => {
    const saldo = campo(F2, 1, {
      tipo: "saldo_unidad", clave: "saldo_unidad:1", estado: "corregido",
      valor: { uf: "1", depto: "PB-A", saldo_anterior: "500.00", pagos: "100.00", deuda: "400.00", interes: "4.00", disponible: "0.00" },
      valor_corregido: { uf: "1", depto: "PB-A", saldo_anterior: "500.00", pagos: "150.00", deuda: "400.00", interes: "4.00", disponible: "0.00" },
    });  // prettier-ignore
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, campos: [LUZ, saldo] }),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Saldo de la unidad/ }));
    const ficha = await screen.findByRole("dialog", { name: "Saldo de la unidad" });
    const valor = (etiqueta: string) => within(ficha).getByText(etiqueta).nextElementSibling?.textContent;
    expect(valor("Pagos")).toBe("$ 100,00 → corregido: $ 150,00");
    expect(valor("Deuda")).toBe("$ 400,00");
    expect(valor("Interés")).toBe("$ 4,00");
    expect(valor("Disponible")).toBe("$ 0,00");
    expect(valor("Unidad funcional")).toBe("1");
  });

  it("un dato que el servidor no deja decidir dice por qué en la ficha", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [DECISION]: () => Response.json({ codigo: "sin_permiso", mensaje: "sin permiso" }, { status: 403 }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).queryByRole("alert")).toBeNull();  // control: antes del pedido no hay falla
    fireEvent.click(within(ficha).getByRole("button", { name: CONFIRMAR_DATO }));
    expect((await within(ficha).findByRole("alert")).textContent).toBe("Esta cuenta no tiene la facultad para esto en este edificio.");
  });

  it("si la confirmación de la liquidación no llega, lo dice", async () => {
    const decidida = { ...IMPORTACION, campos: [{ ...LUZ, estado: "confirmado" as const }, { ...UNIDAD, estado: "descartado" as const }] };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(decidida),
      [`POST ${BASE}/liquidaciones-importadas/${I1}/confirmacion`]: () => Promise.reject(new TypeError("Failed to fetch")),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(screen.queryByText(SIN_CONEXION)).toBeNull();  // control
    fireEvent.click(await screen.findByRole("button", { name: CONFIRMAR_LIQUIDACION }));
    expect(await screen.findByText(SIN_CONEXION)).toBeTruthy();
  });

  it("corregir un importe manda el valor con la misma forma, en texto decimal", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [DECISION]: DETALLE(),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Gasto" })).getByRole("button", { name: CORREGIR_DATO }));
    const ventana = await screen.findByRole("dialog", { name: "Corregir: Gasto" });
    // Un importe mal escrito se marca en su campo y no se manda nada.
    fireEvent.change(within(ventana).getByLabelText("Total"), { target: { value: "mil" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Guardar la corrección" }));
    expect(await within(ventana).findByText("Un importe va con dos decimales, por ejemplo 1.234,50.")).toBeTruthy();
    expect(pedidos(api, "POST", DECISION.split(" ")[1] ?? "")).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Total"), { target: { value: "1.000,05" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Guardar la corrección" }));
    await waitFor(() => expect(pedidos(api, "POST", DECISION.split(" ")[1] ?? "")).toHaveLength(1));
    const [pedido] = pedidos(api, "POST", DECISION.split(" ")[1] ?? "");
    expect(await pedido?.json()).toEqual({
      decision: "corregido",
      valor_corregido: { rubro: 1, concepto: "Luz SINTETICA", importes: { "Gastos Comunes": "1000.00" }, total: "1000.05" },
    });
  });

  it("sin datos pendientes ofrece confirmar la liquidación y la confirma", async () => {
    const decidida = { ...IMPORTACION, campos: [{ ...LUZ, estado: "confirmado" as const }, { ...UNIDAD, estado: "descartado" as const }] };
    const CONFIRMACION = `${BASE}/liquidaciones-importadas/${I1}/confirmacion`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(decidida),
      [`POST ${CONFIRMACION}`]: DETALLE({ ...decidida, estado: "confirmada", decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await abrirLaImportacion();
    fireEvent.click(await screen.findByRole("button", { name: CONFIRMAR_LIQUIDACION }));
    await waitFor(() => expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(1));
    expect(await screen.findByText(/Se confirmó la liquidación/)).toBeTruthy();
    // Confirmada, ya no se decide nada.
    expect(screen.queryByRole("button", { name: CONFIRMAR_LIQUIDACION })).toBeNull();
    expect(screen.queryByRole("button", { name: DESCARTAR_LIQUIDACION })).toBeNull();
  });

  it("descartar la liquidación pide el motivo", async () => {
    const DESCARTE = `${BASE}/liquidaciones-importadas/${I1}/descarte`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [`POST ${DESCARTE}`]: DETALLE({ ...IMPORTACION, estado: "descartada", decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z", motivo_descarte: "Es de otro edificio" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await abrirLaImportacion();
    fireEvent.click(await screen.findByRole("button", { name: DESCARTAR_LIQUIDACION }));
    const ventana = await screen.findByRole("dialog", { name: DESCARTAR_LIQUIDACION });
    fireEvent.click(within(ventana).getByRole("button", { name: "Confirmar el descarte" }));
    expect(await within(ventana).findByText(FALTA_MOTIVO)).toBeTruthy();
    expect(pedidos(api, "POST", DESCARTE)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Motivo"), { target: { value: "Es de otro edificio" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Confirmar el descarte" }));
    await waitFor(() => expect(pedidos(api, "POST", DESCARTE)).toHaveLength(1));
    expect(await pedidos(api, "POST", DESCARTE)[0]?.json()).toEqual({ motivo: "Es de otro edificio" });
    expect(await screen.findByText("Motivo del descarte: Es de otro edificio")).toBeTruthy();
  });

  it("subir el PDF crea el documento de la categoría, espera el antivirus y pide importarlo", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([]),
      [`POST ${BASE}/documentos`]: () => Response.json({ id: D1 }, { status: 201 }),
      [`POST ${BASE}/documentos/${D1}/versiones`]: () => Response.json({ id: V1 }, { status: 201 }),
      [`GET ${BASE}/documentos/${D1}/versiones/${V1}/estado`]: () => Response.json({ estado: "disponible", motivo_rechazo: null }),
      [`POST ${BASE}/liquidaciones-importadas`]: () => Response.json({ id: I1 }, { status: 201 }),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, estado: "leyendo", formato: null, periodo: null, leida_en: null, campos: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByText("Todavía no se subió ninguna liquidación del administrador.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: SUBIR }));
    const ventana = await screen.findByRole("dialog", { name: SUBIR });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir" }));
    expect(await within(ventana).findByText("Falta elegir el archivo.")).toBeTruthy();
    const pdf = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "liquidacion.pdf", { type: "application/pdf" });
    fireEvent.change(within(ventana).getByLabelText("Archivo de la liquidación"), { target: { files: [pdf] } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir" }));
    expect(await screen.findByText(EN_ANTIVIRUS)).toBeTruthy();
    const [creado] = pedidos(api, "POST", `${BASE}/documentos`);
    expect(await creado?.json()).toMatchObject({ categoria: "liquidaciones_del_administrador" });
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/liquidaciones-importadas`)).toHaveLength(1), { timeout: 6000 });
    expect(await pedidos(api, "POST", `${BASE}/liquidaciones-importadas`)[0]?.json()).toEqual({ documento_id: D1, version_documento_id: V1 });
    expect((await screen.findAllByText("Se está leyendo la liquidación.")).length).toBeGreaterThan(0);
  }, 15000);

  it("sin `liquidacion.importar` en ningún edificio, la dirección lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("fiscalizacion.ver_resumen"),
    }));  // prettier-ignore
    abrir();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Liquidación del administrador" })).toBeNull());
    expect(screen.queryByRole("button", { name: SUBIR })).toBeNull();
  });
});
