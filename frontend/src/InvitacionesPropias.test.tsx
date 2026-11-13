import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { PREFIJO_COPIA } from "./copiaContactos";
import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const INVITACION = {
  id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
  consorcio: "Edificio Azul",
  unidad: "1° A",
  tipo: "inquilino",
  nombre: "Ana",
  invitada_por: "Beto",
  vence_en: "2026-11-20T00:00:00Z",
};
const LISTA = "/api/v1/me/invitaciones";
const ACEPTAR = `${LISTA}/${INVITACION.id}/aceptar`;
const SECCION = { name: "Invitaciones pendientes" };
const BOTON = { name: "Aceptar la invitación" };
const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };

function error(codigo: string, status: number, mensaje = "x") {
  return Response.json({ codigo, mensaje, id_correlacion: "c" }, { status });
}

type Respuesta = () => Response | Promise<Response>;

/**
 * El inicio de quien todavía no participa de ningún edificio. Como el backend, aceptar saca la
 * invitación de la lista y suma el edificio, con lo que alcanza en él; un 401 al aceptar es que la
 * sesión venció, y `/auth/sesion` lo dice también: con un 200 fijo, /entrar volvería al inicio.
 */
function backend({ lista, aceptar }: { lista?: Respuesta; aceptar?: Respuesta } = {}) {
  let aceptada = false;
  let vencida = false;
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/api/v1/auth/sesion") return vencida ? error("no_autenticado", 401) : Response.json({});
    if (camino === "/health") return error("servicio_no_disponible", 503);
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    if (camino === "/api/v1/me/consorcios")
      return Response.json(aceptada ? [{ id: C1, nombre: "Edificio Azul", vinculos: [] }] : []);
    if (camino === `/api/v1/consorcios/${C1}/mis-acciones`) return Response.json({ acciones: ["contactos.ver"] });
    if (camino === LISTA) return lista ? lista() : Response.json({ elementos: aceptada ? [] : [INVITACION] });
    if (camino === ACEPTAR) {
      const respuesta = await (aceptar ?? (() => Response.json({ consorcio_id: C1 })))();
      aceptada = respuesta.ok;
      vencida = respuesta.status === 401;
      return respuesta;
    }
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => new URL(p.url).pathname === camino);
}

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/"]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

