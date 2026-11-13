import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { VIGENCIA_ENLACE_MS, tokenDelFragmento } from "./enlace";
import { guardarToken } from "./Entrar";
import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION_ENLACE } from "./fallas";
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

const SALUD = {
  estado: "ok",
  version: "0.1.0",
  sha_imagen: "0849ee9cafe0000000000000000000000000000",
  sha_contratos: "",
  version_contrato: "0.1.0",
};

const CORREO = "ana@ejemplo.com";
const RUTA_SESION = "/api/v1/auth/sesion";
const RUTA_CANJE = "/api/v1/auth/canje";
const RUTA_ENLACE = "/api/v1/auth/enlace";
const RUTA_CODIGO = "/api/v1/auth/codigo";
// El texto lo arma el backend (router.py, MENSAJE_PEDIDO); la página muestra el que llega.
const MENSAJE = "Si el correo corresponde a una cuenta, en unos minutos llega un código de 6 números para entrar.";

const INICIO = { level: 1, name: "El consorcio, en la mano" };
const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };
const PEDIR = { name: "Pedir un código para entrar" };
const CODIGO = { name: "Escribir el código del correo" };
const CONTRASENA = { name: "Entrar con contraseña" };
const RECHAZADO = /Este enlace no sirve acá/;
const REINTENTAR = { name: "Reintentar" };
// Una pantalla que no pide nada al backend: la de origen de quien vuelve a entrar.

type Respuesta = () => Response | Promise<Response>;

const noAutenticado: Respuesta = () =>
  Response.json({ codigo: "no_autenticado", mensaje: "x", id_correlacion: "c" }, { status: 401 });
const enlaceInvalido: Respuesta = () =>
  Response.json({ codigo: "enlace_invalido", mensaje: "x", id_correlacion: "c" }, { status: 401 });

/**
 * Responde por ruta y lo que no está previsto falla la prueba. Como el backend de verdad, el canje
 * que sale bien abre la sesión: antes `/auth/sesion` da 401 y después 200.
 */
function backend({ canje, sesion }: { canje?: Respuesta; sesion?: Respuesta } = {}) {
  let adentro = false;
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/health") return Response.json(SALUD);
    if (camino === RUTA_SESION) {
      if (sesion) return sesion();
      return adentro ? Response.json(SESION) : noAutenticado();
    }
    if (camino === RUTA_CANJE) {
      const respuesta = await (canje ?? (() => Response.json(SESION)))();
      adentro = respuesta.ok;
      return respuesta;
    }
    if (camino === RUTA_CODIGO) {
      adentro = true;
      return Response.json(SESION);
    }
    if (camino === RUTA_ENLACE) return Response.json({ estado: "pedido", mensaje: MENSAJE }, { status: 202 });
    if (camino === "/api/v1/me/consorcios") return Response.json([]);
    if (camino === "/api/v1/me/invitaciones" || camino === "/api/v1/pagos-por-aclarar")
      return Response.json({ elementos: [] });
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function abrir(url: string, llegada?: { renovar: boolean; volver?: string }) {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // StrictMode, como en main.tsx: monta dos veces y el canje tiene que salir una sola.
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[{ pathname: "/entrar", state: llegada }]}>
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

function postsDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return pedidosDe(simulado).filter((p) => p.method === "POST");
}

function caminosDe(simulado: ReturnType<typeof vi.fn>): string[] {
  return pedidosDe(simulado).map((p) => new URL(p.url).pathname);
}

/** El estado de la pantalla: el primero es el de AvisoLlegada, que está vacío si no hay aviso. */
function estadoDePantalla() {
  const [aviso, estado, ...otros] = screen.getAllByRole("status");
  expect(aviso?.textContent).toBe("");
  expect(otros).toEqual([]);
  if (estado === undefined) throw new Error("la pantalla no tiene estado");
  return estado;
}

const otrasFormas = () => within(screen.getByRole("region", { name: "Otras formas de entrar" }));

function elegir(forma: string) {
  fireEvent.click(otrasFormas().getByRole("button", { name: forma }));
}

const correoDe = (forma: { name: string }) =>
  within(screen.getByRole("region", forma)).getByLabelText("Correo") as HTMLInputElement;

function escribirCorreo(forma: { name: string }, correo: string) {
  fireEvent.change(correoDe(forma), { target: { value: correo } });
}

