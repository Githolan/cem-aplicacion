import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { DEJAR_DE_AYUDAR, LIMITE_AYUDA, textoAvisoAyuda, textoAyudaRevocada } from "./AvisoAyuda";
import {
  AUTORIZAR,
  AYUDAR_A,
  cambiarAlcance,
  CONFIRMAR_AUTORIZAR,
  CONFIRMAR_REVOCAR,
  CORREO,
  FALTA_ALCANCE,
  FALTA_CORREO,
  REVOCAR,
  RUTA_AYUDA,
  SIN_ALMACEN,
  mientrasSeAyuda,
  SIN_NADA,
  TEXTO_ALCANCE,
  textoAutorizada,
  textoRevocada,
} from "./Ayuda";
import { type Ayuda, autorDeBorradores, ayudaEnCurso, conAyuda, ENCABEZADO_AYUDA, recargas } from "./estadoAyuda";

// La página /ayuda y el aviso de la ayuda (RF53, app 0127). Datos SINTETICOS: ningún edificio ni
// persona real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "22222222-2222-4222-8222-222222222222";
const A1 = "55555555-5555-4555-8555-555555555555";
const A2 = "66666666-6666-4666-8666-666666666666";
const R1 = "77777777-7777-4777-8777-777777777777";
const BASE = `/api/v1/consorcios/${C1}`;
const AYUDAS = `${BASE}/ayudas`;
const CLAVE = "cem:ayuda";

type Otorgada = components["schemas"]["AyudaOtorgada"];
const VIGENTE: Otorgada = {
  id: A1, autorizado_nombre: "Beto SINTETICO", alcances: ["consultar_expensas"], otorgada_en: "2026-11-20T10:00:00Z", revocada_en: null,
};  // prettier-ignore
const REVOCADA: Otorgada = { ...VIGENTE, id: A2, autorizado_nombre: "Carla SINTETICA", revocada_en: "2026-11-21T10:00:00Z" };
const LISTA = { elementos: [VIGENTE, REVOCADA], alcances_otorgables: ["consultar_expensas", "preparar_reclamos"] };
const RECIBIDA = {
  id: R1, consorcio_id: C2, consorcio_nombre: "Torre SINTETICA", otorgante_nombre: "Dora SINTETICA",
  alcances: ["consultar_expensas", "preparar_reclamos"], otorgada_en: "2026-11-20T10:00:00Z",
};  // prettier-ignore
const EN_CURSO: Ayuda = { autorizacion: R1, consorcio: C2, edificio: "Torre SINTETICA", asistida: "Dora SINTETICA" };

type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

const pedidos = (simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] =>
  simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);

const CONSORCIOS = () => Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [] }]);
const PROPIETARIO = alcanza("ayuda.otorgar", "ayuda.revocar", "ayuda.ver_otorgadas");
const SIN_RECIBIDAS = () => Response.json({ elementos: [] });
const error = (estado: number, codigo: string, detalle: unknown = null) =>
  Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status: estado });

