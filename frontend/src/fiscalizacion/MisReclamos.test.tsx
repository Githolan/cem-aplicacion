import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "../api/tipos";
import { alcanza } from "../accionesDePrueba";
import { App } from "../App";
import { ContextoVersion } from "../version";
import { type Borrador, guardarBorrador, leerBorradores, PREFIJO_BORRADOR } from "./borradorReclamo";
import { RECLAMAR_SALDO, RUTA_MIS_EXPENSAS } from "./MisExpensas";
import {
  BORRADOR_SIN_ENVIAR,
  CARGANDO_BORRADORES,
  CITAR_LA_VIGENTE,
  CONFLICTO_REEMPLAZADA,
  DESCARTAR,
  ENVIAR_AHORA,
  HACER_RECLAMO,
  MIS_RECLAMOS,
  NO_SE_ENVIO,
  PERMISO_VENCIDO,
  POR_ENVIAR,
  RETIRAR_RECLAMO,
  SALIERON,
  SALIO,
  SEGUIR_ESCRIBIENDO,
  SIN_CONEXION_BORRADOR,
  SIN_CONEXION_SIN_GUARDAR,
  SIN_GUARDAR,
  SIN_PERSONA,
  SOBRE_LA_REEMPLAZADA,
  TEXTO_CAMPO,
} from "./textosReclamos";

// Mis reclamos dentro de «Mis expensas» (RF34 y RF38, app 0123; PA16). Datos SINTETICOS: ningún
// edificio, unidad, persona ni importe real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const U2 = "33333333-3333-4333-8333-333333333333";
const OFICIAL = "44444444-4444-4444-8444-444444444444";
const REEMPLAZO = "66666666-6666-4666-8666-666666666666";
const R1 = "77777777-7777-4777-8777-777777777777";
const BASE = `/api/v1/consorcios/${C1}`;
const RECLAMOS = `${BASE}/reclamos-liquidacion`;
const TEXTO = "El saldo anterior no coincide con lo que pagué. PRUEBA SINTETICA.";
/** La persona de la sesión, y otra que usó antes el mismo navegador (copropietaria de la unidad). */
const YO = "88888888-8888-4888-8888-888888888888";
const OTRA = "55555555-5555-4555-8555-555555555555";
const SESION = { persona_id: YO, nombre: "Beto SINTETICO", correo: "beto@ejemplo.com", sesion_id: "s1", vence_en: "2026-12-01T00:00:00Z" };

type MiCuenta = components["schemas"]["MiCuenta"];
type Reclamo = components["schemas"]["Reclamo"];
type Lista = components["schemas"]["ListaReclamos"];

const PERIODOS = [{ periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z" }];
const unidad = (extra: Partial<components["schemas"]["UnidadEnLaLiquidacion"]>) => ({
  unidad_id: U1, designacion: "1-A", figura: true, saldo_anterior: "100.00", pagos: "100.00", deuda: "0.00",
  interes: "0.00", disponible: null, grupos: [], otros_gastos: null, redondeo: null, total: "1326.00", ...extra,
});  // prettier-ignore
const cuentaDe = (importacion: string): MiCuenta => ({
  periodos: PERIODOS,
  liquidacion: {
    importacion_id: importacion, periodo: "2026-10-01", confirmada_en: "2026-11-05T15:00:00Z", vencimientos: [],
    unidades: [unidad({}), unidad({ unidad_id: U2, designacion: "1° A", figura: false, saldo_anterior: null, pagos: null, deuda: null, interes: null, total: null })],
  },
});  // prettier-ignore
const RECLAMO: Reclamo = {
  id: R1, liquidacion_id: OFICIAL, periodo: "2026-10-01", liquidacion_vigente: true, unidad_id: U1, designacion: "1-A",
  campo: "saldo_unidad", presentado_por_nombre: "Beto SINTETICO", presentado_en: "2026-11-22T10:00:00Z", estado: "presentado",
  propio: true, texto: TEXTO, acciones: ["retirar"],
  historia: [{ estado: "presentado", texto: null, solicitud_id: null, solicitud_numero: null, registrado_por_nombre: "Beto SINTETICO", registrado_en: "2026-11-22T10:00:00Z" }],
};  // prettier-ignore
const resumen = ({ id, liquidacion_id, periodo, liquidacion_vigente, unidad_id, designacion, campo, presentado_por_nombre, presentado_en, estado, propio }: Reclamo) =>
  ({ id, liquidacion_id, periodo, liquidacion_vigente, unidad_id, designacion, campo, presentado_por_nombre, presentado_en, estado, propio });  // prettier-ignore
const lista = (elementos: Reclamo[] = []): Lista => ({ elementos: elementos.map(resumen), atiende: false, largo_texto: 2000, largo_motivo: 500 });

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
const PROPIETARIA = alcanza("cuenta.ver_propia", "reclamos_liquidacion.presentar", "reclamos_liquidacion.ver");
const error = (estado: number, codigo: string) => Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status: estado });