function pedirElEnlace(correo: string) {
  escribirCorreo(PEDIR, correo);
  fireEvent.submit(within(screen.getByRole("region", PEDIR)).getByRole("button", { name: "Enviar el código" }).closest("form")!);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("página /entrar", () => {
  it("canjea el token del fragmento por POST, en el cuerpo, una sola vez, y lo borra de la URL", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText("Listo: sesión abierta.")).toBeTruthy();
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    const [canje, ...otros] = postsDe(pedido);
    expect(otros).toEqual([]);
    if (canje === undefined) throw new Error("no hubo pedido de canje");
    // Con un token no se pregunta por la sesión antes de canjearlo: el canje es lo primero que sale.
    expect(pedidosDe(pedido)[0]).toBe(canje);
    const url = new URL(canje.url);
    expect([canje.method, url.pathname, url.search]).toEqual(["POST", RUTA_CANJE, ""]);
    expect(await canje.json()).toEqual({ token: TOKEN });
  });

  it("si el backend rechaza, muestra un error genérico", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(/Este enlace no sirve acá/)).toBeTruthy();
    expect(screen.queryByText("Listo: sesión abierta.")).toBeNull();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(window.location.hash).toBe("");
  });

  it("sin token en el fragmento no canjea nada: solo pregunta por la sesión y ofrece el enlace", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    abrir("/entrar");

    expect(screen.getByRole("region", PEDIR)).toBeTruthy();
    await vi.waitFor(() => expect(pedido).toHaveBeenCalled());
    expect(new Set(caminosDe(pedido))).toEqual(new Set([RUTA_SESION]));
    expect(postsDe(pedido)).toEqual([]);
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend());
    const { container } = abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText("Listo: sesión abierta.");
    await screen.findByRole("heading", INICIO);

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
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);
    const { container } = abrir("/entrar");

    expect(await screen.findByText("Listo: sesión abierta.")).toBeTruthy();
    await screen.findByRole("heading", INICIO);
    const [canje, ...otros] = postsDe(pedido);
    expect(otros).toEqual([]);
    if (canje === undefined) throw new Error("no hubo pedido de canje después de recargar");
    expect(new URL(canje.url).pathname).toBe(RUTA_CANJE);
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
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText("Listo: sesión abierta.");
    const [canje] = postsDe(pedido);
    expect(await canje!.json()).toEqual({ token: TOKEN });
  });

  it("lo borra al terminar aunque el backend lo rechace", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText(/Este enlace no sirve acá/);
    expect(guardado()).toBeNull();
  });

  it("sin red no lo borra: el enlace no se gastó", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));

    abrir(`/entrar#token=${TOKEN}`);

    await screen.findByText(SIN_CONEXION_ENLACE);
    expect(guardado()?.token).toBe(TOKEN);
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
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    const { container } = abrir("/entrar");

    expect(screen.getByRole("region", PEDIR)).toBeTruthy();
    await vi.waitFor(() => expect(pedido).toHaveBeenCalled());
    expect(postsDe(pedido)).toEqual([]);
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

