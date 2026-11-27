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
  ACEPTAR,
  aceptarLa,
  CORREGIR_DATO,
  DESCARTAR_DATO,
  VOLVER_AL_LEIDO,
  enPalabras,
  enPorcentaje,
  ELEGIR_COEFICIENTES,
  FALTA_ESQUEMA,
  PREPARAR_SOLICITUD,
  SIN_GRUPOS,
  textoDeSolicitud,
  DESCARTAR_LIQUIDACION,
  EN_ANTIVIRUS,
  IMPORTAR_DE_NUEVO,
  NUMERO_MAL,
  enPesos,
  FALTA_MOTIVO,
  hojasDe,
  REEMPLAZAR_OFICIAL,
  REEMPLAZARLA,
  RUTA_LIQUIDACION_ADMINISTRADOR,
  SUBIR,
  textoOficial,
  textoUnidadRepetida,
  ENLACES,
  NO_SE_TRAJO,
  SOLO_HTTPS,
  TRAER,
  TRANSCRITO,
} from "./LiquidacionDelAdministrador";
import { ENTENDIDO, PREGUNTAR, type Control } from "./ResumenLiquidacion";

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
  reemplaza_a: null, motivo_reemplazo: null,
  campos: [LUZ, UNIDAD],
  enlaces: [],
  controles: [],
};
const ACEPTAR_SEPTIEMBRE = aceptarLa("2026-09-01");
const SUBIR_EN = `${SUBIR} de Edificio SINTETICO`;
const RESUMEN = {
  id: I1, documento_id: D1, version_documento_id: V1, subida_por: PERSONA, creada_en: "2026-11-20T12:00:00Z",
  estado: "para_confirmar", formato: "mis_expensas", periodo: "2026-09-01", motivo: null, diferencias: 0,
  campos: 2, pendientes: 2, decidida_por: null, decidida_en: null, motivo_descarte: null,
  reemplaza_a: null, motivo_reemplazo: null,
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
    // Más de 15 cifras: sin pasar por `Number`, que redondearía a 9007199254740992.
    expect(aImporte("9.007.199.254.740.993,00")).toBe("9007199254740993.00");
    expect(aImporte("007,5")).toBe("7.50");
  });

  it("se corrige cada valor del dato, con su forma", () => {
    expect(hojasDe(LUZ.valor).map((h) => [h.camino.join("."), h.forma])).toEqual([
      ["rubro", "numero"],
      ["concepto", "texto"],
      ["importes.Gastos Comunes", "importe"],
      ["total", "importe"],
    ]);
    // Un importe vacío sigue siendo importe por su clave; una lista se escribe separada por comas.
    expect(hojasDe({ nombre: null, total: null, grupos: { A: null }, vencimientos: ["2026-10-10", "2026-10-20"] })).toEqual([
      { camino: ["nombre"], etiqueta: "Nombre", leido: null, forma: "texto" },
      { camino: ["total"], etiqueta: "Total", leido: null, forma: "importe" },
      { camino: ["grupos", "A"], etiqueta: "Grupos · A", leido: null, forma: "importe" },
      { camino: ["vencimientos"], etiqueta: "Vencimientos", leido: "2026-10-10, 2026-10-20", forma: "lista" },
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
    expect(await screen.findByText("2 datos leídos, para revisar y aceptar")).toBeTruthy();
    // El edificio, a la vista antes de subir (RL4-A).
    expect(screen.getByText("Edificio SINTETICO").closest("p")?.textContent).toBe("Edificio: Edificio SINTETICO");
    const datos = await abrirLaImportacion();
    expect(within(datos).getByRole("button", { name: /Gasto · Luz SINTETICA: \$ 1\.000,00/ })).toBeTruthy();
    expect(within(datos).getByRole("button", { name: /Leído con IA/ })).toBeTruthy();
    // Con datos sin mirar uno por uno, la liquidación se acepta entera (RL1-A).
    expect(screen.getByRole("button", { name: ACEPTAR_SEPTIEMBRE })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Septiembre de 2026 · Edificio SINTETICO" })).toBeTruthy();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).getByText("Página 1: «Luz SINTETICA 1.000,00 1.000,00»")).toBeTruthy();
    expect(within(ficha).getByText("Se reparte en Gastos Comunes").nextElementSibling?.textContent).toBe("$ 1.000,00");
    // Sin confianza a la vista en lo leído sin IA, y sin «Confirmar» de a uno.
    expect(within(ficha).queryByText("100 %")).toBeNull();
    expect(within(ficha).queryByRole("button", { name: "Confirmar" })).toBeNull();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("leída de una imagen, lo dice y la ficha avisa que el fragmento es la transcripción de la IA", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([{ ...RESUMEN, formato: "lectura_ia_imagen" }]),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, formato: "lectura_ia_imagen" }),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    expect(screen.getByText(/Leída con IA de una imagen/)).toBeTruthy();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).getByText(`Página 1 (${TRANSCRITO}): «Luz SINTETICA 1.000,00 1.000,00»`)).toBeTruthy();
  });

  it("muestra los enlaces del documento y trae el de https: entra al antivirus y se importa como uno subido", async () => {
    const CON_ENLACES: Importacion = {
      ...IMPORTACION,
      enlaces: [
        { orden: 1, url: "https://comprobantes.prueba/2026-09.pdf", origen: "qr", pagina: 2 },
        { orden: 2, url: "http://viejo.prueba/estado", origen: "texto", pagina: 1 },
      ],
    };
    const TRAER_URL = `${BASE}/liquidaciones-importadas/${I1}/enlaces/1/descarga`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_ENLACES),
      [`POST ${TRAER_URL}`]: () => Response.json({ documento_id: D1, version_documento_id: V1, tipo: "application/pdf", tamano: 10 }, { status: 201 }),
      [`GET ${BASE}/documentos/${D1}/versiones/${V1}/estado`]: () => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await abrirLaImportacion();
    const lista = screen.getByRole("list", { name: ENLACES });
    expect(within(lista).getByText(/comprobantes\.prueba \(QR, página 2\)/)).toBeTruthy();
    // Sin https no se ofrece traerlo (sería un error que se sabe de antemano): solo se dice.
    expect(within(lista).getByText(/viejo\.prueba \(escrito, página 1\)/).textContent).toContain(SOLO_HTTPS);
    expect(within(lista).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(within(lista).getByRole("button", { name: `${TRAER} de comprobantes.prueba` }));
    expect(await screen.findByText(EN_ANTIVIRUS)).toBeTruthy();
    expect(pedidos(api, "POST", TRAER_URL)).toHaveLength(1);
    // Mientras el traído está en el antivirus no se ofrece traer otro.
    await waitFor(() => expect(within(screen.getByRole("list", { name: ENLACES })).queryAllByRole("button")).toHaveLength(0));
  });

  it("con un traído aprobado que espera su importación no se ofrece traer otro", async () => {
    const CON_ENLACES: Importacion = { ...IMPORTACION, enlaces: [{ orden: 1, url: "https://comprobantes.prueba/2026-09.pdf", origen: "qr", pagina: 2 }] };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_ENLACES),
      [`POST ${BASE}/liquidaciones-importadas/${I1}/enlaces/1/descarga`]: () => Response.json({ documento_id: D1, version_documento_id: V1, tipo: "application/pdf", tamano: 10 }, { status: 201 }),
      [`GET ${BASE}/documentos/${D1}/versiones/${V1}/estado`]: () => Response.json({ estado: "disponible", motivo_rechazo: null }),
      [`POST ${BASE}/liquidaciones-importadas`]: () => Promise.reject(new TypeError("Failed to fetch")),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await abrirLaImportacion();
    fireEvent.click(within(screen.getByRole("list", { name: ENLACES })).getByRole("button", { name: `${TRAER} de comprobantes.prueba` }));
    // Aprobado por el antivirus, la importación no llega: queda para reintentar (Codex).
    expect(await screen.findByRole("button", { name: IMPORTAR_DE_NUEVO }, { timeout: 6000 })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: ENLACES })).queryAllByRole("button")).toHaveLength(0);
  }, 15000);

  it("un enlace que el servidor no deja traer dice por qué", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, enlaces: [{ orden: 1, url: "https://listado.prueba/", origen: "anotacion", pagina: 1 }] }),
      [`POST ${BASE}/liquidaciones-importadas/${I1}/enlaces/1/descarga`]: () =>
        Response.json({ codigo: "enlace_no_traido", mensaje: "El enlace no lleva a un PDF, JPEG o PNG (puede ser una página web)." }, { status: 422 }),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    fireEvent.click(screen.getByRole("button", { name: `${TRAER} de listado.prueba` }));
    expect(await screen.findByText(`${NO_SE_TRAJO} El enlace no lleva a un PDF, JPEG o PNG (puede ser una página web).`)).toBeTruthy();
  });

  it("del formato conocido no habla de transcripción", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(screen.getByText(/Formato Mis Expensas/)).toBeTruthy();
    expect(screen.queryByText(new RegExp(TRANSCRITO))).toBeNull();
  });

  it("leído con IA, la ficha dice cómo se leyó y con qué confianza", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Unidad · UF 1 PB-A/ }));
    const ficha = await screen.findByRole("dialog", { name: "Unidad" });
    expect(within(ficha).getByText("Cómo se leyó").nextElementSibling?.textContent).toBe("Con IA, que informó una confianza del 90 %");
  });

  it("la ficha del saldo de una unidad lo dice en palabras, con lo corregido", async () => {
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
    expect(valor("Le cobraban el mes pasado")).toBe("$ 500,00");
    expect(valor("Pagó")).toBe("$ 150,00");
    expect(valor("Quedó debiendo")).toBe("$ 400,00");
    expect(valor("Interés por la deuda")).toBe("$ 4,00");
    expect(valor("Saldo a favor")).toBe("$ 0,00");
    expect(valor("Estado")).toBe("Corregido");
  });

  it("un dato descartado puede volver al valor leído", async () => {
    const descartado = { ...LUZ, estado: "descartado" as const };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, campos: [descartado] }),
      [DECISION]: DETALLE(),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).queryByRole("button", { name: DESCARTAR_DATO })).toBeNull();
    fireEvent.click(within(ficha).getByRole("button", { name: VOLVER_AL_LEIDO }));
    expect(await screen.findByText("Gasto: volvió al valor leído.")).toBeTruthy();
    const [pedido] = pedidos(api, "POST", DECISION.replace("POST ", ""));
    expect(await pedido?.json()).toEqual({ decision: "confirmado", valor_corregido: null });
  });

  it("un dato pendiente no ofrece volver al valor leído", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gasto" });
    expect(within(ficha).getByRole("button", { name: DESCARTAR_DATO })).toBeTruthy();  // control: la ficha tiene sus botones
    expect(within(ficha).queryByRole("button", { name: VOLVER_AL_LEIDO })).toBeNull();
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
    fireEvent.click(within(ficha).getByRole("button", { name: DESCARTAR_DATO }));
    expect((await within(ficha).findByRole("alert")).textContent).toBe("Esta cuenta no tiene la facultad para esto en este edificio.");
  });

  it("si la aceptación de la liquidación no llega, lo dice", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [`POST ${BASE}/liquidaciones-importadas/${I1}/confirmacion`]: () => Promise.reject(new TypeError("Failed to fetch")),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(screen.queryByText(SIN_CONEXION)).toBeNull();  // control
    fireEvent.click(await screen.findByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE })).getByRole("button", { name: ACEPTAR }));
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

  it("corregir el número de rubro y una lista los manda en su forma", async () => {
    const TOTAL = campo(F2, 1, { tipo: "total", clave: "total:gastos", valor: { total: "1000.00", vencimientos: ["2026-10-10"] } });
    const DECISION_TOTAL = `${BASE}/liquidaciones-importadas/${I1}/campos/${F2}/decision`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, campos: [LUZ, TOTAL] }),
      [DECISION]: DETALLE({ ...IMPORTACION, campos: [LUZ, TOTAL] }),
      [`POST ${DECISION_TOTAL}`]: DETALLE({ ...IMPORTACION, campos: [LUZ, TOTAL] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const datos = await abrirLaImportacion();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Gasto" })).getByRole("button", { name: CORREGIR_DATO }));
    const ventana = await screen.findByRole("dialog", { name: "Corregir: Gasto" });
    fireEvent.change(within(ventana).getByLabelText("Número de rubro"), { target: { value: "dos" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Guardar la corrección" }));
    expect(await within(ventana).findByText(NUMERO_MAL)).toBeTruthy();
    expect(pedidos(api, "POST", DECISION.split(" ")[1] ?? "")).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Número de rubro"), { target: { value: "2" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Guardar la corrección" }));
    await waitFor(() => expect(pedidos(api, "POST", DECISION.split(" ")[1] ?? "")).toHaveLength(1));
    const gasto = (await pedidos(api, "POST", DECISION.split(" ")[1] ?? "")[0]?.json()) as { valor_corregido: { rubro: unknown } };
    expect(gasto.valor_corregido.rubro).toBe(2);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Corregir: Gasto" })).toBeNull());
    fireEvent.click(within(await screen.findByRole("list", { name: "Datos leídos" })).getByRole("button", { name: /^Total/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Total" })).getByRole("button", { name: CORREGIR_DATO }));
    const otra = await screen.findByRole("dialog", { name: "Corregir: Total" });
    fireEvent.change(within(otra).getByLabelText("Vencimientos (separados por coma)"), { target: { value: "2026-10-10, 2026-10-25" } });
    fireEvent.click(within(otra).getByRole("button", { name: "Guardar la corrección" }));
    await waitFor(() => expect(pedidos(api, "POST", DECISION_TOTAL)).toHaveLength(1));
    const total = (await pedidos(api, "POST", DECISION_TOTAL)[0]?.json()) as { valor_corregido: unknown };
    expect(total.valor_corregido).toEqual({ total: "1000.00", vencimientos: ["2026-10-10", "2026-10-25"] });
  });

  it("aceptar la liquidación la manda entera, con lo pendiente, y aceptada ya no se decide nada", async () => {
    const CONFIRMACION = `${BASE}/liquidaciones-importadas/${I1}/confirmacion`;
    const aceptada = { ...IMPORTACION, estado: "confirmada" as const, decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z", campos: [{ ...LUZ, estado: "confirmado" as const }, { ...UNIDAD, estado: "confirmado" as const }] };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(),
      [`POST ${CONFIRMACION}`]: DETALLE(aceptada),
      [`GET ${BASE}/liquidaciones-importadas/${I1}/recalculo`]: () => Response.json({ importacion_id: I1, periodo: "2026-09-01", sin_grupos: true, grupos: [], esquemas: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await abrirLaImportacion();
    fireEvent.click(await screen.findByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    const ventana = await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE });
    expect(within(ventana).getByText(/Queda registrado quién la aceptó y cuándo/)).toBeTruthy();
    expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(0);  // control: abrir la ventana no manda nada
    fireEvent.click(within(ventana).getByRole("button", { name: ACEPTAR }));
    await waitFor(() => expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(1));
    expect(await pedidos(api, "POST", CONFIRMACION)[0]?.json()).toEqual({ aceptar_pendientes: true });
    expect(await screen.findByText(/Se aceptó la liquidación/)).toBeTruthy();
    expect(screen.getByText(/aceptada el/)).toBeTruthy();
    // Aceptada, ya no se decide nada.
    expect(screen.queryByRole("button", { name: ACEPTAR_SEPTIEMBRE })).toBeNull();
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
    const ventana = await screen.findByRole("dialog", { name: SUBIR_EN });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir" }));
    expect(await within(ventana).findByText("Falta elegir el archivo.")).toBeTruthy();
    // Acepta el PDF y también una foto (H5-5).
    expect((within(ventana).getByLabelText("Archivo de la liquidación") as HTMLInputElement).accept).toBe(
      ".pdf,application/pdf,.jpg,.jpeg,image/jpeg,.png,image/png",
    );
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

  it("mientras un archivo está en el antivirus no se ofrece subir otro", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([]),
      [`POST ${BASE}/documentos`]: () => Response.json({ id: D1 }, { status: 201 }),
      [`POST ${BASE}/documentos/${D1}/versiones`]: () => Response.json({ id: V1 }, { status: 201 }),
      [`GET ${BASE}/documentos/${D1}/versiones/${V1}/estado`]: () => Response.json({ estado: "en_cuarentena", motivo_rechazo: null }),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText("Todavía no se subió ninguna liquidación del administrador.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: SUBIR }));
    const ventana = await screen.findByRole("dialog", { name: SUBIR_EN });
    const pdf = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "liquidacion.pdf", { type: "application/pdf" });
    fireEvent.change(within(ventana).getByLabelText("Archivo de la liquidación"), { target: { files: [pdf] } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir" }));
    expect(await screen.findByText(EN_ANTIVIRUS)).toBeTruthy();
    expect(screen.queryByRole("button", { name: SUBIR })).toBeNull();
  });

  it("si el pedido de importar no llega, el archivo subido queda para reintentar con la misma clave", async () => {
    let intentos = 0;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([]),
      [`POST ${BASE}/documentos`]: () => Response.json({ id: D1 }, { status: 201 }),
      [`POST ${BASE}/documentos/${D1}/versiones`]: () => Response.json({ id: V1 }, { status: 201 }),
      [`GET ${BASE}/documentos/${D1}/versiones/${V1}/estado`]: () => Response.json({ estado: "disponible", motivo_rechazo: null }),
      [`POST ${BASE}/liquidaciones-importadas`]: () => {
        intentos += 1;
        return intentos === 1 ? Promise.reject(new TypeError("Failed to fetch")) : Response.json({ id: I1 }, { status: 201 });
      },
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, estado: "leyendo", formato: null, periodo: null, leida_en: null, campos: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    expect(await screen.findByText("Todavía no se subió ninguna liquidación del administrador.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: SUBIR }));
    const ventana = await screen.findByRole("dialog", { name: SUBIR_EN });
    const pdf = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "liquidacion.pdf", { type: "application/pdf" });
    fireEvent.change(within(ventana).getByLabelText("Archivo de la liquidación"), { target: { files: [pdf] } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Subir" }));
    expect(await screen.findByText(EN_ANTIVIRUS)).toBeTruthy();
    expect(await screen.findByText(SIN_CONEXION, {}, { timeout: 6000 })).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: IMPORTAR_DE_NUEVO }));
    await waitFor(() => expect(pedidos(api, "POST", `${BASE}/liquidaciones-importadas`)).toHaveLength(2));
    const [primero, segundo] = pedidos(api, "POST", `${BASE}/liquidaciones-importadas`);
    expect(segundo?.headers.get("Idempotency-Key")).toBe(primero?.headers.get("Idempotency-Key"));
    expect((await screen.findAllByText("Se está leyendo la liquidación.")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: IMPORTAR_DE_NUEVO })).toBeNull();
  }, 15000);

  it("si el período ya tiene su liquidación oficial, aceptar ofrece reemplazarla con el motivo, o Cancelar", async () => {
    const I2 = "77777777-7777-4777-8777-777777777777";
    const decidida = { ...IMPORTACION, campos: [{ ...LUZ, estado: "confirmado" as const }, { ...UNIDAD, estado: "confirmado" as const }] };
    const OFICIAL = { ...RESUMEN, id: I2, estado: "confirmada", pendientes: 0, decidida_por: PERSONA, decidida_en: "2026-10-05T15:00:00Z" };
    const CONFIRMACION = `${BASE}/liquidaciones-importadas/${I1}/confirmacion`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([RESUMEN, OFICIAL]),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(decidida),
      [`POST ${CONFIRMACION}`]: DETALLE({ ...decidida, estado: "confirmada", decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z", reemplaza_a: I2, motivo_reemplazo: "Mandó otra versión" }),
      [`GET ${BASE}/liquidaciones-importadas/${I1}/recalculo`]: () => Response.json({ importacion_id: I1, periodo: "2026-09-01", sin_grupos: true, grupos: [], esquemas: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Período 2026-09.*Para confirmar/ }));
    await screen.findByRole("list", { name: "Datos leídos" });
    fireEvent.click(await screen.findByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    let ventana = await screen.findByRole("dialog", { name: REEMPLAZAR_OFICIAL });
    // Dice cuál es la oficial: el período y cuándo se confirmó.
    expect(within(ventana).getByText(textoOficial("2026-09-01", "2026-10-05T15:00:00Z"))).toBeTruthy();
    expect(within(ventana).getByText(/septiembre de 2026/)).toBeTruthy();
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: REEMPLAZAR_OFICIAL })).toBeNull());
    expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    ventana = await screen.findByRole("dialog", { name: REEMPLAZAR_OFICIAL });
    fireEvent.click(within(ventana).getByRole("button", { name: REEMPLAZARLA }));
    expect(await within(ventana).findByText(FALTA_MOTIVO)).toBeTruthy();
    expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Motivo del reemplazo"), { target: { value: " Mandó otra versión " } });
    fireEvent.click(within(ventana).getByRole("button", { name: REEMPLAZARLA }));
    await waitFor(() => expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(1));
    expect(await pedidos(api, "POST", CONFIRMACION)[0]?.json()).toEqual({ reemplaza_a: I2, motivo: "Mandó otra versión", aceptar_pendientes: true });
    expect(await screen.findByText(/reemplazó a la anterior, que queda como historia/)).toBeTruthy();
    expect(screen.getByText(/Motivo: Mandó otra versión/)).toBeTruthy();
  }, 15000);

  it("sin otra oficial del período, aceptar no ofrece reemplazar y va solo con lo pendiente", async () => {
    const decidida = { ...IMPORTACION, campos: [{ ...LUZ, estado: "confirmado" as const }, { ...UNIDAD, estado: "confirmado" as const }] };
    const CONFIRMACION = `${BASE}/liquidaciones-importadas/${I1}/confirmacion`;
    // Una confirmada de otro período no cuenta.
    const OTRA = { ...RESUMEN, id: "77777777-7777-4777-8777-777777777777", periodo: "2026-08-01", estado: "confirmada", decidida_en: "2026-09-05T15:00:00Z" };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([RESUMEN, OTRA]),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(decidida),
      [`POST ${CONFIRMACION}`]: DETALLE({ ...decidida, estado: "confirmada", decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z" }),
      [`GET ${BASE}/liquidaciones-importadas/${I1}/recalculo`]: () => Response.json({ importacion_id: I1, periodo: "2026-09-01", sin_grupos: true, grupos: [], esquemas: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Período 2026-09/ }));
    fireEvent.click(await screen.findByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    expect(screen.queryByRole("dialog", { name: REEMPLAZAR_OFICIAL })).toBeNull();
    fireEvent.click(within(await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE })).getByRole("button", { name: ACEPTAR }));
    await waitFor(() => expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(1));
    expect(await pedidos(api, "POST", CONFIRMACION)[0]?.json()).toEqual({ aceptar_pendientes: true });
  });

  it("si una corrección deja dos datos del mismo tipo en una unidad, el aviso dice cuál y cuáles, y la importación sigue para corregir", async () => {
    const leido = campo(F1, 1, { tipo: "importe_unidad", clave: "importe_unidad:001", estado: "confirmado", valor: { uf: "001", depto: "PB-A", total: "750.25" } });
    const movido = campo(F2, 2, {
      tipo: "importe_unidad", clave: "importe_unidad:002", estado: "corregido",
      valor: { uf: "002", depto: "1-A", total: "1326.00" }, valor_corregido: { uf: "002", depto: "PB-A", total: "1326.00" },
    });  // prettier-ignore
    const decidida = { ...IMPORTACION, campos: [leido, movido] };
    const CONFIRMACION = `${BASE}/liquidaciones-importadas/${I1}/confirmacion`;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([{ ...RESUMEN, pendientes: 0 }]),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(decidida),
      [`POST ${CONFIRMACION}`]: () => Response.json({
        codigo: "unidad_repetida", mensaje: "Dos datos del mismo tipo quedan en una misma unidad.",
        detalle: { unidad: "PB-A", tipo: "importe_unidad", campos: [1, 2] }, id_correlacion: "x",
      }, { status: 409 }),
    });  // prettier-ignore
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Período 2026-09/ }));
    await screen.findByRole("list", { name: "Datos leídos" });
    const lecturas = pedidos(api, "GET", `${BASE}/liquidaciones-importadas/${I1}`).length;
    fireEvent.click(await screen.findByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE })).getByRole("button", { name: ACEPTAR }));
    await waitFor(() => expect(pedidos(api, "POST", CONFIRMACION)).toHaveLength(1));
    const aviso = await screen.findByText(textoUnidadRepetida({ unidad: "PB-A", tipo: "importe_unidad", campos: [1, 2] }, decidida.campos));
    // La unidad y los dos datos, cada uno como se ve en la lista.
    expect(aviso.textContent).toMatch(/unidad PB-A \(«UF 001 PB-A: .+» y «UF 002 PB-A: .+»\)\. Corrija uno de los dos o descarte/);
    // No cambió lo visto: no se relee la importación y se sigue pudiendo corregir o descartar.
    expect(pedidos(api, "GET", `${BASE}/liquidaciones-importadas/${I1}`)).toHaveLength(lecturas);
    expect(screen.getByRole("button", { name: ACEPTAR_SEPTIEMBRE })).toBeTruthy();
    expect(screen.getByRole("button", { name: DESCARTAR_LIQUIDACION })).toBeTruthy();
  });

  it("una reemplazada queda como historia, con el motivo del reemplazo", async () => {
    const I2 = "77777777-7777-4777-8777-777777777777";
    const VIEJA = { ...RESUMEN, estado: "reemplazada", pendientes: 0, decidida_por: PERSONA, decidida_en: "2026-10-05T15:00:00Z" };
    const NUEVA = { ...RESUMEN, id: I2, estado: "confirmada", pendientes: 0, decidida_por: PERSONA, decidida_en: "2026-10-20T15:00:00Z", reemplaza_a: I1, motivo_reemplazo: "Corrigió el rubro 2" };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: () => Response.json([NUEVA, VIEJA]),
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE({ ...IMPORTACION, estado: "reemplazada", decidida_por: PERSONA, decidida_en: "2026-10-05T15:00:00Z", campos: [{ ...LUZ, estado: "confirmado" }] }),
      [`GET ${BASE}/liquidaciones-importadas/${I1}/recalculo`]: () => Response.json({ importacion_id: I1, periodo: "2026-09-01", sin_grupos: true, grupos: [], esquemas: [] }),
    }));  // prettier-ignore
    abrir();
    const fila = await screen.findByRole("button", { name: /Reemplazada/ });
    expect(fila.textContent).toContain("Motivo del reemplazo: Corrigió el rubro 2");
    fireEvent.click(fila);
    expect(await screen.findByText(/Reemplazada por la liquidación confirmada el .*\. Motivo: Corrigió el rubro 2/)).toBeTruthy();
    // Historia: ni se confirma ni se descarta.
    expect(screen.queryByRole("button", { name: ACEPTAR_SEPTIEMBRE })).toBeNull();
    expect(screen.queryByRole("button", { name: DESCARTAR_LIQUIDACION })).toBeNull();
  });

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

