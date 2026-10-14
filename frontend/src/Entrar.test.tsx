import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { tokenDelFragmento } from "./Entrar";

const TOKEN = "A".repeat(21) + "_-" + "b".repeat(20);

const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: "ana@ejemplo.com",
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};

function abrir(url: string) {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // StrictMode, como en main.tsx: monta dos veces y el canje tiene que salir una sola.
  return render(
    <StrictMode>
      <QueryClientProvider client={consultas}>
        <MemoryRouter initialEntries={["/entrar"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("página /entrar", () => {
  it("canjea el token del fragmento por POST, en el cuerpo, una sola vez, y lo borra de la URL", async () => {
    const pedido = vi.fn(async () => Response.json(SESION));
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText("Entraste.")).toBeTruthy();
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    const [canje, ...otros] = pedidosDe(pedido);
    expect(otros).toEqual([]);
    if (canje === undefined) throw new Error("no hubo pedido de canje");
    const url = new URL(canje.url);
    expect([canje.method, url.pathname, url.search]).toEqual(["POST", "/api/v1/auth/canje", ""]);
    expect(await canje.json()).toEqual({ token: TOKEN });
  });

  it("si el backend rechaza, muestra un error genérico", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ codigo: "enlace_invalido", mensaje: "x", id_correlacion: "c" }, { status: 401 })),
    );

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(/No pudimos hacerte entrar con este enlace/)).toBeTruthy();
    expect(screen.queryByText("Entraste.")).toBeNull();
    expect(window.location.hash).toBe("");
  });

  it("sin la red, también muestra el error genérico", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(/No pudimos hacerte entrar con este enlace/)).toBeTruthy();
  });

  it("sin token en el fragmento no consulta al backend", () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);

    abrir("/entrar");

    expect(screen.getByText(/abrí el enlace que te mandamos por correo/)).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(SESION)));
    const { container } = abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText("Entraste.");

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("tokenDelFragmento", () => {
  it("lee token= del fragmento y nada más", () => {
    expect(tokenDelFragmento(`#token=${TOKEN}`)).toBe(TOKEN);
    expect(tokenDelFragmento(`#otra=1&token=${TOKEN}`)).toBe(TOKEN);
    expect(tokenDelFragmento("")).toBeNull();
    expect(tokenDelFragmento("#otra=1")).toBeNull();
  });
});
