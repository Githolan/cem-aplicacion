import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { trazoQr } from "./CodigoQr";
import { PREFIJO_COPIA } from "./copiaContactos";
import { ENTRAR_DE_NUEVO, RECARGAR } from "./fallas";
import { ACTIVADO_CON_LLAVE, codigoAplicacion, codigoVerificacion, secretoEnBloques } from "./SegundoFactor";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const SECRETO = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI = `otpauth://totp/Consorcio%20en%20Mano:ana%40ejemplo.com?secret=${SECRETO}&issuer=Consorcio%20en%20Mano`;
const CODIGOS = Array.from({ length: 10 }, (_, i) => `abcd${i + 2}-fgh${i + 2}k`);
const INACTIVO = { activo: false, activado_en: null, codigos_recuperacion_restantes: 0, verificado_en: null };
const ACTIVO = {
  activo: true,
  activado_en: "2026-10-15T20:00:00Z",
  codigos_recuperacion_restantes: 10,
  verificado_en: null,
};
const VERIFICADO = {
  verificado_en: "2026-10-15T21:00:00Z",
  vale_hasta: "2026-10-15T21:10:00Z",
  codigos_recuperacion_restantes: 9,
};

const SOLO_LLAVE = { ...INACTIVO, llaves: 1 };
const LLAVE = {
  id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
  credencial_id: "Y3JlZGVuY2lhbA",
  nombre: "Mi PC",
  creada_en: "2026-11-04T10:00:00Z",
  ultimo_uso_en: null,
};
const ENTRAR = { level: 1, name: "Entrar a Consorcio en Mano" };
const MI_CUENTA = { level: 1, name: "Mi cuenta" };
const YO = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: "ana@ejemplo.com",
  sesion_id: "11111111-1111-4111-8111-111111111111",
  vence_en: "2026-11-15T20:00:00Z",
};
/** Lo que lee /cuenta al llegar; el segundo factor lo responde cada prueba. */
const LECTURAS_DE_CUENTA: Record<string, () => Response> = {
  "GET /api/v1/auth/sesion": () => Response.json(YO),
  "GET /api/v1/auth/sesiones": () => Response.json({ elementos: [] }),
  "GET /api/v1/auth/contrasena": () => Response.json({ definida: false, cambiada_en: null }),
  "GET /api/v1/privacidad/casos": () => Response.json({ elementos: [] }),
};
const SIN_SESION: Record<string, () => Response> = {
  "GET /api/v1/auth/sesion": () => error("no_autenticado", 401),
};

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

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

function pedidosDe(simulado: ReturnType<typeof vi.fn>): Request[] {
  return simulado.mock.calls.map((c) => (c as unknown as [Request])[0]);
}

function rutas(simulado: ReturnType<typeof vi.fn>): string[] {
  return pedidosDe(simulado).map((p) => `${p.method} ${new URL(p.url).pathname}`);
}

function Lugar() {
  return <output data-testid="lugar">{useLocation().pathname}</output>;
}

const lugar = () => screen.getByTestId("lugar").textContent;

function abrir(entrada: string | { pathname: string; state: unknown } = "/cuenta/segundo-factor") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[entrada]}>
            <App />
            <Lugar />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

function escribirCodigo(etiqueta: string, valor: string) {
  fireEvent.change(screen.getByLabelText(etiqueta), { target: { value: valor } });
}

beforeEach(() => {
  // jsdom no guarda cookies __Host- (piden Secure y https): se simula la lectura.
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "credentials");
  localStorage.clear();
});

