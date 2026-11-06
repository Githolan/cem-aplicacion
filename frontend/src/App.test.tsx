import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AvisoVersion } from "./AvisoVersion";
import type { Salud } from "./api/cliente";
import { manifiesto } from "./manifiesto";
import { NOMBRE_MODO_LECTURA } from "./ModoLectura";
import { ContextoVersion, type Version } from "./version";

const SALUD: Salud = {
  estado: "ok",
  version: "0.1.0",
  sha_imagen: "0849ee9cafe0000000000000000000000000000",
  sha_contratos: "",
  version_contrato: "0.1.0",
};

const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: "ana@ejemplo.com",
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};

const INICIO = { level: 1, name: "Tu consorcio, en tu mano" };
const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };
const RUTA_SESION = "/api/v1/auth/sesion";

const SIN_VERSION_NUEVA: Version = { hayVersionNueva: false, actualizar: () => undefined };

type Respuesta = () => Response | Promise<Response>;

const sinSesion: Respuesta = () =>
  Response.json({ codigo: "no_autenticado", mensaje: "x", id_correlacion: "c" }, { status: 401 });

/** Responde por ruta, con sesión y servicio sanos; lo que no está previsto falla la prueba. */
function backend({
  sesion,
  salud,
  consorcios = [],
  invitaciones = [],
}: { sesion?: Respuesta; salud?: Respuesta; consorcios?: object[]; invitaciones?: object[] } = {}) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === RUTA_SESION) return sesion ? sesion() : Response.json(SESION);
    if (camino === "/health") return salud ? salud() : Response.json(SALUD);
    if (camino === "/api/v1/me/consorcios") return Response.json(consorcios);
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: invitaciones });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    if (camino.endsWith("/mis-acciones")) return Response.json({ acciones: [] });
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function caminosDe(simulado: ReturnType<typeof vi.fn>): string[] {
  return simulado.mock.calls.map((c) => new URL((c as unknown as [Request])[0].url).pathname);
}

