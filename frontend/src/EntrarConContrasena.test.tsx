import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const CORREO = "ana@ejemplo.com";
const CONTRASENA = "una frase larga de verdad";
const VENCE = "2026-10-16T21:05:00Z";

const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: CORREO,
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};
const ABIERTA = { estado: "sesion_abierta", sesion: SESION };
const FALTA_CODIGO = { estado: "falta_segundo_factor", vence_en: VENCE };

const ENTRAR = "/api/v1/auth/contrasena/entrar";
const PASO = "/api/v1/auth/contrasena/segundo-factor";

function error(codigo: string, estado: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status: estado });
}

const INICIO = { level: 1, name: "Consorcio en mano" };

/**
 * Un backend que responde por ruta, en orden; lo que no está previsto falla la prueba. Como el de
 * verdad, `/auth/sesion` da 401 hasta que la entrada termina (`sesion_abierta` o el segundo factor).
 */
function backend(respuestas: Record<string, (() => Response | Promise<Response>)[]>) {
  let adentro = false;
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/api/v1/auth/sesion") return adentro ? Response.json(SESION) : error("no_autenticado", 401);
    if (camino === "/health") return error("servicio_no_disponible", 503);
    const siguiente = respuestas[camino]?.shift();
    if (siguiente === undefined) throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
    const respuesta = await siguiente();
    if (respuesta.ok) {
      const cuerpo = (await respuesta.clone().json()) as { estado?: string };
      adentro = camino === PASO || cuerpo.estado === "sesion_abierta";
    }
    return respuesta;
  });
}

function postsDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => p.method === "POST");
}