// El resumen con los controles (rediseño de la confirmación, RL1-A a RL3-A). Datos SINTETICOS.

const BIEN: Control = { numero: 1, clave: "gasto_suma_grupos", estado: "bien", titulo: "Cada gasto suma lo de sus grupos", texto: "El gasto suma lo de sus grupos.", lineas: [] };
const PORCENTAJE: Control = {
  numero: 7, clave: "porcentaje_grupo", estado: "para_mirar", titulo: "Lo que paga cada unidad coincide con su porcentaje",
  texto: "Hay unidades que no pagan su porcentaje del total del grupo.",
  lineas: ["PB-A (UF 1) paga $ 750 de Gastos Comunes: $ 250 más de lo que le toca por su 50 % de $ 1.000 ($ 500)."],
};  // prettier-ignore
const DEUDA: Control = {
  numero: 8, clave: "deuda_unidad", estado: "para_mirar", titulo: "Deuda e interés de cada unidad",
  texto: "Hay unidades que deben del mes anterior: su total incluye la deuda y el interés.",
  lineas: ["PB-A (UF 1) debe $ 100 del mes anterior, más $ 3 de interés (3 % de la deuda)."],
};  // prettier-ignore
const SIN_ANTERIOR: Control = { numero: 9, clave: "saldo_anterior", estado: "no_aplica", titulo: "El saldo anterior es lo que pagaba el mes pasado", texto: "No hay liquidación aceptada del mes anterior en CEM para comparar.", lineas: [] };
const TOTAL_GASTOS = campo("99999999-9999-4999-8999-999999999991", 2, { tipo: "total", clave: "total:gastos", valor: { grupos: { "Gastos Comunes": "1000.00" }, total: "1000.00", vencimientos: [] } });
const CON_CONTROLES: Importacion = { ...IMPORTACION, campos: [LUZ, UNIDAD, TOTAL_GASTOS], controles: [BIEN, PORCENTAJE, DEUDA, SIN_ANTERIOR] };