describe("página /cuenta/segundo-factor", () => {
  it("sin sesión va sola a /entrar y no pide nada más del factor", async () => {
    const pedido = backend({ "GET /api/v1/auth/totp": () => error("no_autenticado", 401), ...SIN_SESION });
    vi.stubGlobal("fetch", pedido);
    abrir();

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(lugar()).toBe("/entrar");
    expect(rutas(pedido).filter((r) => r.includes("/totp"))).toEqual(["GET /api/v1/auth/totp"]);
  });

  it("la sesión vencida borra las copias de los contactos; con sesión siguen ahí", async () => {
    const copia = JSON.stringify({
      consorcio_id: "c1",
      nombre: "Edificio",
      guardada_en: "2026-10-21T12:00:00Z",
      lista: { elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" },
    });
    localStorage.setItem(`${PREFIJO_COPIA}c1`, copia);
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => error("no_autenticado", 401),
        ...SIN_SESION,
      }),
    );
    abrir();
    const activar = await screen.findByRole("button", { name: "Activar el segundo factor" });
    // Control: con la sesión vigente, la copia sigue en el dispositivo.
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBe(copia);

    fireEvent.click(activar);

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(localStorage.getItem(`${PREFIJO_COPIA}c1`)).toBeNull();
  });

  it("da de alta: QR y clave, confirma con CSRF, muestra los diez códigos una vez y sigue a /cuenta cuando los guardó", async () => {
    let activo = false;
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(activo ? ACTIVO : INACTIVO),
      "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
      "POST /api/v1/auth/totp/confirmar": () => {
        activo = true;
        return Response.json({ codigos: CODIGOS });
      },
      ...LECTURAS_DE_CUENTA,
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    const qr = await screen.findByRole("img", { name: /Código QR/ });
    expect(qr.tagName.toLowerCase()).toBe("svg");
    expect(qr.querySelector("path")?.getAttribute("d")).toBe(trazoQr(URI).trazo);
    expect(screen.getByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeTruthy();
    expect(screen.getByRole("link", { name: "abrí la aplicación desde acá" }).getAttribute("href")).toBe(URI);

    escribirCodigo("Código de la aplicación", " 042 917 ");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));

    const lista = await screen.findByRole("list", { name: "Códigos de recuperación" });
    expect(within(lista).getAllByRole("listitem").map((li) => li.textContent)).toEqual(CODIGOS);
    expect(screen.queryByRole("img", { name: /Código QR/ })).toBeNull();
    expect(screen.queryByText(/JBSW/)).toBeNull();

    const [, alta, confirmacion] = pedidosDe(pedido);
    expect(alta?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(confirmacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await confirmacion?.json()).toEqual({ codigo: "042917" });

    // Un dato de una sola vez no avanza solo: sigue a la vista hasta que la persona lo dice.
    expect(lugar()).toBe("/cuenta/segundo-factor");
    expect(rutas(pedido)).toEqual([
      "GET /api/v1/auth/totp",
      "POST /api/v1/auth/totp/alta",
      "POST /api/v1/auth/totp/confirmar",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Ya los guardé" }));
    expect(await screen.findByRole("heading", MI_CUENTA)).toBeTruthy();
    expect(await screen.findByText("Listo: el segundo factor quedó activado.")).toBeTruthy();
    expect(screen.queryByText(CODIGOS[0] ?? "")).toBeNull();
  });

  it("un código con formato malo no sale; uno rechazado avisa y deja seguir en el alta", async () => {
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
      "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
      "POST /api/v1/auth/totp/confirmar": () => error("segundo_factor_invalido", 400),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole("img", { name: /Código QR/ });

    escribirCodigo("Código de la aplicación", "04291");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));
    expect(screen.getByText("Escribí los seis números que muestra la aplicación.")).toBeTruthy();
    expect(rutas(pedido)).toHaveLength(2);

    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));
    expect(await screen.findByText(/El código no sirve/)).toBeTruthy();
    expect(screen.getByRole("img", { name: /Código QR/ })).toBeTruthy();
  });

  it("si el alta ya no está pendiente, vuelve al principio y lo explica", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
        "POST /api/v1/auth/totp/confirmar": () => error("segundo_factor_sin_alta", 409),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole("img", { name: /Código QR/ });
    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));

    expect(await screen.findByText(/Empezá de nuevo/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Activar el segundo factor" })).toBeTruthy();
  });

  it("activado no ofrece verificar por adelantado: el código se pide cuando una acción lo necesita", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/auth/totp": () => Response.json(ACTIVO) }));
    abrir();

    await screen.findByRole("heading", { name: "Está activado" });
    expect(screen.queryByRole("button", { name: "Verificar" })).toBeNull();
    expect(screen.queryByLabelText("Código de la aplicación o de recuperación")).toBeNull();
    expect(screen.queryByText("Verificado en esta sesión")).toBeNull();
    // Control: lo demás de la tarjeta sigue ahí.
    expect(screen.getByText("Códigos de recuperación sin usar")).toBeTruthy();
    // Desactivar es una acción negativa: va en rojo (comentario 26).
    expect(screen.getByRole("button", { name: "Desactivar el segundo factor" }).className).toBe("peligro");
  });

  it("al pedir confirmación acepta un código de recuperación escrito como venga, con CSRF", async () => {
    let reciente = false;
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
      "POST /api/v1/auth/totp/verificar": () => {
        reciente = true;
        return Response.json(VERIFICADO);
      },
      "POST /api/v1/auth/totp/baja": () =>
        reciente ? new Response(null, { status: 204 }) : error("segundo_factor_requerido", 403),
      ...LECTURAS_DE_CUENTA,
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));
    fireEvent.click(await screen.findByRole("button", { name: "Usar otro método" }));
    fireEvent.click(await screen.findByRole("button", { name: "Código de recuperación" }));
    await screen.findByRole("button", { name: "Verificar y seguir" });
    escribirCodigo("Código", " K7M2Q X9P4T ");
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    expect(await screen.findByText("Listo: el segundo factor quedó desactivado.")).toBeTruthy();
    const verificacion = pedidosDe(pedido).find((p) => p.url.endsWith("/verificar"));
    expect(verificacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await verificacion?.json()).toEqual({ codigo: "k7m2q-x9p4t" });
  });

  it("si llegó desde otra pantalla con state.volver, vuelve ahí y no a /cuenta", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
        "POST /api/v1/auth/totp/baja": () => new Response(null, { status: 204 }),
      }),
    );
    abrir({ pathname: "/cuenta/segundo-factor", state: { volver: "/instalar" } });
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));

    expect(await screen.findByText("Listo: el segundo factor quedó desactivado.")).toBeTruthy();
    expect(lugar()).toBe("/instalar");
  });

  it("demasiados intentos lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
        "POST /api/v1/auth/totp/confirmar": () => error("demasiadas_peticiones", 429),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole("img", { name: /Código QR/ });
    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));

    expect(await screen.findByText(/demasiados intentos/)).toBeTruthy();
    // El código sigue escrito, para corregirlo sin empezar de nuevo.
    expect((screen.getByLabelText("Código de la aplicación") as HTMLInputElement).value).toBe("042917");
  });

  it("el alta con una sesión vieja (403 reautenticacion_requerida) no dice nada: lo pide la hoja", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => error("reautenticacion_requerida", 403),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Activar el segundo factor" })).toHaveProperty("disabled", false));
    expect(screen.queryByText(ENTRAR_DE_NUEVO)).toBeNull();
    expect(lugar()).toBe("/cuenta/segundo-factor");
  });

  it("control: otro rechazo del alta no ofrece volver a entrar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => error("csrf_invalido", 403),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    expect(await screen.findByText(RECARGAR)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Volver a entrar" })).toBeNull();
  });

  it("desactivar sin verificación reciente pide el código acá mismo; con él, desactiva y sigue a /cuenta", async () => {
    let reciente = false;
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
      "POST /api/v1/auth/totp/verificar": () => {
        reciente = true;
        return Response.json(VERIFICADO);
      },
      "POST /api/v1/auth/totp/baja": () =>
        reciente ? new Response(null, { status: 204 }) : error("segundo_factor_requerido", 403),
      ...LECTURAS_DE_CUENTA,
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));
    // Control: cancelar deja la pantalla como estaba y no desactiva nada.
    fireEvent.click(await screen.findByRole("button", { name: "Cancelar" }));
    expect(screen.getByRole("heading", { name: "Está activado" })).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));

    await screen.findByLabelText("Código");
    escribirCodigo("Código", "042917");

    expect(await screen.findByText("Listo: el segundo factor quedó desactivado.")).toBeTruthy();
    expect(await screen.findByRole("heading", MI_CUENTA)).toBeTruthy();
    const bajas = pedidosDe(pedido).filter((p) => p.url.endsWith("/baja"));
    expect(bajas).toHaveLength(3);
    expect(bajas[2]?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("quien solo tiene llave ve que está activado como información, y la aplicación como opción secundaria; sin nada, que no", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/auth/totp": () => Response.json(SOLO_LLAVE) }));
    abrir();
    expect((await screen.findByText(ACTIVADO_CON_LLAVE)).className).toBe("aviso-info");
    expect(screen.getByRole("button", { name: "Agregar también la aplicación" }).className).toBe("secundario");
    expect(screen.queryByRole("heading", { name: "Todavía no está activado" })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", backend({ "GET /api/v1/auth/totp": () => Response.json({ ...INACTIVO, llaves: 0 }) }));
    abrir();
    expect(await screen.findByRole("heading", { name: "Todavía no está activado" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Activar el segundo factor" }).className).toBe("");
    expect(screen.queryByText(ACTIVADO_CON_LLAVE)).toBeNull();
  });

  it("quien tiene llave suma la aplicación confirmando acá mismo", async () => {
    let confirmado = false;
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json({ ...SOLO_LLAVE, codigos_recuperacion_restantes: 5 }),
        "POST /api/v1/auth/totp/verificar": () => {
          confirmado = true;
          return Response.json(VERIFICADO);
        },
        "POST /api/v1/auth/totp/alta": () =>
          confirmado ? Response.json({ secreto: SECRETO, uri: URI }) : error("segundo_factor_requerido", 403),
      }),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Agregar también la aplicación" }));
    await screen.findByRole("button", { name: "Verificar y seguir" });
    escribirCodigo("Código", "k7m2q-x9p4t");
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    expect(await screen.findByRole("img", { name: /Código QR/ })).toBeTruthy();
  });

  it("sin backend lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();

    expect(await screen.findByText(/No pudimos consultar el segundo factor/)).toBeTruthy();
  });

  it.each([
    ["sin red", () => Promise.reject(new TypeError("sin red"))],
    ["con un 503", async () => error("servicio_no_disponible", 503)],
  ])("%s al consultar ofrece reintentar, y al reintentar sale bien", async (_, falla) => {
    let intentos = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        intentos += 1;
        return intentos === 1 ? falla() : Response.json(INACTIVO);
      }),
    );
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }));

    expect(await screen.findByRole("button", { name: "Activar el segundo factor" })).toBeTruthy();
    expect(intentos).toBe(2);
    // Control: con la consulta bien, ni el error ni el botón.
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    expect(screen.queryByText(/No pudimos consultar el segundo factor/)).toBeNull();
  });

  it("un 503 al consultar habla del servicio y no de la conexión", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/auth/totp": () => error("servicio_no_disponible", 503) }));
    abrir();

    expect(await screen.findByText(/No pudimos consultar el segundo factor. El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });

  it.each([
    ["el alta", INACTIVO, "img"],
    ["el estado activo", ACTIVO, "heading"],
  ])("en %s no tiene faltas de accesibilidad detectables por axe", async (_, estado, espera) => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(estado),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
      }),
    );
    const { container } = abrir();
    if (espera === "img")
      fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole(espera, espera === "img" ? { name: /Código QR/ } : { name: "Está activado" });

    // jsdom no calcula estilos: el contraste se mide en el navegador (e2e), no aquí.
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations).toEqual([]);
  });
});

