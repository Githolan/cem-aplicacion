import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { trazoQr } from "./CodigoQr";
import { codigoAplicacion, codigoVerificacion, secretoEnBloques } from "./SegundoFactor";
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

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/cuenta/segundo-factor"]}>
            <App />
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
});

describe("página /cuenta/segundo-factor", () => {
  it("sin sesión manda a entrar y no pide nada más", async () => {
    const pedido = backend({ "GET /api/v1/auth/totp": () => error("no_autenticado", 401) });
    vi.stubGlobal("fetch", pedido);
    abrir();

    const enlace = await screen.findByRole("link", { name: "entrá a tu cuenta" });
    expect(enlace.getAttribute("href")).toBe("/entrar");
    expect(rutas(pedido)).toEqual(["GET /api/v1/auth/totp"]);
  });

  it("da de alta: QR y clave, confirma con CSRF, muestra los diez códigos una vez y vuelve al estado", async () => {
    let activo = false;
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(activo ? ACTIVO : INACTIVO),
      "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: SECRETO, uri: URI }),
      "POST /api/v1/auth/totp/confirmar": () => {
        activo = true;
        return Response.json({ codigos: CODIGOS });
      },
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
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    const lista = await screen.findByRole("list", { name: "Códigos de recuperación" });
    expect(within(lista).getAllByRole("listitem").map((li) => li.textContent)).toEqual(CODIGOS);
    expect(screen.queryByRole("img", { name: /Código QR/ })).toBeNull();
    expect(screen.queryByText(/JBSW/)).toBeNull();

    const [, alta, confirmacion] = pedidosDe(pedido);
    expect(alta?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(confirmacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await confirmacion?.json()).toEqual({ codigo: "042917" });

    fireEvent.click(screen.getByRole("button", { name: "Ya los guardé" }));
    expect(await screen.findByRole("heading", { name: "Está activado" })).toBeTruthy();
    expect(screen.queryByText(CODIGOS[0] ?? "")).toBeNull();
    expect(rutas(pedido)).toEqual([
      "GET /api/v1/auth/totp",
      "POST /api/v1/auth/totp/alta",
      "POST /api/v1/auth/totp/confirmar",
      "GET /api/v1/auth/totp",
    ]);
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
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(screen.getByText("Escribí los seis números que muestra la aplicación.")).toBeTruthy();
    expect(rutas(pedido)).toHaveLength(2);

    escribirCodigo("Código de la aplicación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    expect(await screen.findByText(/Empezá de nuevo/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Activar el segundo factor" })).toBeTruthy();
  });

  it("verifica con un código de recuperación escrito como venga y actualiza lo que queda", async () => {
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
      "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    await screen.findByRole("heading", { name: "Está activado" });
    expect(screen.getByText("Todavía no")).toBeTruthy();
    escribirCodigo("Código de la aplicación o de recuperación", " K7M2Q X9P4T ");
    fireEvent.click(screen.getByRole("button", { name: "Verificar" }));

    expect(await screen.findByText("Listo: verificado hasta las 18:10.")).toBeTruthy();
    expect(screen.getByText("9")).toBeTruthy();
    expect(screen.queryByText("Todavía no")).toBeNull();
    const verificacion = pedidosDe(pedido)[1];
    expect(verificacion?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(await verificacion?.json()).toEqual({ codigo: "k7m2q-x9p4t" });
  });

  it("demasiados intentos lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
        "POST /api/v1/auth/totp/verificar": () => error("demasiadas_peticiones", 429),
      }),
    );
    abrir();
    await screen.findByRole("heading", { name: "Está activado" });
    escribirCodigo("Código de la aplicación o de recuperación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Verificar" }));

    expect(await screen.findByText(/demasiados intentos/)).toBeTruthy();
  });

  it("desactivar sin verificación reciente lo explica; con ella, desactiva", async () => {
    let reciente = false;
    const pedido = backend({
      "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
      "POST /api/v1/auth/totp/baja": () =>
        reciente ? new Response(null, { status: 204 }) : error("segundo_factor_requerido", 403),
    });
    vi.stubGlobal("fetch", pedido);
    abrir();

    fireEvent.click(await screen.findByRole("button", { name: "Desactivar el segundo factor" }));
    expect(await screen.findByText(/primero escribí arriba un código/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Está activado" })).toBeTruthy();

    reciente = true;
    fireEvent.click(screen.getByRole("button", { name: "Desactivar el segundo factor" }));
    expect(await screen.findByText("Listo: el segundo factor quedó desactivado.")).toBeTruthy();
    const baja = pedidosDe(pedido)[2];
    expect(baja?.headers.get("X-CSRF-Token")).toBe("abc123");
  });

  it("sin backend lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("sin red"))));
    abrir();

    expect(await screen.findByText(/No pudimos consultar el segundo factor/)).toBeTruthy();
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

    expect(screen.getByRole("status").textContent).toBe("Consultando…");
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

  it("sin red al verificar: lo dice y no marca la sesión como verificada", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
        "POST /api/v1/auth/totp/verificar": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    abrir();
    await screen.findByRole("heading", { name: "Está activado" });
    escribirCodigo("Código de la aplicación o de recuperación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Verificar" }));

    expect(await screen.findByText("No pudimos completar el pedido. Revisá la conexión y probá de nuevo.")).toBeTruthy();
    expect(screen.getByText("Todavía no")).toBeTruthy();
  });

  it("sesión vencida (401) al empezar el alta: manda a entrar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(INACTIVO),
        "POST /api/v1/auth/totp/alta": () => error("no_autenticado", 401),
      }),
    );
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Activar el segundo factor" }));

    expect((await screen.findByRole("link", { name: "entrá a tu cuenta" })).getAttribute("href")).toBe("/entrar");
  });

  // DEFECTO (pruebas/pantallas-minimas, 20-10-2026): si la sesión vence con la página abierta,
  // solo `empezarAlta` atiende el 401 (SegundoFactor.tsx:133). `verificar`, `confirmar` y
  // `desactivar` lo tratan como cualquier otra falla (avisoDeRechazo, SegundoFactor.tsx:113-121, y
  // SegundoFactor.tsx:214) y dicen "Revisá la conexión", sin ofrecer entrar de nuevo. /cuenta, en
  // cambio, manda a /entrar ante un 401 en cualquier acción (Cuenta.tsx:46-48). Queda pendiente
  // hasta que se corrija.
  it.fails("sesión vencida (401) al verificar: ofrece entrar y no habla de la conexión", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/auth/totp": () => Response.json(ACTIVO),
        "POST /api/v1/auth/totp/verificar": () => error("no_autenticado", 401),
      }),
    );
    abrir();
    await screen.findByRole("heading", { name: "Está activado" });
    escribirCodigo("Código de la aplicación o de recuperación", "042917");
    fireEvent.click(screen.getByRole("button", { name: "Verificar" }));

    expect((await screen.findByRole("link", { name: "entrá a tu cuenta" })).getAttribute("href")).toBe("/entrar");
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
});