describe("un canje que falla por algo que no es el enlace", () => {
  const guardadoEnLaPestana = () => window.sessionStorage.getItem(CLAVE_GUARDADO);
  const canjesDe = (simulado: ReturnType<typeof vi.fn>) =>
    postsDe(simulado).filter((p) => new URL(p.url).pathname === RUTA_CANJE);
  const limite = () => Response.json({ codigo: "demasiadas_peticiones", mensaje: "x", id_correlacion: "c" }, { status: 429 });
  const caido = (status: number) => () =>
    Response.json({ codigo: "servicio_no_disponible", mensaje: "x", id_correlacion: "c" }, { status });

  it.each<[string, Respuesta, string | RegExp]>([
    ["un 503", caido(503), SERVICIO_NO_DISPONIBLE],
    ["un 500", caido(500), SERVICIO_NO_DISPONIBLE],
    ["un 429", limite, /Se llegó al límite de pedidos/],
    ["sin red", () => Promise.reject(new TypeError("Failed to fetch")), SIN_CONEXION_ENLACE],
  ])("con %s conserva el token, y Reintentar repite el canje y entra", async (_, falla, mensaje) => {
    let fallando = true;
    const pedido = backend({ canje: () => (fallando ? falla() : Response.json(SESION)) });
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.queryByText(RECHAZADO)).toBeNull();
    expect(guardadoEnLaPestana()).toContain(TOKEN);

    fallando = false;
    fireEvent.click(screen.getByRole("button", REINTENTAR));

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(await screen.findByText("Listo: sesión abierta.")).toBeTruthy();
    const canjes = canjesDe(pedido);
    expect(canjes).toHaveLength(2);
    expect(await canjes[1]?.json()).toEqual({ token: TOKEN });
    expect(guardadoEnLaPestana()).toBeNull();
  });

  it("mientras tanto se puede entrar de otra forma: los formularios siguen a la vista", async () => {
    vi.stubGlobal("fetch", backend({ canje: caido(503) }));
    abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText(SERVICIO_NO_DISPONIBLE);

    expect(screen.getByRole("region", PEDIR)).toBeTruthy();
  });

  it("control: un 401 es del enlace, se borra y no ofrece reintentar", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));
    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(RECHAZADO)).toBeTruthy();
    expect(screen.queryByRole("button", REINTENTAR)).toBeNull();
    expect(guardadoEnLaPestana()).toBeNull();
  });

  it("control: un token sin la forma del contrato (422) tampoco se reintenta", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ canje: () => Response.json({ codigo: "validacion", mensaje: "x", id_correlacion: "c" }, { status: 422 }) }),
    );
    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(RECHAZADO)).toBeTruthy();
    expect(screen.queryByRole("button", REINTENTAR)).toBeNull();
    expect(guardadoEnLaPestana()).toBeNull();
  });

  it("si el enlace ya se había consumido, el reintento cae en el 401 y lo dice", async () => {
    let intentos = 0;
    vi.stubGlobal(
      "fetch",
      backend({
        canje: () => {
          intentos += 1;
          return intentos === 1 ? caido(503)() : enlaceInvalido();
        },
      }),
    );
    abrir(`/entrar#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", REINTENTAR));

    expect(await screen.findByText(RECHAZADO)).toBeTruthy();
    expect(screen.queryByRole("button", REINTENTAR)).toBeNull();
    expect(guardadoEnLaPestana()).toBeNull();
  });

  it("elegir otra forma deja atrás el enlace: ni el error ni el token siguen", async () => {
    vi.stubGlobal("fetch", backend({ canje: caido(503) }));
    abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText(SERVICIO_NO_DISPONIBLE);

    elegir("Ya tengo un código");

    expect(screen.queryByText(SERVICIO_NO_DISPONIBLE)).toBeNull();
    expect(screen.queryByRole("button", REINTENTAR)).toBeNull();
    expect(guardadoEnLaPestana()).toBeNull();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend({ canje: caido(503) }));
    const { container } = abrir(`/entrar#token=${TOKEN}`);
    await screen.findByRole("button", REINTENTAR);

    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("después de un enlace rechazado", () => {
  it("pedir uno nuevo saca el error: no queda junto al aviso de que va en camino", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));
    abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText(RECHAZADO);

    elegir("Pedir un código por correo");
    pedirElEnlace(CORREO);

    expect(await screen.findByText(MENSAJE)).toBeTruthy();
    expect(screen.queryByText(RECHAZADO)).toBeNull();
  });

  it("elegir otra forma también lo saca", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));
    abrir(`/entrar#token=${TOKEN}`);
    await screen.findByText(RECHAZADO);

    elegir("Entrar con contraseña");

    expect(screen.queryByText(RECHAZADO)).toBeNull();
  });

  it("control: mientras no haga nada, el error sigue", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));
    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(RECHAZADO)).toBeTruthy();
    await new Promise((listo) => setTimeout(listo, 0));
    expect(screen.getByText(RECHAZADO)).toBeTruthy();
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
      backend({
        canje: async () => {
          await listo;
          return Response.json(SESION);
        },
      }),
    );
    abrir(`/entrar#token=${TOKEN}`);

    expect(estadoDePantalla().textContent).toBe("Entrando…");
    expect(screen.queryByRole("region", PEDIR)).toBeNull();
    expect(screen.queryByRole("region", CODIGO)).toBeNull();
    expect(screen.queryByRole("region", CONTRASENA)).toBeNull();
    expect(screen.queryByRole("region", { name: "Otras formas de entrar" })).toBeNull();
    soltar();

    expect(await screen.findByText("Listo: sesión abierta.")).toBeTruthy();
    expect(screen.queryByText("Entrando…")).toBeNull();
  });
});