describe("formatos del segundo factor", () => {
  it("el código de la aplicación son seis números, con espacios o guion en el medio", () => {
    expect(codigoAplicacion(" 042 917 ")).toBe("042917");
    expect(codigoAplicacion("042-917")).toBe("042917");
    for (const malo of ["04291", "0429170", "04291a", ""]) expect(codigoAplicacion(malo)).toBeNull();
  });

  it("la verificación acepta también un código de recuperación y lo lleva a la forma del contrato", () => {
    expect(codigoVerificacion("042917")).toBe("042917");
    expect(codigoVerificacion("K7M2Q-X9P4T")).toBe("k7m2q-x9p4t");
    expect(codigoVerificacion("k7m2qx9p4t")).toBe("k7m2q-x9p4t");
    // 0 y 1 no están en el alfabeto de los códigos de recuperación.
    for (const malo of ["k7m2q-x9p40", "k7m2q-x9p4", "k7m2q-x9p4tt", "hola"])
      expect(codigoVerificacion(malo)).toBeNull();
  });

  it("el secreto va en bloques de cuatro", () => {
    expect(secretoEnBloques("ABCDEFGHIJ")).toBe("ABCD EFGH IJ");
  });

  it("el QR lleva el margen de cuatro módulos y cambia con el texto", () => {
    const qr = trazoQr(URI);
    // Versión v: 17 + 4v módulos, más 4 de margen por lado.
    expect((qr.lado - 8 - 17) % 4).toBe(0);
    expect(qr.trazo).not.toMatch(/M[0-3] /);
    expect(trazoQr(URI.replace("ana", "beto")).trazo).not.toBe(qr.trazo);
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

describe("página /cuenta/segundo-factor: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta dice «Consultando…» y lo saca al llegar el estado", async () => {
    const { retenido, soltar } = retener(
      backend({ "GET /api/v1/auth/totp": () => Response.json(INACTIVO) }),
      "/api/v1/auth/totp",
    );
    vi.stubGlobal("fetch", retenido);
    abrir();

    expect(screen.getByText("Consultando…").parentElement?.textContent).toBe("Consultando…");
    expect(screen.queryByRole("button", { name: "Activar el segundo factor" })).toBeNull();
    soltar();

    expect(await screen.findByRole("button", { name: "Activar el segundo factor" })).toBeTruthy();
    expect(screen.queryByText("Consultando…")).toBeNull();
  });

  it("sin red al empezar el alta: lo dice y deja reintentar desde el mismo lugar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    expect(await screen.findByText("No pudimos completar el pedido. Revisá la conexión y probá de nuevo.")).toBeTruthy();
    const boton = screen.getByRole("button", { name: "Activar el segundo factor" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(false);
    expect(screen.queryByRole("img", { name: /Código QR/ })).toBeNull();
  });

  it("sin red al confirmar el alta: lo dice y sigue en el alta, con el código escrito", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
        "POST /api/v1/auth/totp/confirmar": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole("img", { name: /Código QR/ });
    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));

    expect(await screen.findByText("No pudimos completar el pedido. Revisá la conexión y probá de nuevo.")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Código QR/ })).toBeTruthy();
    expect((screen.getByLabelText("Código de la aplicación") as HTMLInputElement).value).toBe("042917");
  });

  it("sesión vencida (401) al empezar el alta: va sola a entrar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => error("no_autenticado", 401),
        ...SIN_SESION,
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it("sesión vencida (401) al confirmar el alta: va sola a entrar y no habla de la conexión", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
        "POST /api/v1/auth/totp/confirmar": () => error("no_autenticado", 401),
        ...SIN_SESION,
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
    await screen.findByRole("img", { name: /Código QR/ });
    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });

  it("sesión vencida (401) al desactivar: va sola a entrar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
        "POST /api/v1/auth/totp/baja": () => error("no_autenticado", 401),
        ...SIN_SESION,
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it.each([
    ["POST /api/v1/auth/totp/confirmar", 503],
    ["POST /api/v1/auth/totp/confirmar", 500],
    ["POST /api/v1/auth/totp/baja", 503],
  ])("un %s con %i habla del servicio y no de la conexión", async (ruta, status) => {
    const enElAlta = ruta.endsWith("confirmar");
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(enElAlta ? INACTIVO : ACTIVO),
        "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
        [ruta]: () => error("error_interno", status),
      }),
    );
    abrir();
    if (enElAlta) {
      fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));
      await screen.findByRole("img", { name: /Código QR/ });
      escribirCodigo("Código de la aplicación", "042917");
      fireEvent.click(screen.getByRole("button", { name: "Activar la aplicación" }));
    } else fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));

    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
});

