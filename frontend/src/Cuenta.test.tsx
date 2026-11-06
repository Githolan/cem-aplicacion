import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { App } from "./App";
import { PREFIJO_COPIA } from "./copiaContactos";
import { ENTRAR_DE_NUEVO, RECARGAR, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { LARGO_MAXIMO_CONTRASENA, LARGO_MINIMO_CONTRASENA, largoContrasena } from "./SeccionContrasena";
import { ContextoVersion } from "./version";

/** Una copia de contactos válida, para probar el borrado real y no el descarte de una dañada. */
const COPIA_VALIDA = JSON.stringify({
  consorcio_id: "c1",
  nombre: "Edificio",
  guardada_en: "2026-10-21T12:00:00Z",
  lista: { elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" },
});

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const ACTUAL = "11111111-1111-4111-8111-111111111111";
const OTRA = "22222222-2222-4222-8222-222222222222";
const TERCERA = "33333333-3333-4333-8333-333333333333";
const YO = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: "ana@ejemplo.com",
  sesion_id: ACTUAL,
  vence_en: "2026-11-15T20:00:00Z",
};

function sesion(id: string, dispositivo: string, actual: boolean) {
  return {
    id,
    dispositivo,
    actual,
    creada_en: "2026-10-01T12:00:00Z",
    ultimo_uso_en: "2026-10-16T12:00:00Z",
    vence_en: "2026-11-15T12:00:00Z",
  };
}

const SESIONES = [
  sesion(ACTUAL, "Firefox en Windows", true),
  sesion(OTRA, "Chrome en Android", false),
  sesion(TERCERA, "Safari en iPhone", false),
];
const TOTP_INACTIVO = { activo: false, activado_en: null, codigos_recuperacion_restantes: 0, verificado_en: null };
const TOTP_ACTIVO = {
  activo: true,
  activado_en: "2026-10-15T20:00:00Z",
  codigos_recuperacion_restantes: 10,
  verificado_en: null,
};

const SIN_CONTRASENA = { definida: false, cambiada_en: null };
const CON_CONTRASENA = { definida: true, cambiada_en: "2026-10-10T15:30:00Z" };

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

const sinCuerpo = (status: number) => () => new Response(null, { status });

type Respuestas = Record<string, () => Response>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

/** El backend de una cuenta con sesión: quién soy, las sesiones y el segundo factor. */
function conSesion(extra: Respuestas = {}, totp: object = TOTP_INACTIVO, contrasena: object = SIN_CONTRASENA) {
  return backend({
    "GET /api/v1/auth/sesion": () => Response.json(YO),
    "GET /api/v1/auth/sesiones": () => Response.json({ elementos: SESIONES }),
    "GET /api/v1/auth/totp": () => Response.json(totp),
    "GET /api/v1/auth/contrasena": () => Response.json(contrasena),
    "GET /api/v1/privacidad/casos": () => Response.json({ elementos: [] }),
    ...extra,
  });
}

/**
 * Como el backend: la acción cierra la sesión y `/auth/sesion` pasa a dar 401. Con un 200 fijo,
 * /entrar devolvería a la persona al inicio.
 */
function sesionQueSeCierra(accion: string, respuesta: () => Response): Respuestas {
  let cerrada = false;
  return {
    "GET /api/v1/auth/sesion": () => (cerrada ? error("no_autenticado", 401) : Response.json(YO)),
    [accion]: () => {
      cerrada = true;
      return respuesta();
    },
  };
}

/** Llegó a /entrar y se queda: la consulta de la sesión de /entrar no la manda a otro lado. */
async function quedoEnEntrar() {
  expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  await new Promise((listo) => setTimeout(listo, 0));
  expect(screen.getByRole("heading", ENTRAR)).toBeTruthy();
}

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

function pedido(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request | undefined {
  return pedidosDe(simulado).find((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/cuenta") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[ruta]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

/** El botón «Cerrar esta sesión» de un dispositivo: lo distingue su descripción. */
function botonCerrar(dispositivo: string) {
  return screen.findByRole("button", { name: "Cerrar esta sesión", description: dispositivo });
}

const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };

beforeEach(() => {
  // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

/** El estado de la pantalla: el primero es el de AvisoLlegada, que está vacío si no hay aviso. */
function estadoDePantalla() {
  const [aviso, estado, ...otros] = screen.getAllByRole("status");
  expect(aviso?.textContent).toBe("");
  expect(otros).toEqual([]);
  if (estado === undefined) throw new Error("la pantalla no tiene estado");
  return estado;
}

/** Lo que consulta el inicio con la sesión abierta; el estado del servicio no importa acá. */
const INICIO_CON_SESION = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () => error("servicio_no_disponible", 503),
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("página /cuenta", () => {
  it("el inicio tiene el enlace a Mi cuenta", async () => {
    vi.stubGlobal("fetch", backend(INICIO_CON_SESION));
    abrir("/");
    expect((await screen.findByRole("link", { name: "Mi cuenta" })).getAttribute("href")).toBe("/cuenta");
  });

  it("la versión va al pie, y sin saberla no se muestra nada", async () => {
    const salud = { estado: "ok", version: "1.2.3", version_contrato: "0.50.0", sha_imagen: "0849ee9abcdef" };
    vi.stubGlobal("fetch", conSesion({ "GET /health": () => Response.json(salud) }));
    abrir();
    expect(await screen.findByText("0849ee9")).toBeTruthy();
    expect(screen.getByText(/Versión 1\.2\.3 · Contrato 0\.50\.0/)).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", conSesion({ "GET /health": () => error("servicio_no_disponible", 503) }));
    abrir();
    await screen.findByText("Ana Pérez");
    expect(screen.queryByText(/Versión/)).toBeNull();
  });

  it("sin sesión (401) manda a /entrar", async () => {
    const simulado = backend({
      "GET /api/v1/auth/sesion": () => error("no_autenticado", 401),
      "GET /api/v1/auth/sesiones": () => error("no_autenticado", 401),
      "GET /api/v1/auth/totp": () => error("no_autenticado", 401),
      "GET /api/v1/auth/contrasena": () => error("no_autenticado", 401),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Mi cuenta" })).toBeNull();
  });

  it("muestra quién soy, las sesiones con la actual marcada y el segundo factor inactivo", async () => {
    vi.stubGlobal("fetch", conSesion());
    abrir();

    expect(await screen.findByText("Ana Pérez")).toBeTruthy();
    expect(screen.getByText("ana@ejemplo.com")).toBeTruthy();
    const lista = screen.getByRole("heading", { name: "Dónde tenés la sesión abierta" }).parentElement!;
    const items = within(lista).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    const [actual, otra] = items as [HTMLElement, HTMLElement, HTMLElement];
    expect(actual.getAttribute("aria-current")).toBe("true");
    expect(actual.textContent).toContain("Firefox en Windows (este dispositivo)");
    expect(otra.getAttribute("aria-current")).toBeNull();
    expect(within(lista).getAllByRole("button", { name: "Cerrar esta sesión" })).toHaveLength(3);
    // Cada botón dice de qué sesión es a quien usa un lector de pantalla.
    expect(
      within(otra).getByRole("button", { name: "Cerrar esta sesión", description: "Chrome en Android" }),
    ).toBeTruthy();
    expect(screen.getByText("No está activado.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Activar el segundo factor" }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
  });

  it("con el segundo factor activo lo dice", async () => {
    vi.stubGlobal("fetch", conSesion({}, TOTP_ACTIVO));
    abrir();

    expect(await screen.findByText(/Está activado/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ver el segundo factor" }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
  });

  it("quien solo tiene llave también lo ve activado", async () => {
    vi.stubGlobal("fetch", conSesion({}, { ...TOTP_INACTIVO, llaves: 1 }));
    abrir();

    expect(await screen.findByText(/Está activado/)).toBeTruthy();
    expect(screen.queryByText("No está activado.")).toBeNull();
  });

  it("cierra otra sesión con CSRF y la saca de la lista", async () => {
    const simulado = conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await botonCerrar("Chrome en Android"));

    expect(await screen.findByText("Listo: cerramos la sesión de Chrome en Android.")).toBeTruthy();
    expect(screen.queryByText("Chrome en Android")).toBeNull();
    expect(screen.getByRole("heading", { name: "Mi cuenta" })).toBeTruthy();
    const revocar = pedido(simulado, "POST", `/api/v1/auth/sesiones/${OTRA}/revocar`);
    expect(revocar?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("el resultado de cerrar una sesión queda en esa tarjeta y lleva el foco: la fila ya no está", async () => {
    vi.stubGlobal("fetch", conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: sinCuerpo(204) }));
    abrir();

    fireEvent.click(await botonCerrar("Chrome en Android"));

    const aviso = await screen.findByText("Listo: cerramos la sesión de Chrome en Android.");
    expect(screen.getByRole("region", { name: "Dónde tenés la sesión abierta" }).contains(aviso)).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
  });

  it.each([
    [`POST /api/v1/auth/sesiones/${OTRA}/revocar`, 503],
    [`POST /api/v1/auth/sesiones/${OTRA}/revocar`, 500],
    ["POST /api/v1/auth/sesiones/revocar-otras", 503],
    ["POST /api/v1/auth/sesiones/revocar-otras", 500],
    ["POST /api/v1/auth/salir", 503],
    ["POST /api/v1/auth/salir", 500],
  ])("un %s con %i habla del servicio, no de la conexión, y no cambia de pantalla", async (ruta, status) => {
    vi.stubGlobal("fetch", conSesion({ [ruta]: () => error("error_interno", status) }));
    abrir();
    await screen.findByText("Ana Pérez");
    if (ruta.endsWith("revocar")) fireEvent.click(await botonCerrar("Chrome en Android"));
    else if (ruta.endsWith("salir")) fireEvent.click(screen.getByRole("button", { name: "Salir" }));
    else fireEvent.click(screen.getByRole("button", { name: "Cerrar las otras sesiones" }));

    const aviso = await screen.findByText(SERVICIO_NO_DISPONIBLE);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
    expect(screen.getByRole("heading", { name: "Mi cuenta" })).toBeTruthy();
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
  });

  it("cerrar la sesión actual lleva a /entrar", async () => {
    const simulado = conSesion({ [`POST /api/v1/auth/sesiones/${ACTUAL}/revocar`]: sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(
      await botonCerrar("Firefox en Windows (este dispositivo)"),
    );

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    const revocar = pedido(simulado, "POST", `/api/v1/auth/sesiones/${ACTUAL}/revocar`);
    expect(revocar?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si el backend rechaza el cierre (CSRF), lo dice y no saca la sesión", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: () => error("csrf_invalido", 403) }),
    );
    abrir();

    fireEvent.click(await botonCerrar("Chrome en Android"));

    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
  });

  it("cierra las otras sesiones con CSRF y deja solo la actual", async () => {
    const simulado = conSesion({
      "POST /api/v1/auth/sesiones/revocar-otras": () => Response.json({ revocadas: 2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Cerrar las otras sesiones" }));

    expect(await screen.findByText("Listo: cerramos 2 sesiones en otros dispositivos.")).toBeTruthy();
    const lista = screen.getByRole("heading", { name: "Dónde tenés la sesión abierta" }).parentElement!;
    expect(within(lista).getAllByRole("listitem")).toHaveLength(1);
    // Sin otras sesiones, el botón ya no tiene sentido.
    expect(screen.queryByRole("button", { name: "Cerrar las otras sesiones" })).toBeNull();
    const pedidoOtras = pedido(simulado, "POST", "/api/v1/auth/sesiones/revocar-otras");
    expect(pedidoOtras?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("salir con CSRF lleva a /entrar y borra las copias de los contactos", async () => {
    const simulado = conSesion(sesionQueSeCierra("POST /api/v1/auth/salir", sinCuerpo(204)));
    vi.stubGlobal("fetch", simulado);
    localStorage.setItem(`${PREFIJO_COPIA}c1`, COPIA_VALIDA);
    abrir();
    // Control: con la sesión abierta, la copia sigue ahí.
    await screen.findByRole("button", { name: "Salir" });
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBe(COPIA_VALIDA);

    fireEvent.click(screen.getByRole("button", { name: "Salir" }));

    await quedoEnEntrar();
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
    expect(pedido(simulado, "POST", "/api/v1/auth/salir")?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("salir con la sesión ya vencida (401) también borra las copias", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion(sesionQueSeCierra("POST /api/v1/auth/salir", () => error("no_autenticado", 401))),
    );
    localStorage.setItem(`${PREFIJO_COPIA}c1`, COPIA_VALIDA);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Salir" }));
    await quedoEnEntrar();
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
  });

  it("si la sesión venció entre medio, una acción lleva a /entrar", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion(sesionQueSeCierra("POST /api/v1/auth/sesiones/revocar-otras", () => error("no_autenticado", 401))),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Cerrar las otras sesiones" }));

    await quedoEnEntrar();
  });

  it("sin backend lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();

    expect(await screen.findByText(/Revisá la conexión/)).toBeTruthy();
  });

  it("sin nada de la cuenta, un solo aviso; Reintentar la vuelve a leer entera", async () => {
    const simulado = conSesion();
    let caida = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (p: Request) => (caida ? Promise.reject(new TypeError("sin red")) : simulado(p))),
    );
    abrir();
    const boton = await screen.findByRole("button", { name: "Reintentar" });
    expect(screen.queryByRole("alert")).toBeNull();

    caida = false;
    fireEvent.click(boton);

    expect(await screen.findByText("Ana Pérez")).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it.each([
    ["GET /api/v1/auth/sesiones", "Dónde tenés la sesión abierta", "Ana Pérez", "Chrome en Android"],
    ["GET /api/v1/auth/sesion", "Tus datos", "Chrome en Android", "Ana Pérez"],
    ["GET /api/v1/auth/totp", "Segundo factor", "Ana Pérez", "No está activado."],
    ["GET /api/v1/auth/contrasena", "Contraseña", "Ana Pérez", "Definir una contraseña"],
  ])("si falla %s, «%s» lo dice y deja reintentar sin perder el resto", async (ruta, parte, sigue, recupera) => {
    let caida = true;
    const respuestas = conSesion();
    const simulado = vi.fn(async (p: Request) =>
      caida && `${p.method} ${new URL(p.url).pathname}` === ruta ? error("error_interno", 500) : respuestas(p),
    );
    vi.stubGlobal("fetch", simulado);
    abrir();

    expect(await screen.findByText(sigue)).toBeTruthy();
    const region = within(screen.getByRole("region", { name: parte }));
    expect(region.getByRole("alert").textContent).toBe(SERVICIO_NO_DISPONIBLE);
    expect(screen.queryByText(recupera)).toBeNull();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
    // Salir tiene que seguir a mano aunque falte una parte.
    expect(screen.getByRole("button", { name: "Salir" })).toBeTruthy();
    const pedidosAntes = simulado.mock.calls.length;

    caida = false;
    fireEvent.click(region.getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByText(recupera)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    // Reintentar pide esa parte: las que estaban bien no se vuelven a leer.
    const nuevas = pedidosDe(simulado)
      .slice(pedidosAntes)
      .map((p) => `${p.method} ${new URL(p.url).pathname}`);
    expect(nuevas.filter((r) => r === ruta)).toHaveLength(1);
    expect(nuevas.filter((r) => r !== ruta && r !== "GET /api/v1/privacidad/casos")).toEqual([]);
  });

  it("sin red en una parte también queda en esa parte y un reintento fallido deja seguir probando", async () => {
    let intentos = 0;
    const respuestas = conSesion();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (p: Request) => {
        if (new URL(p.url).pathname !== "/api/v1/auth/sesiones") return respuestas(p);
        intentos += 1;
        return intentos < 3 ? Promise.reject(new TypeError("sin red")) : respuestas(p);
      }),
    );
    abrir();
    expect(await screen.findByText("Ana Pérez")).toBeTruthy();
    const region = within(screen.getByRole("region", { name: "Dónde tenés la sesión abierta" }));
    expect(region.getByRole("alert").textContent).toBe(SIN_CONEXION);

    fireEvent.click(region.getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(intentos).toBe(2));
    await waitFor(() =>
      expect((region.getByRole("button", { name: "Reintentar" }) as HTMLButtonElement).disabled).toBe(false),
    );
    expect(screen.getByText("Ana Pérez")).toBeTruthy();

    fireEvent.click(region.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Chrome en Android")).toBeTruthy();
  });

  it("control: un 401 en una sola lectura no es una falla de esa parte, lleva a /entrar", async () => {
    vi.stubGlobal("fetch", conSesion({ "GET /api/v1/auth/totp": () => error("no_autenticado", 401) }));
    abrir();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it.each([
    ["la cuenta con sesiones", "lista"],
    ["un cierre hecho", "aviso"],
    ["sin servicio", "sin_servicio"],
  ])("en %s no tiene faltas de accesibilidad detectables por axe", async (_, estado) => {
    vi.stubGlobal(
      "fetch",
      estado === "sin_servicio"
        ? backend({ "GET /api/v1/auth/sesion": () => error("interno", 500) })
        : conSesion({ [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: sinCuerpo(204) }),
    );
    const { container } = abrir();
    if (estado === "sin_servicio") await screen.findByText(/El servicio no está disponible/);
    else await screen.findByText("Ana Pérez");
    if (estado === "aviso") {
      fireEvent.click(await botonCerrar("Chrome en Android"));
      await screen.findByText(/Listo: cerramos/);
    }

    // jsdom no calcula colores: el contraste lo mide axe en el navegador (e2e/cuenta.e2e.ts).
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("contraseña en /cuenta", () => {
  const seccion = () => within(screen.getByRole("region", { name: "Contraseña" }));

  /** El formulario empieza plegado: lo abre con su botón si todavía está cerrado. */
  function desplegar() {
    const boton = seccion().queryByRole("button", { name: /^(Cambiar la contraseña|Definir una contraseña)$/ });
    if (boton !== null) fireEvent.click(boton);
  }

  /** Abre el formulario si hace falta, lo completa y lo manda; `actual` solo si el campo está. */
  function completar(nueva: string, repetida = nueva, actual?: string) {
    desplegar();
    if (actual !== undefined)
      fireEvent.change(seccion().getByLabelText("Contraseña actual"), { target: { value: actual } });
    fireEvent.change(seccion().getByLabelText(/^Contraseña( nueva)?$/), { target: { value: nueva } });
    fireEvent.change(seccion().getByLabelText("Repetí la contraseña"), { target: { value: repetida } });
    const formulario = seccion()
      .getByRole("button", { name: /^Guardar la contraseña( nueva)?$/ })
      .closest("form");
    if (formulario === null) throw new Error("el botón no está en un formulario");
    fireEvent.submit(formulario);
  }

  const NUEVA = "una frase larga de verdad";
  const ANTERIOR = "la de antes, larga";
  const GUARDADA = { cambiada_en: "2026-10-16T18:00:00Z", sesiones_cerradas: 0 };

  it("el largo del formulario es el del contrato", () => {
    const esquema = contrato.components.schemas.NuevaContrasena.properties;
    expect(LARGO_MINIMO_CONTRASENA).toBe(esquema.contrasena_nueva.minLength);
    expect(LARGO_MAXIMO_CONTRASENA).toBe(esquema.contrasena_nueva.maxLength);
  });

  it("sin contraseña la define con PUT, X-CSRF-Token y sin la actual, y la muestra definida", async () => {
    const simulado = conSesion({ "PUT /api/v1/auth/contrasena": () => Response.json(GUARDADA) });
    vi.stubGlobal("fetch", simulado);
    abrir();

    expect(await screen.findByText(/Todavía no tenés contraseña/)).toBeTruthy();
    const definir = seccion().getByRole("button", { name: "Definir una contraseña" });
    expect(definir.getAttribute("aria-expanded")).toBe("false");
    expect(seccion().queryByLabelText("Contraseña")).toBeNull();
    desplegar();
    expect(seccion().queryByLabelText("Contraseña actual")).toBeNull();
    const campo = seccion().getByLabelText("Contraseña");
    expect(document.activeElement).toBe(campo);
    expect(campo.getAttribute("autocomplete")).toBe("new-password");
    expect(campo.getAttribute("type")).toBe("password");
    expect(seccion().getByLabelText("Repetí la contraseña").getAttribute("autocomplete")).toBe("new-password");
    completar(NUEVA);

    expect(await screen.findByText("Listo: definiste tu contraseña. Te avisamos por correo.")).toBeTruthy();
    expect(screen.getByText(/Tenés una contraseña. La cambiaste por última vez el 16 de octubre de 2026/)).toBeTruthy();
    const put = pedido(simulado, "PUT", "/api/v1/auth/contrasena");
    if (put === undefined) throw new Error("no hubo PUT");
    expect(put.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await put.json()).toEqual({ contrasena_nueva: NUEVA, contrasena_actual: null });
    // Guardada, el formulario se pliega y el foco vuelve al botón, que ahora es el de cambiarla.
    expect(seccion().queryByLabelText("Contraseña nueva")).toBeNull();
    expect(document.activeElement).toBe(seccion().getByRole("button", { name: "Cambiar la contraseña" }));
  });

  it("la primera contraseña con una sesión vieja (403 reautenticacion_requerida) no pisa lo escrito: lo pide la hoja", async () => {
    const simulado = conSesion({ "PUT /api/v1/auth/contrasena": () => error("reautenticacion_requerida", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);
    desplegar();
    completar(NUEVA);

    await waitFor(() => expect(seccion().getByRole("button", { name: /^Guardar la contraseña/ })).toHaveProperty("disabled", false));
    expect(seccion().queryByText(ENTRAR_DE_NUEVO)).toBeNull();
    expect(seccion().queryByText(/Revisá la conexión/)).toBeNull();
    expect(seccion().queryByText(/Listo:/)).toBeNull();
    expect((seccion().getByLabelText(/^Contraseña( nueva)?$/) as HTMLInputElement).value).toBe(NUEVA);
  });

  it("control: otro rechazo no ofrece volver a entrar", async () => {
    vi.stubGlobal("fetch", conSesion({ "PUT /api/v1/auth/contrasena": () => error("csrf_invalido", 403) }));
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);
    completar(NUEVA);

    expect(await seccion().findByText(/No pudimos verificar el pedido/)).toBeTruthy();
    expect(seccion().queryByRole("link", { name: "Volver a entrar" })).toBeNull();
  });

  it("sin segundo factor, cambiarla pide la actual; cierra las otras sesiones y las saca de la lista", async () => {
    const simulado = conSesion(
      { "PUT /api/v1/auth/contrasena": () => Response.json({ ...GUARDADA, sesiones_cerradas: 2 }) },
      TOTP_INACTIVO,
      CON_CONTRASENA,
    );
    vi.stubGlobal("fetch", simulado);
    abrir();

    expect(await screen.findByText(/La cambiaste por última vez el 10 de octubre de 2026/)).toBeTruthy();
    // Plegado: ni la actual a la vista (que el navegador autocompletaría) ni el aviso de sesiones.
    expect(seccion().queryByLabelText("Contraseña actual")).toBeNull();
    expect(seccion().queryByText(/cerramos las sesiones de tus otros dispositivos/)).toBeNull();
    desplegar();
    const actual = seccion().getByLabelText("Contraseña actual");
    expect(actual.getAttribute("autocomplete")).toBe("current-password");
    expect(document.activeElement).toBe(actual);
    expect(seccion().getByText(/cerramos las sesiones de tus otros dispositivos/)).toBeTruthy();
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
    completar(NUEVA, NUEVA, ANTERIOR);

    expect(
      await screen.findByText(
        "Listo: cambiaste tu contraseña. Cerramos 2 sesiones en otros dispositivos. Te avisamos por correo.",
      ),
    ).toBeTruthy();
    const put = pedido(simulado, "PUT", "/api/v1/auth/contrasena");
    expect(put?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await put?.json()).toEqual({ contrasena_nueva: NUEVA, contrasena_actual: ANTERIOR });
    expect(screen.queryByText("Chrome en Android")).toBeNull();
    expect(screen.queryByText("Safari en iPhone")).toBeNull();
    expect(screen.getByText("Firefox en Windows")).toBeTruthy();
  });

  it("una corta o dos distintas no salen y se explican junto al campo", async () => {
    const simulado = conSesion();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);

    completar("corta 11 ch");
    const explicacion = seccion().getByText(/tiene que tener al menos 12/);
    const campo = seccion().getByLabelText("Contraseña");
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toContain(explicacion.id);

    completar(NUEVA, `${NUEVA}!`);
    expect(seccion().getByText("Las dos contraseñas no coinciden.")).toBeTruthy();
    expect(seccion().getByLabelText("Repetí la contraseña").getAttribute("aria-invalid")).toBe("true");
    expect(pedido(simulado, "PUT", "/api/v1/auth/contrasena")).toBeUndefined();
  });

  const VERIFICADO = {
    verificado_en: "2026-10-16T18:00:00Z",
    vale_hasta: "2026-10-16T18:10:00Z",
    codigos_recuperacion_restantes: 9,
  };

  it("con segundo factor no pide la actual, no anticipa lo que va a pedir y no manda a otra página", async () => {
    vi.stubGlobal("fetch", conSesion({}, TOTP_ACTIVO, CON_CONTRASENA));
    abrir();
    await screen.findByText(/La cambiaste por última vez/);

    desplegar();
    expect(seccion().queryByLabelText("Contraseña actual")).toBeNull();
    expect(seccion().queryByText(/te vamos a pedir/)).toBeNull();
    expect(seccion().queryByRole("link")).toBeNull();
    // Control: el formulario está abierto.
    expect(seccion().getByLabelText("Contraseña nueva")).toBeTruthy();
  });

  it("sin la verificación reciente (403) pide el código acá mismo y guarda lo que estaba escrito", async () => {
    let verificado = false;
    const simulado = conSesion(
      {
        "POST /api/v1/auth/totp/verificar": () => {
          verificado = true;
          return Response.json(VERIFICADO);
        },
        "PUT /api/v1/auth/contrasena": () =>
          verificado ? Response.json(GUARDADA) : error("segundo_factor_requerido", 403),
      },
      TOTP_ACTIVO,
      CON_CONTRASENA,
    );
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/La cambiaste por última vez/);
    completar(NUEVA);

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "042917" } });

    expect(await screen.findByText("Listo: cambiaste tu contraseña. Te avisamos por correo.")).toBeTruthy();
    const puts = pedidosDe(simulado).filter((p) => p.method === "PUT");
    expect(puts).toHaveLength(2);
    expect(await puts[1]?.json()).toEqual({ contrasena_nueva: NUEVA, contrasena_actual: null });
    expect(screen.getByRole("heading", { name: "Mi cuenta" })).toBeTruthy();
  });

  it("si cancela el código vuelve al formulario con lo escrito y no guarda; un código malo se queda ahí", async () => {
    const simulado = conSesion(
      {
        "POST /api/v1/auth/totp/verificar": () => error("segundo_factor_invalido", 400),
        "PUT /api/v1/auth/contrasena": () => error("segundo_factor_requerido", 403),
      },
      TOTP_ACTIVO,
      CON_CONTRASENA,
    );
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/La cambiaste por última vez/);
    completar(NUEVA);

    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "042917" } });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(pedidosDe(simulado).filter((p) => p.method === "PUT")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect((seccion().getByLabelText(/^Contraseña( nueva)?$/) as HTMLInputElement).value).toBe(NUEVA);
    expect((seccion().getByLabelText("Repetí la contraseña") as HTMLInputElement).value).toBe(NUEVA);
    expect(pedidosDe(simulado).filter((p) => p.method === "PUT")).toHaveLength(1);
  });

  it.each([
    [() => error("contrasena_actual_invalida", 403), "La contraseña actual no es correcta."],
    [
      () =>
        Response.json(
          { codigo: "contrasena_debil", mensaje: "No puede ser tu correo (mensaje del backend).", id_correlacion: "c" },
          { status: 422 },
        ),
      "No puede ser tu correo (mensaje del backend).",
    ],
    [() => error("csrf_invalido", 403), /No pudimos verificar el pedido. Recargá la página/],
    [() => error("demasiadas_peticiones", 429), /Hubo demasiados intentos/],
    [() => error("servicio_no_disponible", 503), SERVICIO_NO_DISPONIBLE],
    [() => error("error_interno", 500), SERVICIO_NO_DISPONIBLE],
    [() => error("no_permitido", 403), RECARGAR],
  ])("muestra el rechazo del backend a la vista (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal("fetch", conSesion({ "PUT /api/v1/auth/contrasena": respuesta }, TOTP_INACTIVO, CON_CONTRASENA));
    abrir();
    await screen.findByText(/La cambiaste por última vez/);
    completar(NUEVA, NUEVA, ANTERIOR);

    expect(await seccion().findByText(mensaje)).toBeTruthy();
    expect(seccion().queryByText(/Listo:/)).toBeNull();
    // Los campos quedan como estaban, para corregir sin volver a escribir todo.
    expect((seccion().getByLabelText(/^Contraseña( nueva)?$/) as HTMLInputElement).value).toBe(NUEVA);
  });

  it("Cancelar pliega el formulario, borra lo escrito y devuelve el foco al botón", async () => {
    const simulado = conSesion({}, TOTP_INACTIVO, CON_CONTRASENA);
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(/La cambiaste por última vez/);

    desplegar();
    fireEvent.change(seccion().getByLabelText("Contraseña nueva"), { target: { value: NUEVA } });
    fireEvent.click(seccion().getByRole("button", { name: "Cancelar" }));

    expect(seccion().queryByLabelText("Contraseña nueva")).toBeNull();
    const cambiar = seccion().getByRole("button", { name: "Cambiar la contraseña" });
    expect(document.activeElement).toBe(cambiar);
    desplegar();
    expect((seccion().getByLabelText(/^Contraseña( nueva)?$/) as HTMLInputElement).value).toBe("");
    expect(pedido(simulado, "PUT", "/api/v1/auth/contrasena")).toBeUndefined();
  });

  it("sin red al guardar, lo dice", async () => {
    const simulado = conSesion();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (p: Request) => (p.method === "PUT" ? Promise.reject(new TypeError("sin red")) : simulado(p))),
    );
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);
    completar(NUEVA);

    expect(await seccion().findByText(/No pudimos completar el pedido/)).toBeTruthy();
  });

  it("si la sesión venció al guardar (401), manda a /entrar", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion(sesionQueSeCierra("PUT /api/v1/auth/contrasena", () => error("no_autenticado", 401))),
    );
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);
    completar(NUEVA);

    await quedoEnEntrar();
  });

  it.each([
    ["sin contraseña", SIN_CONTRASENA, TOTP_INACTIVO, null],
    ["con contraseña y sin segundo factor", CON_CONTRASENA, TOTP_INACTIVO, null],
    ["con segundo factor", CON_CONTRASENA, TOTP_ACTIVO, null],
    ["con un campo mal", SIN_CONTRASENA, TOTP_INACTIVO, "corta"],
    ["guardada", SIN_CONTRASENA, TOTP_INACTIVO, "ok"],
    ["sin la verificación reciente", CON_CONTRASENA, TOTP_ACTIVO, "falta_verificacion"],
    ["con una sesión vieja", SIN_CONTRASENA, TOTP_INACTIVO, "entrar_de_nuevo"],
    ["con un rechazo", CON_CONTRASENA, TOTP_INACTIVO, "error"],
  ] as const)("%s no tiene faltas de accesibilidad detectables por axe", async (_, contrasena, totp, paso) => {
    const respuestas = {
      ok: () => Response.json(GUARDADA),
      falta_verificacion: () => error("segundo_factor_requerido", 403),
      entrar_de_nuevo: () => error("reautenticacion_requerida", 403),
      error: () => error("contrasena_actual_invalida", 403),
    };
    const extra: Respuestas = paso === null || paso === "corta" ? {} : { "PUT /api/v1/auth/contrasena": respuestas[paso] };
    vi.stubGlobal("fetch", conSesion(extra, totp, contrasena));
    const { container } = abrir();
    await screen.findByRole("region", { name: "Contraseña" });
    if (paso === "corta") completar("corta");
    else if (paso !== null) {
      completar(NUEVA, NUEVA, contrasena.definida && !totp.activo ? ANTERIOR : undefined);
      if (paso === "falta_verificacion") await screen.findByRole("dialog");
      else if (paso !== "entrar_de_nuevo") await seccion().findByText(paso === "ok" ? /Listo:/ : /actual no es correcta/);
    }

    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("largoContrasena", () => {
  it("cuenta puntos de código y vale el menor entre el texto y su NFKC", () => {
    expect(largoContrasena("año")).toBe(3);
    expect(largoContrasena("😀😀")).toBe(2);
    // «e» + tilde combinante: NFKC la compone en «é».
    expect(largoContrasena("é")).toBe(1);
    // La ligadura «ﬁ» es un carácter; su NFKC son dos: el contrato mira el texto tal cual.
    expect(largoContrasena("ﬁ")).toBe(1);
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

describe("página /cuenta: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras falta una de las cuatro consultas dice «Consultando…» y no muestra datos a medias", async () => {
    const { retenido, soltar } = retener(conSesion(), "/api/v1/auth/contrasena");
    vi.stubGlobal("fetch", retenido);
    abrir();

    const estado = estadoDePantalla();
    expect(estado.textContent).toBe("Consultando…");
    // Las otras tres ya respondieron: igual no se pinta nada hasta tener las cuatro.
    // Las cuatro, la versión y lo que pide la navegación.
    await waitFor(() => expect(retenido.mock.calls.length).toBeGreaterThanOrEqual(5));
    expect(screen.queryByText("Ana Pérez")).toBeNull();
    soltar();

    expect(await screen.findByText("Ana Pérez")).toBeTruthy();
    expect(screen.queryByText("Consultando…")).toBeNull();
  });

  it("sin red al cerrar otra sesión: lo dice y la sesión sigue en la lista", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion({
        [`POST /api/v1/auth/sesiones/${OTRA}/revocar`]: () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    abrir();
    fireEvent.click(await botonCerrar("Chrome en Android"));

    expect(await screen.findByText("No pudimos completar el pedido. Revisá la conexión y probá de nuevo.")).toBeTruthy();
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
  });
});
