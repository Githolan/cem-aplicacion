import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { Salud } from "./api/cliente";

const SALUD: Salud = {
  estado: "ok",
  version: "0.1.0",
  sha_imagen: "0849ee9cafe0000000000000000000000000000",
  sha_contratos: "",
  version_contrato: "0.1.0",
};

function montar() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={consultas}>
      <App />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("página de inicio", () => {
  it("muestra el estado del backend leído de /health", async () => {
    const pedido = vi.fn(async () => Response.json(SALUD));
    vi.stubGlobal("fetch", pedido);

    montar();

    expect(await screen.findByText("El servicio funciona.")).toBeTruthy();
    expect(screen.getByText("0849ee9")).toBeTruthy();
    const url = new URL((pedido.mock.calls[0] as unknown as [Request])[0].url);
    expect(url.pathname).toBe("/health");
  });

  it("avisa cuando el backend no responde", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no available server", { status: 503 })));

    montar();

    expect(await screen.findByText(/No pudimos contactar al servicio/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(SALUD)));
    const { container } = montar();
    await screen.findByText("El servicio funciona.");

    // jsdom no calcula estilos: el contraste se mide en el navegador, no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});