function abrir(ruta = RUTA_AYUDA) {
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

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("estado de la ayuda", () => {
  it("marcar un alcance marca lo que exige y desmarcar lo exigido desmarca lo que lo exige", () => {
    expect(cambiarAlcance([], "preparar_reclamos", true).sort()).toEqual(["consultar_expensas", "preparar_reclamos"]);
    expect(cambiarAlcance(["consultar_expensas", "preparar_reclamos"], "consultar_expensas", false)).toEqual([]);
    expect(cambiarAlcance(["consultar_expensas", "preparar_reclamos"], "preparar_reclamos", false)).toEqual(["consultar_expensas"]);
  });

  it("lo guardado sin la forma se descarta, y los borradores ayudando son de quien ayuda para esa autorización", () => {
    window.sessionStorage.setItem(CLAVE, "{roto");
    expect(ayudaEnCurso()).toBeNull();
    expect(window.sessionStorage.getItem(CLAVE)).toBeNull();
    window.sessionStorage.setItem(CLAVE, JSON.stringify({ autorizacion: R1 }));
    expect(ayudaEnCurso()).toBeNull();
    expect(autorDeBorradores("p1")).toBe("p1");
    window.sessionStorage.setItem(CLAVE, JSON.stringify(EN_CURSO));
    expect(autorDeBorradores("p1")).toBe(`p1:ayuda:${R1}`);
  });

  it("el encabezado va solo al edificio de la ayuda y al selector, nunca a otro edificio ni a la cuenta", () => {
    const encabezado = (camino: string) => conAyuda(new Request(`https://cem.test${camino}`)).headers.get(ENCABEZADO_AYUDA);
    expect(encabezado(`/api/v1/consorcios/${C2}/mis-acciones`)).toBeNull();
    window.sessionStorage.setItem(CLAVE, JSON.stringify(EN_CURSO));
    expect(encabezado(`/api/v1/consorcios/${C2}/mis-acciones`)).toBe(R1);
    expect(encabezado("/api/v1/me/consorcios")).toBe(R1);
    expect(encabezado(`/api/v1/consorcios/${C1}/mis-acciones`)).toBeNull();
    expect(encabezado("/api/v1/me/ayudas")).toBeNull();
    expect(encabezado("/api/v1/auth/sesion")).toBeNull();
    expect(encabezado(`/api/v1/consorcios/${C2}`)).toBeNull();
  });
});

describe("pantalla /ayuda", () => {
  it("el propietario ve quién puede ayudar; la revocada no ofrece revocar", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: PROPIETARIO,
        "GET /api/v1/me/ayudas": SIN_RECIBIDAS,
        [`GET ${AYUDAS}`]: () => Response.json(LISTA),
      }),
    );
    const { container } = abrir();
    const lista = await screen.findByRole("list", { name: "Autorizaciones para ayudar" });
    expect(within(lista).getAllByRole("button")).toHaveLength(2);
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(within(lista).getByRole("button", { name: /Carla SINTETICA/ }));
    const ficha = within(await screen.findByRole("dialog"));
    expect(ficha.getByText(/Revocada el/)).toBeTruthy();
    expect(ficha.queryByRole("button", { name: REVOCAR })).toBeNull();
  });

  it("autorizar marca lo que falta, marca el correo sin cuenta y, con todo, manda el alcance elegido", async () => {
    let intento = 0;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIO,
      "GET /api/v1/me/ayudas": SIN_RECIBIDAS,
      [`GET ${AYUDAS}`]: () => Response.json(LISTA),
      [`POST ${AYUDAS}`]: () =>
        ++intento === 1
          ? error(422, "ayuda_sin_cuenta", { campos: ["body.correo"] })
          : Response.json({ ...VIGENTE, id: R1, autorizado_nombre: "Eva SINTETICA", alcances: ["consultar_expensas", "preparar_reclamos"] }, { status: 201 }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: AUTORIZAR }));
    const ventana = within(await screen.findByRole("dialog", { name: AUTORIZAR }));
    fireEvent.click(ventana.getByRole("button", { name: CONFIRMAR_AUTORIZAR }));
    expect(await ventana.findByText(FALTA_CORREO)).toBeTruthy();
    expect(ventana.getByText(FALTA_ALCANCE)).toBeTruthy();
    expect(pedidos(api, "POST", AYUDAS)).toHaveLength(0);
    fireEvent.change(ventana.getByLabelText(CORREO), { target: { value: "eva@ejemplo.test" } });
    fireEvent.click(ventana.getByRole("checkbox", { name: TEXTO_ALCANCE.preparar_reclamos }));
    expect((ventana.getByRole("checkbox", { name: TEXTO_ALCANCE.consultar_expensas }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(ventana.getByRole("button", { name: CONFIRMAR_AUTORIZAR }));
    expect(await ventana.findByText(/no tiene una cuenta en Consorcio en Mano/, { selector: ".error-de-campo" })).toBeTruthy();
    fireEvent.click(ventana.getByRole("button", { name: CONFIRMAR_AUTORIZAR }));
    expect(await screen.findByText(textoAutorizada("Eva SINTETICA"))).toBeTruthy();
    const [, segundo] = pedidos(api, "POST", AYUDAS);
    expect(await segundo?.json()).toEqual({ correo: "eva@ejemplo.test", alcances: ["preparar_reclamos", "consultar_expensas"] });
    expect(segundo?.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("revocar pide confirmación y manda la revocación de esa autorización", async () => {
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIO,
      "GET /api/v1/me/ayudas": SIN_RECIBIDAS,
      [`GET ${AYUDAS}`]: () => Response.json(LISTA),
      [`POST ${AYUDAS}/${A1}/revocacion`]: () => Response.json({ ...VIGENTE, revocada_en: "2026-11-22T10:00:00Z" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: /Beto SINTETICO/ }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: REVOCAR }));
    const ventana = within(await screen.findByRole("dialog", { name: /Revocar la autorización de Beto/ }));
    fireEvent.click(ventana.getByRole("button", { name: CONFIRMAR_REVOCAR }));
    expect(await screen.findByText(textoRevocada("Beto SINTETICO"))).toBeTruthy();
    expect(pedidos(api, "POST", `${AYUDAS}/${A1}/revocacion`)).toHaveLength(1);
  });

  it("quien recibió una autorización empieza a ayudar; sin almacenamiento en la pestaña, se dice y no empieza", async () => {
    const aInicio = vi.spyOn(recargas, "aInicio").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json([]),
        "GET /api/v1/me/ayudas": () => Response.json({ elementos: [RECIBIDA] }),
      }),
    );
    const { container } = abrir();
    const boton = await screen.findByRole("button", { name: AYUDAR_A("Dora SINTETICA") });
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(boton);
    expect(aInicio).toHaveBeenCalledTimes(1);
    expect(ayudaEnCurso()).toEqual(EN_CURSO);
    window.sessionStorage.clear();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("bloqueado", "SecurityError");
    });
    fireEvent.click(boton);
    expect(await screen.findByText(SIN_ALMACEN)).toBeTruthy();
    expect(aInicio).toHaveBeenCalledTimes(1);
  });

  it("sin autorizaciones recibidas ni unidades propias, lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ "GET /api/v1/me/consorcios": () => Response.json([]), "GET /api/v1/me/ayudas": SIN_RECIBIDAS }),
    );
    abrir();
    expect(await screen.findByText(SIN_NADA)).toBeTruthy();
    expect(screen.queryByRole("button", { name: AUTORIZAR })).toBeNull();
  });
});

