import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { App } from "./App";
import { mensajeAlInvitar } from "./Invitar";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const U1 = "33333333-3333-4333-8333-333333333333";
const U2 = "44444444-4444-4444-8444-444444444444";
const PENDIENTE = "55555555-5555-4555-8555-555555555555";
const ACEPTADA = "66666666-6666-4666-8666-666666666666";
const NUEVA = "77777777-7777-4777-8777-777777777777";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: ["propietario"] }];
const UNIDADES = { elementos: [{ id: U1, designacion: "1° A" }, { id: U2, designacion: "2° B" }] };

function invitacion(id: string, nombre: string, estado: string, correo: string | null) {
  return {
    id,
    unidad_id: U1,
    unidad: "1° A",
    tipo: "inquilino",
    correo,
    nombre,
    estado,
    creada_en: "2026-10-18T12:00:00Z",
    vence_en: "2026-10-25T12:00:00Z",
    aceptada_en: estado === "aceptada" ? "2026-10-19T12:00:00Z" : null,
    revocada_en: null,
  };
}

const LISTA = {
  elementos: [
    invitacion(PENDIENTE, "Beto Pérez", "pendiente", "beto@ejemplo.com"),
    invitacion(ACEPTADA, "Carla Gómez", "aceptada", null),
  ],
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

const RUTA_LISTA = `/api/v1/consorcios/${C1}/invitaciones`;

function conConsorcio(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET /api/v1/consorcios/${C1}/unidades`]: () => Response.json(UNIDADES),
    [`GET ${RUTA_LISTA}`]: () => Response.json(LISTA),
    ...extra,
  });
}

/** El elemento `i`, o la prueba falla: el índice fuera de rango es un error de la prueba. */
function en<T>(lista: T[], i: number): T {
  const elemento = lista[i];
  if (elemento === undefined) throw new Error(`no hay elemento ${i} (hay ${lista.length})`);
  return elemento;
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/invitaciones") {
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

async function completar(nombre = "Dora Ruiz", correo = "dora@ejemplo.com") {
  await screen.findByText("Beto Pérez, 1° A");
  fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: nombre } });
  fireEvent.change(screen.getByLabelText("Correo"), { target: { value: correo } });
}

const invitarBoton = () => screen.getByRole("button", { name: "Invitar" });

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /invitaciones", () => {
  it("el inicio tiene el enlace para invitar", () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ estado: "ok" })));
    abrir("/");
    expect(screen.getByRole("link", { name: "Invitar a una persona" }).getAttribute("href")).toBe(
      "/invitaciones",
    );
  });

  it("sin sesión (401) manda a /entrar", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("muestra todas las unidades, el aviso fijo y el correo solo si la API lo manda", async () => {
    vi.stubGlobal("fetch", conConsorcio());
    abrir();
    await screen.findByText("Beto Pérez, 1° A");
    expect(screen.getByText(/Invitar no acredita el carácter de propietario, inquilino u ocupante/)).toBeTruthy();
    const unidades = within(screen.getByLabelText("Unidad")).getAllByRole("option");
    expect(unidades.map((o) => o.textContent)).toEqual(["1° A", "2° B"]);
    expect(screen.getByText("beto@ejemplo.com")).toBeTruthy();
    const aceptada = screen.getByText("Carla Gómez, 1° A").closest("li") as HTMLElement;
    expect(within(aceptada).queryByText("Correo")).toBeNull();
    expect(within(aceptada).queryByRole("button", { name: "Retirar" })).toBeNull();
  });

  it("invita con CSRF y una clave de reintento, y vuelve a pedir la lista", async () => {
    const simulado = conConsorcio({
      [`POST ${RUTA_LISTA}`]: () =>
        Response.json(invitacion(NUEVA, "Dora Ruiz", "pendiente", "dora@ejemplo.com"), { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.change(screen.getByLabelText("Unidad"), { target: { value: U2 } });
    fireEvent.change(screen.getByLabelText("Vínculo con la unidad"), { target: { value: "ocupante" } });
    fireEvent.click(invitarBoton());
    expect(await screen.findByText("Listo: le mandamos la invitación a Dora Ruiz.")).toBeTruthy();
    const post = en(pedidos(simulado, "POST", RUTA_LISTA), 0);
    expect(post.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await post.json()).toEqual({ unidad_id: U2, tipo: "ocupante", correo: "dora@ejemplo.com", nombre: "Dora Ruiz" });
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_LISTA)).toHaveLength(2));
    expect((screen.getByLabelText("Correo") as HTMLInputElement).value).toBe("");
  });

  it("si se corta la red, el reintento usa la misma clave; si cambian los datos, otra", async () => {
    let caida = true;
    const simulado = conConsorcio({
      [`POST ${RUTA_LISTA}`]: () => {
        if (caida) throw new TypeError("sin red");
        return Response.json(invitacion(NUEVA, "Dora Ruiz", "pendiente", "dora@ejemplo.com"), { status: 201 });
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.click(invitarBoton());
    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
    caida = false;
    fireEvent.click(invitarBoton());
    await screen.findByText(/Listo: le mandamos/);
    const [primera, segunda] = pedidos(simulado, "POST", RUTA_LISTA).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    await completar("Eva Sosa", "eva@ejemplo.com");
    fireEvent.click(invitarBoton());
    await waitFor(() => expect(pedidos(simulado, "POST", RUTA_LISTA)).toHaveLength(3));
    expect(en(pedidos(simulado, "POST", RUTA_LISTA), 2).headers.get("Idempotency-Key")).not.toBe(primera);
  });

  it("si se corta la red y se corrigen los datos, el intento nuevo lleva otra clave", async () => {
    const simulado = conConsorcio({
      [`POST ${RUTA_LISTA}`]: () => {
        throw new TypeError("sin red");
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.click(invitarBoton());
    await screen.findByText(/No pudimos completar el pedido/);
    fireEvent.change(screen.getByLabelText("Correo"), { target: { value: "dora.ruiz@ejemplo.com" } });
    fireEvent.click(invitarBoton());
    await waitFor(() => expect(pedidos(simulado, "POST", RUTA_LISTA)).toHaveLength(2));
    const [primera, segunda] = pedidos(simulado, "POST", RUTA_LISTA).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).not.toBe(primera);
  });

  it.each([
    [403, "sin_permiso", "No tenés permiso para invitar a esa unidad."],
    [403, "csrf_invalido", "No pudimos verificar el pedido. Recargá la página."],
    [404, "no_encontrado", "Esa unidad ya no está en el consorcio. Elegí otra."],
    [409, "idempotencia_en_curso", "La invitación se está procesando. Probá de nuevo en unos segundos."],
    [422, "peticion_invalida", "Revisá los datos: el correo tiene que ser válido y el nombre, de hasta 100 caracteres."],
    [429, "demasiadas_peticiones", "Llegaste al límite de invitaciones por ahora. Probá más tarde."],
  ])("un %i (%s) se explica sin tocar la lista", async (estado, codigo, texto) => {
    const simulado = conConsorcio({ [`POST ${RUTA_LISTA}`]: () => error(codigo, estado) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.click(invitarBoton());
    expect(await screen.findByText(texto)).toBeTruthy();
    expect(pedidos(simulado, "GET", RUTA_LISTA)).toHaveLength(1);
  });

  it("solo el 409 en curso conserva la clave; otro error la renueva", async () => {
    let respuesta = () => error("idempotencia_en_curso", 409);
    const simulado = conConsorcio({ [`POST ${RUTA_LISTA}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.click(invitarBoton());
    await screen.findByText(/se está procesando/);
    respuesta = () => error("sin_permiso", 403);
    fireEvent.click(invitarBoton());
    await screen.findByText(/No tenés permiso/);
    fireEvent.click(invitarBoton());
    await waitFor(() => expect(pedidos(simulado, "POST", RUTA_LISTA)).toHaveLength(3));
    const claves = pedidos(simulado, "POST", RUTA_LISTA).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves[1]).toBe(claves[0]);
    expect(claves[2]).not.toBe(claves[1]);
  });

  it("un 5xx del proxy conserva la clave: el reintento recibe lo que el backend guardó", async () => {
    let respuesta = () => new Response("<html>Bad Gateway</html>", { status: 502 });
    const simulado = conConsorcio({ [`POST ${RUTA_LISTA}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completar();
    fireEvent.click(invitarBoton());
    await waitFor(() => expect(pedidos(simulado, "POST", RUTA_LISTA)).toHaveLength(1));
    await waitFor(() => expect(invitarBoton()).not.toHaveProperty("disabled", true));
    respuesta = () => error("sin_permiso", 403);
    fireEvent.click(invitarBoton());
    await screen.findByText(/No tenés permiso/);
    fireEvent.click(invitarBoton());
    await waitFor(() => expect(pedidos(simulado, "POST", RUTA_LISTA)).toHaveLength(3));
    const claves = pedidos(simulado, "POST", RUTA_LISTA).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves[1]).toBe(claves[0]);
    expect(claves[2]).not.toBe(claves[1]);
  });

  it("retirar pide confirmación; «No» no manda nada y «Sí» retira con CSRF", async () => {
    const ruta = `${RUTA_LISTA}/${PENDIENTE}/revocar`;
    const simulado = conConsorcio({ [`POST ${ruta}`]: () => new Response(null, { status: 204 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText("Beto Pérez, 1° A");
    fireEvent.click(screen.getByRole("button", { name: "Retirar", description: "Beto Pérez, 1° A" }));
    expect(screen.getByText("¿Retirar la invitación de Beto Pérez a la unidad 1° A?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(pedidos(simulado, "POST", ruta)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Retirar", description: "Beto Pérez, 1° A" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirarla" }));
    expect(await screen.findByText("Listo: retiramos la invitación de Beto Pérez.")).toBeTruthy();
    expect(en(pedidos(simulado, "POST", ruta), 0).headers.get("X-CSRF-Token")).toBe("abc123");
    const fila = screen.getByText("Beto Pérez, 1° A").closest("li") as HTMLElement;
    expect(within(fila).getByText("Retirada")).toBeTruthy();
  });

  it("retirar una que ya se aceptó (409) lo explica y la marca aceptada", async () => {
    const ruta = `${RUTA_LISTA}/${PENDIENTE}/revocar`;
    vi.stubGlobal("fetch", conConsorcio({ [`POST ${ruta}`]: () => error("invitacion_no_pendiente", 409) }));
    abrir();
    await screen.findByText("Beto Pérez, 1° A");
    fireEvent.click(screen.getByRole("button", { name: "Retirar", description: "Beto Pérez, 1° A" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirarla" }));
    expect(await screen.findByText(/ya se aceptó/)).toBeTruthy();
    const fila = screen.getByText("Beto Pérez, 1° A").closest("li") as HTMLElement;
    expect(within(fila).getByText("Aceptada")).toBeTruthy();
  });

  it("con dos edificios se elige cuál, y cambia unidades e invitaciones", async () => {
    const simulado = conConsorcio({
      "GET /api/v1/me/consorcios": () =>
        Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
      [`GET /api/v1/consorcios/${C2}/unidades`]: () => Response.json({ elementos: [{ id: U2, designacion: "9° Z" }] }),
      [`GET /api/v1/consorcios/${C2}/invitaciones`]: () => Response.json({ elementos: [] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText("Beto Pérez, 1° A");
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByText("Todavía no hay invitaciones que puedas ver.")).toBeTruthy();
    expect(within(screen.getByLabelText("Unidad")).getAllByRole("option").map((o) => o.textContent)).toEqual(["9° Z"]);
  });

  it("al cambiar de edificio no queda elegida una unidad del anterior", async () => {
    vi.stubGlobal(
      "fetch",
      conConsorcio({
        "GET /api/v1/me/consorcios": () =>
          Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
        [`GET /api/v1/consorcios/${C2}/unidades`]: () => error("error_interno", 500),
        [`GET /api/v1/consorcios/${C2}/invitaciones`]: () => Response.json({ elementos: [] }),
      }),
    );
    abrir();
    await completar();
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
    expect(within(screen.getByLabelText("Unidad")).queryAllByRole("option")).toHaveLength(0);
    expect((invitarBoton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("sin edificios lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => Response.json([]) }));
    abrir();
    expect(await screen.findByText("Todavía no participás de ningún edificio.")).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal("fetch", conConsorcio());
    const { container } = abrir();
    await screen.findByText("Beto Pérez, 1° A");
    fireEvent.click(screen.getByRole("button", { name: "Retirar", description: "Beto Pérez, 1° A" }));
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
  });

  it("las rutas y el encabezado de reintento son los del contrato", () => {
    const rutas = contrato.paths as Record<string, Record<string, { parameters?: { name: string; required?: boolean }[] }> | undefined>;
    const crear = rutas["/api/v1/consorcios/{consorcio_id}/invitaciones"]?.post;
    expect(crear?.parameters?.find((p) => p.name === "Idempotency-Key")?.required).toBe(true);
    expect(rutas["/api/v1/consorcios/{consorcio_id}/invitaciones/{invitacion_id}/revocar"]?.post).toBeTruthy();
    expect(mensajeAlInvitar(500, undefined)).toMatch(/No pudimos completar/);
  });
});
