import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { esIphone, escucharInstalacion, reiniciarInstalacion } from "./instalacion";

const AGENTE_ANDROID =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36";
const AGENTE_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

function montar(ruta = "/instalar") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={consultas}>
      <MemoryRouter initialEntries={[ruta]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function simularNavegador(agente: string, standalone = false) {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agente);
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: standalone && consulta === "(display-mode: standalone)",
  }));
}

function ofrecerInstalacion() {
  const evento = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
    prompt: vi.fn(async () => {}),
    userChoice: Promise.resolve({ outcome: "accepted" as const }),
  });
  act(() => {
    window.dispatchEvent(evento);
  });
  return evento;
}

beforeAll(() => {
  escucharInstalacion();
});

afterEach(() => {
  cleanup();
  act(() => reiniciarInstalacion());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("página /instalar", () => {
  it("en iPhone explica Compartir → Agregar a inicio, sin botón", () => {
    simularNavegador(AGENTE_IPHONE);
    montar();

    expect(screen.getByRole("heading", { name: /Instalá Consorcio en Mano/ })).toBeTruthy();
    expect(screen.getByText("Agregar a inicio")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Instalar la aplicación" })).toBeNull();
  });

  it("cuando el navegador lo permite, el botón abre la instalación del navegador", async () => {
    simularNavegador(AGENTE_ANDROID);
    montar();
    expect(screen.getByText(/Tocá el menú/)).toBeTruthy();

    const evento = ofrecerInstalacion();
    expect(evento.defaultPrevented).toBe(true);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Instalar la aplicación" }));
    });

    expect(evento.prompt).toHaveBeenCalledOnce();
    // El evento sirve una sola vez: el botón desaparece después de usarlo.
    expect(screen.queryByRole("button", { name: "Instalar la aplicación" })).toBeNull();
  });

  it("abierta desde el ícono, dice que ya está instalada", () => {
    simularNavegador(AGENTE_ANDROID, true);
    montar();

    expect(screen.getByText(/Ya está instalada/)).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    simularNavegador(AGENTE_IPHONE);
    const { container } = montar();

    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("enlace en el inicio", () => {
  it("lleva a /instalar y se oculta si la aplicación ya está instalada", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    simularNavegador(AGENTE_ANDROID);
    montar("/");
    const enlace = screen.getByRole("link", { name: "Instalá la aplicación en tu teléfono" });
    expect(enlace.getAttribute("href")).toBe("/instalar");
    cleanup();

    simularNavegador(AGENTE_ANDROID, true);
    montar("/");
    expect(screen.queryByRole("link", { name: "Instalá la aplicación en tu teléfono" })).toBeNull();
  });
});

describe("esIphone", () => {
  it("reconoce iPhone y iPad (que se presenta como Mac táctil), no Android ni Mac", () => {
    expect(esIphone(AGENTE_IPHONE, 5)).toBe(true);
    expect(esIphone("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", 5)).toBe(true);
    expect(esIphone("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", 0)).toBe(false);
    expect(esIphone(AGENTE_ANDROID, 5)).toBe(false);
  });
});
