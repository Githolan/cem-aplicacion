import { BOTON_ENTRAR_PARA_ACTIVAR, ENTRAR_PARA_ACTIVAR } from "../altaDeConfirmacion";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render as dibujar, screen, waitFor } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { recordarLlave } from "../llaveAcceso";
import { recordarMetodo, ultimoMetodo } from "../metodoConfirmar";
import { PedirCodigo } from "./PedirCodigo";

/** La hoja lee el plazo para activar (FL-CTA-01) con una consulta: va dentro de su proveedor. */
function ConConsultas({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}
const render = (ui: ReactElement) => dibujar(ui, { wrapper: ConConsultas });

const LLAVE = { id: "1", nombre: "Mi PC", credencial_id: "AQID", creada_en: "2026-11-04T10:00:00Z", ultimo_uso_en: null };
const OPCIONES = { opciones: { challenge: "AAEC", allowCredentials: [{ id: "AwQF", type: "public-key" }] } };
const VERIFICADO = {
  verificado_en: "2026-11-04T10:00:00Z",
  vale_hasta: "2026-11-04T10:10:00Z",
  codigos_recuperacion_restantes: 10,
};
const bytes = () => Uint8Array.from([1, 2, 3]).buffer;
const CREDENCIAL = {
  id: "AQID",
  rawId: bytes(),
  type: "public-key",
  getClientExtensionResults: () => ({}),
  response: { clientDataJSON: bytes(), authenticatorData: bytes(), signature: bytes(), userHandle: null },
};
const HOJA_LLAVE = "Confirmar con PIN, huella o cara";
const HOJA_CODIGO = "Código de la aplicación";

type Respuestas = Record<string, () => Response>;

const estadoTotp = (activo: boolean, llaves: number, codigos = 0) => () =>
  Response.json({ activo, llaves, codigos_recuperacion_restantes: codigos, verificado_en: null });

const conLlave = (extra: Respuestas = {}): Respuestas => ({
  "GET /api/v1/auth/totp": estadoTotp(true, 1, 5),
  "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
  "POST /api/v1/auth/llave/verificar/opciones": () => Response.json(OPCIONES),
  "POST /api/v1/auth/llave/verificar": () => Response.json(VERIFICADO),
  ...extra,
});

/** La sesión, con el plazo para activar la confirmación (FL-CTA-01, app 0110). */
const sesion = (minutos: number) => () =>
  Response.json({
    persona_id: "0199e3a2-1234-7abc-8def-0123456789ab", nombre: "Ana", correo: "ana@ejemplo.com",
    sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac", vence_en: "2099-01-01T00:00:00Z",
    alta_confirmacion_hasta: new Date(Date.now() + minutos * 60_000).toISOString(),
  });

function backend(respuestas: Respuestas) {
  const lista: Respuestas = { "GET /api/v1/auth/totp": estadoTotp(true, 0), "GET /api/v1/auth/sesion": sesion(5), ...respuestas };
  const simulado = vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = lista[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
  vi.stubGlobal("fetch", simulado);
  return simulado;
}

const pedidos = (simulado: ReturnType<typeof backend>, ruta: string) =>
  simulado.mock.calls.filter(([p]) => new URL(p.url).pathname === ruta).length;

function dispositivo(get: (opciones?: CredentialRequestOptions) => Promise<unknown>) {
  vi.stubGlobal("PublicKeyCredential", class {});
  Object.defineProperty(navigator, "credentials", { value: { get }, configurable: true });
}

function mostrar() {
  const alVerificar = vi.fn();
  const alCancelar = vi.fn();
  const vista = render(<PedirCodigo acto="aprobar la orden" alVerificar={alVerificar} alCancelar={alCancelar} />);
  return { alVerificar, alCancelar, vista };
}

const titulo = (nombre: string) => screen.findByRole("heading", { name: nombre });

describe("confirmar en el momento", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("con la llave como último método dispara el aviso del sistema sin otro toque y sigue con el pedido", async () => {
    recordarMetodo("llave");
    const simulado = backend(conLlave());
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    const { alVerificar } = mostrar();

    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(get).toHaveBeenCalledOnce();
    // «Directo» no saltea el gesto: lo que sigue es después de que el servidor lo comprobó.
    expect(pedidos(simulado, "/api/v1/auth/llave/verificar")).toBe(1);
    expect(screen.queryByRole("heading", { name: "Confirmá que sos vos" })).toBeNull();
  });

  it("mientras espera el gesto no ofrece «Usar otro método»", async () => {
    recordarMetodo("llave");
    backend(conLlave());
    dispositivo(() => new Promise(() => undefined));
    mostrar();

    await titulo(HOJA_LLAVE);
    expect(screen.queryByRole("button", { name: "Usar otro método" })).toBeNull();
  });

  it("recuerda solo el tipo de método, sin nada de la llave ni del código", async () => {
    backend({
      "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO),
    });
    const { alVerificar } = mostrar();

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(ultimoMetodo()).toBe("aplicacion");
    expect(Object.values({ ...window.localStorage })).toEqual(["aplicacion"]);
  });

  it("con el código como último método lo pide encima y se confirma solo al completar los seis números", async () => {
    recordarMetodo("aplicacion");
    const simulado = backend(conLlave({ "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO) }));
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    const { alVerificar } = mostrar();

    await titulo(HOJA_CODIGO);
    expect(get).not.toHaveBeenCalled();
    const campo = screen.getByLabelText("Código");
    fireEvent.change(campo, { target: { value: "12345" } });
    expect(pedidos(simulado, "/api/v1/auth/totp/verificar")).toBe(0); // control: incompleto no sale
    fireEvent.change(campo, { target: { value: "123456" } });

    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(pedidos(simulado, "/api/v1/auth/totp/verificar")).toBe(1);
  });

  it("la primera vez en un dispositivo usa la llave de ese dispositivo", async () => {
    recordarLlave(LLAVE.credencial_id);
    backend(conLlave());
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    const { alVerificar } = mostrar();

    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(ultimoMetodo()).toBe("llave");
  });

  it("control: la llave de otro dispositivo no se dispara si hay código", async () => {
    backend(conLlave());
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    mostrar();

    await titulo(HOJA_CODIGO);
    expect(get).not.toHaveBeenCalled();
  });

  it("si el último método ya no está activo, usa uno que sí", async () => {
    recordarMetodo("llave");
    backend({ "GET /api/v1/auth/totp": estadoTotp(true, 0) });
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    mostrar();

    await titulo(HOJA_CODIGO);
    expect(get).not.toHaveBeenCalled();
  });

  it("si la persona cancela el aviso del sistema, ofrece probar de nuevo y otro método, y no sigue", async () => {
    recordarMetodo("llave");
    backend(conLlave());
    const get = vi.fn(async (): Promise<unknown> => {
      throw new DOMException("cancelado", "NotAllowedError");
    });
    dispositivo(get);
    const { alVerificar } = mostrar();

    await titulo("No se pudo confirmar");
    expect(screen.getByRole("alert").textContent).toContain("No se confirmó");
    expect(screen.getByRole("button", { name: "Usar otro método" })).toBeTruthy();
    expect(alVerificar).not.toHaveBeenCalled();

    get.mockResolvedValueOnce(CREDENCIAL);
    fireEvent.click(screen.getByRole("button", { name: "Probar de nuevo" }));
    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("AC-35: el error de confirmar se ve como error", async () => {
    recordarMetodo("llave");
    backend(conLlave());
    dispositivo(async (): Promise<unknown> => {
      throw new DOMException("cancelado", "NotAllowedError");
    });
    mostrar();
    await titulo("No se pudo confirmar");
    expect(screen.getByRole("alert").className).toContain("aviso-error");
  });

  it("AC-35: con la llave en otro dispositivo no ofrece «Probar de nuevo», que no cambiaría nada", async () => {
    // Solo la llave, y este navegador no puede usar llaves de acceso.
    backend({ "GET /api/v1/auth/totp": estadoTotp(false, 1) });
    mostrar();
    await titulo("No se pudo confirmar");
    expect(screen.getByRole("alert").textContent).toMatch(/^Este navegador no puede usar/);
    expect(screen.getByRole("alert").className).toContain("aviso-error");
    expect(screen.queryByRole("button", { name: "Probar de nuevo" })).toBeNull();
    // Control: «Cancelar» sigue ahí para salir.
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeTruthy();
  });

  it("si el backend rechaza la firma, no sigue", async () => {
    recordarMetodo("llave");
    backend(
      conLlave({
        "POST /api/v1/auth/llave/verificar": () =>
          Response.json({ codigo: "segundo_factor_invalido", mensaje: "x" }, { status: 400 }),
      }),
    );
    dispositivo(async () => CREDENCIAL);
    const { alVerificar } = mostrar();

    await titulo("No se pudo confirmar");
    expect(alVerificar).not.toHaveBeenCalled();
  });

  it("COD-01: «Usar otro método» muestra solo lo activo, sin el que se deja, y el que se usa queda como último", async () => {
    recordarMetodo("llave");
    backend(
      conLlave({
        "GET /api/v1/auth/totp": estadoTotp(true, 1, 5),
        "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO),
      }),
    );
    dispositivo(async () => {
      throw new DOMException("cancelado", "NotAllowedError");
    });
    const { alVerificar } = mostrar();

    await titulo("No se pudo confirmar");
    fireEvent.click(screen.getByRole("button", { name: "Usar otro método" }));
    await titulo("Usar otro método");
    expect(screen.getByRole("button", { name: "Código de la aplicación" })).toBeTruthy();
    // El que acaba de fallar no se ofrece otra vez.
    expect(screen.queryByRole("button", { name: "PIN, huella o cara" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Código de recuperación" }));
    await titulo("Código de recuperación");
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "k7m2q-x9p4t" } });
    expect(alVerificar).not.toHaveBeenCalled(); // control: uno de recuperación no sale solo
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    await waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(ultimoMetodo()).toBe("recuperacion");
  });

  it("COD-01: si queda un solo método más, el botón lo nombra y va directo", async () => {
    recordarMetodo("llave");
    backend(conLlave({ "GET /api/v1/auth/totp": estadoTotp(false, 1, 5) }));
    dispositivo(async () => {
      throw new DOMException("cancelado", "NotAllowedError");
    });
    mostrar();

    await titulo("No se pudo confirmar");
    expect(screen.queryByRole("button", { name: "Usar otro método" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Usar un código de recuperación" }));
    await titulo("Código de recuperación");
    // Control: desde el código, el único otro es la llave, y no se ofrece el código de recuperación otra vez.
    expect(screen.getByRole("button", { name: "Usar PIN, huella o cara" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Usar un código de recuperación" })).toBeNull();
  });

  it("COD-02: la hoja del código no repite el título ni dice «sigue el mismo pedido»", async () => {
    backend({ "GET /api/v1/auth/totp": estadoTotp(true, 0, 0) });
    mostrar();

    const hoja = await screen.findByRole("dialog");
    expect(hoja.textContent).not.toContain("sigue el mismo pedido");
  });

  it("con un solo método no ofrece otro", async () => {
    backend({ "GET /api/v1/auth/totp": estadoTotp(true, 0, 0) });
    mostrar();

    await titulo(HOJA_CODIGO);
    expect(screen.queryByRole("button", { name: "Usar otro método" })).toBeNull();
  });

  it("un código equivocado se dice en la misma hoja y no sigue", async () => {
    backend({
      "POST /api/v1/auth/totp/verificar": () =>
        Response.json({ codigo: "segundo_factor_invalido", mensaje: "x" }, { status: 400 }),
    });
    const { alVerificar } = mostrar();

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(alVerificar).not.toHaveBeenCalled();
    expect(ultimoMetodo()).toBeNull();
    expect((screen.getByLabelText("Código") as HTMLInputElement).value).toBe("");
  });

  it("sin red al preguntar lo dice y deja probar de nuevo", async () => {
    let conRed = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (pedido: Request) => {
        if (!conRed) throw new TypeError("Failed to fetch");
        return new URL(pedido.url).pathname === "/api/v1/auth/totp" ? estadoTotp(true, 0)() : new Response(null);
      }),
    );
    mostrar();

    await titulo("No se pudo confirmar");
    conRed = true;
    fireEvent.click(screen.getByRole("button", { name: "Probar de nuevo" }));
    await titulo(HOJA_CODIGO);
  });

  it("FL-CTA-03: sin llave en el navegador, la aplicación se activa encima del acto, sin salir, y el acto sigue", async () => {
    const simulado = backend({
      "GET /api/v1/auth/totp": estadoTotp(false, 0),
      "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: "ABCDEFGHIJKLMNOP", uri: "otpauth://totp/CEM:ana?secret=ABCDEFGHIJKLMNOP" }),
      "POST /api/v1/auth/totp/confirmar": () => Response.json({ codigos: ["k7m2q-x9p4t", "a2b3c-d4e5f"] }),
    });
    const alVerificar = vi.fn();
    render(
      <MemoryRouter initialEntries={["/pagos"]}>
        <Routes>
          <Route path="/pagos" element={<PedirCodigo acto="aprobar la orden" alVerificar={alVerificar} alCancelar={() => undefined} />} />
          <Route path="/cuenta/segundo-factor" element={<p>Otra página</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await titulo("Falta la confirmación con PIN, huella o código");
    // Ningún enlace saca de la pantalla: lo escrito en ella no se pierde.
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Usar una aplicación de códigos" }));
    await titulo("Cargar la cuenta en la aplicación");
    fireEvent.change(screen.getByLabelText("Código de la aplicación"), { target: { value: "123456" } });

    await titulo("Guardar los códigos de recuperación");
    expect(screen.getByText("Listo: la confirmación con código quedó activada.")).toBeTruthy();
    expect(alVerificar).not.toHaveBeenCalled(); // control: hasta guardar los códigos, el acto espera
    fireEvent.click(screen.getByRole("button", { name: "Códigos guardados: seguir" }));
    expect(alVerificar).toHaveBeenCalledOnce();
    expect(screen.queryByText("Otra página")).toBeNull();
    expect(pedidos(simulado, "/api/v1/auth/totp/confirmar")).toBe(1);
  });

  it("FL-CTA-01 (tanda 9): sin ningún método y con el plazo de la sesión vencido, pide entrar de nuevo en lugar de activar", async () => {
    backend({ "GET /api/v1/auth/totp": estadoTotp(false, 0), "GET /api/v1/auth/sesion": sesion(-1) });
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={() => undefined} />
      </MemoryRouter>,
    );

    await titulo("Falta la confirmación con PIN, huella o código");
    expect(await screen.findByText(ENTRAR_PARA_ACTIVAR)).toBeTruthy();
    expect(screen.getByRole("button", { name: BOTON_ENTRAR_PARA_ACTIVAR })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Usar una aplicación de códigos" })).toBeNull();
  });

  it("FL-CTA-01, control: dentro del plazo, se ofrece activar", async () => {
    backend({ "GET /api/v1/auth/totp": estadoTotp(false, 0), "GET /api/v1/auth/sesion": sesion(5) });
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={() => undefined} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Usar una aplicación de códigos" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(ENTRAR_PARA_ACTIVAR)).toBeNull());
  });

  it("si la pantalla se cierra mientras espera el gesto, corta el aviso y no sigue", async () => {
    recordarMetodo("llave");
    backend(conLlave());
    let soltar: (valor: unknown) => void = () => undefined;
    let señal: AbortSignal | null | undefined;
    dispositivo((opciones) => {
      señal = opciones?.signal;
      return new Promise((resolver) => {
        soltar = resolver;
      });
    });
    const { alVerificar, vista } = mostrar();

    await titulo(HOJA_LLAVE);
    vista.unmount();
    expect(señal?.aborted).toBe(true);
    soltar(CREDENCIAL);
    await Promise.resolve();
    expect(alVerificar).not.toHaveBeenCalled();
  });

  it("la hoja deja inerte lo de atrás y devuelve el foco al cerrarse", async () => {
    backend({});
    const atras = document.createElement("button");
    document.body.append(atras);
    atras.focus();
    const { vista } = mostrar();

    await titulo(HOJA_CODIGO);
    expect(atras.inert).toBe(true);
    expect(document.activeElement).toBe(screen.getByLabelText("Código"));
    vista.unmount();
    expect(atras.inert).toBe(false);
    expect(document.activeElement).toBe(atras);
    atras.remove();
  });

  it("Escape cancela", async () => {
    backend({});
    const { alCancelar } = mostrar();

    fireEvent.keyDown(await screen.findByLabelText("Código"), { key: "Escape" });
    expect(alCancelar).toHaveBeenCalledOnce();
  });
});

describe("informe de fricción, tanda 7 (AC-34)", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("con la llave en otro dispositivo no abre directo el código de recuperación: lo explica y lo ofrece", async () => {
    // Este navegador no sabe usar llaves; la persona tiene una llave y códigos de recuperación.
    backend({ "GET /api/v1/auth/totp": estadoTotp(false, 1, 5) });
    mostrar();

    expect(await titulo("La llave está en otro dispositivo")).toBeTruthy();
    expect(screen.queryByLabelText("Código")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Usar un código de recuperación (se gasta uno)" }));
    expect(await titulo("Código de recuperación")).toBeTruthy();
  });

  it("control: si el último método fue un código de recuperación, va directo a ese campo", async () => {
    recordarMetodo("recuperacion");
    backend({ "GET /api/v1/auth/totp": estadoTotp(false, 1, 5) });
    mostrar();

    expect(await titulo("Código de recuperación")).toBeTruthy();
  });

  it("sin nada activado, activa la llave en la misma hoja, muestra los códigos y sigue con el acto", async () => {
    const vacio = new ArrayBuffer(1);
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: async () => ({
          id: "x",
          rawId: vacio,
          type: "public-key",
          getClientExtensionResults: () => ({}),
          response: { clientDataJSON: vacio, attestationObject: vacio, getTransports: () => [] },
        }),
      },
    });
    const codigos = Array.from({ length: 10 }, (_, i) => `abcd${i + 2}-fgh${i + 2}k`);
    const simulado = backend({
      "GET /api/v1/auth/totp": estadoTotp(false, 0),
      "POST /api/v1/auth/llave/alta/opciones": () =>
        Response.json({ opciones: { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } } }),
      "POST /api/v1/auth/llave/alta": () => Response.json({ id: "1", codigos_recuperacion: codigos }, { status: 201 }),
    });
    const alVerificar = vi.fn();
    // La hoja enlaza a la página de confirmación con PIN, huella o código: necesita el enrutador.
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={alVerificar} alCancelar={() => undefined} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Activar con PIN, huella o cara" }));
    expect(await titulo("Guardar los códigos de recuperación")).toBeTruthy();
    expect(pedidos(simulado, "/api/v1/auth/llave/alta")).toBe(1);
    // Los códigos no se sueltan solos: el acto sigue recién con «Códigos guardados: seguir».
    expect(alVerificar).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Códigos guardados: seguir" }));
    expect(alVerificar).toHaveBeenCalledOnce();
    expect(ultimoMetodo()).toBe("llave");
  });
});

describe("tanda 7, revisión de #237: el alta de la llave dentro de la hoja no se puede cancelar a medias", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("con el alta en camino, Cancelar y Escape no cierran la hoja; al llegar, muestra los códigos", async () => {
    const vacio = new ArrayBuffer(1);
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: async () => ({
          id: "x",
          rawId: vacio,
          type: "public-key",
          getClientExtensionResults: () => ({}),
          response: { clientDataJSON: vacio, attestationObject: vacio, getTransports: () => [] },
        }),
      },
    });
    const codigos = Array.from({ length: 10 }, (_, i) => `abcd${i + 2}-fgh${i + 2}k`);
    let soltar: () => void = () => undefined;
    const alta = new Promise<Response>((resolver) => {
      soltar = () => resolver(Response.json({ id: "1", codigos_recuperacion: codigos }, { status: 201 }));
    });
    backend({
      "GET /api/v1/auth/totp": estadoTotp(false, 0),
      "POST /api/v1/auth/llave/alta/opciones": () =>
        Response.json({ opciones: { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } } }),
      "POST /api/v1/auth/llave/alta": () => alta as unknown as Response,
    });
    const alCancelar = vi.fn();
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={alCancelar} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Activar con PIN, huella o cara" }));
    const cancelar = await screen.findByRole("button", { name: "Cancelar" });
    await waitFor(() => expect(cancelar).toHaveProperty("disabled", true));
    fireEvent.click(cancelar);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(alCancelar).not.toHaveBeenCalled();

    soltar();
    expect(await titulo("Guardar los códigos de recuperación")).toBeTruthy();
    expect(alCancelar).not.toHaveBeenCalled();
  });

  it("con el alta de la llave en camino no se ofrece la aplicación; si falla, vuelve", async () => {
    vi.stubGlobal("PublicKeyCredential", class {});
    let rechazar: () => void = () => undefined;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: () =>
          new Promise((_, no) => {
            rechazar = () => no(new DOMException("no", "NotAllowedError"));
          }),
      },
    });
    backend({
      "GET /api/v1/auth/totp": estadoTotp(false, 0),
      "POST /api/v1/auth/llave/alta/opciones": () =>
        Response.json({ opciones: { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } } }),
    });
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={() => undefined} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Usar una aplicación de códigos" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Activar con PIN, huella o cara" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Usar una aplicación de códigos" })).toBeNull());
    // Cerrar la pestaña también pregunta antes.
    const salida = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(salida);
    expect(salida.defaultPrevented).toBe(true);

    rechazar();
    expect(await screen.findByRole("button", { name: "Usar una aplicación de códigos" })).toBeTruthy();
    const otra = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(otra);
    expect(otra.defaultPrevented).toBe(false);
  });

  it("control: si el alta falla, Cancelar vuelve a funcionar", async () => {
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: { create: () => Promise.reject(new DOMException("no", "NotAllowedError")) },
    });
    backend({
      "GET /api/v1/auth/totp": estadoTotp(false, 0),
      "POST /api/v1/auth/llave/alta/opciones": () =>
        Response.json({ opciones: { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } } }),
    });
    const alCancelar = vi.fn();
    render(
      <MemoryRouter>
        <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={alCancelar} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Activar con PIN, huella o cara" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(alCancelar).toHaveBeenCalledOnce();
  });
});
