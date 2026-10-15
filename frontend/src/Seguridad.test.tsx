import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { agruparSecreto } from "./Seguridad";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const INACTIVO = {
  activo: false,
  activado_en: null,
  codigos_recuperacion_restantes: 0,
  verificado_en: null,
};
const ACTIVO = {
  activo: true,
  activado_en: "2026-10-15T22:00:00Z",
  codigos_recuperacion_restantes: 9,
  verificado_en: null,
};
const ALTA = {
  secreto: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
  uri: "otpauth://totp/Consorcio%20en%20Mano%3Aana%40ejemplo.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
};
const CODIGOS = Array.from({ length: 10 }, (_, i) => `abcd${i}-efgh${i}`);

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

/** Backend simulado: cada ruta responde lo que diga `rutas` (por sufijo de la ruta). */
function backend(rutas: Record<string, () => Response>) {
  return vi.fn(async (pedido: Request) => {
    const ruta = new URL(pedido.url).pathname.replace("/api/v1/auth/totp", "") || "/";
    const responder = rutas[ruta];
    if (responder === undefined) throw new Error(`ruta inesperada ${ruta}`);
    return responder();
  });
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/seguridad"]}>
            <App />
          </MemoryRouter>
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

describe("página /seguridad", () => {
  it("activa: alta, confirmación con el código y los diez códigos de recuperación", async () => {
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=tok");
    const pedido = backend({
      "/": () => Response.json(INACTIVO),
      "/alta": () => Response.json(ALTA),
      "/confirmar": () => Response.json({ codigos: CODIGOS }),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Activar la verificación en dos pasos" }));
    expect(await screen.findByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeTruthy();
    expect(screen.getByRole("link", { name: "abrir en la aplicación de autenticación" }).getAttribute("href")).toBe(
      ALTA.uri,
    );
    fireEvent.change(screen.getByLabelText("Código de seis números"), { target: { value: "042 917" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    expect(await screen.findByText("Quedó activada")).toBeTruthy();
    for (const c of CODIGOS) expect(screen.getByText(c)).toBeTruthy();
    const confirmacion = pedidosDe(pedido).find((p) => p.url.endsWith("/confirmar"));
    if (confirmacion === undefined) throw new Error("no hubo confirmación");
    expect(await confirmacion.json()).toEqual({ codigo: "042917" });
    expect(confirmacion.headers.get("X-CSRF-Token")).toBe("tok");
  });

  it("un código que no sirve muestra el error y deja reintentar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "/": () => Response.json(INACTIVO),
        "/alta": () => Response.json(ALTA),
        "/confirmar": () => error("segundo_factor_invalido", 400),
      }),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Activar la verificación en dos pasos" }));
    fireEvent.change(await screen.findByLabelText("Código de seis números"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/El código no sirve/);
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeTruthy();
  });

  it("desactiva verificando primero el código y después pidiendo la baja", async () => {
    const pedido = backend({
      "/": () => Response.json(ACTIVO),
      "/verificar": () =>
        Response.json({
          verificado_en: "2026-10-15T22:10:00Z",
          vale_hasta: "2026-10-15T22:20:00Z",
          codigos_recuperacion_restantes: 8,
        }),
      "/baja": () => new Response(null, { status: 204 }),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    expect(await screen.findByText("Te quedan 9 códigos de recuperación.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Para desactivarla/), { target: { value: "ABCD1-EFGH1" } });
    fireEvent.click(screen.getByRole("button", { name: "Desactivar" }));

    expect(await screen.findByText("Está desactivada")).toBeTruthy();
    const rutas = pedidosDe(pedido).map((p) => new URL(p.url).pathname);
    expect(rutas.slice(-2)).toEqual(["/api/v1/auth/totp/verificar", "/api/v1/auth/totp/baja"]);
    const verificacion = pedidosDe(pedido).find((p) => p.url.endsWith("/verificar"));
    expect(await verificacion?.json()).toEqual({ codigo: "abcd1-efgh1" });
  });

  it("si la verificación falla no pide la baja", async () => {
    const pedido = backend({
      "/": () => Response.json(ACTIVO),
      "/verificar": () => error("segundo_factor_invalido", 400),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.change(await screen.findByLabelText(/Para desactivarla/), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Desactivar" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/El código no sirve/);
    expect(pedidosDe(pedido).some((p) => p.url.endsWith("/baja"))).toBe(false);
  });

  it("sin sesión manda a entrar", async () => {
    vi.stubGlobal("fetch", backend({ "/": () => error("no_autenticado", 401) }));
    abrir();

    expect((await screen.findByRole("link", { name: "Entrá" })).getAttribute("href")).toBe("/entrar");
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend({ "/": () => Response.json(INACTIVO), "/alta": () => Response.json(ALTA) }));
    const { container } = abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar la verificación en dos pasos" }));
    await screen.findByLabelText("Código de seis números");

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("agruparSecreto", () => {
  it("separa de a cuatro", () => {
    expect(agruparSecreto("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
  });
});