/** Un navegador que sabe usar llaves: `create` devuelve una credencial que el backend simulado acepta. */
function conLlaves() {
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
}

function altaDeLlave(codigos: string[] | null): Respuestas {
  return {
    "POST /api/v1/auth/llave/alta/opciones": () =>
      Response.json({ opciones: { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } } }),
    "POST /api/v1/auth/llave/alta": () => Response.json({ id: LLAVE.id, codigos_recuperacion: codigos }, { status: 201 }),
  };
}

const textosDeCodigos = (lista: HTMLElement) => within(lista).getAllByRole("listitem").map((li) => li.textContent);

/** Da tiempo a que cualquier respuesta pendiente llegue a la pantalla. */
const asentar = () => act(() => new Promise<void>((r) => setTimeout(r, 20)));

describe("códigos de recuperación de la llave: nunca se pierden", () => {
  it.each([
    ["el servicio caído", async () => error("servicio_no_disponible", 503)],
    ["sin red", () => Promise.reject(new TypeError("sin red"))],
    ["la sesión vencida", async () => error("no_autenticado", 401)],
  ])("con %s en la consulta siguiente, siguen a la vista hasta «Ya los guardé»", async (_, falla) => {
    conLlaves();
    let consultas = 0;
    const base = backend({ "GET /api/v1/auth/llave": () => Response.json([]), ...altaDeLlave(CODIGOS) });
    const pedido = vi.fn(async (p: Request) => {
      if (new URL(p.url).pathname !== "/api/v1/auth/totp") return base(p);
      consultas += 1;
      return consultas === 1 ? Response.json(INACTIVO) : falla();
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Activar en este dispositivo" }));

    const lista = await screen.findByRole("list", { name: "Códigos de recuperación" });
    await asentar();
    expect(textosDeCodigos(lista)).toEqual(CODIGOS);
    expect(screen.getByRole("button", { name: "Ya los guardé" })).toBeTruthy();
    expect(screen.getByText("Listo: tu llave quedó activada.")).toBeTruthy();
    expect(lugar()).toBe("/cuenta/segundo-factor");
    // Nada que pueda sacar los códigos de la pantalla: ni consultas ni otras acciones a mano.
    expect(consultas).toBe(1);
    expect(screen.queryByRole("button", { name: "Activar el segundo factor" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar este dispositivo" })).toBeNull();
  });

  it("una respuesta que venía en camino, aunque sea un 401, no los tapa", async () => {
    conLlaves();
    let soltar: () => void = () => undefined;
    const enCamino = new Promise<void>((r) => {
      soltar = r;
    });
    let consultas = 0;
    const base = backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: () => new Response(null, { status: 204 }),
      ...altaDeLlave(CODIGOS),
      ...SIN_SESION,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (p: Request) => {
        if (new URL(p.url).pathname !== "/api/v1/auth/totp") return base(p);
        consultas += 1;
        if (consultas === 1) return Response.json(SOLO_LLAVE);
        await enCamino;
        return error("no_autenticado", 401);
      }),
    );
    abrir();

    // Quitar una llave deja salir la consulta del estado, que se queda en camino.
    fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));
    await screen.findByText("Listo: quitaste la llave.");
    expect(consultas).toBe(2);
    fireEvent.click(await screen.findByRole("button", { name: "Agregar este dispositivo" }));
    const lista = await screen.findByRole("list", { name: "Códigos de recuperación" });

    soltar();
    await asentar();

    expect(textosDeCodigos(lista)).toEqual(CODIGOS);
    expect(lugar()).toBe("/cuenta/segundo-factor");
    expect(screen.queryByRole("heading", ENTRAR)).toBeNull();
  });

  it("control: sin códigos a la vista, ese mismo 401 sí lleva a /entrar", async () => {
    conLlaves();
    let consultas = 0;
    const base = backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: () => new Response(null, { status: 204 }),
      ...SIN_SESION,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (p: Request) => {
        if (new URL(p.url).pathname !== "/api/v1/auth/totp") return base(p);
        consultas += 1;
        return consultas === 1 ? Response.json(SOLO_LLAVE) : error("no_autenticado", 401);
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));

    expect(await screen.findByRole("heading", ENTRAR)).toBeTruthy();
  });

  it("«Ya los guardé» sigue a /cuenta con el aviso y los códigos ya no están", async () => {
    conLlaves();
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "GET /api/v1/auth/llave": () => Response.json([]),
        ...altaDeLlave(CODIGOS),
        ...LECTURAS_DE_CUENTA,
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar en este dispositivo" }));
    fireEvent.click(await screen.findByRole("button", { name: "Ya los guardé" }));

    expect(await screen.findByRole("heading", MI_CUENTA)).toBeTruthy();
    expect(await screen.findByText("Listo: tu llave quedó activada.")).toBeTruthy();
    expect(screen.queryByText(CODIGOS[0] ?? "")).toBeNull();
  });

  it("una llave más, sin códigos nuevos, no frena en nada: sigue a /cuenta", async () => {
    conLlaves();
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(SOLO_LLAVE),
        "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
        ...altaDeLlave(null),
        ...LECTURAS_DE_CUENTA,
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar este dispositivo" }));

    expect(await screen.findByRole("heading", MI_CUENTA)).toBeTruthy();
    expect(await screen.findByText("Listo: tu llave quedó activada.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ya los guardé" })).toBeNull();
  });

  it("activar la llave no se queda en el «Listo»: vuelve a donde se pidió", async () => {
    conLlaves();
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(SOLO_LLAVE),
        "GET /api/v1/auth/llave": () => Response.json([]),
        ...altaDeLlave(null),
      }),
    );
    abrir({ pathname: "/cuenta/segundo-factor", state: { volver: "/instalar" } });
    fireEvent.click(await screen.findByRole("button", { name: "Activar en este dispositivo" }));

    expect(await screen.findByText("Listo: tu llave quedó activada.")).toBeTruthy();
    expect(lugar()).toBe("/instalar");
  });
});
