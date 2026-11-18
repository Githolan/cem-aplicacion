import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import type { components } from "../api/tipos";
import { App } from "../App";
import { SIN_CONEXION } from "../fallas";
import {
  EJEMPLO_EXTRACTO,
  FALTA_ARCHIVO_EXTRACTO,
  FALTA_MOTIVO_DESHACER,
  textoBancoNoAca,
  textoLosCotejaOtra,
  textoLosDeshaceOtra,
  textoTopeTildes,
  TILDAR_PAGOS,
} from "./textos";
import { ContextoVersion } from "../version";

// `/pagos/cotejo`. Datos ficticios, banco 999, y nada se transfiere. La API está simulada: que el
// servidor lea el archivo, compare y separe lo prueban `backend/tests/test_cotejo_0074.py` y
// `test_extracto.py`; acá, qué se pide y qué se muestra.
//
// Fricción 17-11 (tanda 6): la pantalla es la lista de lo que falta cotejar; el extracto se sube en
// su ventana y el resultado se mira en una hoja que se cierra con «Listo».

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "abababab-abab-4bab-8bab-abababababab";
const P1 = "22222222-2222-4222-8222-222222222222";
const P2 = "33333333-3333-4333-8333-333333333333";
const P3 = "34343434-3434-4434-8434-343434343434";
const O1 = "44444444-4444-4444-8444-444444444444";
const BASE = `/api/v1/consorcios/${C1}`;
const LISTA = `GET ${BASE}/pagos/cotejo`;
const TILDAR = `POST ${BASE}/pagos/cotejo`;
const VISTA = `POST ${BASE}/pagos/cotejo/columnas`;
const EXTRACTO = `POST ${BASE}/pagos/cotejo/extracto`;
const COTEJADOS = `GET ${BASE}/pagos/cotejo/cotejados`;
const ANULAR = `POST ${BASE}/pagos/cotejo/${P1}/anulacion`;
const TOPE = 20 * 1024 * 1024;
const CSV = "Fecha valor;Detalle;Debito;Referencia\n05/11/2026;x;-1,00;0048213377\n";
const SUBIR_EXTRACTO = "Subir el extracto";
const SOLO_PESOS = () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS"] }]);
const CON_DOLARES = () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS", "USD"] }]);
const VERIFICADO = () => Response.json({ verificado_en: "2026-11-06T12:00:00Z", vale_hasta: "2026-11-06T12:10:00Z", codigos_recuperacion_restantes: 9 });

type Elemento = components["schemas"]["PagoPorCotejar"];
type Vista = components["schemas"]["VistaExtracto"];
type Resultado = components["schemas"]["ResultadoCotejo"];
type Respuestas = Record<string, () => Response | Promise<Response>>;

function elemento(id: string, extra: Partial<Elemento> = {}): Elemento {
  return {
    pago_id: id, orden_id: O1, proveedor: `Proveedor ${id.slice(0, 2)}`, concepto: "Limpieza", numero_operacion: "0048213377",
    fecha_operacion: "2026-11-05", importe_centavos: 1_500_000, moneda: "ARS", puede_cotejar: true, ...extra,
  };
}

const cotejado = (id: string, extra: Partial<Elemento> = {}) => ({ ...elemento(id, extra), origen: "extracto", cotejado_en: "2026-11-06T12:00:00Z" });

const MAXIMO_PAGOS = 100;

const lista = (elementos: Elemento[], siguiente: string | null = null, maximo_pagos = MAXIMO_PAGOS) => () =>
  Response.json({ elementos, siguiente, tope_bytes: TOPE, maximo_pagos, moneda_extracto: "ARS" });

const VISTA_NUEVA: Vista = {
  columnas: ["Fecha valor", "Detalle", "Debito", "Referencia"], muestra: ["05/11/2026", "x", "-1,00", "0048213377"],
  signo_sugerido: "con_signo", columnas_guardadas: null, columnas_reconocidas: null,
};
const GUARDADAS = { fecha: "Fecha valor", importe: "Debito", numero_operacion: "Referencia", signo: "con_signo", moneda: "ARS" } as const;

function resultado(extra: Partial<Resultado> = {}): Resultado {
  return {
    desde: "2026-11-01", hasta: "2026-11-30", movimientos_nuevos: 1, movimientos_repetidos: 0, cotejados: 12, ya_cotejados: 0,
    esperan_conciliar: 0, para_otra_persona: 0, anulados: 0, lineas_no_leidas: 0, filas_no_leidas: [], aca_no_banco: [], total_aca_no_banco: 0,
    banco_no_aca: [], total_banco_no_aca: 0, ...extra,
  };
}

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status });
}

function backend(respuestas: Respuestas) {
  const base: Respuestas = {
    "GET /api/v1/me/consorcios": SOLO_PESOS,
    "GET /api/v1/auth/totp": () => Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
    [`GET ${BASE}/pagos/bandeja`]: () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: 1, sin_explicar: 0 }),
    [LISTA]: lista([]),
  };
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? base[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, clave: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => `${p.method} ${new URL(p.url).pathname}` === clave);
}

