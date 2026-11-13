import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { Link, MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "abababab-abab-4bab-8bab-abababababab";
const C3 = "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd";
const AZUL = { id: C1, nombre: "Edificio Azul", vinculos: [] };
const VERDE = { id: C2, nombre: "Edificio Verde", vinculos: [] };
const ROJO = { id: C3, nombre: "Edificio Rojo", vinculos: [] };
const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: "ana@ejemplo.com",
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};
const SALUD = { estado: "ok", version: "0.1.0", sha_imagen: "x", sha_contratos: "", version_contrato: "0.1.0" };
const ADMINISTRA = ["egresos.preparar_orden", "contactos.ver"];
const CONSEJO = ["egresos.revisar_orden", "egresos.conciliar_pago", "contactos.ver"];
const VECINO = ["contactos.ver"];
const TOTP_ACTIVO = { activo: true, activado_en: "2026-10-15T20:00:00Z", codigos_recuperacion_restantes: 10, verificado_en: null };

const bandejaDe = (id: string) => `/api/v1/consorcios/${id}/pagos/bandeja`;

function elemento(n: number, grupo: string) {
  return {
    orden_id: `3333333${n}-3333-4333-8333-333333333333`,
    proveedor: `Proveedor ${n}`,
    concepto: "Abono",
    importe_centavos: 100_000,
    moneda: "ARS",
    paso: grupo === "esperando" ? "espera_conciliar" : "aprobar",
    grupo,
    le_toca_a: grupo === "esperando" ? "consejo" : "vos",
    // Si cuenta como pendiente lo dice el servidor con la tabla de la pantalla (tanda 2).
    pendiente: grupo !== "esperando",
    situacion: grupo === "esperando" ? "falta_conciliar" : "en_revision",
    estado_visible: grupo === "esperando" ? "falta_conciliar" : "esperando_aprobacion",
    principal: grupo === "esperando" ? null : "aprobar",
    creada_en: "2026-11-09T15:00:00Z",
    actualizada_en: "2026-11-09T15:00:00Z",
    posible_duplicada_de: null,
  };
}

const conElementos = (...grupos: string[]) => () =>
  Response.json({ elementos: grupos.map((g, i) => elemento(i, g)), avisos_rechazo: [], por_cotejar: 0 });
/** Pagos conciliados sin cotejar: el servidor los cuenta solo para quien coteja (P-24). */
const conPorCotejar = (n: number) => () => Response.json({ elementos: [], avisos_rechazo: [], por_cotejar: n });

type Respuesta = () => Response;

function backend({
  consorcios = [AZUL],
  acciones = {},
  bandejas = {},
  otras = {},
}: {
  consorcios?: object[];
  acciones?: Record<string, readonly string[]>;
  bandejas?: Record<string, Respuesta>;
  otras?: Record<string, Respuesta>;
}) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    const clave = `${pedido.method} ${camino}`;
    const otra = otras[clave];
    if (otra !== undefined) return otra();
    if (camino === "/api/v1/auth/sesion") return Response.json(SESION);
    if (camino === "/health") return Response.json(SALUD);
    if (camino === "/api/v1/me/consorcios") return Response.json(consorcios);
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: [] });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    const deAcciones = /^\/api\/v1\/consorcios\/([^/]+)\/mis-acciones$/.exec(camino);
    if (deAcciones !== null) return Response.json({ acciones: acciones[deAcciones[1] ?? ""] ?? [] });
    const deBandeja = /^\/api\/v1\/consorcios\/([^/]+)\/pagos\/bandeja$/.exec(camino);
    const bandeja = deBandeja === null ? undefined : bandejas[deBandeja[1] ?? ""];
    if (bandeja !== undefined) return bandeja();
    throw new Error(`pedido no previsto: ${clave}`);
  });
}

function pedidosA(simulado: ReturnType<typeof backend>, camino: string): number {
  return simulado.mock.calls.filter((c) => new URL((c[0] as Request).url).pathname === camino).length;
}

