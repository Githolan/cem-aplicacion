import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RECARGAR, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { SeccionPrivacidad } from "./SeccionPrivacidad";

const CASO = "01a18105-0000-7000-8000-000000000001";
const CASOS = "/api/v1/privacidad/casos";

/** Un día después de ahora: el código del correo todavía sirve. */
const VENCE = new Date(Date.now() + 86_400_000).toISOString();

function caso(estado: string, derecho = "acceso", codigoVenceEn: string | null = estado === "abierto" ? VENCE : null) {
  return {
    id: CASO,
    alcance_consorcio_id: null,
    derecho,
    estado,
    abierto_en: "2026-10-28T12:00:00Z",
    codigo_vence_en: codigoVenceEn,
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
  // La confirmación (PedirCodigo) lee el plazo para activar con una consulta (FL-CTA-01): va dentro de su proveedor.
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <SeccionPrivacidad contrasena={contrasena} fecha={(iso) => iso.slice(0, 10)} sinSesion={sinSesion} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** La ventana abierta para cargar el código o la contraseña. */
function ventana() {
  return within(screen.getByRole("dialog"));
}

/** Toca el botón de la fila; con «Confirmar el pedido» o «Descargar…» se abre la ventana. */
async function tocarFila(nombre: string) {
  const boton = await screen.findByRole("button", { name: nombre });
  boton.focus();
  fireEvent.click(boton);
}

/** Abre la ventana de confirmar, la completa y la manda. */
async function confirmarConDatos() {
  await tocarFila("Confirmar el pedido");
  fireEvent.change(ventana().getByLabelText("Código que llegó por correo"), { target: { value: "ABCDE" } });
  fireEvent.change(ventana().getByLabelText("Contraseña"), { target: { value: "una frase" } });
  fireEvent.click(ventana().getByRole("button", { name: "Confirmar el pedido" }));
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
    // CTA-06: el pedido es una fila; el código y la contraseña se escriben en su ventana.
    expect(await screen.findByText(`El código sirve hasta el ${VENCE.slice(0, 10)}.`)).toBeTruthy();
    expect(screen.queryByLabelText("Código que llegó por correo")).toBeNull();
    await tocarFila("Confirmar el pedido");
    fireEvent.change(ventana().getByLabelText("Código que llegó por correo"), { target: { value: "ABCDE-FGHIJ" } });
    fireEvent.change(ventana().getByLabelText("Contraseña"), { target: { value: "una frase larga" } });
    fireEvent.click(ventana().getByRole("button", { name: "Confirmar el pedido" }));
    expect(await screen.findByText("Listo: el pedido quedó confirmado.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await Promise.all(cuerpos(simulado, "POST"))).toEqual([
      { codigo: "ABCDE-FGHIJ", contrasena: "una frase larga" },
    ]);
    // La contraseña se vuelve a escribir para descargar, en otra ventana que empieza vacía.
    await tocarFila("Descargar la copia de mis datos");
    const campo = ventana().getByLabelText("Contraseña") as HTMLInputElement;
    expect(campo.value).toBe("");
    fireEvent.change(campo, { target: { value: "una frase larga" } });
    fireEvent.click(ventana().getByRole("button", { name: "Descargar la copia de mis datos" }));
    expect(
      await screen.findByText("La copia se descargó. Conviene revisar que el archivo se haya guardado y avisarlo acá."),
    ).toBeTruthy();
    expect(creado).toHaveBeenCalledTimes(1);
    expect(clic).toHaveBeenCalledTimes(1);
    // El enlace estaba en el documento al hacer clic y se libera después, no en el acto.
    expect(enDocumento).toBe(true);
    expect(liberado).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(liberado).toHaveBeenCalledWith("blob:local");
    // CTA-07: lo que sigue es avisar que se guardó; «No se guardó» la descarga de nuevo.
    expect(screen.queryByRole("button", { name: "Descargar la copia de mis datos" })).toBeNull();
    await tocarFila("No se guardó");
    fireEvent.change(ventana().getByLabelText("Contraseña"), { target: { value: "una frase larga" } });
    fireEvent.click(ventana().getByRole("button", { name: "Descargar la copia de mis datos" }));
    await waitFor(() => expect(creado).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Copia guardada: cerrar el pedido" }));
    expect(await screen.findByText("Listo: el pedido quedó cerrado.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "No se guardó" })).toBeNull();
    vi.useRealTimers();
  });

  it("sin contraseña no la pide: lo resuelve la hoja de entrar encima", async () => {
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("confirmado")] }),
      [`POST ${CASOS}/${CASO}/copia`]: () =>
        error("reautenticacion_requerida", 403, { falta: "entrar_de_nuevo" }),
    });
    vi.stubGlobal("fetch", simulado);
    mostrar(false);
    fireEvent.click(await screen.findByRole("button", { name: "Descargar la copia de mis datos" }));
    expect(screen.queryByLabelText("Contraseña")).toBeNull();
    // Lo pide la hoja de entrar encima: acá no queda un mensaje viejo para después de entrar.
    await waitFor(async () => expect(await Promise.all(cuerpos(simulado, "POST"))).toEqual([{ contrasena: null }]));
    expect(screen.getByRole("button", { name: "Descargar la copia de mis datos" })).toBeTruthy();
    expect(screen.queryByText(/Por seguridad/)).toBeNull();
  });

  it("con segundo factor sin verificar lo pide acá mismo y repite el paso con lo que ya estaba escrito", async () => {
    let verificado = false;
    const simulado = backend({
      [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }),
      "GET /api/v1/auth/totp": () =>
        Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
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
    await tocarFila("Confirmar el pedido");
    fireEvent.change(ventana().getByLabelText("Código que llegó por correo"), { target: { value: "ABCDE" } });
    fireEvent.change(ventana().getByLabelText("Contraseña"), { target: { value: "una frase" } });
    fireEvent.click(ventana().getByRole("button", { name: "Confirmar el pedido" }));

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "042917" } });

    expect(await screen.findByText("Listo: el pedido quedó confirmado.")).toBeTruthy();
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
    mostrar(false);
    await tocarFila("Confirmar el pedido");
    fireEvent.change(ventana().getByLabelText("Código que llegó por correo"), { target: { value: "ABCDE" } });
    fireEvent.click(ventana().getByRole("button", { name: "Confirmar el pedido" }));

    // El «Cancelar» de la hoja del código, no el de la ventana.
    await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(2));
    const hojaDelCodigo = screen.getAllByRole("dialog")[1] as HTMLElement;
    fireEvent.click(within(hojaDelCodigo).getByRole("button", { name: "Cancelar" }));

    await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(1));
    const campo = ventana().getByLabelText("Código que llegó por correo") as HTMLInputElement;
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
    await tocarFila("Descargar la copia de mis datos");
    fireEvent.change(ventana().getByLabelText("Contraseña"), { target: { value: "una frase" } });
    fireEvent.click(ventana().getByRole("button", { name: "Descargar la copia de mis datos" }));

    expect(await ventana().findByText("Falta la contraseña actual.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Verificar y seguir" })).toBeNull();
  });

  it("C8G-14: con los campos vacíos marca lo que falta y no manda nada", async () => {
    const simulado = backend({ [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto")] }) });
    vi.stubGlobal("fetch", simulado);
    mostrar();
    await tocarFila("Confirmar el pedido");
    fireEvent.click(ventana().getByRole("button", { name: "Confirmar el pedido" }));

    expect(await ventana().findByText("Falta el código que llegó por correo.")).toBeTruthy();
    expect(ventana().getByText("Falta la contraseña.")).toBeTruthy();
    expect(document.activeElement).toBe(ventana().getByLabelText("Código que llegó por correo"));
    expect(cuerpos(simulado, "POST")).toHaveLength(0);
  });

  it.each([
    ["vencido", "2026-10-31T12:00:00Z", "El código de este pedido venció el 2026-10-31."],
    ["sin código", null, "El código de este pedido ya no sirve."],
  ])("C8G-15: con el código %s no ofrece confirmar y dice qué hace falta", async (_caso, vence, texto) => {
    vi.stubGlobal("fetch", backend({ [`GET ${CASOS}`]: () => Response.json({ elementos: [caso("abierto", "acceso", vence)] }) }));
    const { container } = mostrar();

    expect(await screen.findByText(`${texto} Para seguir hace falta un código nuevo, que se pide a soporte.`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirmar el pedido" })).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
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
    await confirmarConDatos();

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText(/Hay que revisar la conexión/)).toBeNull();
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
    await confirmarConDatos();

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
    expect(await screen.findByRole("heading", { name: "Suprimir los datos: espera confirmación" })).toBeTruthy();
    await confirmarConDatos();
    expect(await ventana().findByText("El código no sirve.")).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
    expect((await axe.run(screen.getByRole("dialog"))).violations).toEqual([]);
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
    expect(await screen.findByRole("heading", { name: "Copia de los datos: espera confirmación" })).toBeTruthy();
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
    expect(screen.getByRole("heading", { name: "Pedidos sobre los datos de la cuenta" })).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    // El botón desaparece: el foco va al título de la sección, no al cuerpo de la página (P3 77).
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Pedidos sobre los datos de la cuenta" }));
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
    expect(screen.getByText("Consultando los pedidos…").getAttribute("role")).toBe("status");
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Pedidos sobre los datos de la cuenta" }));
    responder(Response.json({ elementos: [caso("resuelto")] }));
    expect(await screen.findByText("No hay pedidos pendientes sobre los datos de la cuenta.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("con el servicio caído lo dice, sin culpar a la conexión", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${CASOS}`]: () => error("error_interno", 500) }));
    mostrar();
    expect((await screen.findByRole("alert")).textContent).toBe(SERVICIO_NO_DISPONIBLE);
  });
});