/** /entrar muestra una forma por vez: la contraseña se elige en «Otras formas de entrar». */
function abrir() {
  window.history.replaceState(null, "", "/entrar");
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pantalla = render(
    <StrictMode>
      <QueryClientProvider client={consultas}>
        <MemoryRouter initialEntries={["/entrar"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Entrar con contraseña" }));
  return pantalla;
}

const seccion = () => within(screen.getByRole("region", { name: "Entrar con contraseña" }));

function mandar(boton: string) {
  const formulario = seccion().getByRole("button", { name: boton }).closest("form");
  if (formulario === null) throw new Error("el botón no está en un formulario");
  fireEvent.submit(formulario);
}

function escribirContrasena(correo = CORREO, contrasena = CONTRASENA) {
  fireEvent.change(seccion().getByLabelText("Correo"), { target: { value: correo } });
  fireEvent.change(seccion().getByLabelText("Contraseña"), { target: { value: contrasena } });
  mandar("Entrar con la contraseña");
}

async function llegoAlInicio() {
  expect(await screen.findByText("Listo: sesión abierta.")).toBeTruthy();
  expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Entrar con contraseña" })).toBeNull();
}

function escribirCodigo(codigo: string) {
  fireEvent.change(seccion().getByLabelText("Código de la aplicación o de recuperación"), {
    target: { value: codigo },
  });
  mandar("Entrar con el código");
}

async function sinFaltas(contenedor: HTMLElement) {
  // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations).toEqual([]);
}

afterEach(() => {
  cleanup();
  // La última forma de entrar queda en este dispositivo (AC-30): cada prueba empieza sin ella.
  window.localStorage.clear();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("entrar con contraseña", () => {
  it("los campos piden lo que espera un gestor de contraseñas", () => {
    vi.stubGlobal("fetch", backend({}));
    abrir();

    expect(seccion().getByLabelText("Correo").getAttribute("autocomplete")).toBe("username");
    const campo = seccion().getByLabelText("Contraseña");
    expect(campo.getAttribute("type")).toBe("password");
    expect(campo.getAttribute("autocomplete")).toBe("current-password");
  });

  it("sin segundo factor: manda correo y contraseña por POST, en el cuerpo, y entra", async () => {
    const simulado = backend({ [ENTRAR]: [() => Response.json(ABIERTA)] });
    vi.stubGlobal("fetch", simulado);
    abrir();
    escribirContrasena(`  ${CORREO} `);

    await llegoAlInicio();
    const [entrada, ...otros] = postsDe(simulado);
    expect(otros).toEqual([]);
    if (entrada === undefined) throw new Error("no hubo pedido");
    const url = new URL(entrada.url);
    expect([entrada.method, url.pathname, url.search]).toEqual(["POST", ENTRAR, ""]);
    expect(entrada.headers.get("content-type")).toBe("application/json");
    expect(await entrada.json()).toEqual({ correo: CORREO, contrasena: CONTRASENA });
    expect(window.location.href).not.toContain(encodeURIComponent(CONTRASENA));
  });

  it("AC-30: entrar con contraseña la recuerda en este dispositivo para la próxima vez", async () => {
    vi.stubGlobal("fetch", backend({ [ENTRAR]: [() => Response.json(ABIERTA)] }));
    abrir();
    escribirContrasena(CORREO);

    await llegoAlInicio();
    expect(window.localStorage.getItem("cem.entrar.ultima-forma")).toBe("contrasena");
  });

  it("con segundo factor: pide el código, lo manda normalizado a /segundo-factor y entra", async () => {
    const simulado = backend({
      [ENTRAR]: [() => Response.json(FALTA_CODIGO)],
      [PASO]: [() => Response.json(SESION)],
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    escribirContrasena();

    const campo = await seccion().findByLabelText("Código de la aplicación o de recuperación");
    expect(document.activeElement).toBe(campo);
    expect(seccion().getByText(/Hay tiempo hasta las 18:05/)).toBeTruthy();
    expect(seccion().queryByLabelText("Contraseña")).toBeNull();
    escribirCodigo("042 917");

    await llegoAlInicio();
    const paso = postsDe(simulado).find((p) => new URL(p.url).pathname === PASO);
    if (paso === undefined) throw new Error("no hubo paso del segundo factor");
    expect(paso.method).toBe("POST");
    expect(await paso.json()).toEqual({ codigo: "042917" });
  });

  it("acepta un código de recuperación, con o sin guion, en mayúsculas", async () => {
    const simulado = backend({
      [ENTRAR]: [() => Response.json(FALTA_CODIGO)],
      [PASO]: [() => Response.json(SESION)],
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    escribirCodigo("K7M2QX9P4T");

    await llegoAlInicio();
    const paso = postsDe(simulado).find((p) => new URL(p.url).pathname === PASO);
    expect(await paso?.json()).toEqual({ codigo: "k7m2q-x9p4t" });
  });

  it("un código mal formado no sale y se explica junto al campo", async () => {
    const simulado = backend({ [ENTRAR]: [() => Response.json(FALTA_CODIGO)] });
    vi.stubGlobal("fetch", simulado);
    abrir();
    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    escribirCodigo("04291");

    const campo = seccion().getByLabelText("Código de la aplicación o de recuperación");
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toBe(seccion().getByText(/Hacen falta los seis números/).id);
    expect(postsDe(simulado).map((p) => new URL(p.url).pathname)).toEqual([ENTRAR]);
  });

  it.each([
    [error("credenciales_invalidas", 401), /El correo o la contraseña no son correctos/],
    [error("demasiadas_peticiones", 429), /Hubo demasiados intentos/],
    [error("servicio_no_disponible", 503), /El servicio no está disponible/],
    [error("csrf_invalido", 403), /No se pudo completar el pedido/],
  ])("ante un rechazo de la contraseña muestra un mensaje genérico (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal("fetch", backend({ [ENTRAR]: [() => respuesta] }));
    abrir();
    escribirContrasena();

    expect(await seccion().findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText("Listo: sesión abierta.")).toBeNull();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(seccion().getByLabelText("Contraseña")).toBeTruthy();
  });

  it("sin la red, avisa que no se pudo completar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();
    escribirContrasena();

    expect(await seccion().findByText(/No se pudo completar el pedido/)).toBeTruthy();
  });

  it("si el paso venció (401 entrada_vencida), vuelve a la contraseña con el foco en ella", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [ENTRAR]: [() => Response.json(FALTA_CODIGO)],
        [PASO]: [() => error("entrada_vencida", 401)],
      }),
    );
    abrir();
    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    escribirCodigo("042917");

    expect(await seccion().findByText(/Hay que escribir la contraseña de nuevo/)).toBeTruthy();
    const campo = seccion().getByLabelText("Contraseña") as HTMLInputElement;
    expect(campo.value).toBe("");
    await vi.waitFor(() => expect(document.activeElement).toBe(campo));
    expect(seccion().queryByLabelText("Código de la aplicación o de recuperación")).toBeNull();
  });

  it.each([
    [error("segundo_factor_invalido", 400), /El código no sirve/],
    [error("demasiadas_peticiones", 429), /Hubo demasiados intentos/],
    [error("servicio_no_disponible", 503), /El servicio no está disponible/],
  ])("ante un código rechazado se queda en el código y lo explica (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal(
      "fetch",
      backend({ [ENTRAR]: [() => Response.json(FALTA_CODIGO)], [PASO]: [() => respuesta] }),
    );
    abrir();
    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    escribirCodigo("042917");

    expect(await seccion().findByText(mensaje)).toBeTruthy();
    expect(seccion().getByLabelText("Código de la aplicación o de recuperación")).toBeTruthy();
    expect(screen.queryByText("Listo: sesión abierta.")).toBeNull();
  });

  it("«Volver a la contraseña» deja el paso del código", async () => {
    vi.stubGlobal("fetch", backend({ [ENTRAR]: [() => Response.json(FALTA_CODIGO)] }));
    abrir();
    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    fireEvent.click(seccion().getByRole("button", { name: "Volver a la contraseña" }));

    expect(seccion().getByLabelText("Contraseña")).toBeTruthy();
    expect(seccion().queryByRole("status")?.textContent).toBe("");
  });

  it("no tiene faltas de accesibilidad detectables por axe en ningún estado", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [ENTRAR]: [() => error("credenciales_invalidas", 401), () => Response.json(FALTA_CODIGO)],
        [PASO]: [() => error("segundo_factor_invalido", 400), () => error("entrada_vencida", 401)],
      }),
    );
    const { container } = abrir();
    await sinFaltas(container);

    escribirContrasena();
    await seccion().findByText(/no son correctos/);
    await sinFaltas(container);

    escribirContrasena();
    await seccion().findByLabelText("Código de la aplicación o de recuperación");
    await sinFaltas(container);

    escribirCodigo("0429");
    await sinFaltas(container);

    escribirCodigo("042917");
    await seccion().findByText(/El código no sirve/);
    await sinFaltas(container);

    escribirCodigo("042917");
    await seccion().findByText(/Hay que escribir la contraseña de nuevo/);
    await sinFaltas(container);
  });

  it("adentro no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend({ [ENTRAR]: [() => Response.json(ABIERTA)] }));
    const { container } = abrir();
    escribirContrasena();
    await llegoAlInicio();
    await sinFaltas(container);
  });
});

describe("entrar con contraseña: estado de carga (documento 08, §13.1)", () => {
  it("mientras entra dice «Entrando…» con el botón deshabilitado, y lo saca al terminar", async () => {
    let soltar: () => void = () => undefined;
    const listo = new Promise<void>((r) => {
      soltar = r;
    });
    vi.stubGlobal(
      "fetch",
      backend({
        [ENTRAR]: [
          async () => {
            await listo;
            return Response.json(ABIERTA);
          },
        ],
      }),
    );
    abrir();
    escribirContrasena();

    expect(await seccion().findByText("Entrando…")).toBeTruthy();
    const boton = seccion().getByRole("button", { name: "Entrar con la contraseña" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true);
    soltar();

    await llegoAlInicio();
    expect(screen.queryByText("Entrando…")).toBeNull();
  });
});