describe("/entrar decide por la sesión", () => {
  const conSesion: Respuesta = () => Response.json(SESION);

  it("con sesión lleva al inicio", async () => {
    vi.stubGlobal("fetch", backend({ sesion: conSesion }));

    abrir("/entrar");

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it("control: sin sesión se queda en /entrar", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);

    abrir("/entrar");

    await vi.waitFor(() => expect(caminosDe(pedido)).toContain(RUTA_SESION));
    await new Promise((listo) => setTimeout(listo, 0));
    expect(screen.getByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
  });

  it("sin saber si hay sesión (sin red) se queda en /entrar", async () => {
    const pedido = backend({ sesion: () => Promise.reject(new TypeError("Failed to fetch")) });
    vi.stubGlobal("fetch", pedido);

    abrir("/entrar");

    await vi.waitFor(() => expect(caminosDe(pedido)).toContain(RUTA_SESION));
    await new Promise((listo) => setTimeout(listo, 0));
    expect(screen.getByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
  });

  it("con un token en el fragmento canjea aunque haya sesión: no se lo saltea yendo al inicio", async () => {
    const pedido = backend({ sesion: conSesion, canje: enlaceInvalido });
    vi.stubGlobal("fetch", pedido);

    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(/Este enlace no sirve acá/)).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(caminosDe(pedido)).toEqual([RUTA_CANJE]);
  });
});

describe("/entrar: una forma por vez", () => {
  const formasVisibles = () =>
    [PEDIR, CODIGO, CONTRASENA].filter((forma) => screen.queryByRole("region", forma) !== null).map((f) => f.name);
  const botones = () => otrasFormas().getAllByRole("button").map((b) => b.textContent);

  it("empieza por el enlace y ofrece las otras dos", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");

    expect(formasVisibles()).toEqual([PEDIR.name]);
    expect(botones()).toEqual(["Ya tengo un código", "Entrar con contraseña"]);
  });

  it("cambia de forma sin dejar la anterior, y la que está a la vista no se ofrece", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");

    elegir("Ya tengo un código");
    expect(formasVisibles()).toEqual([CODIGO.name]);
    expect(botones()).toEqual(["Pedir un código por correo", "Entrar con contraseña"]);

    elegir("Entrar con contraseña");
    expect(formasVisibles()).toEqual([CONTRASENA.name]);
    expect(botones()).toEqual(["Pedir un código por correo", "Ya tengo un código"]);

    elegir("Pedir un código por correo");
    expect(formasVisibles()).toEqual([PEDIR.name]);
    expect(botones()).toEqual(["Ya tengo un código", "Entrar con contraseña"]);
  });

  it("al cambiar, el foco va al primer campo de la forma elegida; al abrir la página, no", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    expect(document.activeElement).toBe(document.body);

    elegir("Entrar con contraseña");

    const correo = within(screen.getByRole("region", CONTRASENA)).getByLabelText("Correo");
    expect(document.activeElement).toBe(correo);
  });

  it("pedir el enlace pasa al código con el correo escrito y el mensaje del servidor arriba", async () => {
    const pedido = backend();
    vi.stubGlobal("fetch", pedido);
    abrir("/entrar");
    const enlace = within(screen.getByRole("region", PEDIR));
    fireEvent.change(enlace.getByLabelText("Correo"), { target: { value: `  ${CORREO} ` } });
    fireEvent.submit(enlace.getByRole("button", { name: "Enviar el código" }).closest("form")!);

    const codigo = within(await screen.findByRole("region", CODIGO));
    expect(formasVisibles()).toEqual([CODIGO.name]);
    expect(codigo.getByText(MENSAJE)).toBeTruthy();
    expect((codigo.getByLabelText("Correo") as HTMLInputElement).value).toBe(CORREO);
    expect(postsDe(pedido).map((p) => new URL(p.url).pathname)).toEqual([RUTA_ENLACE]);
  });

  it("control: elegir el código sin pedir el enlace no trae correo ni mensaje", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    elegir("Ya tengo un código");

    const codigo = within(screen.getByRole("region", CODIGO));
    expect((codigo.getByLabelText("Correo") as HTMLInputElement).value).toBe("");
    expect(screen.queryByText(MENSAJE)).toBeNull();
  });

  it("el correo escrito sigue al cambiar de forma, en las tres", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    elegir("Ya tengo un código");
    escribirCorreo(CODIGO, CORREO);

    elegir("Pedir un código por correo");
    expect(correoDe(PEDIR).value).toBe(CORREO);

    elegir("Entrar con contraseña");
    expect(correoDe(CONTRASENA).value).toBe(CORREO);

    escribirCorreo(CONTRASENA, "otra@ejemplo.com");
    elegir("Ya tengo un código");
    expect(correoDe(CODIGO).value).toBe("otra@ejemplo.com");
  });

  it("pedir el enlace deja el foco en el código, no en el correo ya escrito", async () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");

    pedirElEnlace(CORREO);

    const codigo = within(await screen.findByRole("region", CODIGO));
    expect(document.activeElement).toBe(codigo.getByLabelText(/^Código de 6 números/));
  });

  it("con el correo escrito, elegir otra forma lleva el foco a lo que falta", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    escribirCorreo(PEDIR, CORREO);

    elegir("Entrar con contraseña");

    expect(document.activeElement).toBe(within(screen.getByRole("region", CONTRASENA)).getByLabelText("Contraseña"));
  });

  it("no tiene faltas de accesibilidad detectables por axe en ninguna de las formas", async () => {
    vi.stubGlobal("fetch", backend());
    const { container } = abrir("/entrar");

    for (const forma of ["Ya tengo un código", "Entrar con contraseña", "Pedir un código por correo"]) {
      elegir(forma);
      const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
      expect(resultado.violations).toEqual([]);
    }
  });
});