describe("resumen de la liquidación", () => {
  it("dice las cifras y cuántos controles están bien, muestra solo lo raro y aceptar dice lo que queda abierto", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_CONTROLES),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    await abrirLaImportacion();
    expect(screen.getByText("Gastos del mes: $ 1.000.")).toBeTruthy();
    expect(screen.getByText("CEM revisó 3 controles: 1 está bien, 2 para mirar.")).toBeTruthy();
    const mirar = screen.getByRole("list", { name: "Para mirar" });
    expect(within(mirar).getByRole("heading", { name: PORCENTAJE.titulo })).toBeTruthy();
    expect(within(mirar).getByText(PORCENTAJE.lineas[0] ?? "")).toBeTruthy();
    // La deuda se explica: no se pregunta al administrador por ella.
    const deuda = within(mirar).getByRole("heading", { name: DEUDA.titulo }).closest("li") as HTMLElement;
    expect(within(deuda).queryByRole("button", { name: PREGUNTAR })).toBeNull();
    // Lo que está bien y lo que no se pudo comprobar, plegados.
    expect(screen.getByText(`✓ ${BIEN.texto}`)).toBeTruthy();
    expect(screen.getByText(`${SIN_ANTERIOR.titulo}: ${SIN_ANTERIOR.texto}`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    let ventana = await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE });
    expect(within(ventana).getByText(/Quedan 2 cosas para mirar sin responder/)).toBeTruthy();
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: ACEPTAR_SEPTIEMBRE })).toBeNull());
    // «Entendido» deja la tarjeta como vista y ya no cuenta como abierta.
    fireEvent.click(within(deuda).getByRole("button", { name: ENTENDIDO }));
    expect(await screen.findByText("Visto: deuda e interés de cada unidad.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: ACEPTAR_SEPTIEMBRE }));
    ventana = await screen.findByRole("dialog", { name: ACEPTAR_SEPTIEMBRE });
    expect(within(ventana).getByText(/Quedan una cosa para mirar sin responder: lo que paga cada unidad coincide con su porcentaje\./)).toBeTruthy();
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: ACEPTAR_SEPTIEMBRE })).toBeNull());
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("si un control cambia después de «Entendido», su tarjeta vuelve a estar abierta", async () => {
    const OTRA_LINEA: Control = { ...DEUDA, lineas: [...DEUDA.lineas, "1-A (UF 2) debe $ 50 del mes anterior, sin interés."] };
    const corregida = { ...CON_CONTROLES, controles: [BIEN, PORCENTAJE, OTRA_LINEA, SIN_ANTERIOR] };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_CONTROLES),
      [DECISION]: DETALLE(corregida),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    const mirar = screen.getByRole("list", { name: "Para mirar" });
    const deuda = within(mirar).getByRole("heading", { name: DEUDA.titulo }).closest("li") as HTMLElement;
    fireEvent.click(within(deuda).getByRole("button", { name: ENTENDIDO }));
    expect(await screen.findByText("Visto: deuda e interés de cada unidad.")).toBeTruthy();
    fireEvent.click(within(datos).getByRole("button", { name: /Luz SINTETICA/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Gasto" })).getByRole("button", { name: DESCARTAR_DATO }));
    expect(await screen.findByText(OTRA_LINEA.lineas[1] ?? "")).toBeTruthy();
    expect(screen.queryByText("Visto: deuda e interés de cada unidad.")).toBeNull();
  });

  it("«Preguntar al administrador» abre la solicitud formal ya armada con la cuenta", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_CONTROLES),
      [`GET ${BASE}/solicitudes-formales`]: () =>
        Response.json({ elementos: [], puede_preparar: true, plazo_por_defecto_dias: 15, plazo_minimo_dias: 5, plazo_maximo_dias: 60, hay_administrador_vigente: true }),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    fireEvent.click(screen.getByRole("button", { name: PREGUNTAR }));
    const ventana = await screen.findByRole("dialog", { name: "Preparar una solicitud" });
    expect((within(ventana).getByLabelText(/^Cuál/) as HTMLInputElement).value).toBe("Liquidación de septiembre de 2026");
    const texto = (within(ventana).getByLabelText(/Documentación o aclaración/) as HTMLTextAreaElement).value;
    expect(texto).toContain(`- ${PORCENTAJE.lineas[0] ?? ""}`);
    expect(texto).toContain("Se pide la base de cálculo");
  });

  it("sin `solicitud_formal.preparar` no se ofrece preguntar", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("liquidacion.importar"),
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_CONTROLES),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(screen.getAllByRole("button", { name: ENTENDIDO })).toHaveLength(2);  // control: las tarjetas están
    expect(screen.queryByRole("button", { name: PREGUNTAR })).toBeNull();
  });

  it("los filtros de lo leído se combinan y «Todos» los saca", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CON_CONTROLES),
    }));  // prettier-ignore
    abrir();
    const datos = await abrirLaImportacion();
    const filtros = screen.getByRole("group", { name: "Qué datos mostrar" });
    expect(within(datos).getAllByRole("button")).toHaveLength(3);
    fireEvent.click(within(filtros).getByRole("button", { name: "Gastos" }));
    expect(within(datos).getAllByRole("button").map((x) => x.textContent?.slice(0, 5))).toEqual(["Gasto"]);
    fireEvent.click(within(filtros).getByRole("button", { name: "Unidades" }));
    expect(within(datos).getAllByRole("button")).toHaveLength(2);
    expect(within(filtros).getByRole("button", { name: "Gastos" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(filtros).getByRole("button", { name: "Todos" }));
    expect(within(datos).getAllByRole("button")).toHaveLength(3);
    // Solo hay filtro para lo que la liquidación trae.
    expect(within(filtros).queryByRole("button", { name: "Saldos" })).toBeNull();
  });

  it("un porcentaje se lee como porcentaje y lo que paga una unidad, por grupo", () => {
    expect(enPorcentaje("16.00")).toBe("16 %");
    expect(enPorcentaje("3.50")).toBe("3,5 %");
    const paga = campo(F2, 1, {
      tipo: "importe_unidad", clave: "importe_unidad:5",
      valor: { uf: "5", depto: "2° B", grupos: { "Gastos Comunes": { porcentaje: "21.00", monto: "41500.00" } }, otros_gastos: "0.00", redondeo: "0.00", total: "41500.00" },
    });  // prettier-ignore
    expect(enPalabras(paga)).toEqual([
      { etiqueta: "Gastos Comunes", valor: "le toca el 21 % → $ 41.500,00" },
      { etiqueta: "Otros gastos", valor: "$ 0,00" },
      { etiqueta: "Redondeo", valor: "$ 0,00" },
      { etiqueta: "Total a pagar este mes", valor: "$ 41.500,00" },
    ]);
  });
});

