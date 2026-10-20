import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { VIGENCIA_ENLACE_MS, tokenDelFragmento } from "./enlace";
import { guardarToken } from "./Entrar";
import { ContextoVersion } from "./version";

const CLAVE_GUARDADO = "cem.entrar.enlace";
const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };

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
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/entrar"]}>
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
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

describe("token guardado en la pestaña (recarga o actualización a mitad de camino)", () => {
  function guardado(): { token: string; venceEn: number } | null {
    const texto = window.sessionStorage.getItem(CLAVE_GUARDADO);
    return texto === null ? null : (JSON.parse(texto) as { token: string; venceEn: number });
  }

  it("lo guarda antes de sacarlo de la URL y, después de recargar, lo canjea sin el fragmento", async () => {
    // El primer canje no termina: la recarga llega antes.
    const colgado = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", colgado);
    const antes = Date.now();
    abrir(`/entrar#token=${TOKEN}`);

    await vi.waitFor(() => expect(colgado).toHaveBeenCalledTimes(1));
    expect(window.location.href).not.toContain(TOKEN);
    const primero = guardado();
    expect(primero?.token).toBe(TOKEN);
    expect(primero!.venceEn - antes).toBeGreaterThanOrEqual(VIGENCIA_ENLACE_MS);
    expect(primero!.venceEn - Date.now()).toBeLessThanOrEqual(VIGENCIA_ENLACE_MS);

    // "Recarga": se desmonta todo y se vuelve a abrir /entrar, ya sin fragmento.
    cleanup();
    const pedido = vi.fn(async () => Response.json(SESION));
    vi.stubGlobal("fetch", pedido);
    const { container } = abrir("/entrar");

    expect(await screen.findByText("Entraste.")).toBeTruthy();
    const [canje, ...otros] = pedidosDe(pedido);
    expect(otros).toEqual([]);
    if (canje === undefined) throw new Error("no hubo pedido de canje después de recargar");
    expect(new URL(canje.url).pathname).toBe("/api/v1/auth/canje");
    expect(await canje.json()).toEqual({ token: TOKEN });
    expect(guardado()).toBeNull();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("uno guardado conserva su vencimiento: recargar no lo extiende", async () => {
    const guardadoEn = Date.now() - 60_000;
    guardarToken(TOKEN, guardadoEn);
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));

    abrir("/entrar");

    await screen.findByText("Entrando…");
    expect(guardado()?.venceEn).toBe(guardadoEn + VIGENCIA_ENLACE_MS);
  });

  it("el del fragmento reemplaza a uno guardado de antes", async () => {
    guardarToken("otro-token-anterior", Date.now());
    const pedido = vi.fn(async () => Response.json(SESION));
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText("Entraste.");
    const [canje] = pedidosDe(pedido);
    expect(await canje!.json()).toEqual({ token: TOKEN });
  });

  it("lo borra al terminar aunque el backend lo rechace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ codigo: "enlace_invalido", mensaje: "x", id_correlacion: "c" }, { status: 401 })),
    );

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText(/No pudimos hacerte entrar con este enlace/);
    expect(guardado()).toBeNull();
  });

  it("lo borra al terminar aunque no haya red", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText(/No pudimos hacerte entrar con este enlace/);
    expect(guardado()).toBeNull();
  });

  it("si la recarga corta el canje, no lo borra: ese canje no terminó", async () => {
    let cortar: (motivo: unknown) => void = () => {
      throw new Error("el canje no empezó");
    };
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((_, rechazar) => (cortar = rechazar))));

    abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText("Entrando…");
    await vi.waitFor(() => expect(guardado()?.token).toBe(TOKEN));

    // Como el navegador al recargar: primero pagehide, después corta los pedidos en curso.
    window.dispatchEvent(new Event("pagehide"));
    cortar(new TypeError("Failed to fetch"));
    await new Promise((listo) => setTimeout(listo, 0));

    expect(guardado()?.token).toBe(TOKEN);
    expect(screen.getByText("Entrando…")).toBeTruthy();
  });

  it("uno vencido no se usa y se borra", async () => {
    guardarToken(TOKEN, Date.now() - VIGENCIA_ENLACE_MS - 1);
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);

    const { container } = abrir("/entrar");

    expect(screen.getByText(/abrí el enlace que te mandamos por correo/)).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
    expect(guardado()).toBeNull();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });

  it("si lo guardado no tiene la forma esperada, falla en vez de adivinar", () => {
    window.sessionStorage.setItem(CLAVE_GUARDADO, JSON.stringify({ token: TOKEN }));
    vi.stubGlobal("fetch", vi.fn());
    // React informa el error por consola además de lanzarlo; acá se espera que lance.
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(() => abrir("/entrar")).toThrow(/no tiene la forma esperada/);
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

describe("página /entrar: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras canjea dice «Entrando…», sin formularios, y lo saca al terminar", async () => {
    let soltar: () => void = () => undefined;
    const listo = new Promise<void>((r) => {
      soltar = r;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        await listo;
        return Response.json(SESION);
      }),
    );
    abrir(`/entrar#token=${TOKEN}`);

    expect(screen.getByRole("status").textContent).toBe("Entrando…");
    expect(screen.queryByRole("region", { name: "Entrar con contraseña" })).toBeNull();
    soltar();

    expect(await screen.findByText("Entraste.")).toBeTruthy();
    expect(screen.queryByText("Entrando…")).toBeNull();
  });
});