describe("informe de fricción, tanda 7: acceso", () => {
  const CLAVE_FORMA = "cem.entrar.ultima-forma";
  const formaVisible = () => [PEDIR, CODIGO, CONTRASENA].filter((f) => screen.queryByRole("region", f) !== null).map((f) => f.name);

  it("pregunta 16: /entrar abre en pedir el código del correo; el enlace queda como alternativa", () => {
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");

    expect(formaVisible()).toEqual([PEDIR.name]);
    const pedir = within(screen.getByRole("region", PEDIR));
    expect(pedir.getByRole("button", { name: "Enviar el código" })).toBeTruthy();
    expect(pedir.getByText(/El mismo correo trae un enlace, que sirve solo en este dispositivo/)).toBeTruthy();
  });

  it("AC-28: después de pedirlo dice qué hacer si no llega, sin otro pedido que distinga si hay cuenta", async () => {
    const simulado = backend();
    vi.stubGlobal("fetch", simulado);
    abrir("/entrar");
    pedirElEnlace(CORREO);

    const codigo = within(await screen.findByRole("region", CODIGO));
    expect(codigo.getByText(MENSAJE)).toBeTruthy();
    expect(codigo.getByText(/la cuenta se crea con la invitación que envía la administración del edificio/)).toBeTruthy();
    // El aviso sale de la página, igual para todos: el único pedido es el del código, cuyo 202 no cambia.
    expect(postsDe(simulado).map((p) => new URL(p.url).pathname)).toEqual([RUTA_ENLACE]);
  });

  it("AC-29: con el enlace rechazado, el campo del código del mismo correo queda a la vista", async () => {
    vi.stubGlobal("fetch", backend({ canje: enlaceInvalido }));
    abrir(`/entrar#token=${TOKEN}`);

    expect(await screen.findByText(RECHAZADO)).toBeTruthy();
    expect(within(screen.getByRole("region", CODIGO)).getByLabelText("Código de 6 números")).toBeTruthy();
    expect(screen.queryByRole("region", PEDIR)).toBeNull();
  });

  it("AC-30: si la última entrada fue con contraseña, abre esa forma; sin nada guardado, el código", () => {
    window.localStorage.setItem(CLAVE_FORMA, "contrasena");
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    expect(formaVisible()).toEqual([CONTRASENA.name]);
    cleanup();

    window.localStorage.clear();
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    expect(formaVisible()).toEqual([PEDIR.name]);
  });

  it("AC-30: entrar con el código lo recuerda como forma por correo, y la próxima vez abre el código", async () => {
    window.localStorage.setItem(CLAVE_FORMA, "contrasena");
    vi.stubGlobal("fetch", backend());
    abrir("/entrar");
    elegir("Ya tengo un código");
    const codigo = within(screen.getByRole("region", CODIGO));
    fireEvent.change(codigo.getByLabelText("Correo"), { target: { value: CORREO } });
    fireEvent.change(codigo.getByLabelText("Código de 6 números"), { target: { value: "042917" } });
    fireEvent.submit(codigo.getByRole("button", { name: "Entrar con el código" }).closest("form")!);

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(window.localStorage.getItem(CLAVE_FORMA)).toBe("correo");
  });
});
