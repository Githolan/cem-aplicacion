import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { encabezadoCsrf } from "./csrf";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const TOKEN = "A".repeat(21) + "_-" + "b".repeat(20);

const RECIBIDA = {
  consorcio: "Edificio Alvear 100",
  unidad: "3° B",
  tipo: "inquilino",
  correo: "beto@ejemplo.com",
  nombre: "Beto",
  invitada_por: "Ana",
  vence_en: "2026-10-22T00:00:00Z",
};
const ACEPTADA = {
  consorcio_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  cuenta_nueva: true,
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

function abrir(url: string) {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // StrictMode, como en main.tsx: monta dos veces y la consulta tiene que salir una sola.
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/invitacion"]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

/** Responde la consulta con la invitación y la aceptación con `aceptacion`. */
function backend(aceptacion: () => Response) {
  return vi.fn(async (pedido: Request) =>
    new URL(pedido.url).pathname.endsWith("/consultar") ? Response.json(RECIBIDA) : aceptacion(),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("página /invitacion", () => {
  it("consulta una sola vez con el token en el cuerpo, lo borra de la URL y muestra la invitación", async () => {
    const pedido = backend(() => Response.json(ACEPTADA));
    vi.stubGlobal("fetch", pedido);

    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByRole("heading", { name: "Edificio Alvear 100" })).toBeTruthy();
    expect(screen.getByText("3° B")).toBeTruthy();
    expect(screen.getByText("inquilino/a")).toBeTruthy();
    expect(window.location.href).not.toContain(TOKEN);
    const [consulta, ...otros] = pedidosDe(pedido);
    expect(otros).toEqual([]);
    if (consulta === undefined) throw new Error("no hubo consulta");
    const url = new URL(consulta.url);
    expect([consulta.method, url.pathname, url.search]).toEqual([
      "POST",
      "/api/v1/auth/invitacion/consultar",
      "",
    ]);
    expect(await consulta.json()).toEqual({ token: TOKEN });
  });

  it("acepta con el nombre elegido y avisa que quedó adentro", async () => {
    const pedido = backend(() => Response.json(ACEPTADA));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);

    const nombre = await screen.findByLabelText("Tu nombre");
    fireEvent.change(nombre, { target: { value: "  Alberto " } });
    fireEvent.click(screen.getByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText("Listo: ya sos parte de Edificio Alvear 100.")).toBeTruthy();
    const aceptacion = pedidosDe(pedido)[1];
    if (aceptacion === undefined) throw new Error("no hubo aceptación");
    expect(new URL(aceptacion.url).pathname).toBe("/api/v1/auth/invitacion/aceptar");
    expect(await aceptacion.json()).toEqual({ token: TOKEN, nombre: "Alberto" });
  });

  it("con sesión abierta manda el CSRF", async () => {
    // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    const pedido = backend(() => Response.json({ ...ACEPTADA, cuenta_nueva: false }));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    await screen.findByText(/ya sos parte/);
    expect(pedidosDe(pedido)[1]?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si el correo ya tiene cuenta, manda a entrar", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_requiere_sesion", 409)));
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/Ya tenés una cuenta con este correo/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Entrá" }).getAttribute("href")).toBe("/entrar");
  });

  it("si la sesión es de otra persona, lo dice", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_de_otra_persona", 409)));
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/no es la del correo invitado/)).toBeTruthy();
  });

  it("una invitación que no sirve muestra un error genérico", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => error("invitacion_invalida", 404)));
    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByText(/Esta invitación no sirve/)).toBeTruthy();
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
  });

  it("sin token no pide nada", () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);
    abrir("/invitacion");

    expect(screen.getByText(/abrí el enlace que te llegó por correo/)).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend(() => Response.json(ACEPTADA)));
    const { container } = abrir(`/invitacion#token=${TOKEN}`);
    await screen.findByRole("button", { name: "Aceptar la invitación" });

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("encabezadoCsrf", () => {
  it("lee la cookie CSRF entre otras y no devuelve nada sin ella", () => {
    expect(encabezadoCsrf("a=1; __Host-cem_csrf=x=y; b=2")).toEqual({ "X-CSRF-Token": "x=y" });
    expect(encabezadoCsrf("a=1")).toEqual({});
    expect(encabezadoCsrf("")).toEqual({});
  });
});
