import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RECARGAR, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { SeccionPrivacidad } from "./SeccionPrivacidad";

const CASO = "01a18105-0000-7000-8000-000000000001";
const CASOS = "/api/v1/privacidad/casos";

function caso(estado: string, derecho = "acceso") {
  return {
    id: CASO,
    alcance_consorcio_id: null,
    derecho,
    estado,
    abierto_en: "2026-10-28T12:00:00Z",
    codigo_vence_en: estado === "abierto" ? "2026-10-31T12:00:00Z" : null,
    confirmado_en: null,
    copia_entregada_en: null,
    resuelto_en: null,
  };
}

function error(codigo: string, status: number, detalle: object | null = null) {
  return Response.json({ codigo, mensaje: "El código no sirve.", detalle, id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

function cuerpos(simulado: ReturnType<typeof vi.fn>, metodo: string): Promise<unknown>[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo)
    .map((p) => p.clone().json());
}

function mostrar(contrasena = true, sinSesion: () => void = () => undefined) {
  return render(
    <MemoryRouter>
      <SeccionPrivacidad contrasena={contrasena} fecha={(iso) => iso.slice(0, 10)} sinSesion={sinSesion} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("sección de pedidos sobre los datos", () => {
  it("sin casos pendientes no se muestra", async () => {
    const simulado = backend({ [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("resuelto")] }) });
    vi.stubGlobal("fetch", simulado);
    const { container } = mostrar();
    await waitFor(() => expect(simulado).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("confirma, descarga (de nuevo si hace falta) y cierra cuando la guardó", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const creado = vi.fn(() => "blob:local");
    const liberado = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: creado, revokeObjectURL: liberado }));
    let enDocumento = false;
    const clic = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      enDocumento = document.body.contains(this);
    });
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
      [`POST ${CASOS}/${CASO}/confirmar`]: () => new Response(null, { status: 204 }),
      [`POST ${CASOS}/${CASO}/copia`]: () =>
        Response.json({ caso_id: CASO, generada_en: "2026-10-28T12:05:00Z", alcance_consorcio_id: null, datos: {} }),
      [`POST ${CASOS}/${CASO}/recibida`]: () => new Response(null, { status: 204 }),
    });
    vi.stubGlobal("fetch", simulado);
    mostrar();
    fireEvent.change(await screen.findByLabelText("Código que te llegó por correo"), {
      target: { value: "ABCDE-FGHIJ" },
    });
    fireEvent.change(screen.getByLabelText("Tu contraseña"), { target: { value: "una frase larga" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el pedido" }));
    expect(await screen.findByText("Listo: confirmaste tu pedido.")).toBeTruthy();
    expect(await Promise.all(cuerpos(simulado, "POST"))).toEqual([
      { codigo: "ABCDE-FGHIJ", contrasena: "una frase larga" },
    ]);
    // La contraseña se vacía: para descargar se vuelve a escribir.
    const campo = screen.getByLabelText("Tu contraseña") as HTMLInputElement;
    expect(campo.value).toBe("");
    fireEvent.change(campo, { target: { value: "una frase larga" } });
    fireEvent.click(screen.getByRole("button", { name: "Descargar la copia de mis datos" }));
    expect(
      await screen.findByText("Te mandamos la copia. Revisá que el archivo se haya guardado y avisanos."),
    ).toBeTruthy();
    expect(creado).toHaveBeenCalledTimes(1);
    expect(clic).toHaveBeenCalledTimes(1);
    // El enlace estaba en el documento al hacer clic y se libera después, no en el acto.
    expect(enDocumento).toBe(true);
    expect(liberado).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(liberado).toHaveBeenCalledWith("blob:local");
    // Puede descargarla de nuevo y después cerrar el pedido.
    fireEvent.change(screen.getByLabelText("Tu contraseña"), { target: { value: "una frase larga" } });
    fireEvent.click(screen.getByRole("button", { name: "Descargar de nuevo" }));
    await waitFor(() => expect(creado).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Ya la guardé: cerrar el pedido" }));
    expect(await screen.findByText("Listo: cerramos tu pedido.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Descargar de nuevo" })).toBeNull();
    vi.useRealTimers();
  });

  it("sin contraseña no la pide y explica que hay que volver a entrar", async () => {
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("confirmado")] }),
      [`POST ${CASOS}/${CASO}/copia`]: () =>
        error("reautenticacion_requerida", 403, { falta: "entrar_de_nuevo" }),
    });
    vi.stubGlobal("fetch", simulado);
    mostrar(false);
    fireEvent.click(await screen.findByRole("button", { name: "Descargar la copia de mis datos" }));
    expect(screen.queryByLabelText("Tu contraseña")).toBeNull();
    const enlace = await screen.findByRole("link", { name: "volvé a entrar con el enlace del correo" });
    expect(enlace.getAttribute("href")).toBe("/entrar");
    expect(await Promise.all(cuerpos(simulado, "POST"))).toEqual([{ contrasena: null }]);
  });

  it("con segundo factor sin verificar lo pide acá mismo y repite el paso con lo que ya estaba escrito", async () => {
    let verificado = false;
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
      "POST /api/v1/auth/totp/verificar": () => {
        verificado = true;
        return Response.json({
          verificado_en: "2026-10-28T12:00:00Z",
          vale_hasta: "2026-10-28T12:10:00Z",
          codigos_recuperacion_restantes: 9,
        });
      },
      [`POST ${CASOS}/${CASO}/confirmar`]: () =>
        verificado ? new Response(null, { status: 204 }) : error("reautenticacion_requerida", 403, { falta: "segundo_factor" }),
    });
    vi.stubGlobal("fetch", simulado);
    mostrar();
    fireEvent.change(await screen.findByLabelText("Código que te llegó por correo"), { target: { value: "ABCDE" } });
    fireEvent.change(screen.getByLabelText("Tu contraseña"), { target: { value: "una frase" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el pedido" }));

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "042917" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    expect(await screen.findByText("Listo: confirmaste tu pedido.")).toBeTruthy();
    expect(await Promise.all(cuerpos(simulado, "POST"))).toEqual([
      { codigo: "ABCDE", contrasena: "una frase" },
      { codigo: "042917" },
      { codigo: "ABCDE", contrasena: "una frase" },
    ]);
  });

  it("si cancela el código, vuelve al formulario con lo que había escrito y sin repetir el paso", async () => {
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
      [`POST ${CASOS}/${CASO}/confirmar`]: () => error("reautenticacion_requerida", 403, { falta: "segundo_factor" }),
    });
    vi.stubGlobal("fetch", simulado);
    mostrar();
    fireEvent.change(await screen.findByLabelText("Código que te llegó por correo"), { target: { value: "ABCDE" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el pedido" }));

    fireEvent.click(await screen.findByRole("button", { name: "Cancelar" }));

    const campo = (await screen.findByLabelText("Código que te llegó por correo")) as HTMLInputElement;
    expect(campo.value).toBe("ABCDE");
    expect(await Promise.all(cuerpos(simulado, "POST"))).toHaveLength(1);
  });

  it("control: si lo que falta es la contraseña no pide el código", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("confirmado")] }),
        [`POST ${CASOS}/${CASO}/copia`]: () => error("reautenticacion_requerida", 403, { falta: "contrasena" }),
      }),
    );
    mostrar();
    fireEvent.click(await screen.findByRole("button", { name: "Descargar la copia de mis datos" }));

    expect(await screen.findByText("Escribí tu contraseña actual.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Verificar y seguir" })).toBeNull();
  });

  it("no avisa por adelantado lo que va a pedir: lo pide si hace falta", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }) }));
    mostrar();

    expect(await screen.findByRole("button", { name: "Confirmar el pedido" })).toBeTruthy();
    expect(screen.queryByText(/Por seguridad/)).toBeNull();
  });

  it.each([
    [503, "servicio_no_disponible", SERVICIO_NO_DISPONIBLE],
    [500, "error_interno", SERVICIO_NO_DISPONIBLE],
    [403, "csrf_invalido", RECARGAR],
  ])("un %i (%s) al confirmar no se explica como un problema de conexión", async (status, codigo, mensaje) => {
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
        [`POST ${CASOS}/${CASO}/confirmar`]: () => error(codigo, status),
      }),
    );
    mostrar();
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar el pedido" }));

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });

  it("control: sin respuesta sí habla de la conexión", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
        [`POST ${CASOS}/${CASO}/confirmar`]: () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    mostrar();
    fireEvent.click(await screen.findByRole("button", { name: "Confirmar el pedido" }));

    expect(await screen.findByText(SIN_CONEXION)).toBeTruthy();
  });

  it("un código que no sirve muestra el mensaje del backend y es accesible", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto", "supresion")] }),
        [`POST ${CASOS}/${CASO}/confirmar`]: () => error("codigo_caso_invalido", 400),
      }),
    );
    const { container } = mostrar();
    expect(await screen.findByRole("heading", { name: "Suprimir tus datos: espera tu confirmación" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar el pedido" }));
    expect(await screen.findByText("El código no sirve.")).toBeTruthy();
    const resultado = await axe.run(container);
    expect(resultado.violations).toEqual([]);
  });

  it("mientras consulta no muestra nada y después muestra el caso", async () => {
    let responder: (r: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((r) => (responder = r))),
    );
    const { container } = mostrar();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
    responder(Response.json({ elementos: [caso("abierto")] }));
    expect(await screen.findByRole("heading", { name: "Copia de tus datos: espera tu confirmación" })).toBeTruthy();
  });

  it("con la sesión vencida lleva a entrar y no muestra nada", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${CASOS}`]: () => error("no_autenticado", 401) }));
    const sinSesion = vi.fn();
    const { container } = mostrar(true, sinSesion);
    await waitFor(() => expect(sinSesion).toHaveBeenCalledOnce());
    expect(container.innerHTML).toBe("");
  });

  it("sin red no se esconde: avisa, es accesible y deja consultar de nuevo", async () => {
    let intentos = 0;
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${CASOS}`]: () => {
          intentos += 1;
          if (intentos === 1) throw new TypeError("Failed to fetch");
          return Response.json({ elementos: [caso("confirmado")] });
        },
      }),
    );
    const { container } = mostrar();
    expect((await screen.findByRole("alert")).textContent).toBe(SIN_CONEXION);
    expect(screen.getByRole("heading", { name: "Tus pedidos sobre tus datos" })).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    // El botón desaparece: el foco va al título de la sección, no al cuerpo de la página (P3 77).
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Tus pedidos sobre tus datos" }));
    expect(await screen.findByRole("button", { name: "Descargar la copia de mis datos" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(intentos).toBe(2);
  });

  it("al consultar de nuevo muestra que consulta y, sin pedidos, lo dice en vez de desaparecer", async () => {
    let responder: (r: Response) => void = () => undefined;
    let intentos = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        intentos += 1;
        if (intentos === 1) return Promise.reject(new TypeError("Failed to fetch"));
        return new Promise<Response>((r) => (responder = r));
      }),
    );
    const { container } = mostrar();
    fireEvent.click(await screen.findByRole("button", { name: "Consultar de nuevo" }));
    expect(screen.getByText("Consultando tus pedidos…").getAttribute("role")).toBe("status");
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Tus pedidos sobre tus datos" }));
    responder(Response.json({ elementos: [caso("resuelto")] }));
    expect(await screen.findByText("No tenés pedidos pendientes sobre tus datos.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("con el servicio caído lo dice, sin culpar a la conexión", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${CASOS}`]: () => error("error_interno", 500) }));
    mostrar();
    expect((await screen.findByRole("alert")).textContent).toBe(SERVICIO_NO_DISPONIBLE);
  });
});