function Lugar() {
  return <output data-testid="lugar">{useLocation().pathname}</output>;
}

const lugar = () => screen.getByTestId("lugar").textContent;

function abrir(ruta: string | { pathname: string; state: unknown } = "/pagos/cotejo") {
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

const ventana = async (titulo: string | RegExp) => within(await screen.findByRole("dialog", { name: titulo }));

/** Abre la ventana de subir el extracto (COT-01). */
async function abrirSubida() {
  fireEvent.click(await screen.findByRole("button", { name: SUBIR_EXTRACTO }));
  return ventana(SUBIR_EXTRACTO);
}

/** Elige el archivo en la ventana; elegirlo no lo manda. */
async function elegir(nombre = "extracto.csv", contenido = CSV) {
  const subida = await abrirSubida();
  const archivo = new File([contenido], nombre, { type: "text/csv" });
  fireEvent.change(subida.getByLabelText("Archivo del extracto"), { target: { files: [archivo] } });
  return subida;
}

/** Elige el archivo y lo sube con el botón de la ventana. */
async function subir(nombre = "extracto.csv", contenido = CSV) {
  const subida = await elegir(nombre, contenido);
  fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
  return subida;
}

const hojaDelResultado = () => ventana(/^Extracto del /);

async function verificarCodigo() {
  fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: false, media: consulta, addEventListener: () => undefined, removeEventListener: () => undefined,
  }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("la entrada", () => {
  it("la bandeja la ofrece a quien concilia, como una fila", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden", "egresos.conciliar_pago") }));
    abrir("/pagos");
    expect((await screen.findByRole("link", { name: "Para cotejar 1 pago" })).getAttribute("href")).toBe("/pagos/cotejo");
  });

  // Quién ve «Para cotejar» lo decide el servidor (`por_cotejar` es 0 para quien no puede cotejar;
  // backend/tests/test_bandeja_honesta_0096.py): la pantalla dibuja lo que llega.
  it("control: a quien solo prepara no se la ofrece y la ruta lo lleva al inicio", async () => {
    const simulado = backend({
      [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden"),
      [`GET ${BASE}/pagos/bandeja`]: () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar: 0 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir("/pagos");
    await screen.findByRole("link", { name: "Consultar pagos" });
    await waitFor(() => expect(screen.queryByRole("link", { name: /^Para cotejar/ })).toBeNull());
    cleanup();
    abrir();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Cotejar con el banco" })).toBeNull());
    expect(pedidos(simulado, LISTA)).toEqual([]);
  });
});

describe("COT-01: la pantalla es la lista de lo que falta cotejar", () => {
  it("sin la subida a la vista: «Subir el extracto» abre su ventana, con el ejemplo y el tope del servidor", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1)]) }));
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    expect(screen.queryByLabelText("Archivo del extracto")).toBeNull();
    expect(screen.queryByRole("heading", { name: "O marcar a mano" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Lo ya cotejado" })).toBeNull();

    const subida = await abrirSubida();
    const entrada = subida.getByLabelText("Archivo del extracto");
    expect(entrada.getAttribute("type")).toBe("file");
    expect(entrada.getAttribute("accept")).toBe(".csv,text/csv");
    expect(subida.getByText(/^CSV, hasta [0-9]+ MB.$/)).toBeTruthy();
    // Soltar o elegir el archivo, en todos los anchos; sin imágenes no hay cámara ni galería.
    expect(subida.getByText("Soltar el archivo acá")).toBeTruthy();
    expect(subida.getByRole("button", { name: "Elegir un archivo" })).toBeTruthy();
    expect(subida.queryByRole("button", { name: "Sacar una foto" })).toBeNull();
    const ejemplo = subida.getByText("Ver el ejemplo").closest("details") as HTMLElement;
    expect(ejemplo.querySelector("pre")?.textContent).toBe(EJEMPLO_EXTRACTO);
    // COT-02: sin la ayuda fija de la moneda.
    expect(subida.queryByText(/El archivo del banco no la dice/)).toBeNull();
  });

  it("elegir el archivo no lo manda; sin archivo, el botón dice qué falta", async () => {
    const simulado = backend({ [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }), [EXTRACTO]: () => Response.json(resultado()) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const subida = await abrirSubida();
    fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
    expect(await subida.findByText(FALTA_ARCHIVO_EXTRACTO)).toBeTruthy();
    expect(pedidos(simulado, VISTA)).toEqual([]);
    fireEvent.change(subida.getByLabelText("Archivo del extracto"), { target: { files: [new File([CSV], "extracto.csv", { type: "text/csv" })] } });
    expect(subida.getByText("Elegido: extracto.csv")).toBeTruthy();
    expect(subida.queryByText(FALTA_ARCHIVO_EXTRACTO)).toBeNull();
    expect(pedidos(simulado, VISTA)).toEqual([]);
    fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
  });
});

