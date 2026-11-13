import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { YA_HAY_CUENTA_MANDATO } from "./InvitacionMandato";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const TOKEN = "A".repeat(21) + "_-" + "b".repeat(20);
const RUTA = "/invitacion-mandato";

const RECIBIDA = {
  consorcio: "Edificio Alvear 100",
  mandatario: "Estudio X",
  invitada_por: "Ana",
  vence_en: "2026-10-26T00:00:00Z",
};

function aceptada(vigenteDesde: string) {
  return {
    consorcio_id: "0199e3a2-1234-7abc-8def-0123456789ab",
    persona_id: "0199e3a2-1234-7abc-8def-0123456789ac",
    mandato_id: "0199e3a2-1234-7abc-8def-0123456789ad",
    cuenta_nueva: true,
    vigente_desde: vigenteDesde,
  };
}

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

function abrir(url: string) {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA]}>
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

const CONSULTAR = "/api/v1/auth/invitacion-mandato/consultar";
const ACEPTAR = "/api/v1/auth/invitacion-mandato/aceptar";
const SALIR = "/api/v1/auth/salir";
const INICIO = { level: 1, name: "Consorcio en mano" };
const AVISO = "Listo: la cuenta quedó asociada a la administración de Edificio Alvear 100.";
const CAMPO_NOMBRE = "Nombre (solo si la cuenta es nueva)";

type Respuesta = () => Response | Promise<Response>;

/**
 * Responde la consulta con la invitación y la aceptación con `aceptacion`; aceptar abre la sesión,
 * que es lo que consulta el inicio. Lo que no está previsto falla la prueba.
 */
function backend(
  aceptacion: () => Response,
  consulta: Respuesta = () => Response.json(RECIBIDA),
  salir: Respuesta = () => new Response(null, { status: 204 }),
) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === CONSULTAR) return consulta();
    if (camino === ACEPTAR) return aceptacion();
    if (camino === SALIR) return salir();
    if (camino === "/api/v1/auth/sesion") return Response.json({});
    if (camino === "/health") return error("servicio_no_disponible", 503);
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function aceptacionesDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return pedidosDe(simulado).filter((p) => new URL(p.url).pathname === ACEPTAR);
}

function consultasDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return pedidosDe(simulado).filter((p) => new URL(p.url).pathname === CONSULTAR);
}

/** El estado de la pantalla: el primero es el de AvisoLlegada, que está vacío si no hay aviso. */
function estadoDePantalla() {
  const [aviso, estado, ...otros] = screen.getAllByRole("status");
  expect(aviso?.textContent).toBe("");
  expect(otros).toEqual([]);
  if (estado === undefined) throw new Error("la pantalla no tiene estado");
  return estado;
}

