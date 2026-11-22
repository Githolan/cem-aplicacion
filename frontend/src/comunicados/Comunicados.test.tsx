import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { CORREGIDO, CORREGIR, llegaA, NO_SE_CAMBIA, NUEVO, RUTA_COMUNICADOS, salioPara, SIN_COMUNICADOS, SIN_DESTINATARIOS, textoEnvios } from "./Comunicados";

// Comunicados oficiales (RF28, app 0122). Datos SINTETICOS: ningún edificio ni persona real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const A = "22222222-2222-4222-8222-222222222222";
const B = "33333333-3333-4333-8333-333333333333";
const NUEVO_ID = "44444444-4444-4444-8444-444444444444";
const BASE = `/api/v1/consorcios/${C1}`;

type Lista = components["schemas"]["ListaComunicados"];
type Comunicado = components["schemas"]["Comunicado"];

const REF_A = { id: A, titulo: "Corte de agua SINTETICO", emitido_en: "2026-11-20T13:00:00Z" };
const DE_A: Comunicado = {
  ...REF_A, emitido_por_nombre: "Ana Consejo", corrige_a: null, corregido: true,
  texto: "El martes de 9 a 13 se corta el agua.\nDatos de PRUEBA.",
  corregido_por: [{ id: B, titulo: "Corrección: es el miércoles", emitido_en: "2026-11-21T13:00:00Z" }],
  envios: null,
};  // prettier-ignore
const DE_B: Comunicado = {
  id: B, titulo: "Corrección: es el miércoles", emitido_en: "2026-11-21T13:00:00Z", emitido_por_nombre: "Ana Consejo",
  corrige_a: REF_A, corregido: false, texto: "El corte es el miércoles.", corregido_por: [], envios: null,
};  // prettier-ignore
const resumen = (c: Comunicado) => ({
  id: c.id, titulo: c.titulo, emitido_en: c.emitido_en, emitido_por_nombre: c.emitido_por_nombre,
  corrige_a: c.corrige_a, corregido: c.corregido,
});  // prettier-ignore
const lista = (cambios: Partial<Lista> = {}): Lista => ({
  elementos: [resumen(DE_B), resumen(DE_A)], puede_emitir: false, destinatarios_posibles: null,
  largo_titulo: 120, largo_texto: 4000, ...cambios,
});  // prettier-ignore
const EMISOR = { puede_emitir: true, destinatarios_posibles: 4 };

type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

const pedidos = (simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] =>
  simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const LECTORA = alcanza("comunicados.ver", "documentos.ver");
const QUIEN_EMITE = alcanza("comunicados.ver", "comunicados.emitir", "padron.ver");

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_COMUNICADOS]}>
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

const ventana = () => screen.getByRole("dialog");