beforeEach(() => {
  // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("invitaciones en el inicio", () => {
  it("muestra la invitación con sus datos y el tipo en palabras", async () => {
    vi.stubGlobal("fetch", backend());
    abrir();

    const seccion = within(await screen.findByRole("region", SECCION));
    expect(seccion.getByRole("heading", { name: "Edificio Azul" })).toBeTruthy();
    expect(seccion.getByText("1° A")).toBeTruthy();
    expect(seccion.getByText("inquilino/a")).toBeTruthy();
    expect(seccion.getByText("Beto")).toBeTruthy();
    expect(seccion.getByRole("button", { ...BOTON, description: "Edificio Azul" })).toBeTruthy();
  });

  it("control: sin invitaciones la sección no aparece, y el pedido salió igual", async () => {
    const simulado = backend({ lista: () => Response.json({ elementos: [] }) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    await waitFor(() => expect(pedidos(simulado, LISTA)).toHaveLength(1));
    await screen.findByRole("link", { name: "Cuenta" });
    await new Promise((listo) => setTimeout(listo, 0));
    expect(screen.queryByRole("region", SECCION)).toBeNull();
  });

  it("aceptar va con CSRF, avisa en el inicio, saca la invitación y suma lo que alcanza", async () => {
    const simulado = backend();
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await screen.findByRole("button", BOTON));

    expect(await screen.findByText("Listo: la cuenta ya es parte de Edificio Azul.")).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Contactos de emergencia" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("region", SECCION)).toBeNull());
    const [aceptacion, ...otras] = pedidos(simulado, ACEPTAR);
    expect(otras).toEqual([]);
    expect(aceptacion?.method).toBe("POST");
    expect(aceptacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(screen.queryByText(/Todavía no participás/)).toBeNull();
  });

  it("mientras acepta lo dice y no deja tocar de nuevo", async () => {
    let soltar: () => void = () => undefined;
    const listo = new Promise<void>((r) => {
      soltar = r;
    });
    vi.stubGlobal(
      "fetch",
      backend({
        aceptar: async () => {
          await listo;
          return Response.json({ consorcio_id: C1 });
        },
      }),
    );
    abrir();
    const boton = (await screen.findByRole("button", BOTON)) as HTMLButtonElement;

    fireEvent.click(boton);

    expect(await screen.findByText("Aceptando…")).toBeTruthy();
    expect(boton.disabled).toBe(true);
    soltar();
    expect(await screen.findByText("Listo: la cuenta ya es parte de Edificio Azul.")).toBeTruthy();
  });

  it.each([
    ["ya no sirve (404)", "invitacion_invalida", 404, "La invitación no sirve: venció, ya se usó o la retiraron."],
    [
      "el vínculo ya no corresponde (409)",
      "invitacion_vinculo_no_corresponde",
      409,
      "El vínculo de esta invitación ya no está en el padrón.",
    ],
  ])("si %s muestra lo que dice el servidor y actualiza la lista", async (_, codigo, status, mensaje) => {
    const simulado = backend({ aceptar: () => error(codigo, status, mensaje) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await screen.findByRole("button", BOTON));

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText(/ya es parte/)).toBeNull();
    await waitFor(() => expect(pedidos(simulado, LISTA)).toHaveLength(2));
  });

  it("el error del servidor sigue a la vista aunque la invitación ya no esté en la lista", async () => {
    const mensaje = "La invitación no sirve: venció, ya se usó o la retiraron.";
    let vigente = true;
    vi.stubGlobal(
      "fetch",
      backend({
        lista: () => Response.json({ elementos: vigente ? [INVITACION] : [] }),
        aceptar: () => {
          vigente = false;
          return error("invitacion_invalida", 404, mensaje);
        },
      }),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", BOTON));

    await waitFor(() => expect(screen.queryByRole("button", BOTON)).toBeNull());
    expect(within(screen.getByRole("region", SECCION)).getByText(mensaje)).toBeTruthy();
  });

  it.each<[string, Respuesta, string]>([
    ["un 503", () => error("servicio_no_disponible", 503), SERVICIO_NO_DISPONIBLE],
    ["sin red", () => Promise.reject(new TypeError("Failed to fetch")), SIN_CONEXION],
  ])("con %s lo dice y deja reintentar", async (_, falla, mensaje) => {
    let caido = true;
    vi.stubGlobal(
      "fetch",
      backend({ aceptar: () => (caido ? falla() : Response.json({ consorcio_id: C1 })) }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", BOTON));

    expect(await screen.findByText(mensaje)).toBeTruthy();
    const boton = screen.getByRole("button", BOTON) as HTMLButtonElement;
    await waitFor(() => expect(boton.disabled).toBe(false));

    caido = false;
    fireEvent.click(boton);

    expect(await screen.findByText("Listo: la cuenta ya es parte de Edificio Azul.")).toBeTruthy();
  });

  it("con la sesión vencida va a /entrar y borra las copias de los contactos", async () => {
    vi.stubGlobal("fetch", backend({ aceptar: () => error("no_autenticado", 401) }));
    localStorage.setItem(
      `${PREFIJO_COPIA}c1`,
      JSON.stringify({
        consorcio_id: "c1",
        nombre: "Edificio",
        guardada_en: "2026-10-21T12:00:00Z",
        lista: { elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" },
      }),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", BOTON));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
  });

  it("si la lista no se pudo leer lo dice y Reintentar la trae", async () => {
    let caido = true;
    vi.stubGlobal(
      "fetch",
      backend({
        lista: () => (caido ? error("servicio_no_disponible", 503) : Response.json({ elementos: [INVITACION] })),
      }),
    );
    abrir();

    const seccion = within(await screen.findByRole("region", SECCION));
    expect(seccion.getByRole("alert").textContent).toBe(SERVICIO_NO_DISPONIBLE);
    expect(seccion.queryByRole("button", BOTON)).toBeNull();

    caido = false;
    fireEvent.click(seccion.getByRole("button", { name: "Reintentar" }));

    expect(await seccion.findByRole("button", BOTON)).toBeTruthy();
    expect(seccion.queryByRole("alert")).toBeNull();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend());
    const { container } = abrir();
    await screen.findByRole("button", BOTON);

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});
