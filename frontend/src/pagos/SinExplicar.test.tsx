import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import type { components } from "../api/tipos";
import { App } from "../App";
import { ContextoVersion } from "../version";

// Lo que el extracto no explica (pregunta 8 del propietario, 13-11-2026, opción c): en la bandeja de
// quien concilia y, solo para leer, en la de la administración. Datos ficticios, banco 999. La API
// está simulada: qué entra en la lista lo prueba `backend/tests/test_avisos_y_sin_explicar_0098.py`.
// Contra 3c2d638 la ruta no existe (lleva a «No se encontró») y la bandeja no trae `sin_explicar`.
// Marcar que un débito no es el pago de una orden (respuesta del 13-11, opción a): contra a668127
// no hay botón. Retirar la marca (respuesta del 13-11): contra 2d2cdff no hay lista de marcas.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const M1 = "55555555-5555-4555-8555-555555555555";
const BASE = `/api/v1/consorcios/${C1}`;
const LISTA = `GET ${BASE}/pagos/extracto/sin-explicar`;
const RUTA = "/pagos/extracto-sin-explicar";
const MARCAR = `POST ${BASE}/pagos/extracto/sin-explicar/${M1}/no-es-pago`;
const MARCAS = `GET ${BASE}/pagos/extracto/no-es-pago`;
const RETIRAR = `POST ${BASE}/pagos/extracto/no-es-pago/${M1}/retiro`;
const CONCILIA = alcanza("egresos.revisar_orden", "egresos.conciliar_pago");
const PREPARA = alcanza("egresos.preparar_orden");

type Movimiento = components["schemas"]["MovimientoSinExplicar"];
type Marca = components["schemas"]["MarcaNoEsPago"];
type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

const MOVIMIENTO: Movimiento = {
  movimiento_id: M1, fecha: "2026-11-02", importe_centavos: 1_240_000, moneda: "ARS", numero_operacion: "0048213999",
  visto_en: "2026-11-05T13:00:00Z",
};

const MARCA: Marca = {
  movimiento_id: M1, fecha: "2026-11-02", importe_centavos: 75_000, moneda: "ARS", numero_operacion: null,
  motivo: "Comisión del banco", marcado_en: "2026-11-06T13:00:00Z",
};

const bandeja =
  (sin_explicar: number, marcados_no_es_pago = 0) =>
  () =>
    Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: 0, sin_explicar, marcados_no_es_pago });

function backend(respuestas: Respuestas) {
  const base: Respuestas = {
    "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS"] }]),
    "GET /api/v1/auth/totp": () => Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
    [LISTA]: () => Response.json({ elementos: [MOVIMIENTO], siguiente: null, total: 1 }),
    [MARCAS]: () => Response.json({ elementos: [], siguiente: null, total: 0 }),
  };
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? base[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, clave: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => `${p.method} ${new URL(p.url).pathname}` === clave);
}

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

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: false, media: consulta, addEventListener: () => undefined, removeEventListener: () => undefined,
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


const ventana = async (titulo: string | RegExp) => within(await screen.findByRole("dialog", { name: titulo }));
/** La fila del débito sin explicar (12.400,00) o de la marca (750,00), por su importe. */
const filaDe = (importe: RegExp) => screen.findByRole("button", { name: importe });
const DEBITO = /12\.400,00/;
const MARCADO = /750,00/;