// Recálculo con los coeficientes del edificio (H5-4, app 0116). Datos SINTETICOS.

type Recalculo = components["schemas"]["RecalculoLiquidacion"];
type GrupoRecalculado = components["schemas"]["GrupoRecalculado"];

const U1 = "77777777-7777-4777-8777-777777777771";
const U2 = "77777777-7777-4777-8777-777777777772";
const CONFIRMADA: Importacion = { ...IMPORTACION, estado: "confirmada", decidida_por: PERSONA, decidida_en: "2026-11-20T13:00:00Z", campos: [{ ...LUZ, estado: "confirmado" }] };
const RECALCULO_URL = `${BASE}/liquidaciones-importadas/${I1}/recalculo`;
const CORRESPONDENCIAS = `${BASE}/liquidaciones-importadas/${I1}/correspondencias`;
const ESQUEMAS = [
  { grupo: "agua", esquema_version: 1, vigente_desde: "2024-01-01" },
  { grupo: "expensas_comunes", esquema_version: 2, vigente_desde: "2025-03-01" },
];
const SIN_ELEGIR: GrupoRecalculado = { nombre: "Gastos Comunes", estado: "sin_correspondencia", grupo: null, esquema_version: null, vigente_desde: null, total: null, unidades: [], senaladas: 0, sin_monto: 0 };
const RECALCULADO: GrupoRecalculado = {
  nombre: "Gastos Comunes",
  estado: "recalculado",
  grupo: "expensas_comunes",
  esquema_version: 2,
  vigente_desde: "2025-03-01",
  total: "1000.00",
  senaladas: 1,
  sin_monto: 0,
  unidades: [
    { unidad_id: U1, designacion: "PB-A", coeficiente: "50.000000", porcentaje_informado: "50.00", esperado: "500.00", informado: "500.00", diferencia: "0.00", senalada: false, sin_monto: false },
    { unidad_id: U2, designacion: "1-A", coeficiente: "30.000000", porcentaje_informado: "35.00", esperado: "300.00", informado: "350.00", diferencia: "50.00", senalada: true, sin_monto: false },
  ],
};
const recalculo = (grupos: GrupoRecalculado[], extra: Partial<Recalculo> = {}): Recalculo => ({
  importacion_id: I1,
  periodo: "2026-09-01",
  sin_grupos: false,
  grupos,
  esquemas: ESQUEMAS,
  ...extra,
});

