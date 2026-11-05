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

const INSTALAR = { name: /Instalá Consorcio en Mano/ };
const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };
const INICIO = { level: 1, name: "Tu consorcio, en tu mano" };
const ENLACE_INSTALAR = { name: "Instalá la aplicación en tu teléfono" };
const NO_SE_INSTALO = /No se instaló/;

/** El backend visto desde la instalación: la sesión y el estado del servicio. */
function backend(sesion: "con_sesion" | "sin_sesion") {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/health") return new Response("", { status: 503 });
    if (camino !== "/api/v1/auth/sesion") throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
    if (sesion === "con_sesion") return Response.json({});
    return Response.json({ codigo: "no_autenticado", mensaje: "x", id_correlacion: "c" }, { status: 401 });
  });
}

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

function ofrecerInstalacion(
  outcome: "accepted" | "dismissed" = "accepted",
  prompt: () => Promise<void> = async () => {},
) {
  const evento = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
    prompt: vi.fn(prompt),
    userChoice: Promise.resolve({ outcome }),
  });
  act(() => {
    window.dispatchEvent(evento);
  });
  return evento;
}

async function tocarInstalar() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Instalar la aplicación" }));
  });
}

function terminarInstalacion() {
  act(() => {
    window.dispatchEvent(new Event("appinstalled"));
  });
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
    await tocarInstalar();

    expect(evento.prompt).toHaveBeenCalledOnce();
    // El evento sirve una sola vez: el botón desaparece después de usarlo.
    expect(screen.queryByRole("button", { name: "Instalar la aplicación" })).toBeNull();
    // Aceptó: falta el `appinstalled` del navegador; hasta entonces no se avanza ni hay error.
    expect(screen.getByText("Instalando…")).toBeTruthy();
    expect(screen.queryByText(NO_SE_INSTALO)).toBeNull();
    expect(screen.getByRole("heading", INSTALAR)).toBeTruthy();
  });

  it("si la persona cancela, lo dice y sigue en /instalar", async () => {
    simularNavegador(AGENTE_ANDROID);
    montar();
    const evento = ofrecerInstalacion("dismissed");
    await tocarInstalar();

    expect(evento.prompt).toHaveBeenCalledOnce();
    expect(await screen.findByText(NO_SE_INSTALO)).toBeTruthy();
    expect(screen.queryByText("Instalando…")).toBeNull();
    expect(screen.getByRole("heading", INSTALAR)).toBeTruthy();
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it("si el navegador falla al abrir la instalación, lo dice y sigue en /instalar", async () => {
    simularNavegador(AGENTE_ANDROID);
    montar();
    ofrecerInstalacion("accepted", () => Promise.reject(new Error("el navegador no pudo")));
    await tocarInstalar();

    expect(await screen.findByText(NO_SE_INSTALO)).toBeTruthy();
    expect(screen.getByRole("heading", INSTALAR)).toBeTruthy();
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it("terminada la instalación sigue a /entrar con el aviso", async () => {
    vi.stubGlobal("fetch", backend("sin_sesion"));
    simularNavegador(AGENTE_ANDROID);
    montar();
    ofrecerInstalacion();
    await tocarInstalar();
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();

    terminarInstalacion();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.getByText(/Ya está instalada. Seguí acá/)).toBeTruthy();
    expect(screen.queryByRole("heading", INSTALAR)).toBeNull();
    // Recién instalada, /entrar no vuelve a ofrecer la instalación.
    expect(screen.queryByRole("link", ENLACE_INSTALAR)).toBeNull();
  });

  it("con sesión, la redirección de /entrar al inicio conserva el aviso de la instalación", async () => {
    vi.stubGlobal("fetch", backend("con_sesion"));
    simularNavegador(AGENTE_ANDROID);
    montar();

    terminarInstalacion();

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.getByText(/Ya está instalada. Seguí acá/)).toBeTruthy();
  });

  it("«Seguir sin instalar» lleva a /entrar, sin aviso", async () => {
    vi.stubGlobal("fetch", backend("sin_sesion"));
    simularNavegador(AGENTE_ANDROID);
    montar();
    expect(screen.queryByRole("link", { name: "Volver al inicio" })).toBeNull();
    // Sin sesión `/` también termina en /entrar: el destino del enlace se afirma aparte.
    const enlace = screen.getByRole("link", { name: "Seguir sin instalar" });
    expect(enlace.getAttribute("href")).toBe("/entrar");

    fireEvent.click(enlace);

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByText(/Ya está instalada/)).toBeNull();
  });

  it("abierta desde el ícono no hay nada que instalar: va al inicio", async () => {
    vi.stubGlobal("fetch", backend("con_sesion"));
    simularNavegador(AGENTE_ANDROID, true);
    montar();

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("heading", INSTALAR)).toBeNull();
    expect(screen.queryByText(/Ya está instalada/)).toBeNull();
  });

  it("control: en el navegador se queda en /instalar y no consulta la sesión", () => {
    const pedido = backend("con_sesion");
    vi.stubGlobal("fetch", pedido);
    simularNavegador(AGENTE_ANDROID);
    montar();

    expect(screen.getByRole("heading", INSTALAR)).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    simularNavegador(AGENTE_IPHONE);
    const { container } = montar();

    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("enlace en el inicio", () => {
  it("lleva a /instalar y se oculta si la aplicación ya está instalada", async () => {
    vi.stubGlobal("fetch", backend("con_sesion"));
    simularNavegador(AGENTE_ANDROID);
    montar("/");
    const enlace = await screen.findByRole("link", ENLACE_INSTALAR);
    expect(enlace.getAttribute("href")).toBe("/instalar");
    cleanup();

    simularNavegador(AGENTE_ANDROID, true);
    montar("/");
    await screen.findByRole("heading", INICIO);
    expect(screen.queryByRole("link", ENLACE_INSTALAR)).toBeNull();
  });

  it("también está en /entrar, y ahí se oculta igual si la aplicación ya está instalada", async () => {
    vi.stubGlobal("fetch", backend("sin_sesion"));
    simularNavegador(AGENTE_ANDROID);
    montar("/entrar");
    expect(screen.getByRole("link", ENLACE_INSTALAR).getAttribute("href")).toBe("/instalar");
    cleanup();

    simularNavegador(AGENTE_ANDROID, true);
    montar("/entrar");
    expect(screen.getByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("link", ENLACE_INSTALAR)).toBeNull();
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