describe("a mano", () => {
  it("guarda lo tildado con una clave y vuelve a leer la lista", async () => {
    let elementos = [elemento(P1), elemento(P2)];
    const simulado = backend({
      [LISTA]: () => lista(elementos)(),
      [TILDAR]: () => {
        elementos = [elemento(P2)];
        return Response.json({ cotejados: 1 });
      },
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    const uno = await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    fireEvent.click(uno);
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    expect(await screen.findByText("Listo: quedó cotejado 1 pago.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: /Proveedor 22/ })).toBeNull());
    const [pedido] = pedidos(simulado, TILDAR);
    expect(await pedido?.clone().json()).toEqual({ pagos: [P1] });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
  });

  it("C8P-16: sin nada tildado no hay botón apagado: dice qué falta; con tildados, el botón dice cuántos", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1), elemento(P2)]) }));
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    expect(screen.queryByRole("button", { name: /como cotejad/ })).toBeNull();
    expect(screen.getByText(TILDAR_PAGOS)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Proveedor 33/ }));
    expect((screen.getByRole("button", { name: "Marcar 2 como cotejados" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByText(TILDAR_PAGOS)).toBeNull();
  });

  it("COT-05: lo que no puede cotejar no se lista: una línea al pie lo cuenta", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false }), elemento(P2)]) }));
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.queryByText(/Proveedor 22/)).toBeNull();
    expect(screen.getByText(textoLosCotejaOtra(1))).toBeTruthy();
  });

  it("control: si no puede cotejar nada, no ofrece guardar ni pide tildar", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false }), elemento(P2, { puede_cotejar: false })]) }));
    abrir();
    expect(await screen.findByText(textoLosCotejaOtra(2))).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /como cotejad/ })).toBeNull();
    expect(screen.queryByText(TILDAR_PAGOS)).toBeNull();
  });

  it("FL-CON-02 (decisión 13): si ningún pago pendiente es suyo, no ofrece «Subir el extracto»: solo la línea", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false })]) }));
    abrir();
    expect(await screen.findByText(textoLosCotejaOtra(1))).toBeTruthy();
    expect(screen.queryByRole("button", { name: SUBIR_EXTRACTO })).toBeNull();
  });

  it("control de FL-CON-02: con uno suyo, o con otra página por leer, sí lo ofrece", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false })], P1) }));
    abrir();
    expect(await screen.findByRole("button", { name: SUBIR_EXTRACTO })).toBeTruthy();
  });

  it("FL-CON-06: cotejado el último pago suyo, vuelve sola a Pagos con el «Listo» arriba", async () => {
    let elementos = [elemento(P1), elemento(P2, { puede_cotejar: false })];
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => lista(elementos)(),
      [TILDAR]: () => {
        elementos = [elemento(P2, { puede_cotejar: false })];
        return Response.json({ cotejados: 1 });
      },
    }));
    abrir();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Pagos" })).toBeTruthy();
    expect(lugar()).toBe("/pagos");
    expect(await screen.findByText("Listo: quedó cotejado 1 pago.")).toBeTruthy();
  });

  it("C8P-11: las casillas no se apagan al llegar al tope; la de más no se tilda y dice por qué junto a ella", async () => {
    const simulado = backend({
      [LISTA]: lista([elemento(P1), elemento(P2), elemento(P3)], null, 2),
      [TILDAR]: () => Response.json({ cotejados: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const [uno, dos, tres] = (await screen.findAllByRole("checkbox")) as [HTMLInputElement, HTMLInputElement, HTMLInputElement];
    fireEvent.click(uno);
    fireEvent.click(dos);
    expect(tres.disabled).toBe(false);
    expect(screen.queryByText(textoTopeTildes(2))).toBeNull();
    fireEvent.click(tres);
    expect(tres.checked).toBe(false);
    const aviso = screen.getByText(textoTopeTildes(2));
    expect(aviso.closest("li")?.contains(tres)).toBe(true);
    fireEvent.click(uno); // destildar uno libera lugar, y el aviso se va
    expect(screen.queryByText(textoTopeTildes(2))).toBeNull();
    fireEvent.click(tres);
    expect(tres.checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Marcar 2 como cotejados" }));
    await screen.findByText(/Listo/);
    expect(await pedidos(simulado, TILDAR)[0]?.clone().json()).toEqual({ pagos: [P2, P3] });
  });

  it("sin nada para cotejar, lo dice", async () => {
    vi.stubGlobal("fetch", backend({}));
    abrir();
    expect(await screen.findByText("No falta cotejar ningún pago.")).toBeTruthy();
  });

  it("pide el código, repite lo mismo con la misma clave y no pierde lo tildado", async () => {
    let veces = 0;
    const simulado = backend({
      [LISTA]: lista([elemento(P1)]),
      [TILDAR]: () => (++veces === 1 ? error("segundo_factor_requerido", 403) : Response.json({ cotejados: 1 })),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    await verificarCodigo();
    expect(await screen.findByText("Listo: quedó cotejado 1 pago.")).toBeTruthy();
    const claves = pedidos(simulado, TILDAR).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves).toHaveLength(2);
    expect(claves[0]).toBe(claves[1]);
  });

  it("una separación que niega se dice en la pantalla y lo tildado queda", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1)]), [TILDAR]: () => error("no_es_quien_ejecuto", 403) }));
    abrir();
    const tilde = await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    fireEvent.click(tilde);
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    expect((await screen.findByRole("alert")).textContent).toContain("no lo concilia");
    expect((tilde as HTMLInputElement).checked).toBe(true);
  });

  it("«Ver más» suma la página por `despues` y no repite", async () => {
    const simulado = backend({});
    simulado.mockImplementation(async (pedido: Request) => {
      const url = new URL(pedido.url);
      const clave = `${pedido.method} ${url.pathname}`;
      if (clave === LISTA) return url.searchParams.get("despues") === P1 ? lista([elemento(P2)])() : lista([elemento(P1)], P1)();
      if (clave === "GET /api/v1/me/consorcios") return SOLO_PESOS();
      const respuesta = alcanzaTodo(clave);
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Ver más" })).toBeNull();
  });

  it("si falla «Ver más», lo cargado queda y el mismo botón vuelve a pedir", async () => {
    let falla = true;
    const simulado = backend({});
    simulado.mockImplementation(async (pedido: Request) => {
      const url = new URL(pedido.url);
      const clave = `${pedido.method} ${url.pathname}`;
      if (clave === LISTA) {
        if (url.searchParams.get("despues") !== P1) return lista([elemento(P1)], P1)();
        if (falla) return error("servicio_no_disponible", 503);
        return lista([elemento(P2)])();
      }
      if (clave === "GET /api/v1/me/consorcios") return SOLO_PESOS();
      const respuesta = alcanzaTodo(clave);
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("checkbox", { name: /Proveedor 22/ })).toBeTruthy();
    falla = false;
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("una página que llega tarde no se mezcla con la lista que se releyó después de cotejar", async () => {
    let soltarVieja: (r: Response) => void = () => undefined;
    const vieja = new Promise<Response>((r) => {
      soltarVieja = r;
    });
    let hecho = false;
    const simulado = backend({});
    simulado.mockImplementation(async (pedido: Request) => {
      const url = new URL(pedido.url);
      const clave = `${pedido.method} ${url.pathname}`;
      if (clave === LISTA) {
        if (url.searchParams.get("despues") === P1) return vieja;
        return hecho ? lista([elemento(P3)])() : lista([elemento(P1)], P1)();
      }
      if (clave === TILDAR) {
        hecho = true;
        return Response.json({ cotejados: 1 });
      }
      if (clave === "GET /api/v1/me/consorcios") return SOLO_PESOS();
      const respuesta = alcanzaTodo(clave);
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    await screen.findByRole("checkbox", { name: /Proveedor 34/ });
    await act(async () => {
      soltarVieja(lista([elemento(P2)])());
      await Promise.resolve();
    });
    expect(screen.queryByRole("checkbox", { name: /Proveedor 33/ })).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });
});

describe("COT-06: lo ya cotejado y deshacer el cotejo", () => {
  it("filas compactas sin botón; «Deshacer el cotejo» en la ficha, con el motivo en su ventana y una clave (C8P-15)", async () => {
    let cotejados = [cotejado(P1)];
    const simulado = backend({
      [LISTA]: lista([]),
      [COTEJADOS]: () => Response.json({ elementos: cotejados, siguiente: null }),
      [ANULAR]: () => {
        cotejados = [];
        return Response.json({ pago_id: P1 });
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    const fila = await screen.findByRole("button", { name: /Proveedor 22/ });
    expect(screen.queryByRole("button", { name: /Deshacer el cotejo/ })).toBeNull();
    fireEvent.click(fila);
    const ficha = await ventana("Proveedor 22");
    fireEvent.click(ficha.getByRole("button", { name: "Deshacer el cotejo" }));
    const motivo = await ventana("Deshacer el cotejo");
    expect(motivo.queryByText(/No se borra/)).toBeNull();
    // Sin motivo, el botón sigue activo y marca el campo (C8P-15).
    const deshacer = motivo.getByRole("button", { name: "Deshacer el cotejo" }) as HTMLButtonElement;
    expect(deshacer.disabled).toBe(false);
    fireEvent.click(deshacer);
    expect(await motivo.findByText(FALTA_MOTIVO_DESHACER)).toBeTruthy();
    expect(motivo.getByLabelText("Por qué se deshace").getAttribute("aria-invalid")).toBe("true");
    expect(pedidos(simulado, ANULAR)).toHaveLength(0);
    fireEvent.change(motivo.getByLabelText("Por qué se deshace"), { target: { value: "SINTETICO: era otro débito" } });
    fireEvent.click(deshacer);
    expect(await screen.findByText(/^Listo: el cotejo se deshizo/)).toBeTruthy();
    const [pedido] = pedidos(simulado, ANULAR);
    expect(await pedido?.clone().json()).toEqual({ motivo: "SINTETICO: era otro débito" });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByText("Todavía no hay pagos cotejados.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("si deshacer pide el código, la ventana queda y el reintento lleva la misma clave", async () => {
    let veces = 0;
    const simulado = backend({
      [LISTA]: lista([]),
      [COTEJADOS]: () => Response.json({ elementos: veces < 2 ? [cotejado(P1)] : [], siguiente: null }),
      [ANULAR]: () => (++veces === 1 ? error("segundo_factor_requerido", 403) : Response.json({ pago_id: P1 })),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    fireEvent.click(await screen.findByRole("button", { name: /Proveedor 22/ }));
    fireEvent.click((await ventana("Proveedor 22")).getByRole("button", { name: "Deshacer el cotejo" }));
    const motivo = await ventana("Deshacer el cotejo");
    fireEvent.change(motivo.getByLabelText("Por qué se deshace"), { target: { value: "SINTETICO" } });
    fireEvent.click(motivo.getByRole("button", { name: "Deshacer el cotejo" }));
    await verificarCodigo();
    expect(await screen.findByText(/^Listo: el cotejo se deshizo/)).toBeTruthy();
    const claves = pedidos(simulado, ANULAR).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves).toHaveLength(2);
    expect(claves[0]).toBe(claves[1]);
  });

  it("un rechazo queda en la ventana, con lo escrito", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: lista([]),
      [COTEJADOS]: () => Response.json({ elementos: [cotejado(P1)], siguiente: null }),
      [ANULAR]: () => {
        throw new TypeError("sin red");
      },
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    fireEvent.click(await screen.findByRole("button", { name: /Proveedor 22/ }));
    fireEvent.click((await ventana("Proveedor 22")).getByRole("button", { name: "Deshacer el cotejo" }));
    const motivo = await ventana("Deshacer el cotejo");
    fireEvent.change(motivo.getByLabelText("Por qué se deshace"), { target: { value: "SINTETICO" } });
    fireEvent.click(motivo.getByRole("button", { name: "Deshacer el cotejo" }));
    expect((await motivo.findByRole("alert")).textContent).toBe(SIN_CONEXION);
    expect((motivo.getByLabelText("Por qué se deshace") as HTMLTextAreaElement).value).toBe("SINTETICO");
  });

  it("después de cotejar, lo ya cotejado se puede volver a pedir y trae lo nuevo", async () => {
    let cotejados: unknown[] = [];
    const simulado = backend({
      [LISTA]: lista([elemento(P1)]),
      [TILDAR]: () => {
        cotejados = [cotejado(P1)];
        return Response.json({ cotejados: 1 });
      },
      [COTEJADOS]: () => Response.json({ elementos: cotejados, siguiente: null }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    expect(await screen.findByText("Todavía no hay pagos cotejados.")).toBeTruthy();
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Marcar 1 como cotejado" }));
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    expect(await screen.findByRole("button", { name: /Proveedor 22/ })).toBeTruthy();
  });

  it("COT-05: lo cotejado de un pago en que participó no se lista: una línea lo cuenta", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: lista([]),
      [COTEJADOS]: () => Response.json({ elementos: [cotejado(P1, { puede_cotejar: false })], siguiente: null }),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    expect(await screen.findByText(textoLosDeshaceOtra(1))).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Proveedor 22/ })).toBeNull();
  });

  it("FL-CON-01: llegado desde «Ver lo cotejado» de la bandeja, lo cotejado ya está a la vista", async () => {
    const simulado = backend({ [COTEJADOS]: () => Response.json({ elementos: [cotejado(P1)], siguiente: null }) });
    vi.stubGlobal("fetch", simulado);
    abrir({ pathname: "/pagos/cotejo", state: { consorcio: C1, cotejados: true } });
    expect(await screen.findByRole("button", { name: /Proveedor 22/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ver lo cotejado" })).toBeNull();
    expect(pedidos(simulado, COTEJADOS)).toHaveLength(1);
  });

  it("control de FL-CON-01: entrando de otro lado, lo cotejado se pide con el botón", async () => {
    const simulado = backend({ [COTEJADOS]: () => Response.json({ elementos: [cotejado(P1)], siguiente: null }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByRole("button", { name: "Ver lo cotejado" })).toBeTruthy();
    expect(pedidos(simulado, COTEJADOS)).toHaveLength(0);
  });

  it("FL-CON-03: la orden abierta desde lo cotejado vuelve al cotejo, con lo cotejado a la vista", async () => {
    vi.stubGlobal("fetch", backend({
      [COTEJADOS]: () => Response.json({ elementos: [cotejado(P1)], siguiente: null }),
      [`GET ${BASE}/pagos/${O1}`]: () => error("no_encontrado", 404),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo cotejado" }));
    fireEvent.click(await screen.findByRole("button", { name: /Proveedor 22/ }));
    fireEvent.click((await ventana("Proveedor 22")).getByRole("link", { name: "Ver la orden" }));
    await waitFor(() => expect(lugar()).toBe(`/pagos/${O1}`));
    fireEvent.click(await screen.findByRole("link", { name: "Volver a Cotejar con el banco" }));
    await waitFor(() => expect(lugar()).toBe("/pagos/cotejo"));
    expect(await screen.findByRole("button", { name: /Proveedor 22/ })).toBeTruthy();
  });
});

describe("con el extracto", () => {
  it("COT-03: si no reconoce las columnas, las pregunta en la misma ventana, con la primera fila, y manda lo elegido", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json(VISTA_NUEVA),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    await subir();
    const columnas = await ventana("¿Qué es cada columna?");
    expect((columnas.getByLabelText("Fecha") as HTMLSelectElement).value).toBe("Fecha valor");
    expect((columnas.getByLabelText("Importe") as HTMLSelectElement).value).toBe("Debito");
    expect((columnas.getByLabelText("Número de operación") as HTMLSelectElement).value).toBe("Referencia");
    expect((columnas.getByLabelText("Cómo figuran los pagos") as HTMLSelectElement).value).toBe("con_signo");
    expect(columnas.queryByLabelText("Moneda del extracto")).toBeNull();
    expect(columnas.getByText("Primera fila: 05/11/2026 · -1,00 · 0048213377")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: SUBIR_EXTRACTO })).toBeNull();
    const faltas = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(faltas.violations.map((v) => v.id)).toEqual([]);

    fireEvent.change(columnas.getByLabelText("Número de operación"), { target: { value: "" } });
    fireEvent.click(columnas.getByRole("button", { name: "Comparar" }));
    const hoja = await hojaDelResultado();
    expect(hoja.getByText("12 pagos coinciden con el banco y quedaron cotejados.")).toBeTruthy();
    const [pedido] = pedidos(simulado, EXTRACTO);
    const parametros = new URL(pedido?.url ?? "").searchParams;
    expect(parametros.get("fecha")).toBe("Fecha valor");
    expect(parametros.get("importe")).toBe("Debito");
    expect(parametros.get("signo")).toBe("con_signo");
    expect(parametros.get("moneda")).toBe("ARS");
    expect(parametros.has("numero_operacion")).toBe(false);
    expect(pedido?.headers.get("Content-Type")).toBe("text/csv");
  });

  it("una columna sin elegir se marca en el campo y no compara", async () => {
    const simulado = backend({ [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas: ["A", "B"], muestra: ["1", "2"] }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    const columnas = await ventana("¿Qué es cada columna?");
    fireEvent.click(columnas.getByRole("button", { name: "Comparar" }));
    expect(await columnas.findByText("Falta elegir la columna de la fecha.")).toBeTruthy();
    expect(columnas.getByText("Falta elegir la columna del importe.")).toBeTruthy();
    expect(pedidos(simulado, EXTRACTO)).toEqual([]);
  });

  it("con las columnas guardadas compara enseguida, sin preguntar", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    await (await hojaDelResultado()).findByText("12 pagos coinciden con el banco y quedaron cotejados.");
    expect(screen.queryByRole("dialog", { name: "¿Qué es cada columna?" })).toBeNull();
    expect([...new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams.keys()].filter((k) => k !== "moneda")).toEqual([]);
  });

  // P-23: el caso del informe. Contra 3c2d638 este archivo iba a «¿Qué es cada columna?».
  it("con las columnas reconocidas por su nombre compara enseguida y manda esas", async () => {
    const reconocidas = { fecha: "Fecha", importe: "Débito", numero_operacion: "Número de operación", signo: "sin_signo", moneda: "ARS" } as const;
    const simulado = backend({
      [VISTA]: () =>
        Response.json({
          columnas: ["Fecha", "Débito", "Número de operación"], muestra: ["05/11/2026", "1,00", "0048213377"],
          signo_sugerido: "sin_signo", columnas_guardadas: null, columnas_reconocidas: reconocidas,
        } satisfies Vista),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir("extracto.csv", "Fecha;Débito;Número de operación\n05/11/2026;1,00;0048213377\n");
    await (await hojaDelResultado()).findByText("12 pagos coinciden con el banco y quedaron cotejados.");
    expect(screen.queryByRole("dialog", { name: "¿Qué es cada columna?" })).toBeNull();
    const parametros = new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams;
    expect(Object.fromEntries(parametros)).toEqual({ fecha: "Fecha", importe: "Débito", numero_operacion: "Número de operación", signo: "sin_signo", moneda: "ARS" });
  });

  it("muestra solo lo que hay que mirar, sin la nota repetida en cada débito (COT-04)", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () =>
        Response.json(resultado({
          aca_no_banco: [elemento(P1)], total_aca_no_banco: 1, total_banco_no_aca: 1,
          banco_no_aca: [{ fecha: "2026-11-02", importe_centavos: 1_240_000, moneda: "ARS", numero_operacion: "0048213999" }],
          esperan_conciliar: 2, para_otra_persona: 1, ya_cotejados: 3, lineas_no_leidas: 2, filas_no_leidas: [14, 15],
        })),
    }));
    abrir();
    await subir();
    const hoja = await hojaDelResultado();
    const aca = (await hoja.findByRole("heading", { name: /Están acá y no en el banco/ })).nextElementSibling as HTMLElement;
    expect(within(aca).getByRole("link", { name: /Proveedor 22/ }).getAttribute("href")).toBe(`/pagos/${O1}`);
    // FL-CON-04: los débitos sin pago se cuentan, con el enlace a su pantalla; la lista no se repite acá.
    expect(hoja.getByText(textoBancoNoAca(1), { exact: false })).toBeTruthy();
    expect(hoja.queryByRole("heading", { name: /Están en el banco y no acá/ })).toBeNull();
    expect(hoja.queryByText(/0048213999/)).toBeNull();
    expect(hoja.getByRole("link", { name: "Ver los débitos sin explicar" }).getAttribute("href")).toBe("/pagos/extracto-sin-explicar");
    expect(hoja.getByText("3 ya estaban cotejados.")).toBeTruthy();
    expect(hoja.getByText("2 coinciden, pero falta conciliarlos.")).toBeTruthy();
    expect(hoja.getByText(/1 coincide, pero lo tiene que cotejar/)).toBeTruthy();
    expect(hoja.getByText(/2 líneas no se pudieron leer del archivo \(línea 14, 15\)/)).toBeTruthy();
  });

  it("COT-04: el resultado se cierra con «Listo», sin «Subir otro extracto» ni «Elegir otras columnas», y queda la lista", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: lista([elemento(P2)]),
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    }));
    abrir();
    await subir();
    const hoja = await hojaDelResultado();
    expect(hoja.queryByRole("button", { name: "Subir otro extracto" })).toBeNull();
    expect(hoja.queryByRole("button", { name: "Elegir otras columnas" })).toBeNull();
    expect(hoja.queryByText("No hay nada para mirar.")).toBeNull();
    expect(hoja.queryByRole("heading", { name: /Están/ })).toBeNull();
    fireEvent.click(hoja.getByRole("button", { name: "Listo" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("checkbox", { name: /Proveedor 33/ })).toBeTruthy();
    expect(lugar()).toBe("/pagos/cotejo");
  });

  it("FL-CON-06: cerrado el resultado, si no queda nada para cotejar vuelve sola a Pagos", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    }));
    abrir();
    await subir();
    fireEvent.click((await hojaDelResultado()).getByRole("button", { name: "Listo" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Pagos" })).toBeTruthy();
    expect(lugar()).toBe("/pagos");
  });

  it("FL-CON-03: la orden abierta desde el resultado vuelve al cotejo, con el resultado a la vista", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado({ aca_no_banco: [elemento(P1)], total_aca_no_banco: 1 })),
      [`GET ${BASE}/pagos/${O1}`]: () => error("no_encontrado", 404),
    }));
    abrir();
    await subir();
    fireEvent.click((await hojaDelResultado()).getByRole("link", { name: /Proveedor 22/ }));
    await waitFor(() => expect(lugar()).toBe(`/pagos/${O1}`));
    fireEvent.click(await screen.findByRole("link", { name: "Volver a Cotejar con el banco" }));
    await waitFor(() => expect(lugar()).toBe("/pagos/cotejo"));
    expect((await hojaDelResultado()).getByRole("link", { name: /Proveedor 22/ })).toBeTruthy();
  });

  it("COT-02: en un edificio que opera solo en pesos no pregunta la moneda y manda pesos", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const subida = await elegir();
    expect(subida.queryByLabelText("Moneda del extracto")).toBeNull();
    fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
    expect(new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams.get("moneda")).toBe("ARS");
  });

  it("COT-02: con dólares, la moneda se elige por su nombre, con la guardada puesta, y va al subir", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": CON_DOLARES,
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const subida = await elegir();
    const moneda = subida.getByLabelText("Moneda del extracto") as HTMLSelectElement;
    expect(moneda.value).toBe("ARS");
    expect([...moneda.options].map((o) => o.textContent)).toEqual(["Pesos", "Dólares"]);
    fireEvent.change(moneda, { target: { value: "USD" } });
    fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
    const parametros = new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams;
    expect(parametros.get("moneda")).toBe("USD");
    expect(parametros.get("fecha")).toBeNull();
  });

  it("COT-03: el paso de las columnas no vuelve a pedir la moneda: va la elegida en el primero", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": CON_DOLARES,
      [VISTA]: () => Response.json(VISTA_NUEVA),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const subida = await elegir();
    fireEvent.change(subida.getByLabelText("Moneda del extracto"), { target: { value: "USD" } });
    fireEvent.click(subida.getByRole("button", { name: SUBIR_EXTRACTO }));
    const columnas = await ventana("¿Qué es cada columna?");
    expect(columnas.queryByLabelText("Moneda del extracto")).toBeNull();
    fireEvent.click(columnas.getByRole("button", { name: "Comparar" }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
    expect(new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams.get("moneda")).toBe("USD");
  });

  it("la moneda elegida no vuelve a la guardada al pedir más de la lista", async () => {
    const simulado = backend({ "GET /api/v1/me/consorcios": CON_DOLARES, [LISTA]: lista([elemento(P1)], "pagina-2") });
    vi.stubGlobal("fetch", simulado);
    abrir();
    let subida = await abrirSubida();
    fireEvent.change(subida.getByLabelText("Moneda del extracto"), { target: { value: "USD" } });
    fireEvent.click(subida.getByRole("button", { name: "Cancelar" }));
    const lecturas = pedidos(simulado, LISTA).length;
    fireEvent.click(await screen.findByRole("button", { name: "Ver más" }));
    await waitFor(() => expect(pedidos(simulado, LISTA).length).toBeGreaterThan(lecturas));
    subida = await abrirSubida();
    expect((subida.getByLabelText("Moneda del extracto") as HTMLSelectElement).value).toBe("USD");
  });

  it("lo que coincide con un cotejo deshecho se cuenta aparte", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado({ cotejados: 0, anulados: 1 })),
    }));
    abrir();
    await subir();
    expect(await (await hojaDelResultado()).findByText(/1 coincide con un cotejo que se deshizo/)).toBeTruthy();
  });

  it.each([
    ["una línea sin leer", { lineas_no_leidas: 1, filas_no_leidas: [14] }],
    ["falta conciliar", { esperan_conciliar: 1 }],
    ["lo coteja otra persona", { para_otra_persona: 1 }],
    ["un cotejo deshecho", { anulados: 1 }],
  ])("%s también es algo para mirar", async (_, campos) => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado(campos)),
    }));
    abrir();
    await subir();
    expect(await (await hojaDelResultado()).findByRole("heading", { name: /Falta revisar/ })).toBeTruthy();
  });

  it("después de comparar se vuelve a leer lo que falta cotejar", async () => {
    let elementos = [elemento(P1)];
    const simulado = backend({
      [LISTA]: () => lista(elementos)(),
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => {
        elementos = [];
        return Response.json(resultado());
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    await subir();
    expect(await screen.findByText("No falta cotejar ningún pago.")).toBeTruthy();
  });

  it("un archivo que no se lee se dice en la ventana y deja elegir otro", async () => {
    vi.stubGlobal("fetch", backend({ [VISTA]: () => error("extracto_invalido", 422) }));
    abrir();
    const subida = await subir();
    expect((await subida.findByRole("alert")).textContent).toContain("El archivo no se pudo leer");
    expect((subida.getByLabelText("Archivo del extracto") as HTMLInputElement).disabled).toBe(false);
  });

  it("un archivo con demasiadas líneas lo dice así", async () => {
    vi.stubGlobal("fetch", backend({ [VISTA]: () => error("extracto_demasiadas_lineas", 422) }));
    abrir();
    const subida = await subir();
    expect((await subida.findByRole("alert")).textContent).toContain("demasiadas líneas");
  });

  it("un archivo que pasa del tope no sale del navegador", async () => {
    const simulado = backend({ [LISTA]: () => Response.json({ elementos: [], siguiente: null, tope_bytes: 3, maximo_pagos: MAXIMO_PAGOS }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText("No falta cotejar ningún pago.");
    const subida = await elegir();
    expect((await subida.findByRole("alert")).textContent).toContain("Hay que elegir uno más chico");
    expect(pedidos(simulado, VISTA)).toEqual([]);
  });

  it("si las columnas no sirven vuelve a preguntar en la misma ventana, sin perder el archivo", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => error("extracto_sin_movimientos", 422),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    const columnas = await ventana("¿Qué es cada columna?");
    expect((await columnas.findByRole("alert")).textContent).toContain("ninguna línea");
    fireEvent.click(columnas.getByRole("button", { name: "Comparar" }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(2));
  });

  it("sin conexión al comparar no queda trabado: lo dice en la ventana", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => {
        throw new TypeError("sin red");
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const subida = await subir();
    expect((await subida.findByRole("alert")).textContent).toBe(SIN_CONEXION);
    await waitFor(() => expect((subida.getByRole("button", { name: SUBIR_EXTRACTO }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("si comparar pide el código, la ventana queda y el reintento lleva la misma clave", async () => {
    let veces = 0;
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => (++veces === 1 ? error("segundo_factor_requerido", 403) : Response.json(resultado())),
      "POST /api/v1/auth/totp/verificar": VERIFICADO,
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    await verificarCodigo();
    expect(await (await hojaDelResultado()).findByText("12 pagos coinciden con el banco y quedaron cotejados.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: SUBIR_EXTRACTO })).toBeNull();
    const claves = pedidos(simulado, EXTRACTO).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves).toHaveLength(2);
    expect(claves[0]).toBe(claves[1]);
  });

  it("al cambiar de edificio no queda nada del anterior", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () =>
        Response.json([{ id: C1, nombre: "Edificio Uno", vinculos: [], monedas: ["ARS"] }, { id: C2, nombre: "Edificio Dos", vinculos: [], monedas: ["ARS"] }]),
      [LISTA]: lista([elemento(P1)]),
      [`GET /api/v1/consorcios/${C2}/pagos/cotejo`]: lista([elemento(P2)]),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C1 } });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.queryByRole("checkbox", { name: /Proveedor 22/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /como cotejad/ })).toBeNull();
  });
});