describe("en la bandeja", () => {
  it("a quien concilia le llega como una fila para mirar (BAN-07), sin el texto fijo", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: CONCILIA, [`GET ${BASE}/pagos/bandeja`]: bandeja(2) }));
    abrir("/pagos");
    const fila = await screen.findByRole("link", { name: "Lo que el extracto no explica 2 débitos" });
    expect(fila.getAttribute("href")).toBe(RUTA);
    expect(screen.queryByText(/ningún pago explica/)).toBeNull();
    expect(screen.queryByText("Nada pendiente.")).toBeNull();
  });

  it("a la administración le llega como consulta, abajo y sin contar como pendiente", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: PREPARA, [`GET ${BASE}/pagos/bandeja`]: bandeja(2) }));
    abrir("/pagos");
    const enlace = await screen.findByRole("link", { name: "Lo que el extracto no explica (2)" });
    expect(enlace.getAttribute("href")).toBe(RUTA);
    expect(enlace.className).toContain("enlace-boton--borde");
    expect(screen.queryByRole("link", { name: /débitos$/ })).toBeNull();
    expect(screen.getByText("Nada pendiente.")).toBeTruthy();
  });

  it("con todo marcado, los dos siguen llegando a las marcas, como consulta y sin contar (Codex en #240)", async () => {
    for (const acciones of [CONCILIA, PREPARA]) {
      vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: acciones, [`GET ${BASE}/pagos/bandeja`]: bandeja(0, 1) }));
      abrir("/pagos");
      const enlace = await screen.findByRole("link", { name: "Lo que el extracto no explica" });
      expect(enlace.getAttribute("href")).toBe(RUTA);
      expect(enlace.className).toContain("enlace-boton--borde");
      expect(screen.getByText("Nada pendiente.")).toBeTruthy();
      cleanup();
    }
  });

  it("control: sin nada sin explicar no aparece en ninguna de las dos", async () => {
    for (const acciones of [CONCILIA, PREPARA]) {
      vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: acciones, [`GET ${BASE}/pagos/bandeja`]: bandeja(0) }));
      abrir("/pagos");
      await screen.findByRole("link", { name: "Consultar pagos" });
      expect(screen.queryByText(/Lo que el extracto no explica/)).toBeNull();
      expect(screen.queryByText(/Marcados como/)).toBeNull();
      cleanup();
    }
  });
});