function abrir(entrada: string | { pathname: string; state: unknown } = "/") {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[entrada]}>
            <App />
            <Link to="/cuenta/segundo-factor" state={{ volver: "/pagos" }}>
              Ir al alta
            </Link>
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

const tarjeta = () => screen.findByRole("region", { name: "Pagos" });
const selector = () => screen.findByRole("combobox", { name: "Edificio" });
const filaDePagos = () => screen.findByRole("link", { name: "Pagos", description: /./ });

beforeEach(() => {
  // Sin ancho de PC: la ficha no se abre sola al lado de la bandeja.
  vi.stubGlobal("matchMedia", (consulta: string) => ({ matches: false, media: consulta, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Pagos en el inicio de quien revisa", () => {
  it("cuenta lo que tiene para hacer, sin lo que espera a otra persona", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("para_aprobar", "para_conciliar", "esperando") } }),
    );
    abrir();

    const hero = within(await tarjeta());
    expect((await hero.findByText("cosas para hacer", { exact: false })).textContent).toBe("2 cosas para hacer");
    expect(hero.getByText("Para hacer")).toBeTruthy();
    expect(hero.getByText("Edificio Azul")).toBeTruthy();
    expect(hero.getByRole("link", { name: "Ver los pagos" }).getAttribute("href")).toBe("/pagos");
  });

  it("los pagos por cotejar cuentan; una rechazada ya vista o un borrador viejo, no (tanda 2)", async () => {
    const bandeja = () =>
      Response.json({
        elementos: [elemento(0, "para_aprobar"), { ...elemento(1, "borradores_viejos"), pendiente: false }],
        avisos_rechazo: [],
        por_cotejar: 2,
      });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: bandeja } }));
    abrir();
    const hero = within(await tarjeta());
    expect((await hero.findByText("cosas para hacer", { exact: false })).textContent).toBe("3 cosas para hacer");
  });

  it("no repite Pagos en la lista: la tarjeta la reemplaza", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("para_aprobar") } }));
    abrir();

    await tarjeta();
    expect(screen.queryByRole("link", { name: "Pagos", description: /./ })).toBeNull();
  });

  it("con una sola cosa va en singular", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("para_aprobar") } }));
    abrir();

    expect((await within(await tarjeta()).findByText("cosa para hacer", { exact: false })).textContent).toBe("1 cosa para hacer");
  });

  it("AC-01: sin nada para hacer no hay tarjeta ni «Ver los pagos»: Pagos queda como una fila más", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("esperando") } }));
    abrir();

    const fila = await filaDePagos();
    expect(fila.getAttribute("href")).toBe("/pagos");
    expect(within(fila).getByText("Nada pendiente.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Ver los pagos" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Pagos" })).toBeNull();
  });

  it("si no se pudo leer lo dice y deja reintentar, sin inventar un cero", async () => {
    let caido = true;
    const bandeja = () =>
      caido ? Response.json({ codigo: "error", mensaje: "x", id_correlacion: "c" }, { status: 500 }) : conElementos("para_aprobar")();
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: bandeja } }));
    abrir();

    const hero = within(await tarjeta());
    expect(await hero.findByRole("alert")).toBeTruthy();
    expect(hero.queryByText("Nada pendiente.")).toBeNull();
    caido = false;
    fireEvent.click(hero.getByRole("button", { name: "Reintentar" }));
    expect((await hero.findByText("cosa para hacer", { exact: false })).textContent).toBe("1 cosa para hacer");
    expect(hero.queryByRole("alert")).toBeNull();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("para_aprobar") } }));
    const { container } = abrir();

    await within(await tarjeta()).findByText("cosa para hacer", { exact: false });
    const { violations } = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(violations.map((v) => v.id)).toEqual([]);
  });
});