async function aceptarBoton() {
  return screen.findByRole("button", { name: "Aceptar y asociar mi cuenta" });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /invitacion-mandato", () => {
  it("consulta una sola vez con el token en el cuerpo, lo borra de la URL y no muestra CUIT", async () => {
    const simulado = backend(() => Response.json(aceptada("2026-01-01")));
    vi.stubGlobal("fetch", simulado);
    abrir(`${RUTA}#token=${TOKEN}`);
    expect(await screen.findByText("Estudio X")).toBeTruthy();
    const consulta = pedidosDe(simulado)[0] as Request;
    expect(pedidosDe(simulado)).toHaveLength(1);
    expect(new URL(consulta.url).pathname).toBe(CONSULTAR);
    expect(new URL(consulta.url).search + new URL(consulta.url).hash).toBe("");
    expect(await consulta.json()).toEqual({ token: TOKEN });
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe(RUTA);
    expect(screen.getByText("Ana")).toBeTruthy();
  });

  it("acepta con el nombre elegido y el CSRF, y avisa que quedó asociada", async () => {
    // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    const simulado = backend(() => Response.json(aceptada("2026-01-01")));
    vi.stubGlobal("fetch", simulado);
    abrir(`${RUTA}#token=${TOKEN}`);
    const campo = await screen.findByLabelText("Nombre (solo si la cuenta es nueva)");
    expect((campo as HTMLInputElement).value).toBe("Estudio X");
    fireEvent.change(campo, { target: { value: "  Beto Estudio " } });
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(AVISO)).toBeTruthy();
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Ir al inicio" })).toBeNull();
    const [aceptacion, ...otras] = aceptacionesDe(simulado);
    expect(otras).toEqual([]);
    if (aceptacion === undefined) throw new Error("no hubo aceptación");
    expect(await aceptacion.json()).toEqual({ token: TOKEN, nombre: "Beto Estudio" });
    expect(aceptacion.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(screen.queryByText(/El mandato empieza/)).toBeNull();
  });

  it("con un mandato futuro avisa desde cuándo va a ver el consorcio", async () => {
    vi.stubGlobal("fetch", backend(() => Response.json(aceptada("2099-11-01"))));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(
      await screen.findByText(`${AVISO} El mandato empieza el 01/11/2099: desde ese día el consorcio aparece en la cuenta.`),
    ).toBeTruthy();
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
  });

  it.each([
    ["invitacion_requiere_sesion", /Ya hay una cuenta con este correo/],
    ["invitacion_de_otra_persona", /no es la del correo invitado/],
    ["asociacion_incompatible_con_consejo", /el Consejo fiscaliza y no administra/],
    ["asociacion_de_si_mismo", /Quien emitió o aprobó la invitación no puede aceptarla para sí/],
  ])("explica el rechazo %s", async (codigo, texto) => {
    vi.stubGlobal("fetch", backend(() => error(codigo, 409)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(texto)).toBeTruthy();
  });

  it("un 409 con otro código no se presenta como un rechazo conocido", async () => {
    vi.stubGlobal("fetch", backend(() => error("idempotencia_en_curso", 409)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(/No se pudo completar el pedido/)).toBeTruthy();
    expect(await aceptarBoton()).toBeTruthy();
  });

  it("un enlace que no sirve muestra el error genérico, al consultar y al aceptar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => error("invitacion_invalida", 404)));
    abrir(`${RUTA}#token=${TOKEN}`);
    expect(await screen.findByText(/Esta invitación no sirve/)).toBeTruthy();
    cleanup();
    vi.stubGlobal("fetch", backend(() => error("invitacion_invalida", 404)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(/Esta invitación no sirve/)).toBeTruthy();
  });

  it("sin token no pide nada", () => {
    const simulado = vi.fn();
    vi.stubGlobal("fetch", simulado);
    abrir(RUTA);
    expect(screen.getByText("Para asociar la cuenta hay que abrir el enlace que llegó por correo.")).toBeTruthy();
    expect(simulado).not.toHaveBeenCalled();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend(() => Response.json(aceptada("2026-01-01"))));
    const { container } = abrir(`${RUTA}#token=${TOKEN}`);
    await aceptarBoton();
    const resultado = await axe.run(container);
    expect(resultado.violations).toEqual([]);
  });
});

/**
 * Deja en vilo los pedidos a `camino` hasta que la prueba llame a `soltar`: así se ve el estado de
 * carga, que con un fetch simulado que responde al instante no llega a pintarse.
 */
function retener(simulado: (pedido: Request) => Promise<Response>, camino: string) {
  let soltar: () => void = () => undefined;
  const listo = new Promise<void>((r) => {
    soltar = r;
  });
  const retenido = vi.fn(async (pedido: Request) => {
    if (new URL(pedido.url).pathname === camino) await listo;
    return simulado(pedido);
  });
  return { retenido, soltar: () => soltar() };
}

describe("ningún error de /invitacion-mandato termina sin un paso siguiente", () => {
  const SALIR_DE_ESTA = { name: "Salir de esta cuenta" };
  const AL_INICIO = { name: "Ir al inicio" };
  const OTRA_PERSONA = /no es la del correo invitado/;
  const OTRA_CUENTA = () => error("invitacion_de_otra_persona", 409);

  async function botonSalir() {
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    return screen.findByRole("button", SALIR_DE_ESTA);
  }

  it("con otra cuenta abierta, salir vuelve a la invitación con el mismo token, sin ir al correo", async () => {
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    const simulado = backend(OTRA_CUENTA);
    vi.stubGlobal("fetch", simulado);

    fireEvent.click(await botonSalir());

    expect(await aceptarBoton()).toBeTruthy();
    expect(screen.queryByText(OTRA_PERSONA)).toBeNull();
    const salida = pedidosDe(simulado).find((p) => new URL(p.url).pathname === SALIR);
    expect(salida?.headers.get("X-CSRF-Token")).toBe("abc123");
    const consultas = consultasDe(simulado);
    expect(consultas).toHaveLength(2);
    expect(await consultas[1]?.json()).toEqual({ token: TOKEN });
  });

  it("un 401 al salir es que ya estaba afuera: sigue igual", async () => {
    const simulado = backend(OTRA_CUENTA, undefined, () => error("no_autenticado", 401));
    vi.stubGlobal("fetch", simulado);

    fireEvent.click(await botonSalir());

    expect(await aceptarBoton()).toBeTruthy();
    expect(consultasDe(simulado)).toHaveLength(2);
  });

  it("con un 503 al salir lo dice, se queda ahí y deja probar de nuevo", async () => {
    const simulado = backend(OTRA_CUENTA, undefined, () => error("servicio_no_disponible", 503));
    vi.stubGlobal("fetch", simulado);

    fireEvent.click(await botonSalir());

    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect(screen.getByText(OTRA_PERSONA)).toBeTruthy();
    const boton = screen.getByRole("button", SALIR_DE_ESTA) as HTMLButtonElement;
    await vi.waitFor(() => expect(boton.disabled).toBe(false));
    expect(consultasDe(simulado)).toHaveLength(1);
  });

  it.each<[string, () => Response, RegExp]>([
    ["no sirve", () => error("invitacion_invalida", 404), /Esta invitación no sirve/],
    ["choca con el Consejo", () => error("asociacion_incompatible_con_consejo", 409), /el Consejo fiscaliza/],
    ["la emitió quien la abre", () => error("asociacion_de_si_mismo", 409), /no puede aceptarla para sí/],
  ])("si %s, lleva al inicio", async (_, rechazo, texto) => {
    vi.stubGlobal("fetch", backend(rechazo));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(texto)).toBeTruthy();

    fireEvent.click(screen.getByRole("link", AL_INICIO));

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
  });

  it("sin enlace también lleva al inicio", () => {
    vi.stubGlobal("fetch", vi.fn());
    abrir(RUTA);

    expect(screen.getByRole("link", AL_INICIO).getAttribute("href")).toBe("/");
  });

  it("control: quien tiene que entrar no ve otra salida que la confunda", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_requiere_sesion", 409)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByRole("region", { name: "Entrar para asociar la cuenta" })).toBeTruthy();
    expect(screen.queryByRole("link", AL_INICIO)).toBeNull();
    expect(screen.queryByRole("button", SALIR_DE_ESTA)).toBeNull();
  });
});

