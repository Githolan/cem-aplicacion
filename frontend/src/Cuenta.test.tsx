import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { App } from "./App";
import { PREFIJO_COPIA } from "./copiaContactos";
import { LARGO_MAXIMO_CONTRASENA, LARGO_MINIMO_CONTRASENA, largoContrasena } from "./SeccionContrasena";
import { ContextoVersion } from "./version";

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
    ...extra,
  });
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /cuenta", () => {
  it("el inicio tiene el enlace a Mi cuenta", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ estado: "ok" })));
    abrir("/");
    expect(screen.getByRole("link", { name: "Mi cuenta" }).getAttribute("href")).toBe("/cuenta");
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
    const simulado = conSesion({ "POST /api/v1/auth/salir": sinCuerpo(204) });
    vi.stubGlobal("fetch", simulado);
    localStorage.setItem(`${PREFIJO_COPIA}c1`, "{}");
    abrir();
    // Control: con la sesión abierta, la copia sigue ahí.
    await screen.findByRole("button", { name: "Salir" });
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBe("{}");

    fireEvent.click(screen.getByRole("button", { name: "Salir" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
    expect(pedido(simulado, "POST", "/api/v1/auth/salir")?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("si la sesión venció entre medio, una acción lleva a /entrar", async () => {
    vi.stubGlobal("fetch", conSesion({ "POST /api/v1/auth/sesiones/revocar-otras": () => error("no_autenticado", 401) }));
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Cerrar las otras sesiones" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it("sin backend lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();

    expect(await screen.findByText(/No pudimos consultar tu cuenta/)).toBeTruthy();
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
    if (estado === "sin_servicio") await screen.findByText(/No pudimos consultar tu cuenta/);
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

  it("con segundo factor no pide la actual, avisa de entrada y explica el 403 con el enlace", async () => {
    vi.stubGlobal(
      "fetch",
      conSesion(
        { "PUT /api/v1/auth/contrasena": () => error("segundo_factor_requerido", 403) },
        TOTP_ACTIVO,
        CON_CONTRASENA,
      ),
    );
    abrir();
    await screen.findByText(/La cambiaste por última vez/);

    desplegar();
    expect(seccion().queryByLabelText("Contraseña actual")).toBeNull();
    expect(seccion().getByRole("link", { name: /verificá un código/ }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
    completar(NUEVA);

    const aviso = within(await seccion().findByText(/Para esto tenés que confirmar/));
    expect(aviso.getByRole("link", { name: /Verificá un código/ }).getAttribute("href")).toBe(
      "/cuenta/segundo-factor",
    );
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
    [() => error("servicio_no_disponible", 503), /No pudimos completar el pedido/],
  ])("muestra el rechazo del backend a la vista (%#)", async (respuesta, mensaje) => {
    vi.stubGlobal("fetch", conSesion({ "PUT /api/v1/auth/contrasena": respuesta }, TOTP_INACTIVO, CON_CONTRASENA));
    abrir();
    await screen.findByText(/La cambiaste por última vez/);
    completar(NUEVA, NUEVA, ANTERIOR);

    expect(await seccion().findByText(mensaje)).toBeTruthy();
    expect(seccion().queryByText(/Listo:/)).toBeNull();
    // Los campos quedan como estaban, para corregir sin volver a escribir todo.
    expect((seccion().getByLabelText("Contraseña nueva") as HTMLInputElement).value).toBe(NUEVA);
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
    expect((seccion().getByLabelText("Contraseña nueva") as HTMLInputElement).value).toBe("");
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
    vi.stubGlobal("fetch", conSesion({ "PUT /api/v1/auth/contrasena": () => error("no_autenticado", 401) }));
    abrir();
    await screen.findByText(/Todavía no tenés contraseña/);
    completar(NUEVA);

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it.each([
    ["sin contraseña", SIN_CONTRASENA, TOTP_INACTIVO, null],
    ["con contraseña y sin segundo factor", CON_CONTRASENA, TOTP_INACTIVO, null],
    ["con segundo factor", CON_CONTRASENA, TOTP_ACTIVO, null],
    ["con un campo mal", SIN_CONTRASENA, TOTP_INACTIVO, "corta"],
    ["guardada", SIN_CONTRASENA, TOTP_INACTIVO, "ok"],
    ["sin la verificación reciente", CON_CONTRASENA, TOTP_ACTIVO, "falta_verificacion"],
    ["con un rechazo", CON_CONTRASENA, TOTP_INACTIVO, "error"],
  ] as const)("%s no tiene faltas de accesibilidad detectables por axe", async (_, contrasena, totp, paso) => {
    const respuestas = {
      ok: () => Response.json(GUARDADA),
      falta_verificacion: () => error("segundo_factor_requerido", 403),
      error: () => error("contrasena_actual_invalida", 403),
    };
    const extra: Respuestas = paso === null || paso === "corta" ? {} : { "PUT /api/v1/auth/contrasena": respuestas[paso] };
    vi.stubGlobal("fetch", conSesion(extra, totp, contrasena));
    const { container } = abrir();
    await screen.findByRole("region", { name: "Contraseña" });
    if (paso === "corta") completar("corta");
    else if (paso !== null) {
      completar(NUEVA, NUEVA, contrasena.definida && !totp.activo ? ANTERIOR : undefined);
      await seccion().findByText(paso === "ok" ? /Listo:/ : /Para esto|actual no es correcta/);
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

    const estado = screen.getByRole("status");
    expect(estado.textContent).toBe("Consultando…");
    // Las otras tres ya respondieron: igual no se pinta nada hasta tener las cuatro.
    await waitFor(() => expect(retenido).toHaveBeenCalledTimes(4));
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