function montar(ruta = "/", version: Version = SIN_VERSION_NUEVA) {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={version}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[ruta]}>
            <App />
          </MemoryRouter>
          <AvisoVersion />
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página de inicio", () => {
  it("muestra el estado del backend leído de /health", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    montar();

    expect(await screen.findByRole("img", { name: "El servicio funciona" })).toBeTruthy();
    // La versión queda al pie de Mi cuenta: el inicio no la muestra.
    expect(screen.queryByText("0849ee9")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(caminosDe(pedido)).toContain("/health");
  });

  it("avisa cuando el backend no responde", async () => {
    vi.stubGlobal("fetch", backend({ salud: () => new Response("no available server", { status: 503 }) }));

    montar();

    expect(await screen.findByRole("img", { name: "El servicio no funciona" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("El servicio no funciona");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend());
    const { container } = montar();
    await screen.findByRole("img", { name: "El servicio funciona" });

    // jsdom no calcula estilos: el contraste se mide en el navegador, no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("/ decide por la sesión", () => {
  it("sin sesión lleva a /entrar y no consulta el estado del servicio", async () => {
    const pedido = backend({ sesion: sinSesion });
    vi.stubGlobal("fetch", pedido);

    montar();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(caminosDe(pedido)).not.toContain("/health");
  });

  it("control: con sesión muestra el inicio y se queda ahí", async () => {
    vi.stubGlobal("fetch", backend());

    montar();

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    await screen.findByRole("img", { name: "El servicio funciona" });
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it.each<[string, Respuesta]>([
    ["sin la red", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["con el servicio caído (503)", () => new Response("no available server", { status: 503 })],
  ])("%s no se sabe si hay sesión: muestra el inicio, no /entrar", async (_, sesion) => {
    const pedido = backend({ sesion });
    vi.stubGlobal("fetch", pedido);

    montar();

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    await screen.findByRole("img", { name: "El servicio funciona" });
    expect(caminosDe(pedido)).toContain(RUTA_SESION);
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it("mientras consulta dice «Cargando…», sin el inicio ni /entrar", async () => {
    let soltar: () => void = () => undefined;
    const listo = new Promise<void>((r) => {
      soltar = r;
    });
    vi.stubGlobal(
      "fetch",
      backend({
        sesion: async () => {
          await listo;
          return Response.json(SESION);
        },
      }),
    );

    montar();

    expect(screen.getByText("Cargando…").getAttribute("role")).toBe("status");
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
    soltar();

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByText("Cargando…")).toBeNull();
  });
});

describe("cabecera", () => {
  it("tiene el botón del modo lectura ampliada (RF53)", () => {
    vi.stubGlobal("fetch", backend());
    montar();
    const cabecera = screen.getByRole("banner");
    const boton = within(cabecera).getByRole("button", { name: NOMBRE_MODO_LECTURA });
    expect(boton.getAttribute("aria-pressed")).toBe("false");
  });

  it("la marca es un enlace al inicio, desde cualquier pantalla", async () => {
    vi.stubGlobal("fetch", backend());
    montar("/no-existe");
    const marca = within(screen.getByRole("banner")).getByRole("link", { name: "Consorcio en Mano" });
    expect(marca.getAttribute("href")).toBe("/");

    fireEvent.click(marca);

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "No encontramos esa página" })).toBeNull();
  });
});

describe("inicio sin edificios", () => {
  const SIN_EDIFICIO = /^Todavía no participás de ningún edificio\./;
  const PARA_SUMARTE = /Para sumarte, abrí el enlace de la invitación que te llegó por correo\./;

  it("dice por qué no hay secciones y cómo sumarse", async () => {
    vi.stubGlobal("fetch", backend());
    montar();

    const texto = await screen.findByText(SIN_EDIFICIO);
    await vi.waitFor(() => expect(texto.textContent).toMatch(PARA_SUMARTE));
    expect(screen.getByRole("link", { name: "Mi cuenta" })).toBeTruthy();
  });

  it("control: con un edificio no lo dice", async () => {
    const pedido = backend({ consorcios: [{ id: "c1", nombre: "Edificio Azul", vinculos: [] }] });
    vi.stubGlobal("fetch", pedido);
    montar();

    await vi.waitFor(() => expect(caminosDe(pedido)).toContain("/api/v1/consorcios/c1/mis-acciones"));
    await screen.findByRole("img", { name: "El servicio funciona" });
    expect(screen.queryByText(SIN_EDIFICIO)).toBeNull();
  });

  it("con una invitación a la vista no manda a buscarla al correo", async () => {
    const invitacion = {
      id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
      consorcio: "Edificio Azul",
      unidad: "1° A",
      tipo: "inquilino",
      nombre: "Ana",
      invitada_por: null,
      vence_en: "2026-11-20T00:00:00Z",
    };
    vi.stubGlobal("fetch", backend({ invitaciones: [invitacion] }));
    montar();

    expect(await screen.findByRole("button", { name: "Aceptar la invitación" })).toBeTruthy();
    expect((await screen.findByText(SIN_EDIFICIO)).textContent).not.toMatch(PARA_SUMARTE);
  });
});

describe("rutas", () => {
  it("una ruta desconocida muestra la página de no encontrada, sin consultar al backend", () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);

    montar("/no-existe");

    expect(screen.getByRole("heading", { name: "No encontramos esa página" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Volver al inicio" }).getAttribute("href")).toBe("/");
    expect(pedido).not.toHaveBeenCalled();
  });
});

describe("versión nueva (documento 13, §14.4)", () => {
  it("en una ruta conocida espera el botón: no se aplica sola", async () => {
    vi.stubGlobal("fetch", backend());
    const actualizar = vi.fn();

    const { container } = montar("/", { hayVersionNueva: true, actualizar });

    await screen.findByRole("img", { name: "El servicio funciona" });
    expect(screen.getByRole("alert").textContent).toContain("Hay una versión nueva");
    expect(actualizar).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    expect(actualizar).toHaveBeenCalled();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("en una ruta que esta versión no conoce, se aplica sola", async () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);
    const actualizar = vi.fn();

    const { container } = montar("/ruta-de-una-version-nueva", { hayVersionNueva: true, actualizar });

    expect(actualizar).toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Actualizando la aplicación" })).toBeTruthy();
    expect(screen.queryByText("No encontramos esa página")).toBeNull();
    expect(pedido).not.toHaveBeenCalled();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("sin versión nueva, una ruta desconocida no actualiza nada", () => {
    vi.stubGlobal("fetch", vi.fn());
    const actualizar = vi.fn();

    montar("/no-existe", { hayVersionNueva: false, actualizar });

    expect(screen.getByRole("heading", { name: "No encontramos esa página" })).toBeTruthy();
    expect(actualizar).not.toHaveBeenCalled();
  });

  it("sin ProveedorVersion falla en vez de suponer que no hay versión nueva", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<AvisoVersion />)).toThrow(/fuera de ProveedorVersion/);
  });
});

describe("manifiesto de la PWA", () => {
  it("es instalable: standalone, inicio en / e iconos 192, 512 y maskable", () => {
    expect(manifiesto.display).toBe("standalone");
    expect(manifiesto.start_url).toBe("/");
    const iconos = manifiesto.icons ?? [];
    expect(iconos.some((i) => i.sizes === "192x192" && i.type === "image/png")).toBe(true);
    expect(iconos.some((i) => i.sizes === "512x512" && !i.purpose)).toBe(true);
    expect(iconos.some((i) => i.purpose === "maskable")).toBe(true);
  });
});