describe("pantalla /comunicados", () => {
  it("quien lee ve la lista y el texto completo, sin emitir ni las cuentas de envíos", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: LECTORA,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista()),
      [`GET ${BASE}/comunicados/${A}`]: () => Response.json(DE_A),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    const filas = await screen.findByRole("list", { name: "Comunicados" });
    const botones = within(filas).getAllByRole("button");
    expect(botones.map((b) => b.querySelector(".fila-registro__titulo")?.textContent)).toEqual([DE_B.titulo, DE_A.titulo]);
    // El corregido se marca; el que corrige dice a cuál.
    expect(within(botones[1] as HTMLElement).getByText(CORREGIDO)).toBeTruthy();
    expect(within(botones[0] as HTMLElement).getByText(/Corrige «Corte de agua SINTETICO»/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: NUEVO })).toBeNull();
    expect((await axe.run(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
    fireEvent.click(botones[1] as HTMLElement);
    const ficha = await screen.findByRole("dialog", { name: DE_A.titulo });
    expect(within(ficha).getByText(/El martes de 9 a 13/).textContent).toBe(DE_A.texto);
    expect(within(ficha).getByText(/Corrección: es el miércoles/)).toBeTruthy();
    expect(within(ficha).queryByText("Envíos")).toBeNull();
    expect(within(ficha).queryByRole("button", { name: CORREGIR })).toBeNull();
    expect((await axe.run(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
    expect(pedidos(api, "POST", `${BASE}/comunicados`)).toHaveLength(0);
  });

  it("sin comunicados lo dice", async () => {
    vi.stubGlobal("fetch", backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: LECTORA,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista({ elementos: [] })),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText(SIN_COMUNICADOS)).toBeTruthy();
  });

  it("quien emite escribe, confirma sabiendo a cuántas personas les llega y emite una sola vez", async () => {
    let emitidos = 0;
    const nuevo: Comunicado = { ...DE_B, id: NUEVO_ID, titulo: "Asamblea el jueves", corrige_a: null, texto: "A las 19.", envios: { destinatarios: 4, enviados: 4 } };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: QUIEN_EMITE,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista({ ...EMISOR, elementos: emitidos > 0 ? [resumen(nuevo), resumen(DE_B), resumen(DE_A)] : [resumen(DE_B), resumen(DE_A)] })),
      [`POST ${BASE}/comunicados`]: () => {
        emitidos += 1;
        return Response.json(nuevo, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    fireEvent.click(await screen.findByRole("button", { name: NUEVO }));
    // Lo obligatorio se marca junto al campo, con el botón siempre activo.
    fireEvent.click(within(ventana()).getByRole("button", { name: "Seguir" }));
    expect(await within(ventana()).findByText("Falta el título.")).toBeTruthy();
    expect(within(ventana()).getByText("Falta el texto.")).toBeTruthy();
    fireEvent.change(within(ventana()).getByLabelText("Título"), { target: { value: "  Asamblea el jueves " } });
    fireEvent.change(within(ventana()).getByLabelText("Texto"), { target: { value: "A las 19." } });
    expect((await axe.run(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
    fireEvent.click(within(ventana()).getByRole("button", { name: "Seguir" }));
    expect(await within(ventana()).findByText(llegaA(4))).toBeTruthy();
    expect(within(ventana()).getByText(NO_SE_CAMBIA)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/comunicados`)).toHaveLength(0); // confirmar todavía no emite
    fireEvent.click(within(ventana()).getByRole("button", { name: "Emitir" }));
    expect(await screen.findByText(salioPara(4))).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [pedido] = pedidos(api, "POST", `${BASE}/comunicados`);
    expect(await pedido?.json()).toEqual({ titulo: "Asamblea el jueves", texto: "A las 19.", corrige_a: null });
    expect(pedido?.headers.get("Idempotency-Key")).toBeTruthy();
    // La lista se vuelve a leer: el nuevo, arriba.
    const filas = await screen.findByRole("list", { name: "Comunicados" });
    await waitFor(() => expect(within(filas).getAllByRole("button")[0]?.textContent).toContain("Asamblea el jueves"));
    expect(emitidos).toBe(1);
  });

  it("una cuenta en el texto se rechaza: vuelve a lo escrito con el motivo, sin perderlo", async () => {
    const claves: (string | null)[] = [];
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: QUIEN_EMITE,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista(EMISOR)),
      [`POST ${BASE}/comunicados`]: (p) => {
        claves.push(p.headers.get("Idempotency-Key"));
        return Response.json({ codigo: "texto_con_cuenta", mensaje: "x", id_correlacion: "c" }, { status: 422 });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: NUEVO }));
    fireEvent.change(within(ventana()).getByLabelText("Título"), { target: { value: "Pago de expensas" } });
    fireEvent.change(within(ventana()).getByLabelText("Texto"), { target: { value: "Transferir a 9990001 1234567890123 4" } });
    fireEvent.click(within(ventana()).getByRole("button", { name: "Seguir" }));
    fireEvent.click(await within(ventana()).findByRole("button", { name: "Emitir" }));
    const motivo = await within(ventana()).findByRole("alert");
    expect(motivo.textContent).toMatch(/no puede llevar el número de una cuenta/);
    expect((within(ventana()).getByLabelText("Texto") as HTMLTextAreaElement).value).toBe("Transferir a 9990001 1234567890123 4");
    // Otro intento, después de corregir, va con otra clave: es otro pedido.
    fireEvent.change(within(ventana()).getByLabelText("Texto"), { target: { value: "Transferir a la cuenta de siempre" } });
    fireEvent.click(within(ventana()).getByRole("button", { name: "Seguir" }));
    fireEvent.click(await within(ventana()).findByRole("button", { name: "Emitir" }));
    await waitFor(() => expect(claves).toHaveLength(2));
    expect(claves[0]).not.toBe(claves[1]);
  });

  it("sin nadie que lo reciba, solo el aviso y Cancelar", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: QUIEN_EMITE,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista({ ...EMISOR, destinatarios_posibles: 0 })),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: NUEVO }));
    expect(within(ventana()).getByText(SIN_DESTINATARIOS)).toBeTruthy();
    expect(within(ventana()).getByRole("link", { name: "Ir al padrón" })).toBeTruthy();
    expect(within(ventana()).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancelar"]);
    expect(within(ventana()).queryByLabelText("Título")).toBeNull();
  });

  it("quien emite ve cuántos lo recibieron y publica una corrección que lo cita", async () => {
    const conEnvios: Comunicado = { ...DE_A, corregido: false, corregido_por: [], envios: { destinatarios: 4, enviados: 4 } };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: QUIEN_EMITE,
      [`GET ${BASE}/comunicados`]: () => Response.json(lista({ ...EMISOR, elementos: [resumen(conEnvios)] })),
      [`GET ${BASE}/comunicados/${A}`]: () => Response.json(conEnvios),
      [`POST ${BASE}/comunicados`]: () => Response.json({ ...DE_B, envios: { destinatarios: 4, enviados: 4 } }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(within(await screen.findByRole("list", { name: "Comunicados" })).getByRole("button"));
    const ficha = await screen.findByRole("dialog", { name: DE_A.titulo });
    expect(within(ficha).getByText(textoEnvios({ destinatarios: 4, enviados: 4 }))).toBeTruthy();
    fireEvent.click(within(ficha).getByRole("button", { name: CORREGIR }));
    const escribir = await screen.findByRole("dialog", { name: CORREGIR });
    expect(within(escribir).getByText(/Corrige a «Corte de agua SINTETICO»/)).toBeTruthy();
    fireEvent.change(within(escribir).getByLabelText("Título"), { target: { value: "Corrección: es el miércoles" } });
    fireEvent.change(within(escribir).getByLabelText("Texto"), { target: { value: "El corte es el miércoles." } });
    fireEvent.click(within(escribir).getByRole("button", { name: "Seguir" }));
    fireEvent.click(await within(screen.getByRole("dialog", { name: CORREGIR })).findByRole("button", { name: "Emitir" }));
    expect(await screen.findByText(salioPara(4))).toBeTruthy();
    const [pedido] = pedidos(api, "POST", `${BASE}/comunicados`);
    expect((await pedido?.json()) as unknown).toMatchObject({ corrige_a: A });
  });
});