describe("G-35 en /invitacion-mandato", () => {
  it("con una cuenta que ya existe, se entra acá mismo y la asociación sale sola, sin volver a abrir el correo", async () => {
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    let conSesion = false;
    const pedido = vi.fn(async (p: Request) => {
      const camino = new URL(p.url).pathname;
      if (camino === CONSULTAR) return Response.json(RECIBIDA);
      if (camino === ACEPTAR)
        return conSesion ? Response.json({ ...aceptada("2026-01-01"), cuenta_nueva: false }) : error("invitacion_requiere_sesion", 409);
      if (camino === "/api/v1/auth/codigo") {
        conSesion = true;
        return Response.json({ persona_id: "11111111-1111-4111-8111-111111111111" });
      }
      if (camino === "/api/v1/auth/sesion") return Response.json({});
      if (camino === "/health") return error("servicio_no_disponible", 503);
      throw new Error(`pedido no previsto: ${p.method} ${camino}`);
    });
    vi.stubGlobal("fetch", pedido);
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByText(YA_HAY_CUENTA_MANDATO)).toBeTruthy();
    expect(screen.queryByText(/abrir de nuevo el enlace del correo/)).toBeNull();
    const entrar = screen.getByRole("region", { name: "Entrar para asociar la cuenta" });
    fireEvent.click(within(entrar).getByRole("button", { name: "Ya tengo un código" }));
    fireEvent.change(within(entrar).getByLabelText("Correo"), { target: { value: "estudio@ejemplo.com" } });
    fireEvent.change(within(entrar).getByLabelText(/Código de/), { target: { value: "042917" } });
    fireEvent.click(within(entrar).getByRole("button", { name: "Entrar con el código" }));

    expect(await screen.findByText(AVISO)).toBeTruthy();
    const aceptaciones = aceptacionesDe(pedido);
    expect(aceptaciones).toHaveLength(2);
    expect(((await aceptaciones[1]?.json()) as { token: string }).token).toBe(TOKEN);
  });
});