describe("Pagos en el inicio de quien prepara", () => {
  it("es una fila con el detalle que da el servidor, sin tarjeta", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: conElementos("para_pagar", "para_preparar", "esperando") } }));
    abrir();

    await waitFor(async () => expect((await filaDePagos()).getAttribute("aria-describedby")).toBeTruthy());
    expect(await screen.findByText("2 cosas para hacer")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Pagos" })).toBeNull();
  });

  it("control: otro número del servidor cambia el detalle", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: conElementos("para_pagar") } }));
    abrir();

    expect(await screen.findByText("1 cosa para hacer")).toBeTruthy();
    expect(screen.queryByText("2 cosas para hacer")).toBeNull();
  });

  it("si no se pudo leer, la fila lo dice y sigue llevando a Pagos", async () => {
    const caida = () => Response.json({ codigo: "error", mensaje: "x", id_correlacion: "c" }, { status: 500 });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: caida } }));
    abrir();

    expect(await screen.findByText("No se pudieron leer los pendientes")).toBeTruthy();
    expect((await filaDePagos()).getAttribute("href")).toBe("/pagos");
  });
});

describe("inicio sin textos fijos (tanda 1, 12-11-2026)", () => {
  it("AC-04: quien prepara, sin pendientes, no tiene el aviso fijo de que la aplicación no mueve dinero", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: conElementos() } }));
    const { container } = abrir();

    expect(await within(await filaDePagos()).findByText("Nada pendiente.")).toBeTruthy();
    expect(screen.queryByText(/no mueve dinero/)).toBeNull();
    expect(container.querySelector(".aviso-info")).toBeNull();
  });

  it.each([
    ["quien prepara", ADMINISTRA],
    ["quien revisa", CONSEJO],
  ])("AC-03: %s no ve «Estamos construyendo la aplicación»", async (_, acciones) => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: acciones }, bandejas: { [C1]: conElementos("para_aprobar") } }));
    abrir();

    await screen.findByRole("link", { name: "Contactos de emergencia" });
    expect(screen.queryByText(/Estamos construyendo/)).toBeNull();
  });
});

describe("quien no ve Pagos", () => {
  it("no pide la bandeja ni ve el selector", async () => {
    const simulado = backend({ consorcios: [AZUL, VERDE], acciones: { [C1]: VECINO, [C2]: VECINO } });
    vi.stubGlobal("fetch", simulado);
    abrir();

    await screen.findByRole("link", { name: "Contactos de emergencia" });
    expect(screen.queryByRole("combobox", { name: "Edificio" })).toBeNull();
    expect(pedidosA(simulado, bandejaDe(C1))).toBe(0);
    expect(pedidosA(simulado, bandejaDe(C2))).toBe(0);
  });
});

describe("selector de edificio del inicio", () => {
  const dosEdificios = () =>
    backend({
      consorcios: [AZUL, VERDE, ROJO],
      acciones: { [C1]: CONSEJO, [C2]: CONSEJO, [C3]: VECINO },
      bandejas: { [C1]: conElementos("para_aprobar"), [C2]: conElementos("para_aprobar", "para_aprobar", "para_conciliar") },
    });

  it("ofrece solo los edificios donde ve Pagos", async () => {
    vi.stubGlobal("fetch", dosEdificios());
    abrir();

    const opciones = within(await selector()).getAllByRole("option");
    expect(opciones.map((o) => o.textContent)).toEqual(["Edificio Azul", "Edificio Verde"]);
  });

  it("elegir otro cambia la cuenta y «Ver los pagos» abre ese edificio", async () => {
    const simulado = dosEdificios();
    vi.stubGlobal("fetch", simulado);
    abrir();

    fireEvent.change(await selector(), { target: { value: C2 } });
    const hero = within(await tarjeta());
    expect((await hero.findByText("cosas para hacer", { exact: false })).textContent).toBe("3 cosas para hacer");
    expect(hero.getByText("Edificio Verde")).toBeTruthy();

    const antes = pedidosA(simulado, bandejaDe(C2));
    fireEvent.click(hero.getByRole("link", { name: "Ver los pagos" }));
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C2))).toBeGreaterThan(antes));
  });

  it("control: sin elegir, «Ver los pagos» abre el primero", async () => {
    const simulado = dosEdificios();
    vi.stubGlobal("fetch", simulado);
    abrir();

    const hero = within(await tarjeta());
    await hero.findByText("cosa para hacer", { exact: false });
    fireEvent.click(hero.getByRole("link", { name: "Ver los pagos" }));
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C1))).toBeGreaterThan(1));
    expect(pedidosA(simulado, bandejaDe(C2))).toBe(0);
  });

  it("con un solo edificio no hay selector, y el nombre va en la tarjeta", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conElementos("para_aprobar") } }));
    abrir();

    expect(within(await tarjeta()).getByText("Edificio Azul")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Edificio" })).toBeNull();
  });
});

