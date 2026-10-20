import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
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

function backend(aceptacion: () => Response) {
  return vi.fn(async (pedido: Request) =>
    new URL(pedido.url).pathname.endsWith("/consultar") ? Response.json(RECIBIDA) : aceptacion(),
  );
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
    expect(new URL(consulta.url).pathname).toBe("/api/v1/auth/invitacion-mandato/consultar");
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
    const campo = await screen.findByLabelText("Tu nombre (solo si la cuenta es nueva)");
    expect((campo as HTMLInputElement).value).toBe("Estudio X");
    fireEvent.change(campo, { target: { value: "  Beto Estudio " } });
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText(/tu cuenta quedó asociada a la administración de Edificio Alvear 100/)).toBeTruthy();
    const aceptacion = pedidosDe(simulado)[1] as Request;
    expect(new URL(aceptacion.url).pathname).toBe("/api/v1/auth/invitacion-mandato/aceptar");
    expect(await aceptacion.json()).toEqual({ token: TOKEN, nombre: "Beto Estudio" });
    expect(aceptacion.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(screen.queryByText(/El mandato empieza/)).toBeNull();
  });

  it("con un mandato futuro avisa desde cuándo va a ver el consorcio", async () => {
    vi.stubGlobal("fetch", backend(() => Response.json(aceptada("2099-11-01"))));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());
    expect(await screen.findByText("El mandato empieza el 01/11/2099: desde ese día vas a ver el consorcio.")).toBeTruthy();
  });

  it.each([
    ["invitacion_requiere_sesion", /Ya tenés una cuenta con este correo/],
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
    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
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
    expect(screen.getByText("Para asociar tu cuenta, abrí el enlace que te llegó por correo.")).toBeTruthy();
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

describe("página /invitacion-mandato: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta dice «Buscando la invitación…» y lo saca al llegar la respuesta", async () => {
    const { retenido, soltar } = retener(
      backend(() => Response.json(aceptada("2026-01-01"))),
      "/api/v1/auth/invitacion-mandato/consultar",
    );
    vi.stubGlobal("fetch", retenido);
    abrir(`${RUTA}#token=${TOKEN}`);

    expect(screen.getByRole("status").textContent).toBe("Buscando la invitación…");
    expect(screen.queryByRole("button", { name: "Aceptar y asociar mi cuenta" })).toBeNull();
    soltar();

    expect(await aceptarBoton()).toBeTruthy();
    expect(screen.queryByText("Buscando la invitación…")).toBeNull();
  });

  it("carga: mientras acepta dice «Aceptando…» con el botón deshabilitado", async () => {
    const { retenido, soltar } = retener(
      backend(() => Response.json(aceptada("2026-01-01"))),
      "/api/v1/auth/invitacion-mandato/aceptar",
    );
    vi.stubGlobal("fetch", retenido);
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(screen.getByRole("status").textContent).toBe("Aceptando…");
    expect(((await aceptarBoton()) as HTMLButtonElement).disabled).toBe(true);
    soltar();

    expect(await screen.findByText(/tu cuenta quedó asociada/)).toBeTruthy();
    expect(screen.queryByText("Aceptando…")).toBeNull();
  });

  it("sin red al consultar: lo dice y no ofrece aceptar", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    abrir(`${RUTA}#token=${TOKEN}`);

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Aceptar y asociar mi cuenta" })).toBeNull();
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

    expect(await screen.findByText(/No pudimos completar el pedido. Revisá la conexión/)).toBeTruthy();
    expect(screen.queryByText(/quedó asociada/)).toBeNull();
  });

  // DEFECTO (pruebas/pantallas-minimas, 20-10-2026): un 403 `csrf_invalido` al aceptar (el backend
  // lo responde con Sec-Fetch-Site de otro sitio o sin el X-CSRF-Token de una sesión válida:
  // backend/app/consorcios/mandatos_invitaciones.py, `aceptar`) cae en "sin_servicio"
  // (InvitacionMandato.tsx:94) y la página dice "Revisá la conexión" (InvitacionMandato.tsx:177-181),
  // contra la regla E34 de src/fallas.ts. Queda pendiente hasta que se corrija.
  it.fails("permiso insuficiente (403 csrf_invalido) al aceptar no se explica como un problema de conexión", async () => {
    vi.stubGlobal("fetch", backend(() => error("csrf_invalido", 403)));
    abrir(`${RUTA}#token=${TOKEN}`);
    fireEvent.click(await aceptarBoton());

    expect(await screen.findByText(/No pudimos/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
  });
});
