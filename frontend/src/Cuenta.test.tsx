import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const ACTUAL = "11111111-1111-4111-8111-111111111111";
const OTRA = "22222222-2222-4222-8222-222222222222";
const TERCERA = "33333333-3333-4333-8333-333333333333";
const YO = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: "ana@ejemplo.com",
  sesion_id: ACTUAL,
  vence_en: "2026-11-15T20:00:00Z",
};

function sesion(id: string, dispositivo: string, actual: boolean) {
  return {
    id,
    dispositivo,
    actual,
    creada_en: "2026-10-01T12:00:00Z",
    ultimo_uso_en: "2026-10-16T12:00:00Z",
    vence_en: "2026-11-15T12:00:00Z",
  };
}

const SESIONES = [
  sesion(ACTUAL, "Firefox en Windows", true),
  sesion(OTRA, "Chrome en Android", false),
  sesion(TERCERA, "Safari en iPhone", false),
];
const TOTP_INACTIVO = { activo: false, activado_en: null, codigos_recuperacion_restantes: 0, verificado_en: null };
const TOTP_ACTIVO = {
  activo: true,
  activado_en: "2026-10-15T20:00:00Z",
  codigos_recuperacion_restantes: 10,
  verificado_en: null,
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

const sinCuerpo = (status: number) => () => new Response(null, { status });

type Respuestas = Record<string, () => Response>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

/** El backend de una cuenta con sesión: quién soy, las sesiones y el segundo factor. */
function conSesion(extra: Respuestas = {}, totp: object = TOTP_INACTIVO) {
  return backend({
    "GET /api/v1/auth/sesion": () => Response.json(YO),
    "GET /api/v1/auth/sesiones": () => Response.json({ elementos: SESIONES }),
    "GET /api/v1/auth/totp": () => Response.json(totp),
    ...extra,
  });
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

function pedido(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request | undefined {
  return pedidosDe(simulado).find((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/cuenta") {
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

/** El botón «Cerrar esta sesión» de un dispositivo: lo distingue su descripción. */
function botonCerrar(dispositivo: string) {
  return screen.findByRole("button", { name: "Cerrar esta sesión", description: dispositivo });
}

const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };

beforeEach(() => {
  // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /cuenta", () => {
  it("el inicio tiene el enlace a Mi cuenta", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ estado: "ok" })));
    abrir("/");
    expect(screen.getByRole("link", { name: "Mi cuenta" }).getAttribute("href")).toBe("/cuenta");
  });

  it("sin sesión (401) manda a /entrar", async () => {
    const simulado = backend({
      "GET /api/v1/auth/sesion": () => error("no_autenticado", 401),
      "GET /api/v1/auth/sesiones": () => error("no_autenticado", 401),
      "GET /api/v1/auth/totp": () => error("no_autenticado", 401),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Mi cuenta" })).toBeNull();
  });

  it("muestra quién soy, las sesiones con la actual marcada y el segundo factor inactivo", async () => {
    vi.stubGlobal("fetch", conSesion());
    abrir();

    expect(await screen.findByText("Ana Pérez")).toBeTruthy();
    expect(screen.getByText("ana@ejemplo.com")).toBeTruthy();
    const lista = screen.getByRole("heading", { name: "Dónde tenés la sesión abierta" }).parentElement!;
    const items = within(lista).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    const [actual, otra] = items as [HTMLElement, HTMLElement, HTMLElement];
    expect(actual.getAttribute("aria-current")).toBe("true");
    expect(actual.textContent).toContain("Firefox en Windows (este dispositivo)");
    expect(otra.getAttribute("aria-current")).toBeNull();
    expect(within(lista).getAllByRole("button", { name: "Cerrar esta sesión" })).toHaveLength(3);
    // Cada botón dice de qué sesión es a quien usa un lector de pantalla.
    expect(
      within(otra).getByRole("button", { name: "Cerrar esta sesión", description: "Chrome en Android" }),
    ).toBeTruthy();
    expect(screen.getByText("No está activado.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Activar el segundo factor" }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
  });

  it("con el segundo factor activo lo dice", async () => {
    vi.stubGlobal("fetch", conSesion({}, TOTP_ACTIVO));
    abrir();

    expect(await screen.findByText(/Está activado/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ver el segundo factor" }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
  });

  it("cierra otra sesión con CSRF y la saca de la lista", async () => {
    const simulado = conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await botonCerrar("Chrome en Android"));

    expect(await screen.findByText("Listo: cerramos la sesión de Chrome en Android.")).toBeTruthy();
    expect(screen.queryByText("Chrome en Android")).toBeNull();
    expect(screen.getByRole("heading", { name: "Mi cuenta" })).toBeTruthy();
    const revocar = pedido(simulado, "POST", `/api/v1/auth/sesiones/${OTRA}/revocar`);
    expect(revocar?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("cerrar la sesión actual lleva a /entrar", async () => {
    const simulado = conSesion({ [`POST /api/v1/auth/sesiones/${ACTUAL}/revocar`]: sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(
      await botonCerrar("Firefox en Windows (este dispositivo)"),
    );

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    const revocar = pedido(simulado, "POST", `/api/v1/auth/sesiones/${ACTUAL}/revocar`);
    expect(revocar?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si el backend rechaza el cierre (CSRF), lo dice y no saca la sesión", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: () => error("csrf_invalido", 403) }),
    );
    abrir();

    fireEvent.click(await botonCerrar("Chrome en Android"));

    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
  });

  it("cierra las otras sesiones con CSRF y deja solo la actual", async () => {
    const simulado = conSesion({
      "POST /api/v1/auth/sesiones/revocar-otras": () => Response.json({ revocadas: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Cerrar las otras sesiones" }));

    expect(await screen.findByText("Listo: cerramos 2 sesiones en otros dispositivos.")).toBeTruthy();
    const lista = screen.getByRole("heading", { name: "Dónde tenés la sesión abierta" }).parentElement!;
    expect(within(lista).getAllByRole("listitem")).toHaveLength(1);
    // Sin otras sesiones, el botón ya no tiene sentido.
    expect(screen.queryByRole("button", { name: "Cerrar las otras sesiones" })).toBeNull();
    const pedidoOtras = pedido(simulado, "POST", "/api/v1/auth/sesiones/revocar-otras");
    expect(pedidoOtras?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("salir con CSRF lleva a /entrar", async () => {
    const simulado = conSesion({ "POST /api/v1/auth/salir": sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Salir" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(pedido(simulado, "POST", "/api/v1/auth/salir")?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si la sesión venció entre medio, una acción lleva a /entrar", async () => {
    vi.stubGlobal("fetch", conSesion({ "POST /api/v1/auth/sesiones/revocar-otras": () => error("no_autenticado", 401) }));
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Cerrar las otras sesiones" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it("sin backend lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();

    expect(await screen.findByText(/No pudimos consultar tu cuenta/)).toBeTruthy();
  });

  it.each([
    ["la cuenta con sesiones", "lista"],
    ["un cierre hecho", "aviso"],
    ["sin servicio", "sin_servicio"],
  ])("en %s no tiene faltas de accesibilidad detectables por axe", async (_, estado) => {
    vi.stubGlobal(
      "fetch",
      estado === "sin_servicio"
        ? backend({ "GET /api/v1/auth/sesion": () => error("interno", 500) })
        : conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: sinCuerpo(204) }),
    );
    const { container } = abrir();
    if (estado === "sin_servicio") await screen.findByText(/No pudimos consultar tu cuenta/);
    else await screen.findByText("Ana Pérez");
    if (estado === "aviso") {
      fireEvent.click(await botonCerrar("Chrome en Android"));
      await screen.findByText(/Listo: cerramos/);
    }

    // jsdom no calcula colores: el contraste lo mide axe en el navegador (e2e/cuenta.e2e.ts).
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});
