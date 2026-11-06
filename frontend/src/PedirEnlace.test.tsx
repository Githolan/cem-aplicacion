import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const CORREO = "ana@ejemplo.com";
const TOKEN = "A".repeat(43);
// El texto lo arma el backend (router.py, MENSAJE_PEDIDO); la página muestra el que llega.
const MENSAJE =
  "Si el correo corresponde a una cuenta, en unos minutos te llega un enlace para entrar.";

function aceptado() {
  return Response.json({ estado: "pedido", mensaje: MENSAJE }, { status: 202 });
}

function error(codigo: string, estado: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status: estado });
}

const RUTA_ENLACE = "/api/v1/auth/enlace";
const CODIGO = { name: "Entrar con un código" };
const INICIO = { level: 1, name: "Tu consorcio, en tu mano" };

/**
 * El backend de quien todavía no entró: `/auth/sesion` da 401 hasta que un canje la abre, `enlace`
 * responde el pedido y lo que no está previsto falla la prueba.
 */
function backend(enlace: () => Response | Promise<Response> = aceptado, canje?: () => Response) {
  let adentro = false;
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === RUTA_ENLACE) return enlace();
    if (camino === "/api/v1/auth/sesion") return adentro ? Response.json({}) : error("no_autenticado", 401);
    if (camino === "/health") return error("servicio_no_disponible", 503);
    if (camino === "/api/v1/auth/canje" && canje) {
      const respuesta = canje();
      adentro = respuesta.ok;
      return respuesta;
    }
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function abrir(url = "/entrar") {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

// El pedido del enlace es una de las formas de /entrar: se busca en su sección.
function seccion() {
  return within(screen.getByRole("region", { name: "Pedir el enlace para entrar" }));
}

function pedir(correo: string) {
  fireEvent.change(seccion().getByLabelText("Correo"), { target: { value: correo } });
  const formulario = seccion().getByRole("button", { name: "Pedir el enlace" }).closest("form");
  if (formulario === null) throw new Error("el botón no está en un formulario");
  fireEvent.submit(formulario);
}

function postsDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => p.method === "POST");
}

async function sinFaltas(contenedor: HTMLElement) {
  // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations).toEqual([]);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("pedir el enlace para entrar", () => {
  it("manda el correo por POST, en el cuerpo, y muestra el mensaje genérico del 202", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    abrir();
    pedir(`  ${CORREO} `);

    // Con el pedido hecho, la pantalla pasa al código: el mensaje se lee ahí.
    const codigo = within(await screen.findByRole("region", CODIGO));
    expect(codigo.getByText(MENSAJE)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Pedir el enlace para entrar" })).toBeNull();
    const [enviado, ...otros] = postsDe(pedido);
    expect(otros).toEqual([]);
    if (enviado === undefined) throw new Error("no hubo pedido");
    const url = new URL(enviado.url);
    expect([enviado.method, url.origin, url.pathname, url.search]).toEqual([
      "POST",
      window.location.origin,
      RUTA_ENLACE,
      "",
    ]);
    // Mismo origen y sin `credentials: "omit"`: la cookie nonce de la respuesta queda en este
    // navegador, que es al que el backend liga el enlace.
    expect(enviado.credentials).not.toBe("omit");
    expect(enviado.headers.get("content-type")).toBe("application/json");
    expect(await enviado.json()).toEqual({ correo: CORREO });
    expect(window.location.href).not.toContain(CORREO);
  });

  it("explica que en otro dispositivo sirve el código del mismo correo", () => {
    vi.stubGlobal("fetch", backend());
    abrir();
    expect(seccion().getByText(/en otro dispositivo, usá el código de 6 números que llega en el mismo correo/)).toBeTruthy();
  });

  it.each([
    [error("validacion", 422), /Revisá el correo: no parece una dirección válida/],
    [error("demasiadas_peticiones", 429), /demasiados pedidos con este correo/],
    [error("servicio_no_disponible", 503), /El servicio no está disponible/],
  ])("ante un rechazo muestra un mensaje en español y no el del 202 (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal("fetch", backend(() => respuesta));

    abrir();
    pedir(CORREO);

    expect(await seccion().findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText(MENSAJE)).toBeNull();
    // El error queda en la misma pantalla, con lo escrito: no pasa al código.
    expect((seccion().getByLabelText("Correo") as HTMLInputElement).value).toBe(CORREO);
    expect(screen.queryByRole("region", CODIGO)).toBeNull();
  });

  it("un 422 marca el campo y lo describe", async () => {
    vi.stubGlobal("fetch", backend(() => error("validacion", 422)));
    abrir();
    pedir(CORREO);

    await seccion().findByText(/no parece una dirección válida/);
    const campo = seccion().getByLabelText("Correo");
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    const descripcion = campo.getAttribute("aria-describedby");
    expect(descripcion && document.getElementById(descripcion)?.textContent).toMatch(/no parece una dirección válida/);
  });

  it("sin la red, avisa que no se pudo completar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));

    abrir();
    pedir(CORREO);

    expect(await seccion().findByText(/No pudimos completar el pedido/)).toBeTruthy();
    expect(screen.queryByRole("region", CODIGO)).toBeNull();
  });

  it("con un enlace válido no se ofrece pedir otro; con uno rechazado, sí", async () => {
    vi.stubGlobal("fetch", backend(aceptado, () => Response.json({})));
    abrir(`/entrar#token=${TOKEN}`);
    expect(screen.queryByRole("heading", { name: "Pedir el enlace para entrar" })).toBeNull();
    expect(await screen.findByText("Entraste.")).toBeTruthy();
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Pedir el enlace para entrar" })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend(aceptado, () => error("enlace_invalido", 401)));
    abrir(`/entrar#token=${TOKEN}`);
    expect(await screen.findByText(/No pudimos hacerte entrar con este enlace/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Pedir el enlace para entrar" })).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe, vacío, pedido ni con error", async () => {
    let respuesta = aceptado;
    vi.stubGlobal("fetch", backend(() => respuesta()));
    const { container } = abrir();
    await sinFaltas(container);

    pedir(CORREO);
    await within(await screen.findByRole("region", CODIGO)).findByText(MENSAJE);
    await sinFaltas(container);

    fireEvent.click(screen.getByRole("button", { name: "Pedir el enlace por correo" }));
    respuesta = () => error("validacion", 422);
    pedir(CORREO);
    await seccion().findByText(/no parece una dirección válida/);
    await sinFaltas(container);
  });
});