describe("volver del alta de la verificación", () => {
  const conAlta = () =>
    backend({
      consorcios: [AZUL, VERDE],
      acciones: { [C1]: CONSEJO, [C2]: CONSEJO },
      bandejas: { [C1]: conElementos(), [C2]: conElementos() },
      otras: {
        "GET /api/v1/auth/totp": () => Response.json(TOTP_ACTIVO),
        "POST /api/v1/auth/totp/baja": () => new Response(null, { status: 204 }),
      },
    });

  async function darDeAltaYVolver() {
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await screen.findByText("Nada pendiente.");
    fireEvent.click(screen.getByRole("link", { name: "Ir al alta" }));
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar la confirmación con código" }));
    fireEvent.click(await screen.findByRole("button", { name: "Desactivar" }));
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await screen.findByText("Listo: la confirmación con código quedó desactivada.");
  }

  it("sigue en el edificio con el que había llegado", async () => {
    const simulado = conAlta();
    vi.stubGlobal("fetch", simulado);
    abrir({ pathname: "/pagos", state: { consorcio: C2 } });

    await screen.findByText("Nada pendiente.");
    const antes = pedidosA(simulado, bandejaDe(C2));
    expect(antes).toBeGreaterThan(0);
    await darDeAltaYVolver();
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C2))).toBeGreaterThan(antes));
    expect(pedidosA(simulado, bandejaDe(C1))).toBe(0);
  });

  it("control: si llegó sin edificio, sigue en el primero", async () => {
    const simulado = conAlta();
    vi.stubGlobal("fetch", simulado);
    abrir("/pagos");

    await screen.findByText("Nada pendiente.");
    const antes = pedidosA(simulado, bandejaDe(C1));
    expect(antes).toBeGreaterThan(0);
    await darDeAltaYVolver();
    await waitFor(() => expect(pedidosA(simulado, bandejaDe(C1))).toBeGreaterThan(antes));
    expect(pedidosA(simulado, bandejaDe(C2))).toBe(0);
  });
});

describe("salidas de la bandeja según el rol", () => {
  // «Proveedores» también está en la navegación: se mira el de la bandeja, que es el último.
  const clase = (nombre: string) => screen.getAllByRole("link", { name: nombre }).at(-1)?.classList;
  // Comentario 24 (09-11-2026): un solo botón lleno; los demás con borde, sin relleno.
  const principal = (nombre: string) => clase(nombre)?.contains("enlace-boton--principal");
  const conBorde = (nombre: string) => clase(nombre)?.contains("enlace-boton--borde");

  it("quien prepara: «Preparar una orden» manda; consultar y proveedores van detrás", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ADMINISTRA }, bandejas: { [C1]: conElementos() } }));
    abrir("/pagos");

    await screen.findByRole("link", { name: "Consultar pagos" });
    await screen.findByRole("link", { name: "Preparar una orden" });
    expect(principal("Preparar una orden")).toBe(true);
    expect(conBorde("Preparar una orden")).toBe(false);
    expect(conBorde("Consultar pagos")).toBe(true);
    expect(conBorde("Proveedores")).toBe(true);
    expect(principal("Consultar pagos")).toBe(false);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Cotejar con el banco" })).toBeNull());
  });

  it("quien concilia, con pagos por cotejar: «Cotejar con el banco» manda y no ofrece preparar", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONSEJO }, bandejas: { [C1]: conPorCotejar(2) } }));
    abrir("/pagos");

    await screen.findByRole("link", { name: "Cotejar con el banco" });
    expect(principal("Cotejar con el banco")).toBe(true);
    expect(conBorde("Consultar pagos")).toBe(true);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Preparar una orden" })).toBeNull());
  });
});
