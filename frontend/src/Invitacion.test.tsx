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

/**
 * Deja en vilo los pedidos a `camino` hasta que la prueba llame a `soltar`: así se ve el estado de
 * carga, que con un fetch simulado que responde al instante no llega a pintarse.
 */
function retener(simulado: (pedido: Request) => Promise<Response>, camino: string) {
  let soltar: () => void = () => undefined;
  const listo = new Promise<void>((r) => {
    soltar = r;
  });
  const retenido = vi.fn(async (pedido: Request) => {
    if (new URL(pedido.url).pathname === camino) await listo;
    return simulado(pedido);
  });
  return { retenido, soltar: () => soltar() };
}

const estado = () => screen.getByRole("status");

describe("página /invitacion: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta dice «Buscando la invitación…» y lo saca al llegar la respuesta", async () => {
    const { retenido, soltar } = retener(backend(() => Response.json(ACEPTADA)), "/api/v1/auth/invitacion/consultar");
    vi.stubGlobal("fetch", retenido);
    abrir(`/invitacion#token=${TOKEN}`);

    expect(estado().textContent).toBe("Buscando la invitación…");
    expect(screen.queryByRole("button", { name: "Aceptar la invitación" })).toBeNull();
    soltar();

    expect(await screen.findByRole("button", { name: "Aceptar la invitación" })).toBeTruthy();
    expect(screen.queryByText("Buscando la invitación…")).toBeNull();
  });

  it("carga: mientras acepta dice «Aceptando…» con el botón deshabilitado", async () => {
    const { retenido, soltar } = retener(backend(() => Response.json(ACEPTADA)), "/api/v1/auth/invitacion/aceptar");
    vi.stubGlobal("fetch", retenido);
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(estado().textContent).toBe("Aceptando…");
    expect((screen.getByRole("button", { name: "Aceptar la invitación" }) as HTMLButtonElement).disabled).toBe(true);
    soltar();

    expect(await screen.findByText("Listo: ya sos parte de Edificio Alvear 100.")).toBeTruthy();
    expect(screen.queryByText("Aceptando…")).toBeNull();
  });

  it("sin red al consultar: lo dice y no ofrece aceptar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
  });

  it("sin red al aceptar: lo dice y no da la invitación por aceptada", async () => {
    vi.stubGlobal(
      "fetch",
      backend(() => {
        throw new TypeError("Failed to fetch");
      }),
    );
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(screen.queryByText(/ya sos parte/)).toBeNull();
  });

  // DEFECTO (pruebas/pantallas-minimas, 20-10-2026): ante cualquier respuesta que no sea 200, 404
  // o 409, la página pasa a "sin_servicio" (Invitacion.tsx:64 y :84) y dice "Revisá la conexión"
  // (Invitacion.tsx:150-154). Un 403 `csrf_invalido` (el backend lo responde con Sec-Fetch-Site de
  // otro sitio o sin el X-CSRF-Token de una sesión válida: backend/app/identidad/invitaciones.py,
  // `aceptar` y `persona_de_la_sesion`) y un 503 de la aplicación no son de la conexión. La regla
  // E34 (src/fallas.ts) pide no mandar a revisar la conexión en esos casos; /invitaciones y
  // /mandatos ya la siguen. Queda pendiente hasta que se corrija.
  it.todo("permiso insuficiente (403 csrf_invalido) al aceptar no se explica como un problema de conexión", async () => {
    vi.stubGlobal("fetch", backend(() => error("csrf_invalido", 403)));
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/No pudimos/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
});

describe("encabezadoCsrf", () => {
  it("lee la cookie CSRF entre otras y no devuelve nada sin ella", () => {
    expect(encabezadoCsrf("a=1; __Host-cem_csrf=x=y; b=2")).toEqual({ "X-CSRF-Token": "x=y" });
    expect(encabezadoCsrf("a=1")).toEqual({});
    expect(encabezadoCsrf("")).toEqual({});
  });
});