describe("recálculo con los coeficientes del edificio", () => {
  it("elige los coeficientes de un grupo, recalcula y muestra la diferencia con su base", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CONFIRMADA),
      [`GET ${RECALCULO_URL}`]: () => Response.json(recalculo([SIN_ELEGIR])),
      [`POST ${CORRESPONDENCIAS}`]: () => Response.json(recalculo([RECALCULADO])),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    await abrirLaImportacion();
    expect(await screen.findByText("Falta indicar qué coeficientes del edificio corresponden a este grupo.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: ELEGIR_COEFICIENTES }));
    const ventana = await screen.findByRole("dialog", { name: "Coeficientes para «Gastos Comunes»" });
    fireEvent.click(within(ventana).getByRole("button", { name: "Recalcular" }));
    expect(await within(ventana).findByText(FALTA_ESQUEMA)).toBeTruthy();
    expect(pedidos(api, "POST", CORRESPONDENCIAS)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText("Coeficientes del edificio"), { target: { value: "expensas_comunes" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Recalcular" }));
    await waitFor(() => expect(pedidos(api, "POST", CORRESPONDENCIAS)).toHaveLength(1));
    const [pedido] = pedidos(api, "POST", CORRESPONDENCIAS);
    expect(await pedido?.json()).toEqual({ nombre: "Gastos Comunes", grupo: "expensas_comunes" });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByText("«Gastos Comunes» se recalcula con los coeficientes «expensas comunes».")).toBeTruthy();
    expect(screen.getByText(/Una unidad difiere en más de un centavo\./)).toBeTruthy();
    // Solo la unidad con diferencia tiene su fila; su ficha dice la base de la diferencia.
    const diferencias = screen.getByRole("list", { name: "Diferencias en Gastos Comunes" });
    expect(within(diferencias).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(within(diferencias).getByRole("button", { name: /1-A · informado \$ 350,00 · según los coeficientes \$ 300,00/ }));
    const ficha = await screen.findByRole("dialog", { name: "Gastos Comunes · 1-A" });
    expect(within(ficha).getByText("Coeficiente cargado").nextElementSibling?.textContent).toBe("30 %");
    expect(within(ficha).getByText("Porcentaje informado").nextElementSibling?.textContent).toBe("35 %");
    expect(within(ficha).getByText("Diferencia").nextElementSibling?.textContent).toBe("$ 50,00");
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("una diferencia se convierte en el borrador de una solicitud formal ya redactado", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CONFIRMADA),
      [`GET ${RECALCULO_URL}`]: () => Response.json(recalculo([RECALCULADO])),
      [`GET ${BASE}/solicitudes-formales`]: () =>
        Response.json({ elementos: [], puede_preparar: true, plazo_por_defecto_dias: 15, plazo_minimo_dias: 5, plazo_maximo_dias: 60, hay_administrador_vigente: true }),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    fireEvent.click(await screen.findByRole("button", { name: PREPARAR_SOLICITUD }));
    const ventana = await screen.findByRole("dialog", { name: "Preparar una solicitud" });
    expect((within(ventana).getByLabelText(/^Cuál/) as HTMLInputElement).value).toBe("Liquidación de septiembre de 2026");
    const texto = (within(ventana).getByLabelText(/Documentación o aclaración/) as HTMLTextAreaElement).value;
    expect(texto).toContain("- Gastos Comunes, 1-A: informado $ 350,00; según los coeficientes «expensas comunes» $ 300,00 (coeficiente 30 %; porcentaje informado 35 %).");
    expect(texto).not.toContain("PB-A");
  });

  it("sin `solicitud_formal.preparar` no se ofrece la solicitud", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: alcanza("liquidacion.importar"),
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CONFIRMADA),
      [`GET ${RECALCULO_URL}`]: () => Response.json(recalculo([RECALCULADO])),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(await screen.findByText(/Una unidad difiere/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: PREPARAR_SOLICITUD })).toBeNull());
  });

  it("una unidad con el monto vacío se nombra y no se compara ni se pide", async () => {
    const [pa] = RECALCULADO.unidades;
    if (pa === undefined) throw new Error("falta la unidad de la prueba");
    const vacia = { ...pa, unidad_id: U2, designacion: "1-A", informado: null, porcentaje_informado: null, esperado: "300.00", diferencia: null, senalada: false, sin_monto: true };
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CONFIRMADA),
      [`GET ${RECALCULO_URL}`]: () => Response.json(recalculo([{ ...RECALCULADO, senaladas: 0, sin_monto: 1, unidades: [pa, vacia] }])),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(await screen.findByText(/Coincide con lo informado en las demás unidades, con hasta un centavo de diferencia\. Sin monto informado, no se compara: 1-A\./)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Diferencias en Gastos Comunes" })).toBeNull();
    expect(screen.queryByRole("button", { name: PREPARAR_SOLICITUD })).toBeNull();
  });

  it("una liquidación sin reparto por grupo lo dice y no ofrece nada", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/liquidaciones-importadas`]: LISTA,
      [`GET ${BASE}/liquidaciones-importadas/${I1}`]: DETALLE(CONFIRMADA),
      [`GET ${RECALCULO_URL}`]: () => Response.json(recalculo([], { sin_grupos: true })),
    }));  // prettier-ignore
    abrir();
    await abrirLaImportacion();
    expect(await screen.findByText(SIN_GRUPOS)).toBeTruthy();
    expect(screen.queryByRole("button", { name: ELEGIR_COEFICIENTES })).toBeNull();
    expect(screen.queryByRole("button", { name: PREPARAR_SOLICITUD })).toBeNull();
  });

  it("el texto de la solicitud entra en el largo que admite y dice cuántas diferencias quedan afuera", () => {
    const base = RECALCULADO.unidades[1];
    if (base === undefined) throw new Error("falta la unidad de la prueba");
    const muchas: GrupoRecalculado = { ...RECALCULADO, unidades: Array.from({ length: 40 }, (_, i) => ({ ...base, unidad_id: null, designacion: `UF ${i + 1} SINTETICA` })) };
    const texto = textoDeSolicitud(recalculo([muchas]));
    expect(texto.length).toBeLessThanOrEqual(2000);
    expect(texto.startsWith("Se pide la base de cálculo del reparto de la liquidación de septiembre de 2026")).toBe(true);
    expect(texto).toMatch(/- Y \d+ diferencias más, en el recálculo de la aplicación\./);
    expect(texto.endsWith("se pide indicar cuál.")).toBe(true);
  });
});
