import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { codigoValido, normalizarCodigo } from "./EntrarConCodigo";

const CORREO = "ana@ejemplo.com";
const CODIGO = "042917";
const TOKEN = "A".repeat(43);

const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: CORREO,
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};

function error(codigo: string, estado: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status: estado });
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

function completar(correo: string, codigo: string) {
  fireEvent.change(screen.getByLabelText("Correo"), { target: { value: correo } });
  fireEvent.change(screen.getByLabelText(/^Código de 6 números/), { target: { value: codigo } });
  const boton = screen.getByRole("button", { name: "Entrar con el código" });
  const formulario = boton.closest("form");
  if (formulario === null) throw new Error("el botón no está en un formulario");
  fireEvent.submit(formulario);
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
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

describe("entrar con el código del correo", () => {
  it("manda correo y código por POST, en el cuerpo y sin separadores, y entra", async () => {
    const pedido = vi.fn(async () => Response.json(SESION));
    vi.stubGlobal("fetch", pedido);

    abrir();
    completar(`  ${CORREO} `, "042 917");

    expect(await screen.findByText("Entraste.")).toBeTruthy();
    const [canje, ...otros] = pedidosDe(pedido);
    expect(otros).toEqual([]);
    if (canje === undefined) throw new Error("no hubo pedido");
    const url = new URL(canje.url);
    expect([canje.method, url.pathname, url.search]).toEqual(["POST", "/api/v1/auth/codigo", ""]);
    expect(canje.headers.get("content-type")).toBe("application/json");
    expect(await canje.json()).toEqual({ correo: CORREO, codigo: CODIGO });
    expect(window.location.href).not.toContain(CODIGO);
  });

  it("un código mal formado no sale y se explica junto al campo", () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);

    abrir();
    completar(CORREO, "04291");

    expect(screen.getByText("El código tiene 6 números.")).toBeTruthy();
    const campo = screen.getByLabelText(/^Código de 6 números/);
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
  });

  it.each([
    [error("codigo_invalido", 401), /No pudimos hacerte entrar con ese código/],
    [error("demasiadas_peticiones", 429), /demasiados intentos con este correo/],
    [error("servicio_no_disponible", 503), /No pudimos completar el pedido/],
  ])("ante un rechazo muestra un mensaje genérico (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal("fetch", vi.fn(async () => respuesta));

    abrir();
    completar(CORREO, CODIGO);

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText("Entraste.")).toBeNull();
  });

  it("sin la red, avisa que no se pudo completar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));

    abrir();
    completar(CORREO, CODIGO);

    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
  });

  it("con un enlace válido no muestra el formulario; con uno rechazado, sí", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(SESION)));
    abrir(`/entrar#token=${TOKEN}`);
    expect(await screen.findByText("Entraste.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Entrar con un código" })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => error("enlace_invalido", 401)));
    abrir(`/entrar#token=${TOKEN}`);
    expect(await screen.findByText(/No pudimos hacerte entrar con este enlace/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Entrar con un código" })).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe, vacío ni con error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => error("codigo_invalido", 401)));
    const { container } = abrir();
    await sinFaltas(container);

    completar(CORREO, "04291");
    await sinFaltas(container);

    completar(CORREO, CODIGO);
    await screen.findByText(/No pudimos hacerte entrar con ese código/);
    await sinFaltas(container);
  });
});

describe("normalizarCodigo y codigoValido", () => {
  it("acepta seis dígitos con o sin espacio o guion, y nada más", () => {
    expect(normalizarCodigo("042 917")).toBe(CODIGO);
    expect(normalizarCodigo(" 042-917 ")).toBe(CODIGO);
    expect(codigoValido(CODIGO)).toBe(true);
    for (const malo of ["04291", "0429170", "04291a", "", "042 917"]) expect(codigoValido(malo)).toBe(false);
  });
});