describe("la pantalla", () => {
  it("COT-07, COT-08: sin el párrafo fijo; cada débito es una fila compacta, y la administración lo lee sin acciones", async () => {
    const simulado = backend({ [`GET ${BASE}/mis-acciones`]: PREPARA });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir(RUTA);
    const fila = await filaDe(DEBITO);
    expect(fila.textContent).toContain("Referencia 0048213999 · 02/11/2026");
    expect(fila.textContent).not.toContain("Débito sin explicar");
    expect(fila.textContent).not.toContain("Extracto subido");
    expect(screen.queryByText(/ningún pago de Consorcio en Mano explica/)).toBeNull();
    expect(screen.queryByText(/desde acá no se cambia nada/)).toBeNull();
    fireEvent.click(fila);
    const ficha = await ventana(DEBITO);
    // Un solo punto después de la hora: «a. m.» ya trae el suyo.
    const subido = ficha.getByText(/\sm\.$/);
    expect(subido.textContent).not.toContain("..");
    // Solo para leer: la ficha no tiene ningún acto.
    expect(ficha.queryByRole("button", { name: "No es el pago de una orden" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Cotejar con el banco" })).toBeNull();
    const faltas = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(faltas.violations.map((v) => v.id)).toEqual([]);
  });

  it("quien concilia llega desde ahí al cotejo", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: CONCILIA }));
    abrir(RUTA);
    await filaDe(DEBITO);
    expect(screen.getByRole("link", { name: "Cotejar con el banco" }).getAttribute("href")).toBe("/pagos/cotejo");
  });

  it("quien concilia marca que no es el pago de una orden desde la ficha, con el motivo en su ventana y la clave (C8P-17)", async () => {
    let marcado = false;
    const simulado = backend({
      [`GET ${BASE}/mis-acciones`]: CONCILIA,
      [LISTA]: () => Response.json(marcado ? { elementos: [], siguiente: null, total: 0 } : { elementos: [MOVIMIENTO], siguiente: null, total: 1 }),
      [MARCAR]: () => ((marcado = true), Response.json({ movimiento_id: M1 })),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir(RUTA);
    const fila = await filaDe(DEBITO);
    expect(screen.queryByRole("button", { name: "No es el pago de una orden" })).toBeNull();
    fireEvent.click(fila);
    fireEvent.click((await ventana(DEBITO)).getByRole("button", { name: "No es el pago de una orden" }));
    const motivo = await ventana("No es el pago de una orden");
    expect(motivo.getByRole("button", { name: "Cancelar" }).className).toContain("secundario");
    expect(motivo.queryByText(/No se borra/)).toBeNull();
    // Sin motivo, el botón sigue activo y marca el campo.
    const guardar = motivo.getByRole("button", { name: "No es el pago de una orden" }) as HTMLButtonElement;
    expect(guardar.disabled).toBe(false);
    fireEvent.click(guardar);
    expect(await motivo.findByText("Falta escribir el motivo.")).toBeTruthy();
    expect(pedidos(simulado, MARCAR)).toHaveLength(0);
    fireEvent.change(motivo.getByLabelText("Qué es este débito"), { target: { value: "  Comisión del banco  " } });
    fireEvent.click(guardar);
    expect(await screen.findByText("Listo: el débito salió de la lista.")).toBeTruthy();
    const enviados = pedidos(simulado, MARCAR);
    expect(enviados).toHaveLength(1);
    const [pedido] = enviados;
    expect(await pedido?.clone().json()).toEqual({ motivo: "Comisión del banco" });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(pedido?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await screen.findByText("El extracto no tiene nada sin explicar.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const faltas = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(faltas.violations.map((v) => v.id)).toEqual([]);
  });

  it("un motivo con una cuenta se marca junto al campo, en la ventana, y lo escrito queda", async () => {
    const detalle = { campos: ["body.motivo"], mensajes: {} };
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${BASE}/mis-acciones`]: CONCILIA,
        [MARCAR]: () => Response.json({ codigo: "motivo_con_cuenta", mensaje: "El motivo no puede llevar el número de una cuenta.", detalle, id_correlacion: "c" }, { status: 422 }),
      }),
    );
    abrir(RUTA);
    fireEvent.click(await filaDe(DEBITO));
    fireEvent.click((await ventana(DEBITO)).getByRole("button", { name: "No es el pago de una orden" }));
    const motivo = await ventana("No es el pago de una orden");
    const campo = motivo.getByLabelText("Qué es este débito");
    fireEvent.change(campo, { target: { value: "Transferencia a 9990000000000000000001" } });
    fireEvent.click(motivo.getByRole("button", { name: "No es el pago de una orden" }));
    await waitFor(() => expect(campo.getAttribute("aria-invalid")).toBe("true"));
    expect((campo as HTMLTextAreaElement).value).toBe("Transferencia a 9990000000000000000001");
  });

  it("pide el código y repite lo mismo con la misma clave", async () => {
    let veces = 0;
    const simulado = backend({
      [`GET ${BASE}/mis-acciones`]: CONCILIA,
      [MARCAR]: () =>
        ++veces === 1
          ? Response.json({ codigo: "segundo_factor_requerido", mensaje: "x", detalle: null, id_correlacion: "c" }, { status: 403 })
          : Response.json({ movimiento_id: M1 }),
      "POST /api/v1/auth/totp/verificar": () =>
        Response.json({ verificado_en: "2026-11-06T12:00:00Z", vale_hasta: "2026-11-06T12:10:00Z", codigos_recuperacion_restantes: 9 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir(RUTA);
    fireEvent.click(await filaDe(DEBITO));
    fireEvent.click((await ventana(DEBITO)).getByRole("button", { name: "No es el pago de una orden" }));
    const motivo = await ventana("No es el pago de una orden");
    fireEvent.change(motivo.getByLabelText("Qué es este débito"), { target: { value: "Comisión del banco" } });
    fireEvent.click(motivo.getByRole("button", { name: "No es el pago de una orden" }));
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    expect(await screen.findByText("Listo: el débito salió de la lista.")).toBeTruthy();
    const claves = pedidos(simulado, MARCAR).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves).toHaveLength(2);
    expect(claves[0]).toBe(claves[1]);
  });

  it("COT-09: lo ya marcado no queda debajo: «Ver los marcados»; quien concilia retira una marca con motivo y vuelve a la lista", async () => {
    let retirada = false;
    const simulado = backend({
      [`GET ${BASE}/mis-acciones`]: CONCILIA,
      [LISTA]: () => Response.json(retirada ? { elementos: [MOVIMIENTO], siguiente: null, total: 1 } : { elementos: [], siguiente: null, total: 0 }),
      [MARCAS]: () => Response.json(retirada ? { elementos: [], siguiente: null, total: 0 } : { elementos: [MARCA], siguiente: null, total: 1 }),
      [RETIRAR]: () => ((retirada = true), Response.json({ movimiento_id: M1 })),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir(RUTA);
    await screen.findByText("El extracto no tiene nada sin explicar.");
    expect(screen.queryByText(/Comisión del banco/)).toBeNull();
    expect(screen.queryByRole("heading", { name: /Marcados como que no son/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ver los marcados (1)" }));
    const marcados = screen.getByRole("list", { name: "Marcados como que no son el pago de una orden" });
    const fila = within(marcados).getByRole("button", { name: MARCADO });
    // Sin el título repetido en cada fila.
    expect(fila.textContent).not.toContain("No es el pago de una orden");
    fireEvent.click(fila);
    const ficha = await ventana(MARCADO);
    expect(ficha.getByText("Comisión del banco")).toBeTruthy();
    const boton = ficha.getByRole("button", { name: "Retirar la marca" });
    expect(boton.className).toContain("secundario");
    fireEvent.click(boton);
    const motivo = await ventana("Retirar la marca");
    fireEvent.change(motivo.getByLabelText("Por qué se retira la marca"), { target: { value: "Era el pago de una orden" } });
    fireEvent.click(motivo.getByRole("button", { name: "Retirar la marca" }));
    expect(await screen.findByText("Listo: la marca se retiró y el débito volvió a la lista.")).toBeTruthy();
    const enviados = pedidos(simulado, RETIRAR);
    expect(enviados).toHaveLength(1);
    const [pedido] = enviados;
    expect(await pedido?.clone().json()).toEqual({ motivo: "Era el pago de una orden" });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(await filaDe(DEBITO)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Marcados como que no son el pago de una orden" })).toBeNull();
    const faltas = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(faltas.violations.map((v) => v.id)).toEqual([]);
  });

  it("la administración ve las marcas sin poder retirarlas", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: PREPARA, [MARCAS]: () => Response.json({ elementos: [MARCA], siguiente: null, total: 1 }) }));
    abrir(RUTA);
    fireEvent.click(await screen.findByRole("button", { name: "Ver los marcados (1)" }));
    fireEvent.click(await filaDe(MARCADO));
    const ficha = await ventana(MARCADO);
    expect(ficha.getByText("Comisión del banco")).toBeTruthy();
    expect(ficha.queryByRole("button", { name: "Retirar la marca" })).toBeNull();
  });

  it("sin nada sin explicar lo dice", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: PREPARA, [LISTA]: () => Response.json({ elementos: [], siguiente: null, total: 0 }) }));
    abrir(RUTA);
    expect(await screen.findByText("El extracto no tiene nada sin explicar.")).toBeTruthy();
  });

  it("«Ver más» trae la página siguiente de cada lista por `despues` y no repite (Codex en #240)", async () => {
    const M2 = "66666666-6666-4666-8666-666666666666";
    const simulado = backend({
      [`GET ${BASE}/mis-acciones`]: CONCILIA,
      [LISTA]: (pedido: Request) =>
        new URL(pedido.url).searchParams.get("despues") === M1
          ? Response.json({ elementos: [{ ...MOVIMIENTO, movimiento_id: M2, numero_operacion: "0048214000" }], siguiente: null, total: 2 })
          : Response.json({ elementos: [MOVIMIENTO], siguiente: M1, total: 2 }),
      [MARCAS]: (pedido: Request) =>
        new URL(pedido.url).searchParams.get("despues") === M1
          ? Response.json({ elementos: [{ ...MARCA, movimiento_id: M2, importe_centavos: 80_000 }], siguiente: null, total: 2 })
          : Response.json({ elementos: [MARCA], siguiente: M1, total: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir(RUTA);
    fireEvent.click(await screen.findByRole("button", { name: "Ver más débitos sin explicar" }));
    expect(await screen.findByText(/Referencia 0048214000/)).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Débitos sin explicar" })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Ver más débitos sin explicar" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ver los marcados (2)" }));
    fireEvent.click(screen.getByRole("button", { name: "Ver más marcados" }));
    expect(await screen.findByRole("button", { name: /800,00/ })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Marcados como que no son el pago de una orden" })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Ver más marcados" })).toBeNull();
    // La segunda página se pidió con el cursor de la primera.
    const despues = (clave: string) => pedidos(simulado, clave).map((p) => new URL(p.url).searchParams.get("despues"));
    expect(despues(LISTA)).toContain(M1);
    expect(despues(MARCAS)).toContain(M1);
  });

  it("quien no prepara ni concilia no llega: la ruta lleva al inicio y no se pide nada", async () => {
    const simulado = backend({ [`GET ${BASE}/mis-acciones`]: alcanza("documentos.ver") });
    vi.stubGlobal("fetch", simulado);
    abrir(RUTA);
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Lo que el extracto no explica" })).toBeNull());
    expect(pedidos(simulado, LISTA)).toEqual([]);
  });
});