describe("aviso de la ayuda", () => {
  it("ayudando, cada pantalla dice a quién y el límite; la página no se usa; «Dejar de ayudar» la termina", async () => {
    window.sessionStorage.setItem(CLAVE, JSON.stringify(EN_CURSO));
    const aInicio = vi.spyOn(recargas, "aInicio").mockImplementation(() => undefined);
    const api = backend({
      "GET /api/v1/auth/sesion": () =>
        Response.json({ persona_id: "99999999-9999-4999-8999-999999999999", nombre: "Beto SINTETICO", correo: "beto@ejemplo.test", sesion_id: A1 }),
      "GET /api/v1/me/consorcios": () => Response.json([{ id: C2, nombre: "Torre SINTETICA", vinculos: [] }]),
      [`GET /api/v1/consorcios/${C2}/mis-acciones`]: alcanza("cuenta.ver_propia"),
    });
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    await waitFor(() => expect(pedidos(api, "GET", `/api/v1/consorcios/${C2}/mis-acciones`)).not.toHaveLength(0));
    const aviso = within(screen.getByRole("region", { name: "Ayuda en curso" }));
    expect(aviso.getByText(textoAvisoAyuda("Dora SINTETICA", "Torre SINTETICA"))).toBeTruthy();
    expect(aviso.getByText(LIMITE_AYUDA)).toBeTruthy();
    // Autorizar es un acto propio: ayudando no se ofrece ni se pide nada a la API.
    expect(screen.getByText(mientrasSeAyuda("Dora SINTETICA"))).toBeTruthy();
    expect(screen.getAllByRole("button", { name: DEJAR_DE_AYUDAR })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: AUTORIZAR })).toBeNull();
    expect(pedidos(api, "GET", "/api/v1/me/ayudas")).toHaveLength(0);
    expect(pedidos(api, "GET", "/api/v1/me/consorcios").every((p) => p.headers.get(ENCABEZADO_AYUDA) === R1)).toBe(true);
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(aviso.getByRole("button", { name: DEJAR_DE_AYUDAR }));
    expect(aInicio).toHaveBeenCalledTimes(1);
    expect(ayudaEnCurso()).toBeNull();
  });

  it("si la persona asistida revocó la autorización, la pestaña sale sola de la ayuda y lo dice una vez", async () => {
    window.sessionStorage.setItem(CLAVE, JSON.stringify(EN_CURSO));
    const aInicio = vi.spyOn(recargas, "aInicio").mockImplementation(() => undefined);
    const revocada = () => error(403, "ayuda_revocada");
    const api = backend({
      "GET /api/v1/auth/sesion": () =>
        Response.json({ persona_id: "99999999-9999-4999-8999-999999999999", nombre: "Beto SINTETICO", correo: "beto@ejemplo.test", sesion_id: A1 }),
      "GET /api/v1/me/consorcios": (p) => (p.headers.get(ENCABEZADO_AYUDA) === R1 ? revocada() : CONSORCIOS()),
      [`GET /api/v1/consorcios/${C2}/mis-acciones`]: revocada,
      [`GET ${BASE}/mis-acciones`]: PROPIETARIO,
      "GET /api/v1/me/ayudas": SIN_RECIBIDAS,
      [`GET ${AYUDAS}`]: () => Response.json(LISTA),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    await waitFor(() => expect(aInicio).toHaveBeenCalled());
    expect(ayudaEnCurso()).toBeNull();
    // La recarga: el aviso lo dice, sin el de la ayuda en curso, y no se repite en la próxima.
    cleanup();
    const { container } = abrir();
    expect(await screen.findByText(textoAyudaRevocada("Dora SINTETICA"))).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Ayuda en curso" })).toBeNull();
    expect((await axe.run(container)).violations).toEqual([]);
    cleanup();
    abrir();
    await screen.findByRole("list", { name: "Autorizaciones para ayudar" });
    expect(screen.queryByText(textoAyudaRevocada("Dora SINTETICA"))).toBeNull();
  });
});