function respuestas(extra: Respuestas, cuenta: () => MiCuenta = () => cuentaDe(OFICIAL)): Respuestas {
  return {
    "GET /api/v1/me/consorcios": CONSORCIOS,
    "GET /api/v1/auth/sesion": () => Response.json(SESION),
    [`GET ${BASE}/mis-acciones`]: PROPIETARIA,
    [`GET ${BASE}/mi-cuenta`]: () => Response.json(cuenta()),
    [`GET ${RECLAMOS}`]: () => Response.json(lista()),
    ...extra,
  };
}

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_MIS_EXPENSAS]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


async function escribirDesdeElSaldo() {
  const propia = await screen.findByRole("region", { name: "Unidad 1-A" });
  fireEvent.click(within(propia).getByRole("button", { name: RECLAMAR_SALDO }));
  const v = await screen.findByRole("dialog", { name: HACER_RECLAMO });
  fireEvent.change(within(v).getByLabelText("Reclamo"), { target: { value: TEXTO } });
  return v;
}

const borradoresGuardados = () => Object.keys(localStorage).filter((k) => k.startsWith(PREFIJO_BORRADOR));

describe("reclamos en /mis-expensas", () => {
  it("desde el saldo abre la ventana con el dato citado, guarda el borrador mientras se escribe y lo envía", async () => {
    const api = backend(respuestas({ [`POST ${RECLAMOS}`]: () => Response.json(RECLAMO, { status: 201 }) }));
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    const v = await escribirDesdeElSaldo();
    expect((within(v).getByLabelText("Dato que se cita") as HTMLSelectElement).value).toBe("saldo_unidad");
    // RF38: lo escrito queda guardado en el dispositivo, y se dice.
    expect(within(v).getByText(new RegExp(`^${BORRADOR_SIN_ENVIAR}`))).toBeTruthy();
    expect(borradoresGuardados()).toHaveLength(1);
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(within(v).getByRole("button", { name: "Enviar el reclamo" }));
    expect(await screen.findByText(SALIO)).toBeTruthy();
    const [envio] = pedidos(api, "POST", RECLAMOS);
    expect(await envio?.json()).toEqual({ liquidacion_id: OFICIAL, unidad_id: U1, campo: "saldo_unidad", texto: TEXTO, acepta_reemplazada: false });
    expect(envio?.headers.get("Idempotency-Key")).toBeTruthy();
    // Recibido: el borrador ya no está y la lista se vuelve a pedir.
    expect(borradoresGuardados()).toHaveLength(0);
    await waitFor(() => expect(pedidos(api, "GET", RECLAMOS).length).toBeGreaterThanOrEqual(2));
  });

  it("la unidad que no figura solo tiene «Hacer un reclamo», sin dato que citar", async () => {
    vi.stubGlobal("fetch", backend(respuestas({})));
    abrir();
    const ajena = await screen.findByRole("region", { name: "Unidad 1° A" });
    expect(within(ajena).queryByRole("button", { name: RECLAMAR_SALDO })).toBeNull();
    fireEvent.click(within(ajena).getByRole("button", { name: HACER_RECLAMO }));
    const v = await screen.findByRole("dialog", { name: HACER_RECLAMO });
    expect(within(v).queryByLabelText("Dato que se cita")).toBeNull();
    expect(within(v).getByText("Unidad 1° A, liquidación de 10/2026.")).toBeTruthy();
  });

  it("sin conexión queda para enviar y sale solo al volver la conexión, con la misma clave", async () => {
    let intentos = 0;
    const api = backend(
      respuestas({
        [`POST ${RECLAMOS}`]: () => {
          intentos += 1;
          if (intentos === 1) throw new TypeError("Failed to fetch");
          return Response.json(RECLAMO, { status: 201 });
        },
      }),
    );
    vi.stubGlobal("fetch", api);
    abrir();
    const v = await escribirDesdeElSaldo();
    fireEvent.click(within(v).getByRole("button", { name: "Enviar el reclamo" }));
    expect(await screen.findByText(SIN_CONEXION_BORRADOR)).toBeTruthy();
    const filas = screen.getByRole("list", { name: "Borradores sin enviar" });
    expect(within(filas).getByText(POR_ENVIAR)).toBeTruthy();
    // Vuelve la conexión: sale solo, con la clave del primer intento.
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(await screen.findByText(SALIERON(1))).toBeTruthy();
    const [primero, segundo] = pedidos(api, "POST", RECLAMOS);
    expect(segundo?.headers.get("Idempotency-Key")).toBe(primero?.headers.get("Idempotency-Key"));
    expect(screen.queryByRole("list", { name: "Borradores sin enviar" })).toBeNull();
    expect(borradoresGuardados()).toHaveLength(0);
  });

  it("PA16: con el permiso vencido el servidor rechaza el borrador al sincronizar; queda marcado y solo se descarta", async () => {
    // Un borrador que quedó para enviar mientras no había conexión; al abrir, sale y se rechaza.
    const pendiente: Borrador = {
      id: "b1", autor: YO, consorcio_id: C1, liquidacion_id: OFICIAL, periodo: "2026-10-01", unidad_id: U1, designacion: "1-A",
      campo: "saldo_unidad", texto: TEXTO, acepta_reemplazada: false, guardado_en: "2026-11-20T10:00:00Z",
      estado: { tipo: "por_enviar", clave: "clave-del-borrador" },
    };  // prettier-ignore
    expect(guardarBorrador(pendiente)).toBe(true);
    const api = backend(respuestas({ [`POST ${RECLAMOS}`]: () => error(403, "sin_permiso") }));
    vi.stubGlobal("fetch", api);
    const { container } = abrir();
    expect(await screen.findByText(PERMISO_VENCIDO)).toBeTruthy();
    expect(pedidos(api, "POST", RECLAMOS)[0]?.headers.get("Idempotency-Key")).toBe("clave-del-borrador");
    const filas = screen.getByRole("list", { name: "Borradores sin enviar" });
    expect(within(filas).getByText(NO_SE_ENVIO)).toBeTruthy();
    // Nunca se borra solo.
    expect(leerBorradores(YO, C1)?.[0]?.estado).toEqual({ tipo: "rechazado", motivo: PERMISO_VENCIDO });
    fireEvent.click(within(filas).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    // Un envío que se sabe que falla no se ofrece: solo descartar (y cerrar).
    expect(within(ficha).queryByRole("button", { name: ENVIAR_AHORA })).toBeNull();
    expect(within(ficha).queryByRole("button", { name: SEGUIR_ESCRIBIENDO })).toBeNull();
    expect(within(ficha).getAllByText(PERMISO_VENCIDO).length).toBeGreaterThan(0);
    expect((await axe.run(container)).violations).toEqual([]);
    fireEvent.click(within(ficha).getByRole("button", { name: DESCARTAR }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Borradores sin enviar" })).toBeNull());
    expect(borradoresGuardados()).toHaveLength(0);
    // Un solo intento: el rechazado no se vuelve a mandar.
    expect(pedidos(api, "POST", RECLAMOS)).toHaveLength(1);
  });

  it("los borradores de otra persona en este navegador no se muestran ni se mandan con esta cuenta; uno sin autor se descarta", async () => {
    const deOtra: Borrador = {
      id: "de-otra", autor: OTRA, consorcio_id: C1, liquidacion_id: OFICIAL, periodo: "2026-10-01", unidad_id: U1, designacion: "1-A",
      campo: null, texto: "Reclamo de OTRA persona SINTETICO", acepta_reemplazada: false, guardado_en: "2026-11-20T10:00:00Z",
      estado: { tipo: "por_enviar", clave: "clave-de-otra" },
    };  // prettier-ignore
    expect(guardarBorrador(deOtra)).toBe(true);
    // El formato anterior, sin autor: nadie puede decir de quién es.
    const sinAutor: Partial<Borrador> = { ...deOtra, id: "sin-autor", estado: { tipo: "por_enviar", clave: "clave-sin-autor" } };
    delete sinAutor.autor;
    localStorage.setItem(`${PREFIJO_BORRADOR}${C1}:sin-autor`, JSON.stringify(sinAutor));
    const api = backend(respuestas({ [`POST ${RECLAMOS}`]: () => Response.json(RECLAMO, { status: 201 }) }));
    vi.stubGlobal("fetch", api);
    abrir();
    await screen.findByRole("heading", { name: MIS_RECLAMOS });
    // Los borradores se leen recién al conocer la persona: el sin autor se borra entonces y el de la
    // otra persona queda (es suyo).
    await waitFor(() => expect(borradoresGuardados()).toEqual([`${PREFIJO_BORRADOR}${OTRA}:${C1}:de-otra`]));
    // Dejar correr la sincronización que sigue a esa lectura.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(pedidos(api, "POST", RECLAMOS)).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Borradores sin enviar" })).toBeNull();
    expect(screen.queryByText(/OTRA persona/)).toBeNull();
  });

  it("si no se puede leer quién es la persona, lo dice y no ofrece reclamar ni muestra borradores", async () => {
    const propio: Borrador = {
      id: "propio", autor: YO, consorcio_id: C1, liquidacion_id: OFICIAL, periodo: "2026-10-01", unidad_id: U1, designacion: "1-A",
      campo: null, texto: TEXTO, acepta_reemplazada: false, guardado_en: "2026-11-20T10:00:00Z", estado: { tipo: "por_enviar", clave: "k" },
    };  // prettier-ignore
    expect(guardarBorrador(propio)).toBe(true);
    const api = backend(respuestas({ "GET /api/v1/auth/sesion": () => error(500, "error_interno") }));
    vi.stubGlobal("fetch", api);
    abrir();
    const seccion = await screen.findByRole("region", { name: MIS_RECLAMOS });
    expect((await within(seccion).findByRole("alert")).textContent).toBe(SIN_PERSONA);
    const propia = screen.getByRole("region", { name: "Unidad 1-A" });
    expect(within(propia).queryByRole("button", { name: HACER_RECLAMO })).toBeNull();
    expect(within(propia).queryByRole("button", { name: RECLAMAR_SALDO })).toBeNull();
    expect(screen.queryByRole("list", { name: "Borradores sin enviar" })).toBeNull();
    expect(screen.queryByText(CARGANDO_BORRADORES)).toBeNull();
    expect(pedidos(api, "POST", RECLAMOS)).toHaveLength(0);
  });

  it("mientras se sabe quién es la persona, no hay botón de reclamo: hay una línea de carga", async () => {
    let soltar: () => void = () => undefined;
    const espera = new Promise<void>((r) => {
      soltar = r;
    });
    vi.stubGlobal("fetch", backend(respuestas({ "GET /api/v1/auth/sesion": async () => (await espera, Response.json(SESION)) })));
    abrir();
    const seccion = await screen.findByRole("region", { name: MIS_RECLAMOS });
    expect(within(seccion).getByText(CARGANDO_BORRADORES)).toBeTruthy();
    const propia = screen.getByRole("region", { name: "Unidad 1-A" });
    expect(within(propia).queryByRole("button", { name: HACER_RECLAMO })).toBeNull();
    soltar();
    expect(await within(propia).findByRole("button", { name: HACER_RECLAMO })).toBeTruthy();
    expect(within(seccion).queryByText(CARGANDO_BORRADORES)).toBeNull();
  });

  it("con la liquidación reemplazada desde el borrador, ofrece citar la vigente y lo manda con ella", async () => {
    let envios = 0;
    const api = backend(
      respuestas(
        {
          [`POST ${RECLAMOS}`]: () => {
            envios += 1;
            return envios === 1 ? error(409, "liquidacion_reemplazada") : Response.json({ ...RECLAMO, liquidacion_id: REEMPLAZO }, { status: 201 });
          },
        },
        () => {
          // Reemplazada después del primer envío: desde entonces la vigente es la otra.
          return cuentaDe(envios === 0 ? OFICIAL : REEMPLAZO);
        },
      ),
    );
    vi.stubGlobal("fetch", api);
    abrir();
    const v = await escribirDesdeElSaldo();
    fireEvent.click(within(v).getByRole("button", { name: "Enviar el reclamo" }));
    expect(await screen.findByText(CONFLICTO_REEMPLAZADA)).toBeTruthy();
    const filas = await screen.findByRole("list", { name: "Borradores sin enviar" });
    fireEvent.click(within(filas).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    expect(within(ficha).getByRole("button", { name: SOBRE_LA_REEMPLAZADA })).toBeTruthy();
    fireEvent.click(await within(ficha).findByRole("button", { name: CITAR_LA_VIGENTE }));
    const otra = await screen.findByRole("dialog", { name: HACER_RECLAMO });
    expect((within(otra).getByLabelText("Reclamo") as HTMLTextAreaElement).value).toBe(TEXTO);
    fireEvent.click(within(otra).getByRole("button", { name: "Enviar el reclamo" }));
    expect(await screen.findByText(SALIO)).toBeTruthy();
    const [primero, segundo] = pedidos(api, "POST", RECLAMOS);
    expect(((await primero?.json()) as { liquidacion_id: string }).liquidacion_id).toBe(OFICIAL);
    expect(((await segundo?.json()) as { liquidacion_id: string }).liquidacion_id).toBe(REEMPLAZO);
    expect(borradoresGuardados()).toHaveLength(0);
  });

  it("si el navegador no deja guardar el borrador, lo dice en la ventana", async () => {
    vi.stubGlobal("fetch", backend(respuestas({})));
    abrir();
    await screen.findByRole("region", { name: "Unidad 1-A" });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("lleno", "QuotaExceededError");
    });
    const v = await escribirDesdeElSaldo();
    expect(within(v).getByRole("alert").textContent).toBe(SIN_GUARDAR);
    expect(within(v).queryByText(new RegExp(`^${BORRADOR_SIN_ENVIAR}`))).toBeNull();
  });

  it("tras un 5xx, reintentar desde la misma ventana abierta lleva la misma clave", async () => {
    let envios = 0;
    const api = backend(
      respuestas({
        [`POST ${RECLAMOS}`]: () => {
          envios += 1;
          return envios === 1 ? error(503, "no_disponible") : Response.json(RECLAMO, { status: 201 });
        },
      }),
    );
    vi.stubGlobal("fetch", api);
    abrir();
    const v = await escribirDesdeElSaldo();
    fireEvent.click(within(v).getByRole("button", { name: "Enviar el reclamo" }));
    await waitFor(() => expect(pedidos(api, "POST", RECLAMOS)).toHaveLength(1));
    // La ventana sigue abierta con lo escrito; se vuelve a tocar «Enviar».
    const enviar = await within(v).findByRole("button", { name: "Enviar el reclamo" });
    await waitFor(() => expect((enviar as HTMLButtonElement).disabled).toBe(false));
    expect((within(v).getByLabelText("Reclamo") as HTMLTextAreaElement).value).toBe(TEXTO);
    fireEvent.click(enviar);
    expect(await screen.findByText(SALIO)).toBeTruthy();
    const [primero, segundo] = pedidos(api, "POST", RECLAMOS);
    expect(primero?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(segundo?.headers.get("Idempotency-Key")).toBe(primero?.headers.get("Idempotency-Key"));
  });

  it("sin conexión y sin poder guardar, la ventana sigue abierta con el texto y dice que no se guardó nada", async () => {
    const api = backend(
      respuestas({
        [`POST ${RECLAMOS}`]: () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    vi.stubGlobal("fetch", api);
    abrir();
    await screen.findByRole("region", { name: "Unidad 1-A" });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("lleno", "QuotaExceededError");
    });
    const v = await escribirDesdeElSaldo();
    fireEvent.click(within(v).getByRole("button", { name: "Enviar el reclamo" }));
    expect(await within(v).findByText(SIN_CONEXION_SIN_GUARDAR)).toBeTruthy();
    expect(pedidos(api, "POST", RECLAMOS)).toHaveLength(1);
    expect(screen.getByRole("dialog", { name: HACER_RECLAMO })).toBe(v);
    expect((within(v).getByLabelText("Reclamo") as HTMLTextAreaElement).value).toBe(TEXTO);
    expect(screen.queryByText(SIN_CONEXION_BORRADOR)).toBeNull();
    expect(borradoresGuardados()).toHaveLength(0);
  });

  it("lista los reclamos propios con su estado y retira uno con el motivo", async () => {
    const api = backend(
      respuestas({
        [`GET ${RECLAMOS}`]: () => Response.json(lista([RECLAMO])),
        [`GET ${RECLAMOS}/${R1}`]: () => Response.json(RECLAMO),
        [`POST ${RECLAMOS}/${R1}/retiro`]: () => Response.json({ ...RECLAMO, estado: "retirado", acciones: [] }),
      }),
    );
    vi.stubGlobal("fetch", api);
    abrir();
    const filas = await screen.findByRole("list", { name: MIS_RECLAMOS });
    expect(within(filas).getByText("Presentado")).toBeTruthy();
    fireEvent.click(within(filas).getByRole("button"));
    const ficha = await screen.findByRole("dialog");
    expect(within(ficha).getByText(TEXTO_CAMPO.saldo_unidad)).toBeTruthy();
    fireEvent.click(within(ficha).getByRole("button", { name: RETIRAR_RECLAMO }));
    const v = await screen.findByRole("dialog", { name: RETIRAR_RECLAMO });
    // Sin motivo se marca y no se manda nada.
    fireEvent.click(within(v).getByRole("button", { name: "Sí, retirar" }));
    expect(await within(v).findByText("Falta escribir el motivo.")).toBeTruthy();
    expect(pedidos(api, "POST", `${RECLAMOS}/${R1}/retiro`)).toHaveLength(0);
    fireEvent.change(within(v).getByLabelText("Motivo"), { target: { value: "Ya lo aclaré." } });
    fireEvent.click(within(v).getByRole("button", { name: "Sí, retirar" }));
    expect(await screen.findByText("Listo: el reclamo quedó retirado.")).toBeTruthy();
    expect(await pedidos(api, "POST", `${RECLAMOS}/${R1}/retiro`)[0]?.json()).toEqual({ motivo: "Ya lo aclaré." });
  });

  it("sin la acción de presentar no hay botones de reclamo ni sección de reclamos", async () => {
    const api = backend({ ...respuestas({}), [`GET ${BASE}/mis-acciones`]: alcanza("cuenta.ver_propia") });
    vi.stubGlobal("fetch", api);
    abrir();
    const propia = await screen.findByRole("region", { name: "Unidad 1-A" });
    expect(within(propia).queryByRole("button", { name: HACER_RECLAMO })).toBeNull();
    expect(screen.queryByRole("heading", { name: MIS_RECLAMOS })).toBeNull();
    expect(pedidos(api, "GET", RECLAMOS)).toHaveLength(0);
  });
});
