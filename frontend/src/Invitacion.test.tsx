import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { PREFIJO_COPIA } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const TOKEN = "A".repeat(21) + "_-" + "b".repeat(20);

const RECIBIDA = {
  consorcio: "Edificio Alvear 100",
  unidad: "3° B",
  tipo: "inquilino",
  correo: "beto@ejemplo.com",
  nombre: "Beto",
  invitada_por: "Ana",
  vence_en: "2026-10-22T00:00:00Z",
};
const ACEPTADA = {
  consorcio_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  cuenta_nueva: true,
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

function abrir(url: string) {
  window.history.replaceState(null, "", url);
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // StrictMode, como en main.tsx: monta dos veces y la consulta tiene que salir una sola.
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/invitacion"]}>
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

const CONSULTAR = "/api/v1/auth/invitacion/consultar";
const ACEPTAR = "/api/v1/auth/invitacion/aceptar";
const SALIR = "/api/v1/auth/salir";
const INICIO = { level: 1, name: "Tu consorcio, en tu mano" };
const AVISO = "Listo: ya sos parte de Edificio Alvear 100.";

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("página /invitacion", () => {
  it("consulta una sola vez con el token en el cuerpo, lo borra de la URL y muestra la invitación", async () => {
    const pedido = backend(() => Response.json(ACEPTADA));
    vi.stubGlobal("fetch", pedido);

    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByRole("heading", { name: "Edificio Alvear 100" })).toBeTruthy();
    expect(screen.getByText("3° B")).toBeTruthy();
    expect(screen.getByText("inquilino/a")).toBeTruthy();
    expect(window.location.href).not.toContain(TOKEN);
    const [consulta, ...otros] = pedidosDe(pedido);
    expect(otros).toEqual([]);
    if (consulta === undefined) throw new Error("no hubo consulta");
    const url = new URL(consulta.url);
    expect([consulta.method, url.pathname, url.search]).toEqual([
      "POST",
      CONSULTAR,
      "",
    ]);
    expect(await consulta.json()).toEqual({ token: TOKEN });
  });

  it("acepta con el nombre elegido y avisa que quedó adentro", async () => {
    const pedido = backend(() => Response.json(ACEPTADA));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);

    const nombre = await screen.findByLabelText("Tu nombre");
    fireEvent.change(nombre, { target: { value: "  Alberto " } });
    fireEvent.click(screen.getByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(AVISO)).toBeTruthy();
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Ir al inicio" })).toBeNull();
    const [aceptacion, ...otras] = aceptacionesDe(pedido);
    expect(otras).toEqual([]);
    if (aceptacion === undefined) throw new Error("no hubo aceptación");
    expect(await aceptacion.json()).toEqual({ token: TOKEN, nombre: "Alberto" });
  });

  it("con sesión abierta manda el CSRF", async () => {
    // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    const pedido = backend(() => Response.json({ ...ACEPTADA, cuenta_nueva: false }));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    await screen.findByText(/ya sos parte/);
    expect(aceptacionesDe(pedido)[0]?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si el correo ya tiene cuenta, manda a entrar", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_requiere_sesion", 409)));
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/Ya tenés una cuenta con este correo/)).toBeTruthy();
    expect(screen.getByText(/la vas a ver en el inicio, lista para aceptar/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Entrá" }).getAttribute("href")).toBe("/entrar");
  });

  it("si la sesión es de otra persona, lo dice", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_de_otra_persona", 409)));
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/no es la del correo invitado/)).toBeTruthy();
  });

  it("si la creó quien la acepta, lo dice (F9)", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_propia", 409)));
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/Esta invitación la creaste vos/)).toBeTruthy();
    expect(screen.queryByText(/no es la del correo invitado/)).toBeNull();
  });

  it("una invitación que no sirve muestra un error genérico", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => error("invitacion_invalida", 404)));
    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByText(/Esta invitación no sirve/)).toBeTruthy();
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
  });

  it("sin token no pide nada", () => {
    const pedido = vi.fn();
    vi.stubGlobal("fetch", pedido);
    abrir("/invitacion");

    expect(screen.getByText(/abrí el enlace que te llegó por correo/)).toBeTruthy();
    expect(pedido).not.toHaveBeenCalled();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend(() => Response.json(ACEPTADA)));
    const { container } = abrir(`/invitacion#token=${TOKEN}`);
    await screen.findByRole("button", { name: "Aceptar la invitación" });

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
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

/** El estado de la pantalla: el primero es el de AvisoLlegada, que está vacío si no hay aviso. */
function estadoDePantalla() {
  const [aviso, estado, ...otros] = screen.getAllByRole("status");
  expect(aviso?.textContent).toBe("");
  expect(otros).toEqual([]);
  if (estado === undefined) throw new Error("la pantalla no tiene estado");
  return estado;
}

describe("página /invitacion: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta dice «Buscando la invitación…» y lo saca al llegar la respuesta", async () => {
    const { retenido, soltar } = retener(backend(() => Response.json(ACEPTADA)), "/api/v1/auth/invitacion/consultar");
    vi.stubGlobal("fetch", retenido);
    abrir(`/invitacion#token=${TOKEN}`);

    expect(estadoDePantalla().textContent).toBe("Buscando la invitación…");
    expect(screen.queryByRole("button", { name: "Aceptar la invitación" })).toBeNull();
    soltar();

    expect(await screen.findByRole("button", { name: "Aceptar la invitación" })).toBeTruthy();
    expect(screen.queryByText("Buscando la invitación…")).toBeNull();
  });

  it("carga: mientras acepta dice «Aceptando…» con el botón deshabilitado", async () => {
    const { retenido, soltar } = retener(backend(() => Response.json(ACEPTADA)), "/api/v1/auth/invitacion/aceptar");
    vi.stubGlobal("fetch", retenido);
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(estadoDePantalla().textContent).toBe("Aceptando…");
    expect((screen.getByRole("button", { name: "Aceptar la invitación" }) as HTMLButtonElement).disabled).toBe(true);
    soltar();

    expect(await screen.findByText(AVISO)).toBeTruthy();
    expect(screen.queryByText("Aceptando…")).toBeNull();
  });

  it("sin red al consultar: lo dice, no ofrece aceptar y deja reintentar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Aceptar la invitación" })).toBeNull();
    expect(within(screen.getByRole("main")).getAllByRole("button").map((b) => b.textContent)).toEqual(["Reintentar"]);
  });

  it("«Reintentar» consulta de nuevo con el mismo token y, si sale bien, muestra la invitación", async () => {
    let caido = true;
    const pedido = backend(
      () => Response.json(ACEPTADA),
      () => (caido ? error("servicio_no_disponible", 503) : Response.json(RECIBIDA)),
    );
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);
    await screen.findByText(/El servicio no está disponible/);
    expect(consultasDe(pedido)).toHaveLength(1);

    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByRole("button", { name: "Aceptar la invitación" })).toBeTruthy();
    expect(screen.queryByText(/El servicio no está disponible/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    const consultas = consultasDe(pedido);
    expect(consultas).toHaveLength(2);
    expect(await consultas[1]?.json()).toEqual({ token: TOKEN });
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("«Reintentar» que vuelve a fallar sigue en el error, con el botón", async () => {
    const pedido = backend(
      () => Response.json(ACEPTADA),
      () => error("servicio_no_disponible", 503),
    );
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }));

    await vi.waitFor(() => expect(consultasDe(pedido)).toHaveLength(2));
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Aceptar la invitación" })).toBeNull();
  });

  it("sin red al aceptar: lo dice y no da la invitación por aceptada", async () => {
    vi.stubGlobal(
      "fetch",
      backend(() => {
        throw new TypeError("Failed to fetch");
      }),
    );
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(screen.queryByText(/ya sos parte/)).toBeNull();
    expect(screen.getByRole("button", { name: "Aceptar la invitación" })).toBeTruthy();
  });

  it("un 503 al aceptar deja el formulario con el nombre escrito y el error, sin ir al inicio", async () => {
    vi.stubGlobal("fetch", backend(() => error("servicio_no_disponible", 503)));
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.change(await screen.findByLabelText("Tu nombre"), { target: { value: "Alberto" } });
    fireEvent.click(screen.getByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect((screen.getByLabelText("Tu nombre") as HTMLInputElement).value).toBe("Alberto");
    expect((screen.getByRole("button", { name: "Aceptar la invitación" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("heading", { name: "Edificio Alvear 100" })).toBeTruthy();
    expect(screen.queryByRole("heading", INICIO)).toBeNull();
    expect(screen.queryByText(/ya sos parte/)).toBeNull();
    // Es un error del pedido, no de la consulta: el «Reintentar» de la consulta no aparece.
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("aceptar de nuevo después del 503 va al inicio con el aviso, y el aviso no vuelve al navegar", async () => {
    let caido = true;
    const pedido = backend(() => (caido ? error("servicio_no_disponible", 503) : Response.json(ACEPTADA)));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.change(await screen.findByLabelText("Tu nombre"), { target: { value: "Alberto" } });
    fireEvent.click(screen.getByRole("button", { name: "Aceptar la invitación" }));
    await screen.findByText(/El servicio no está disponible/);

    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(await screen.findByText(AVISO)).toBeTruthy();
    expect(screen.queryByText(/El servicio no está disponible/)).toBeNull();
    const aceptaciones = aceptacionesDe(pedido);
    expect(aceptaciones).toHaveLength(2);
    expect(await aceptaciones[1]?.json()).toEqual({ token: TOKEN, nombre: "Alberto" });

    // A otra pantalla: el aviso se va. Y de vuelta al inicio (pasando por /entrar) no reaparece.
    fireEvent.click(screen.getByRole("link", { name: "Instalá la aplicación en este dispositivo" }));
    expect(await screen.findByRole("heading", { name: /Instalá Consorcio en Mano/ })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Seguir sin instalar" }));
    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
  });

  it("permiso insuficiente (403 csrf_invalido) al aceptar no se explica como un problema de conexión", async () => {
    vi.stubGlobal("fetch", backend(() => error("csrf_invalido", 403)));
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", { name: "Aceptar la invitación" }));

    expect(await screen.findByText(/Volvé a abrir el enlace desde el correo/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
  it.each([
    [503, "servicio_no_disponible", /El servicio no está disponible/],
    [500, "error_interno", /El servicio no está disponible/],
    [429, "demasiadas_peticiones", /Llegaste al límite de pedidos/],
    [422, "peticion_invalida", /Volvé a abrir el enlace desde el correo/],
  ])("un %i (%s) al consultar no se explica como un problema de conexión", async (status, codigo, texto) => {
    vi.stubGlobal("fetch", vi.fn(async () => error(codigo, status)));
    abrir(`/invitacion#token=${TOKEN}`);

    expect(await screen.findByText(texto)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
});

describe("ningún error de /invitacion termina sin un paso siguiente", () => {
  const ACEPTAR_LA = { name: "Aceptar la invitación" };
  const SALIR_DE_ESTA = { name: "Salir de esta cuenta" };
  const AL_INICIO = { name: "Ir al inicio" };
  const OTRA_PERSONA = /no es la del correo invitado/;
  const OTRA_CUENTA = () => error("invitacion_de_otra_persona", 409);
  const COPIA = JSON.stringify({
    consorcio_id: "c1",
    nombre: "Edificio",
    guardada_en: "2026-10-21T12:00:00Z",
    lista: { elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" },
  });

  /** Lleva al rechazo de quien abrió el enlace con otra cuenta; ya montada la página. */
  async function botonSalir() {
    fireEvent.click(await screen.findByRole("button", ACEPTAR_LA));
    return screen.findByRole("button", SALIR_DE_ESTA);
  }

  it("con otra cuenta abierta, salir vuelve a la invitación con el mismo token, sin ir al correo", async () => {
    vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
    const pedido = backend(OTRA_CUENTA);
    vi.stubGlobal("fetch", pedido);
    localStorage.setItem(`${PREFIJO_COPIA}c1`, COPIA);
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await botonSalir());

    expect(await screen.findByRole("button", ACEPTAR_LA)).toBeTruthy();
    expect(screen.queryByText(OTRA_PERSONA)).toBeNull();
    const [salida, ...otras] = pedidosDe(pedido).filter((p) => new URL(p.url).pathname === SALIR);
    expect(otras).toEqual([]);
    expect(salida?.headers.get("X-CSRF-Token")).toBe("abc123");
    const consultas = consultasDe(pedido);
    expect(consultas).toHaveLength(2);
    expect(await consultas[1]?.json()).toEqual({ token: TOKEN });
    // Como toda salida, borra las copias de los contactos (H2b_7 §5).
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
  });

  it("un 401 al salir es que ya estaba afuera: sigue igual", async () => {
    const pedido = backend(OTRA_CUENTA, undefined, () => error("no_autenticado", 401));
    vi.stubGlobal("fetch", pedido);
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await botonSalir());

    expect(await screen.findByRole("button", ACEPTAR_LA)).toBeTruthy();
    expect(consultasDe(pedido)).toHaveLength(2);
  });

  it.each<[string, Respuesta, RegExp]>([
    ["un 503", () => error("servicio_no_disponible", 503), /El servicio no está disponible/],
    ["sin red", () => Promise.reject(new TypeError("Failed to fetch")), /Revisá la conexión/],
  ])("con %s al salir lo dice, se queda ahí y deja probar de nuevo", async (_, salir, mensaje) => {
    const pedido = backend(OTRA_CUENTA, undefined, salir);
    vi.stubGlobal("fetch", pedido);
    localStorage.setItem(`${PREFIJO_COPIA}c1`, COPIA);
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await botonSalir());

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.getByText(OTRA_PERSONA)).toBeTruthy();
    const boton = screen.getByRole("button", SALIR_DE_ESTA) as HTMLButtonElement;
    await vi.waitFor(() => expect(boton.disabled).toBe(false));
    expect(consultasDe(pedido)).toHaveLength(1);
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBe(COPIA);
  });

  it.each<[string, () => Response, RegExp]>([
    ["no sirve", () => error("invitacion_invalida", 404), /Esta invitación no sirve/],
    ["la creó quien la abre", () => error("invitacion_propia", 409), /Esta invitación la creaste vos/],
  ])("si %s, lleva al inicio", async (_, rechazo, texto) => {
    vi.stubGlobal("fetch", backend(rechazo));
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", ACEPTAR_LA));
    expect(await screen.findByText(texto)).toBeTruthy();

    fireEvent.click(screen.getByRole("link", AL_INICIO));

    expect(await screen.findByRole("heading", INICIO)).toBeTruthy();
  });

  it("sin enlace también lleva al inicio", () => {
    vi.stubGlobal("fetch", vi.fn());
    abrir("/invitacion");

    expect(screen.getByRole("link", AL_INICIO).getAttribute("href")).toBe("/");
  });

  it("control: sin sesión, el mismo enlace termina en /entrar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (pedido: Request) =>
        new URL(pedido.url).pathname === "/api/v1/auth/sesion"
          ? error("no_autenticado", 401)
          : error("invitacion_invalida", 404),
      ),
    );
    abrir(`/invitacion#token=${TOKEN}`);

    fireEvent.click(await screen.findByRole("link", AL_INICIO));

    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("control: quien tiene que entrar no ve otra salida que la confunda", async () => {
    vi.stubGlobal("fetch", backend(() => error("invitacion_requiere_sesion", 409)));
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", ACEPTAR_LA));

    expect(await screen.findByRole("link", { name: "Entrá" })).toBeTruthy();
    expect(screen.queryByRole("link", AL_INICIO)).toBeNull();
    expect(screen.queryByRole("button", SALIR_DE_ESTA)).toBeNull();
  });

  it("un 409 sin pantalla propia muestra lo que dice el servidor, no un error de conexión", async () => {
    const mensaje = "El vínculo de esta invitación ya no está en el padrón.";
    vi.stubGlobal(
      "fetch",
      backend(() =>
        Response.json({ codigo: "invitacion_vinculo_no_corresponde", mensaje, id_correlacion: "c" }, { status: 409 }),
      ),
    );
    abrir(`/invitacion#token=${TOKEN}`);
    fireEvent.click(await screen.findByRole("button", ACEPTAR_LA));

    expect(await screen.findByText(mensaje)).toBeTruthy();
    expect(screen.getByRole("link", AL_INICIO)).toBeTruthy();
    expect(screen.queryByRole("button", ACEPTAR_LA)).toBeNull();
    expect(screen.queryByText(OTRA_PERSONA)).toBeNull();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });

  it("con otra cuenta no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend(OTRA_CUENTA));
    const { container } = abrir(`/invitacion#token=${TOKEN}`);
    await botonSalir();

    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("encabezadoCsrf", () => {
  it("lee la cookie CSRF entre otras y no devuelve nada sin ella", () => {
    expect(encabezadoCsrf("a=1; __Host-cem_csrf=x=y; b=2")).toEqual({ "X-CSRF-Token": "x=y" });
    expect(encabezadoCsrf("a=1")).toEqual({});
    expect(encabezadoCsrf("")).toEqual({});
  });
});
