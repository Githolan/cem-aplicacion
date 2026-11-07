import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import type { components } from "../api/tipos";
import { App } from "../App";
import { EJEMPLO_EXTRACTO } from "./textos";
import { ContextoVersion } from "../version";

// `/pagos/cotejo`. Datos ficticios, banco 999, y nada se transfiere. La API está simulada: que el
// servidor lea el archivo, compare y separe lo prueban `backend/tests/test_cotejo_0074.py` y
// `test_extracto.py`; acá, qué se pide y qué se muestra.

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
const TOPE = 20 * 1024 * 1024;
const CSV = "Fecha valor;Detalle;Debito;Referencia\n05/11/2026;x;-1,00;0048213377\n";

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

const MAXIMO_PAGOS = 100;

const lista = (elementos: Elemento[], siguiente: string | null = null, maximo_pagos = MAXIMO_PAGOS) => () =>
  Response.json({ elementos, siguiente, tope_bytes: TOPE, maximo_pagos, moneda_extracto: "ARS" });

const VISTA_NUEVA: Vista = {
  columnas: ["Fecha valor", "Detalle", "Debito", "Referencia"], muestra: ["05/11/2026", "x", "-1,00", "0048213377"],
  signo_sugerido: "con_signo", columnas_guardadas: null,
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
    "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]),
    "GET /api/v1/auth/totp": () => Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
    [`GET ${BASE}/pagos/bandeja`]: () => Response.json({ elementos: [] }),
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

function abrir(ruta = "/pagos/cotejo") {
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

async function subir(nombre = "extracto.csv", contenido = CSV) {
  const entrada = await screen.findByLabelText(/Elegir el archivo/);
  const archivo = new File([contenido], nombre, { type: "text/csv" });
  fireEvent.change(entrada, { target: { files: [archivo] } });
  return archivo;
}

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
  it("la bandeja la ofrece a quien concilia", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.revisar_orden", "egresos.conciliar_pago") }));
    abrir("/pagos");
    expect((await screen.findByRole("link", { name: "Cotejar con el banco" })).getAttribute("href")).toBe("/pagos/cotejo");
  });

  it("control: a quien solo prepara no se la ofrece y la ruta lo lleva al inicio", async () => {
    const simulado = backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden") });
    vi.stubGlobal("fetch", simulado);
    abrir("/pagos");
    await screen.findByRole("link", { name: "Consultar pagos" });
    await waitFor(() => expect(screen.queryByRole("link", { name: "Cotejar con el banco" })).toBeNull());
    cleanup();
    abrir();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Cotejar con el banco" })).toBeNull());
    expect(pedidos(simulado, LISTA)).toEqual([]);
  });
});