describe("página /invitacion-mandato: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta dice «Buscando la invitación…» y lo saca al llegar la respuesta", async () => {
    const { retenido, soltar } = retener(
      backend(() => Response.json(aceptada("2026-01-01"))),
      CONSULTAR,
    );
    vi.stubGlobal("fetch", retenido);
    abrir(`${RUTA}#token=${TOKEN}`);

    expect(estadoDePantalla().textContent).toBe("Buscando la invitación…");
    expect(screen.queryByRole("button", { name: "Aceptar y asociar mi cuenta" })).toBeNull();
    soltar();

    expect(await aceptarBoton()).toBeTruthy();
    expect(screen.queryByText("Buscando la invitación…")).toBeNull();
  });

  it("carga: mientras acepta dice «Aceptando…» con el botón deshabilitado", async () => {
    const { retenido, soltar } = retener(
      backend(() => Response.json(aceptada("2026-01-01"))),
      ACEPTAR,
    );
    vi.stubGlobal("fetch", retenido);
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(estadoDePantalla().textContent).toBe("Aceptando…");
    expect(((await aceptarBoton()) as HTMLButtonElement).disabled).toBe(true);
    soltar();

    expect(await screen.findByText(/la cuenta quedó asociada/)).toBeTruthy();
    expect(screen.queryByText("Aceptando…")).toBeNull();
  });

  it("sin red al consultar: lo dice, no ofrece aceptar y deja reintentar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    abrir(`${RUTA}#token=${TOKEN}`);

    expect(await screen.findByText(/No se pudo completar el pedido. Hay que revisar la conexión/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Aceptar y asociar mi cuenta" })).toBeNull();
    expect(within(screen.getByRole("main")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Reintentar"]);
  });

  it("«Reintentar» consulta de nuevo con el mismo token y, si sale bien, muestra la invitación", async () => {
    let caido = true;
    const simulado = backend(
      () => Response.json(aceptada("2026-01-01")),
      () => (caido ? error("servicio_no_disponible", 503) : Response.json(RECIBIDA)),
    );
    vi.stubGlobal("fetch", simulado);
    abrir(`${RUTA}#token=${TOKEN}`);
    await screen.findByText(/El servicio no está disponible/);
    expect(consultasDe(simulado)).toHaveLength(1);

    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(await aceptarBoton()).toBeTruthy();
    expect(screen.queryByText(/El servicio no está disponible/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    const consultas = consultasDe(simulado);
    expect(consultas).toHaveLength(2);
    expect(await consultas[1]?.json()).toEqual({ token: TOKEN });
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("sin red al aceptar: lo dice y no da la cuenta por asociada", async () => {
    vi.stubGlobal(
      "fetch",
      backend(() => {
        throw new TypeError("Failed to fetch");
      }),
    );
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByText(/No se pudo completar el pedido. Hay que revisar la conexión/)).toBeTruthy();
    expect(screen.queryByText(/quedó asociada/)).toBeNull();
    expect(await aceptarBoton()).toBeTruthy();
  });

  it("un 503 al aceptar deja el formulario con el nombre escrito y el error, sin ir al inicio", async () => {
    vi.stubGlobal("fetch", backend(() => error("servicio_no_disponible", 503)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.change(await screen.findByLabelText(CAMPO_NOMBRE), { target: { value: "Beto Estudio" } });
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect((screen.getByLabelText(CAMPO_NOMBRE) as HTMLInputElement).value).toBe("Beto Estudio");
    expect(((await aceptarBoton()) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("heading", { name: "Edificio Alvear 100" })).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(screen.queryByText(/quedó asociada/)).toBeNull();
    // Es un error del pedido, no de la consulta: el «Reintentar» de la consulta no aparece.
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("aceptar de nuevo después del 503 va al inicio con el aviso, y el aviso no vuelve al navegar", async () => {
    let caido = true;
    const simulado = backend(() =>
      caido ? error("servicio_no_disponible", 503) : Response.json(aceptada("2026-01-01")),
    );
    vi.stubGlobal("fetch", simulado);
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.change(await screen.findByLabelText(CAMPO_NOMBRE), { target: { value: "Beto Estudio" } });
    fireEvent.click(await aceptarBoton());
    await screen.findByText(/El servicio no está disponible/);

    caido = false;
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(await screen.findByText(AVISO)).toBeTruthy();
    const aceptaciones = aceptacionesDe(simulado);
    expect(aceptaciones).toHaveLength(2);
    expect(await aceptaciones[1]?.json()).toEqual({ token: TOKEN, nombre: "Beto Estudio" });

    // A otra pantalla: el aviso se va. Y de vuelta al inicio (pasando por /entrar) no reaparece.
    fireEvent.click(screen.getByRole("link", { name: "Instalar la aplicación en este dispositivo" }));
    expect(await screen.findByRole("heading", { name: /Instalar Consorcio en Mano/ })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Seguir sin instalar" }));
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
  });

  it("permiso insuficiente (403 csrf_invalido) al aceptar no se explica como un problema de conexión", async () => {
    vi.stubGlobal("fetch", backend(() => error("csrf_invalido", 403)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByText(/Hay que abrir de nuevo el enlace del correo/)).toBeTruthy();
    expect(screen.queryByText(/Hay que revisar la conexión/)).toBeNull();
  });
  it.each([
    [503, "servicio_no_disponible", /El servicio no está disponible/],
    [500, "error_interno", /El servicio no está disponible/],
    [429, "demasiadas_peticiones", /Se llegó al límite de pedidos/],
    [422, "peticion_invalida", /Hay que abrir de nuevo el enlace del correo/],
  ])("un %i (%s) al consultar no se explica como un problema de conexión", async (status, codigo, texto) => {
    vi.stubGlobal("fetch", vi.fn(async () => error(codigo, status)));
    abrir(`${RUTA}#token=${TOKEN}`);

    expect(await screen.findByText(texto)).toBeTruthy();
    expect(screen.queryByText(/Hay que revisar la conexión/)).toBeNull();
  });
});