describe("el botón del archivo y el ejemplo", () => {
  it("«Ver el ejemplo» muestra la forma del archivo y el tope lo dice el servidor", async () => {
    vi.stubGlobal("fetch", backend({}));
    abrir();

    const entrada = await screen.findByLabelText("Elegir el archivo");
    expect(entrada.getAttribute("type")).toBe("file");
    expect(await screen.findByText(/^CSV, hasta [0-9]+ MB$/)).toBeTruthy();
    const ejemplo = screen.getByText("Ver el ejemplo").closest("details") as HTMLElement;
    expect(ejemplo.querySelector("pre")?.textContent).toBe(EJEMPLO_EXTRACTO);
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
    expect((screen.getByRole("button", { name: "Marcar como cotejados" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(uno);
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
    expect(await screen.findByText("Listo: quedó cotejado 1 pago.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: /Proveedor 22/ })).toBeNull());
    const [pedido] = pedidos(simulado, TILDAR);
    expect(await pedido?.clone().json()).toEqual({ pagos: [P1] });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
  });

  it("lo que no puede cotejar se ve sin tilde y dice por qué", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false }), elemento(P2)]) }));
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.queryByRole("checkbox", { name: /Proveedor 22/ })).toBeNull();
    expect(screen.getByText(/Lo coteja otra persona del Consejo/)).toBeTruthy();
  });

  it("control: si no puede cotejar nada, no ofrece guardar", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: lista([elemento(P1, { puede_cotejar: false })]) }));
    abrir();
    await screen.findByText(/Lo coteja otra persona del Consejo/);
    expect(screen.queryByRole("button", { name: "Marcar como cotejados" })).toBeNull();
  });

  it("no deja tildar más de lo que entra en un pedido y dice por qué", async () => {
    const simulado = backend({
      [LISTA]: lista([elemento(P1), elemento(P2), elemento(P3)], null, 2),
      [TILDAR]: () => Response.json({ cotejados: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const [uno, dos, tres] = (await screen.findAllByRole("checkbox")) as [HTMLInputElement, HTMLInputElement, HTMLInputElement];
    fireEvent.click(uno);
    expect(tres.disabled).toBe(false);
    expect(screen.queryByText(/Se marcan hasta 2/)).toBeNull();
    fireEvent.click(dos);
    expect(tres.disabled).toBe(true);
    expect(uno.disabled).toBe(false); // lo tildado se puede destildar
    expect(screen.getByText("Se marcan hasta 2 por vez: primero estos, y después los demás.")).toBeTruthy();
    fireEvent.click(tres);
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
    await screen.findByText(/Listo/);
    expect(await pedidos(simulado, TILDAR)[0]?.clone().json()).toEqual({ pagos: [P1, P2] });
  });

  it("deshacer un cotejo pide el motivo y lo manda con una clave; sin motivo no se manda", async () => {
    const COTEJADOS = `GET ${BASE}/pagos/cotejo/cotejados`;
    const ANULAR = `POST ${BASE}/pagos/cotejo/${P1}/anulacion`;
    let cotejados = [{ ...elemento(P1), origen: "extracto", cotejado_en: "2026-11-06T12:00:00Z" }];
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
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo ya cotejado" }));
    fireEvent.click(await screen.findByRole("button", { name: /Deshacer el cotejo de Proveedor 22/ }));
    const deshacer = screen.getByRole("button", { name: "Deshacer el cotejo" }) as HTMLButtonElement;
    expect(deshacer.disabled).toBe(true);
    expect(pedidos(simulado, ANULAR)).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Por qué se deshace"), { target: { value: "SINTETICO: era otro débito" } });
    fireEvent.click(deshacer);
    expect(await screen.findByText(/Listo: deshiciste el cotejo/)).toBeTruthy();
    const [pedido] = pedidos(simulado, ANULAR);
    expect(await pedido?.clone().json()).toEqual({ motivo: "SINTETICO: era otro débito" });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByText("Todavía no hay pagos cotejados.")).toBeTruthy();
  });

  it("después de cotejar, lo ya cotejado se puede volver a pedir y trae lo nuevo", async () => {
    let cotejados: unknown[] = [];
    const simulado = backend({
      [LISTA]: lista([elemento(P1)]),
      [TILDAR]: () => {
        cotejados = [{ ...elemento(P1), origen: "manual", cotejado_en: "2026-11-06T12:00:00Z" }];
        return Response.json({ cotejados: 1 });
      },
      [`GET ${BASE}/pagos/cotejo/cotejados`]: () => Response.json({ elementos: cotejados, siguiente: null }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo ya cotejado" }));
    expect(await screen.findByText("Todavía no hay pagos cotejados.")).toBeTruthy();
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo ya cotejado" }));
    expect(await screen.findByRole("button", { name: /Deshacer el cotejo de/ })).toBeTruthy();
  });

  it("control: lo cotejado por un pago en que participó no ofrece deshacer", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: lista([]),
      [`GET ${BASE}/pagos/cotejo/cotejados`]: () =>
        Response.json({ elementos: [{ ...elemento(P1, { puede_cotejar: false }), origen: "manual", cotejado_en: "2026-11-06T12:00:00Z" }], siguiente: null }),
    }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Ver lo ya cotejado" }));
    expect(await screen.findByText(/Lo deshace otra persona del Consejo/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Deshacer el cotejo/ })).toBeNull();
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
      "POST /api/v1/auth/totp/verificar": () =>
        Response.json({ verificado_en: "2026-11-06T12:00:00Z", vale_hasta: "2026-11-06T12:10:00Z", codigos_recuperacion_restantes: 9 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
    expect((await screen.findByRole("alert")).textContent).toContain("no lo concilia");
    expect((tilde as HTMLInputElement).checked).toBe(true);
  });

  it("«Ver más» suma la página por `despues` y no repite", async () => {
    const simulado = backend({
      [LISTA]: () => lista([elemento(P1)], P1)(),
    });
    simulado.mockImplementation(async (pedido: Request) => {
      const url = new URL(pedido.url);
      const clave = `${pedido.method} ${url.pathname}`;
      if (clave === LISTA) return url.searchParams.get("despues") === P1 ? lista([elemento(P2)])() : lista([elemento(P1)], P1)();
      if (clave === "GET /api/v1/me/consorcios") return Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);
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
      if (clave === "GET /api/v1/me/consorcios") return Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);
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
    let cotejado = false;
    const simulado = backend({});
    simulado.mockImplementation(async (pedido: Request) => {
      const url = new URL(pedido.url);
      const clave = `${pedido.method} ${url.pathname}`;
      if (clave === LISTA) {
        if (url.searchParams.get("despues") === P1) return vieja;
        return cotejado ? lista([elemento(P3)])() : lista([elemento(P1)], P1)();
      }
      if (clave === TILDAR) {
        cotejado = true;
        return Response.json({ cotejados: 1 });
      }
      if (clave === "GET /api/v1/me/consorcios") return Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [] }]);
      const respuesta = alcanzaTodo(clave);
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ver más" }));
    fireEvent.click(screen.getByRole("button", { name: "Marcar como cotejados" }));
    await screen.findByRole("checkbox", { name: /Proveedor 34/ });
    await act(async () => {
      soltarVieja(lista([elemento(P2)])());
      await Promise.resolve();
    });
    expect(screen.queryByRole("checkbox", { name: /Proveedor 33/ })).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });
});

describe("con el extracto", () => {
  it("la primera vez pregunta las columnas, muestra la primera fila y manda lo elegido", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json(VISTA_NUEVA),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    await subir();
    const formulario = await screen.findByRole("heading", { name: "¿Qué es cada columna?" });
    expect((screen.getByLabelText("Fecha") as HTMLSelectElement).value).toBe("Fecha valor");
    expect((screen.getByLabelText("Importe") as HTMLSelectElement).value).toBe("Debito");
    expect((screen.getByLabelText("Número de operación") as HTMLSelectElement).value).toBe("Referencia");
    expect((screen.getByLabelText("Cómo figuran los pagos") as HTMLSelectElement).value).toBe("con_signo");
    expect(screen.getByText("Primera fila: 05/11/2026 · -1,00 · 0048213377")).toBeTruthy();
    expect(formulario).toBeTruthy();
    const faltas = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(faltas.violations.map((v) => v.id)).toEqual([]);

    fireEvent.change(screen.getByLabelText("Número de operación"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Comparar" }));
    expect(await screen.findByText("12 pagos coinciden con el banco y quedaron cotejados.")).toBeTruthy();
    const [pedido] = pedidos(simulado, EXTRACTO);
    const parametros = new URL(pedido?.url ?? "").searchParams;
    expect(parametros.get("fecha")).toBe("Fecha valor");
    expect(parametros.get("importe")).toBe("Debito");
    expect(parametros.get("signo")).toBe("con_signo");
    expect(parametros.has("numero_operacion")).toBe(false);
    expect(pedido?.headers.get("Content-Type")).toBe("text/csv");
  });

  it("con las columnas guardadas compara enseguida, sin preguntar", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    await screen.findByText("12 pagos coinciden con el banco y quedaron cotejados.");
    expect(screen.queryByRole("heading", { name: "¿Qué es cada columna?" })).toBeNull();
    expect([...new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams.keys()].filter((k) => k !== "moneda")).toEqual([]);
  });

  it("muestra solo lo que hay que mirar, con la acción al lado", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () =>
        Response.json(resultado({
          aca_no_banco: [elemento(P1)], total_aca_no_banco: 1, total_banco_no_aca: 1,
          banco_no_aca: [{ fecha: "2026-11-02", importe_centavos: 1_240_000, moneda: "ARS", numero_operacion: "0048213999" }],
          esperan_conciliar: 2, para_otra_persona: 1, ya_cotejados: 3, lineas_no_leidas: 2, filas_no_leidas: [14, 15],
        })),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    const aca = (await screen.findByRole("heading", { name: /Están acá y no en el banco/ })).nextElementSibling as HTMLElement;
    expect(within(aca).getByRole("link", { name: /Ver el pago/ }).getAttribute("href")).toBe(`/pagos/${O1}`);
    const banco = screen.getByRole("heading", { name: /Están en el banco y no acá/ }).nextElementSibling as HTMLElement;
    expect(banco.textContent).toContain("Débito sin registrar");
    expect(banco.textContent).toContain("12.400,00");
    expect(banco.textContent).toContain("Referencia 0048213999 · 02/11/2026");
    expect(banco.textContent).toContain("Avisale a la administración.");
    expect(screen.getByText("3 ya estaban cotejados.")).toBeTruthy();
    expect(screen.getByText("2 coinciden, pero falta conciliarlos.")).toBeTruthy();
    expect(screen.getByText(/1 coincide, pero lo tiene que cotejar/)).toBeTruthy();
    expect(screen.getByText(/2 líneas no se pudieron leer del archivo \(línea 14, 15\)/)).toBeTruthy();
    expect(screen.queryByText("No hay nada para mirar.")).toBeNull();
  });

  it("la moneda se pide con las columnas, con la sugerida puesta, y va al subir", async () => {
    const simulado = backend({ [LISTA]: lista([elemento(P1)]), [VISTA]: () => Response.json(VISTA_NUEVA), [EXTRACTO]: () => Response.json(resultado()) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    await subir();
    await screen.findByRole("heading", { name: "¿Qué es cada columna?" });
    const moneda = screen.getByLabelText("Moneda del extracto") as HTMLSelectElement;
    expect(moneda.value).toBe("ARS");
    fireEvent.change(moneda, { target: { value: "USD" } });
    fireEvent.click(screen.getByRole("button", { name: /Comparar/ }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
    expect(new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams.get("moneda")).toBe("USD");
  });

  it("con las columnas guardadas se cambia la moneda antes de subir", async () => {
    const simulado = backend({
      [LISTA]: lista([elemento(P1)]),
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByRole("checkbox", { name: /Proveedor 22/ });
    fireEvent.change(screen.getByLabelText("Moneda del extracto"), { target: { value: "USD" } });
    await subir();
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(1));
    const parametros = new URL(pedidos(simulado, EXTRACTO)[0]?.url ?? "").searchParams;
    expect(parametros.get("moneda")).toBe("USD");
    expect(parametros.get("fecha")).toBeNull();
  });

  it("la moneda elegida no vuelve a la guardada al pedir más de la lista", async () => {
    const simulado = backend({
      [LISTA]: lista([elemento(P1)], "pagina-2"),
      [EXTRACTO]: () => Response.json(resultado()),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const moneda = (await screen.findByLabelText("Moneda del extracto")) as HTMLSelectElement;
    fireEvent.change(moneda, { target: { value: "USD" } });
    const lecturas = pedidos(simulado, LISTA).length;
    fireEvent.click(await screen.findByRole("button", { name: "Ver más" }));
    await waitFor(() => expect(pedidos(simulado, LISTA).length).toBeGreaterThan(lecturas));
    await waitFor(() => expect((screen.getByLabelText("Moneda del extracto") as HTMLSelectElement).value).toBe("USD"));
  });

  it("control: sin elegir, la moneda es la guardada del edificio", async () => {
    vi.stubGlobal("fetch", backend({}));
    abrir();
    await waitFor(() => expect((screen.getByLabelText("Moneda del extracto") as HTMLSelectElement).value).toBe("ARS"));
  });

  it("lo que coincide con un cotejo deshecho se cuenta aparte", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado({ cotejados: 0, anulados: 1 })),
    }));
    abrir();
    await subir();
    expect(await screen.findByText(/1 coincide con un cotejo que se deshizo/)).toBeTruthy();
  });

  it("una línea sin leer puede ser un débito sin registrar: no dice que no hay nada para mirar", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado({ lineas_no_leidas: 1, filas_no_leidas: [14] })),
    }));
    abrir();
    await subir();
    const lista = (await screen.findByRole("heading", { name: /Falta revisar/ })).nextElementSibling as HTMLElement;
    expect(lista.textContent).toContain("No pudimos leer 1 línea del archivo (línea 14).");
    expect(screen.queryByText("No hay nada para mirar.")).toBeNull();
  });

  it.each([
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
    expect(await screen.findByRole("heading", { name: /Falta revisar/ })).toBeTruthy();
    expect(screen.queryByText("No hay nada para mirar.")).toBeNull();
  });

  it("control: sin nada para mirar lo dice y no muestra secciones vacías", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => Response.json(resultado()),
    }));
    abrir();
    await subir();
    expect(await screen.findByText("No hay nada para mirar.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Están/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Falta revisar/ })).toBeNull();
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

  it("un archivo que no se lee se dice y deja elegir otro", async () => {
    vi.stubGlobal("fetch", backend({ [VISTA]: () => error("extracto_invalido", 422) }));
    abrir();
    await subir();
    expect((await screen.findByRole("alert")).textContent).toContain("No pudimos leer el archivo");
    expect(((await screen.findByLabelText(/Elegir el archivo/)) as HTMLInputElement).disabled).toBe(false);
  });

  it("un archivo con demasiadas líneas lo dice así", async () => {
    vi.stubGlobal("fetch", backend({ [VISTA]: () => error("extracto_demasiadas_lineas", 422) }));
    abrir();
    await subir();
    expect((await screen.findByRole("alert")).textContent).toContain("demasiadas líneas");
  });

  it("un archivo que pasa del tope no sale del navegador", async () => {
    const simulado = backend({ [LISTA]: () => Response.json({ elementos: [], siguiente: null, tope_bytes: 3, maximo_pagos: MAXIMO_PAGOS }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText("No falta cotejar ningún pago.");
    await subir();
    expect((await screen.findByRole("alert")).textContent).toContain("Elegí uno más chico");
    expect(pedidos(simulado, VISTA)).toEqual([]);
  });

  it("si las columnas no sirven vuelve a preguntar, sin perder el archivo", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => error("extracto_sin_movimientos", 422),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    expect(await screen.findByRole("heading", { name: "¿Qué es cada columna?" })).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("ninguna línea");
    fireEvent.click(screen.getByRole("button", { name: "Comparar" }));
    await waitFor(() => expect(pedidos(simulado, EXTRACTO)).toHaveLength(2));
  });

  it("sin conexión al comparar no queda trabado", async () => {
    const simulado = backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: GUARDADAS }),
      [EXTRACTO]: () => {
        throw new TypeError("sin red");
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await subir();
    await screen.findByRole("alert");
    await waitFor(async () => expect(((await screen.findByLabelText(/Elegir el archivo/)) as HTMLInputElement).disabled).toBe(false));
  });

  it("«Elegir otras columnas» vuelve al formulario con lo guardado", async () => {
    vi.stubGlobal("fetch", backend({
      [VISTA]: () => Response.json({ ...VISTA_NUEVA, columnas_guardadas: { ...GUARDADAS, importe: "Detalle" } }),
      [EXTRACTO]: () => Response.json(resultado()),
    }));
    abrir();
    await subir();
    fireEvent.click(await screen.findByRole("button", { name: "Elegir otras columnas" }));
    expect((screen.getByLabelText("Importe") as HTMLSelectElement).value).toBe("Detalle");
  });

  it("al cambiar de edificio no queda nada del anterior", async () => {
    const simulado = backend({
      "GET /api/v1/me/consorcios": () =>
        Response.json([{ id: C1, nombre: "Edificio Uno", vinculos: [] }, { id: C2, nombre: "Edificio Dos", vinculos: [] }]),
      [LISTA]: lista([elemento(P1)]),
      [`GET /api/v1/consorcios/${C2}/pagos/cotejo`]: lista([elemento(P2)]),
      [VISTA]: () => Response.json(VISTA_NUEVA),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.change(await screen.findByLabelText("Edificio"), { target: { value: C1 } });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Proveedor 22/ }));
    await subir();
    await screen.findByRole("heading", { name: "¿Qué es cada columna?" });
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    await screen.findByRole("checkbox", { name: /Proveedor 33/ });
    expect(screen.queryByRole("checkbox", { name: /Proveedor 22/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "¿Qué es cada columna?" })).toBeNull();
  });
});
