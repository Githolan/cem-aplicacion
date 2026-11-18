import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { File as ArchivoNode } from "node:buffer";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { accionesDeLasPoliticas, alcanza, alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { CUIT_INVALIDO } from "./CampoCuit";
import { hoyDelDispositivo } from "./archivos";
import { VOLVIO_LA_RED } from "./egresos/comun";
import {
  ALTA_SIN_DECISION_USABLE,
  ALTA_SIN_DECISIONES,
  AYUDA_DESASOCIAR,
  CAUSA_REVISION,
  ALTA_SIN_NADIE,
  CONSEJO_SIN_CANDIDATOS,
  DECISION_ESPERA_EL_ACTA,
  LISTO_DECISION,
  LISTO_DECISION_CON_ACTA,
  mensajeDeError,
  RECIEN_DADO_DE_ALTA,
  REMOCION_SIN_ASAMBLEA,
  SIN_CONDICION_PARA_MANTENER,
} from "./Mandatos";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "88888888-8888-4888-8888-888888888888";
const M_MC = "22222222-2222-4222-8222-222222222222";
const M_AE = "33333333-3333-4333-8333-333333333333";
const D_EQUIPO = "44444444-4444-4444-8444-444444444444";
const D_PROPIA = "55555555-5555-4555-8555-555555555555";
const D_OTRA = "99999999-9999-4999-8999-999999999999";
const VECINA = "66666666-6666-4666-8666-666666666666";
const NUEVO = "77777777-7777-4777-8777-777777777777";
const CUIT = "20123456786";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const BASE = `/api/v1/consorcios/${C1}`;
const RUTA_MANDATOS = `${BASE}/mandatos`;
const RUTA_DECISIONES = `${BASE}/decisiones`;
const RUTA_CANDIDATOS = `${BASE}/mandatos/candidatos`;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];

const MANDATOS = {
  elementos: [
    {
      id: M_MC,
      tipo: "miembro_consejo",
      nombre: "Ana Díaz",
      cuit: null,
      vigente_desde: "2026-01-01",
      vigente_hasta: null,
      estado: "vigente",
      con_cuenta: true,
    },
    {
      id: M_AE,
      tipo: "administrador_externo",
      nombre: "Estudio Sur",
      cuit: CUIT,
      vigente_desde: "2026-02-01",
      vigente_hasta: "2027-02-01",
      estado: "vigente",
      con_cuenta: false,
    },
  ],
};

const DECISIONES = {
  elementos: [
    { id: D_EQUIPO, tipo: "asamblea", fecha: "2026-01-10", descripcion: "Designa al Consejo", registrada_por_vos: false, del_equipo: true },
    { id: D_PROPIA, tipo: "consejo", fecha: "2026-03-05", descripcion: "Contrata al estudio", registrada_por_vos: true, del_equipo: false },
  ],
};

const CANDIDATOS = { elementos: [{ persona_id: VECINA, nombre: "Beto Pérez", unidades: ["1° A"], habilita_consejo: true, mandatos: [] }] };

function detalleDecision(id: string, registradaPorVos: boolean) {
  const resumen = DECISIONES.elementos.find((d) => d.id === id);
  return {
    ...resumen,
    referencia: "Acta 14, libro 3, folio 52",
    sha256: null,
    registrada_por_vos: registradaPorVos,
    del_equipo: !registradaPorVos,
  };
}

const DETALLE_AE = {
  ...MANDATOS.elementos[1],
  es_propio: false,
  condicion_cumplida_hoy: null,
  matricula: "RPA 1234",
  decision_id: D_EQUIPO,
  facultades: ["documentos.cargar"],
  causa_baja: null,
  baja_decision_id: null,
  instrumento_referencia: null,
  motivo_fin: null,
  finalizado_en: null,
};

/** Lo que la API devuelve de lo recién creado: el POST solo responde el id. */
const DECISION_NUEVA = {
  id: NUEVO,
  tipo: "contrato_administracion",
  fecha: "2026-10-01",
  descripcion: "Contrata al estudio",
  referencia: "Contrato del 01-10",
  sha256: null,
  registrada_por_vos: true,
  del_equipo: false,
};

const MANDATO_NUEVO = {
  ...DETALLE_AE,
  id: NUEVO,
  tipo: "miembro_consejo",
  nombre: "Beto Pérez",
  cuit: null,
  matricula: null,
  vigente_desde: "2026-11-01",
  vigente_hasta: null,
  con_cuenta: true,
};

function error(codigo: string, status: number, detalle: Record<string, unknown> | null = null) {
  return Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status });
}

// Una respuesta puede quedar retenida (una promesa) para probar lo que llega tarde.
type Respuestas = Record<string, () => Response | Promise<Response>>;

/** Un backend que responde por «MÉTODO ruta»; lo que no está previsto falla la prueba. */
function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

/** El Consejo: lee las tres listas. */
function conConsejo(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET ${RUTA_MANDATOS}`]: () => Response.json(MANDATOS),
    [`GET ${RUTA_DECISIONES}`]: () => Response.json(DECISIONES),
    [`GET ${RUTA_CANDIDATOS}`]: () => Response.json(CANDIDATOS),
    [`GET ${RUTA_DECISIONES}/${D_EQUIPO}`]: () => Response.json(detalleDecision(D_EQUIPO, false)),
    [`GET ${RUTA_DECISIONES}/${D_PROPIA}`]: () => Response.json(detalleDecision(D_PROPIA, true)),
    [`GET ${RUTA_DECISIONES}/${NUEVO}`]: () => Response.json(DECISION_NUEVA),
    [`GET ${RUTA_MANDATOS}/${NUEVO}`]: () => Response.json(MANDATO_NUEVO),
    // El externo sin cuenta muestra su invitación (G-40): por omisión, ninguna.
    [`GET ${RUTA_MANDATOS}/${M_AE}/invitaciones`]: () => Response.json({ elementos: [] }),
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

function abrir(ruta = "/mandatos") {
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

const cargado = () => screen.findByText("Ana Díaz, Consejo de propietarios");
/** El «Listo» del alta (G-44): dice de quién es el mandato y que ya está en la lista. */
const LISTO_ALTA = /^Listo: el mandato de .+ quedó dado de alta y ya está en la lista\./;
/** La ventana de la baja (comentario 36, MAN-02): se abre con su botón, encima de la ficha. */
const VENTANA_BAJA = /^(Dar de baja el mandato de .+|Anular por error de carga)$/;
async function desplegarBaja() {
  fireEvent.click(await screen.findByRole("button", { name: /^(Dar de baja|Anular por error de carga)…$/ }));
  return screen.findByRole("dialog", { name: VENTANA_BAJA });
}
/** La ficha abierta (MAN-01): una hoja con el título del registro, sola sobre las listas. */
const fichaDe = (titulo: string) => screen.findByRole("dialog", { name: titulo });
/** La ficha con sus datos ya llegados: sin ningún «Consultando…» adentro. */
async function esperarFicha(titulo: string) {
  const ficha = await fichaDe(titulo);
  await waitFor(() => expect(within(ficha).queryByText("Consultando…")).toBeNull());
  return ficha;
}
const FORMULARIOS = ["Dar de alta un mandato", "Registrar una decisión"];
/** Una sección de la pantalla; los dos formularios son ventanas que se abren con su botón (MAN-03). */
function seccion(nombre: string) {
  if (!FORMULARIOS.includes(nombre)) return screen.getByRole("region", { name: nombre });
  if (screen.queryByRole("dialog", { name: nombre }) === null) fireEvent.click(boton(nombre));
  return screen.getByRole("dialog", { name: nombre });
}
const boton = (nombre: string) => screen.getByRole("button", { name: nombre });
/** El nombre de la fila que abre el detalle: empieza con el título y sigue con el resumen. */
const fila = (titulo: string) => new RegExp(`^${titulo}( |$)`);

async function completarAlta(decision = D_EQUIPO) {
  await cargado();
  const alta = seccion("Dar de alta un mandato");
  fireEvent.change(within(alta).getByLabelText("Decisión que lo respalda"), { target: { value: decision } });
  fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2026-11-01" } });
}

/** Abre el detalle del externo y completa la baja por renuncia, sin confirmar. */
async function abrirBaja() {
  await cargado();
  fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
  await desplegarBaja();
  fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Renuncia presentada" } });
  fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
  // jsdom no desplaza la página: el alta lleva su fila a la vista (G-44) y la prueba mira el pedido.
  Element.prototype.scrollIntoView = vi.fn();
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
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /mandatos", () => {
  it("el inicio tiene el enlace a mandatos y decisiones", async () => {
    vi.stubGlobal("fetch", backend(INICIO_CON_SESION));
    abrir("/");
    expect((await within(screen.getByRole("main")).findByRole("link", { name: "Mandatos y decisiones" })).getAttribute("href")).toBe("/mandatos");
  });

  it("sin sesión (401) manda a /entrar", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("un 401 en las listas también manda a /entrar", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({ [`GET ${RUTA_MANDATOS}`]: () => error("no_autenticado", 401) }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
  });

  it("MAN-07: cada fila es un renglón: nombre, tipo y un estado corto; fechas, CUIT y cuenta van en la ficha", async () => {
    const larga = "Designa al Consejo por dos años, con la facultad de contratar y de fiscalizar a la administración del edificio";
    const decisiones = { elementos: [{ ...en(DECISIONES.elementos, 0), descripcion: larga }, en(DECISIONES.elementos, 1)] };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json(decisiones) }));
    abrir();
    await cargado();
    const mandatos = seccion("Mandatos");
    // Cada fila es un solo botón que abre la ficha; su resumen dice lo mínimo.
    const consejo = within(mandatos).getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") });
    expect(consejo.textContent).toBe("Ana Díaz, Consejo de propietarios Vigente");
    const externo = within(mandatos).getByRole("button", { name: fila("Estudio Sur, Administración externa") });
    // Lo único que pide algo: la administración externa sin cuenta.
    expect(externo.textContent).toBe("Estudio Sur, Administración externa Sin cuenta asociada Vigente");
    expect(externo.textContent).not.toMatch(/CUIT|febrero|ese día ya no rige/);
    // Control: la del Consejo con cuenta no dice nada de la cuenta.
    expect(consejo.textContent).not.toMatch(/cuenta/);
    const lista = seccion("Decisiones");
    expect(within(lista).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
    expect(within(lista).queryByText(/Acta 14/)).toBeNull();
    // La descripción va recortada a un renglón; entera, en la ficha.
    expect(within(lista).queryByText(larga)).toBeNull();
    expect(within(lista).getByText(/^Designa al Consejo por dos años.*…$/)).toBeTruthy();
  });

  it("MAN-08: sin el párrafo de presentación que repetía el título y los botones", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(screen.queryByText(/^Acá están/)).toBeNull();
    expect(screen.queryByText(/Esta cuenta puede/)).toBeNull();
    // Control: el título y los botones siguen.
    expect(screen.getByRole("heading", { level: 1, name: "Mandatos y decisiones" })).toBeTruthy();
    expect(boton("Registrar una decisión")).toBeTruthy();
  });

  it("con las tres lecturas negadas (403) y sin saber qué alcanza, lleva al inicio", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        ...INICIO_CON_SESION,
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${BASE}/mis-acciones`]: () => error("error_interno", 500),
        [`GET ${RUTA_MANDATOS}`]: () => error("sin_permiso", 403),
        [`GET ${RUTA_DECISIONES}`]: () => error("sin_permiso", 403),
        [`GET ${RUTA_CANDIDATOS}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Consorcio en mano" })).toBeTruthy();
    expect(screen.queryByText(/no tiene permiso/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
  });

  it("MAN-01: la ficha de un mandato se abre sola, en su hoja, con lo autorizado y el foco adentro", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const detalle = await fichaDe("Estudio Sur, Administración externa");
    await within(detalle).findByText("RPA 1234");
    await waitFor(() => expect(detalle.contains(document.activeElement)).toBe(true));
    // Las listas quedan atrás: la ficha no es otra tarjeta debajo de ellas.
    expect(detalle.contains(screen.getByRole("region", { name: "Mandatos" }))).toBe(false);
    expect(within(detalle).queryByText("Ana Díaz, Consejo de propietarios")).toBeNull();
    expect(within(detalle).getByRole("button", { name: "Cerrar" })).toBeTruthy();
    expect(within(detalle).queryByRole("button", { name: "Cerrar el detalle" })).toBeNull();
    expect(within(detalle).getByText("RPA 1234")).toBeTruthy();
    expect(within(detalle).getByText("Cargar documentos")).toBeTruthy();
    expect(within(detalle).queryByText("documentos.cargar")).toBeNull();
    expect(within(detalle).getByText("Asamblea del 10 de enero de 2026: Designa al Consejo")).toBeTruthy();
  });

  it("el detalle de un mandato sigue mostrando la decisión que lo respalda aunque no se ofrezca en los selectores", async () => {
    const respaldado = { ...DETALLE_AE, decision_id: D_PROPIA };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(respaldado) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const titulo = await esperarFicha("Estudio Sur, Administración externa");
    const detalle = titulo.closest('[role="dialog"]') as HTMLElement;
    expect(within(detalle).getByText("Reunión del Consejo del 5 de marzo de 2026: Contrata al estudio")).toBeTruthy();
  });

  it("el detalle de una decisión dice si la registraste vos, sin decir quién", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Reunión del Consejo del 5 de marzo de 2026") }));
    const detalle = await fichaDe("Reunión del Consejo del 5 de marzo de 2026");
    expect(await within(detalle).findByText("Acta 14, libro 3, folio 52")).toBeTruthy();
    // MAN-13: quién da el alta se dice una vez, en el «Listo» de la decisión, no en cada ficha.
    expect(within(detalle).getByText("Esta cuenta.")).toBeTruthy();
    expect(within(detalle).queryByText(/no puede usarla/)).toBeNull();
  });

  it("MAN-01: mientras llega la ficha, la hoja ya está abierta con «Consultando…»", async () => {
    const { retenido, soltar } = retener(conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }), `${RUTA_MANDATOS}/${M_AE}`);
    vi.stubGlobal("fetch", retenido);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const detalle = await fichaDe("Estudio Sur, Administración externa");
    expect(within(detalle).getByText("Consultando…")).toBeTruthy();
    soltar();
    expect(await within(detalle).findByText("RPA 1234")).toBeTruthy();
    expect(within(detalle).queryByText("Consultando…")).toBeNull();
  });

  it("MAN-12: la decisión que la cuenta no ve no deja un renglón que lo diga", async () => {
    const otra = { ...DETALLE_AE, decision_id: D_OTRA };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(otra) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const detalle = await fichaDe("Estudio Sur, Administración externa");
    await within(detalle).findByText("RPA 1234");
    expect(within(detalle).queryByText("Decisión que lo respalda")).toBeNull();
    expect(within(detalle).queryByText(/No figura entre las decisiones visibles/)).toBeNull();
  });

  it("un detalle que ya no está (404) lo dice y vuelve a pedir la lista", async () => {
    const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => error("no_encontrado", 404) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    expect(await screen.findByText("Ese mandato ya no está en este edificio.")).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_MANDATOS)).toHaveLength(2));
  });

  it("registra una decisión con CSRF y una clave de reintento, y vuelve a pedir las listas", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Tipo"), { target: { value: "contrato_administracion" } });
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: " Contrata al estudio " } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Contrato del 01-10" } });
    fireEvent.click(boton("Registrar decisión"));
    expect(await screen.findByText(LISTO_DECISION)).toBeTruthy();
    const post = en(pedidos(simulado, "POST", RUTA_DECISIONES), 0);
    expect(post.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await post.json()).toEqual({
      tipo: "contrato_administracion",
      fecha: "2026-10-01",
      descripcion: "Contrata al estudio",
      referencia: "Contrato del 01-10",
      sha256: null,
    });
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_DECISIONES)).toHaveLength(2));
  });

  it("«Qué se decidió» y «Dónde consta» son textarea, sin pedir la huella del acta (G-48)", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    const que = within(form).getByLabelText("Qué se decidió");
    const donde = within(form).getByLabelText("Dónde consta (libro, acta y folio)");
    expect(que.tagName).toBe("TEXTAREA");
    expect(que.getAttribute("rows")).toBe("4");
    expect(que.getAttribute("maxlength")).toBe("500");
    expect(donde.tagName).toBe("TEXTAREA");
    expect(donde.getAttribute("rows")).toBe("2");
    expect(donde.getAttribute("maxlength")).toBe("300");
    // G-48: nadie calcula a mano la huella de un acta; el campo ya no se pide.
    expect(within(form).queryByLabelText(/Huella SHA-256/)).toBeNull();
    expect(within(form).getByRole("button", { name: "Registrar decisión" }).closest("form")?.className).toMatch(
      /\bformulario-movil\b/,
    );
  });

  it("los textarea no aceptan caracteres de control (la API los rechaza) ni un texto de solo espacios", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    const que = within(form).getByLabelText("Qué se decidió") as HTMLTextAreaElement;
    fireEvent.change(que, { target: { value: "Designa\nal Consejo\tpor dos años" } });
    expect(que.value).toBe("Designa al Consejo por dos años");
    expect(que.validity.valid).toBe(true);
    const enter = fireEvent.keyDown(que, { key: "Enter" });
    expect(enter).toBe(false);
    fireEvent.change(que, { target: { value: "   " } });
    expect(que.validity.customError).toBe(true);
    expect(que.validationMessage).toBe("Hace falta algo más que espacios.");
    fireEvent.change(que, { target: { value: " Algo " } });
    expect(que.validity.valid).toBe(true);
  });

  it("decisión 4: con un texto de solo espacios el botón sigue activo, marca el campo y no manda nada", async () => {
    const simulado = conConsejo();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "   " } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
    const registrar = within(form).getByRole("button", { name: "Registrar decisión" }) as HTMLButtonElement;
    expect(registrar.disabled).toBe(false);
    fireEvent.click(registrar);
    const que = within(form).getByLabelText("Qué se decidió");
    await waitFor(() => expect(que.getAttribute("aria-invalid")).toBe("true"));
    expect(within(form).getByText("Falta escribir qué se decidió.")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(que));
    // Control: los campos completos no se marcan.
    expect(within(form).getByLabelText("Fecha del acto").getAttribute("aria-invalid")).toBeNull();
    expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toEqual([]);
    // Al escribir, la marca se va.
    fireEvent.change(que, { target: { value: "Algo" } });
    expect(que.getAttribute("aria-invalid")).toBeNull();
  });

  it("da de alta al Consejo con la persona de los candidatos", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    const alta = seccion("Dar de alta un mandato");
    const personas = within(within(alta).getByLabelText("Persona (con vínculo confirmado en el edificio)")).getAllByRole("option");
    expect(personas.map((o) => o.textContent)).toEqual(["Beto Pérez (1° A)"]);
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText(LISTO_ALTA)).toBeTruthy();
    const post = en(pedidos(simulado, "POST", RUTA_MANDATOS), 0);
    expect(post.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await post.json()).toEqual({
      tipo: "miembro_consejo",
      decision_id: D_EQUIPO,
      desde: "2026-11-01",
      hasta: null,
      persona_id: VECINA,
      nombre: null,
      cuit: null,
      matricula: null,
    });
  });

  it("la administración externa se da de alta sin cuenta, por razón social y CUIT", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
    // Después de la administración de Estudio Sur, para no chocar con ella (C8G-05).
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-03-01" } });
    expect(within(alta).queryByLabelText("Persona (con vínculo confirmado en el edificio)")).toBeNull();
    fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
    fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
    fireEvent.change(within(alta).getByLabelText("Matrícula (opcional)"), { target: { value: "RPA 99" } });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    const cuerpo = (await en(pedidos(simulado, "POST", RUTA_MANDATOS), 0).json()) as Record<string, unknown>;
    expect([cuerpo.persona_id, cuerpo.nombre, cuerpo.cuit, cuerpo.matricula]).toEqual([null, "Estudio Norte", CUIT, "RPA 99"]);
  });

  it("un CUIT con el verificador mal se marca en el campo y el alta no sale (comentario 27)", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-03-01" } });
    fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
    fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: "20123456787" } });
    expect(within(alta).getByRole("alert").textContent).toBe(CUIT_INVALIDO);
    fireEvent.click(boton("Dar de alta"));
    await new Promise((r) => setTimeout(r, 20));
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(0);
    // Control: con el verificador bien, sale.
    fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
  });

  const opciones = (etiqueta: string) => within(screen.getByLabelText(etiqueta)).getAllByRole("option").map((o) => o.getAttribute("value"));

  it("A15: el alta no ofrece lo que registró quien mira; lo del equipo y lo de otra persona, sí", async () => {
    const otra = { id: D_OTRA, tipo: "asamblea", fecha: "2026-04-01", descripcion: "Renueva el Consejo", registrada_por_vos: false, del_equipo: false };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [...DECISIONES.elementos, otra] }) }));
    abrir();
    await cargado();
    seccion("Dar de alta un mandato");
    expect(opciones("Decisión que lo respalda")).toEqual(["", D_EQUIPO, D_OTRA]);
    // La lista la sigue mostrando: lo que se saca es la opción.
    expect(within(seccion("Decisiones")).getByText("Contrata al estudio")).toBeTruthy();
  });

  it("control: la misma decisión con `registrada_por_vos` en falso se ofrece", async () => {
    const ajenas = { elementos: DECISIONES.elementos.map((d) => ({ ...d, registrada_por_vos: false })) };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json(ajenas) }));
    abrir();
    await cargado();
    seccion("Dar de alta un mandato");
    expect(opciones("Decisión que lo respalda")).toEqual(["", D_EQUIPO, D_PROPIA]);
  });

  it("elegir una decisión no le pregunta nada a la API; abrir su detalle, sí", async () => {
    const simulado = conConsejo();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta(D_EQUIPO);
    await new Promise((r) => setTimeout(r, 0));
    const detalles = () => pedidos(simulado, "GET", `${RUTA_DECISIONES}/${D_EQUIPO}`);
    expect(detalles()).toHaveLength(0);
    // Control: lo que cuenta los pedidos los encuentra cuando el pedido existe.
    fireEvent.click(screen.getByRole("button", { name: fila("Asamblea del 10 de enero de 2026") }));
    await waitFor(() => expect(detalles()).toHaveLength(1));
  });

  it("C8G-01: si todas las decisiones son propias, no hay «Dar de alta un mandato» y una línea dice qué falta", async () => {
    const propias = { elementos: [en(DECISIONES.elementos, 1)] };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json(propias) }));
    abrir();
    await cargado();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    expect(screen.getByText(ALTA_SIN_DECISION_USABLE)).toBeTruthy();
    // El paso que sigue es registrar la decisión: ese es el principal.
    expect(boton("Registrar una decisión").className).toBe("");
    // MAN-13: quién da el alta se dice una sola vez, en el «Listo» de la decisión, no en su ventana.
    expect(within(seccion("Registrar una decisión")).queryByText(/no puede usarla/)).toBeNull();
    // FL-DEC-03: el «Listo» no repite quién da el alta (no toda decisión respalda un alta).
    expect(LISTO_DECISION).toBe("Listo: la decisión quedó registrada.");
  });

  it("C8G-01, control: sin ninguna decisión, quien registra lee dónde se registra; con una usable, el botón", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [] }) }));
    abrir();
    await cargado();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    expect(screen.getByText(ALTA_SIN_DECISIONES)).toBeTruthy();
    expect(screen.queryByText(ALTA_SIN_DECISION_USABLE)).toBeNull();
    cleanup();
    // Control: con una decisión que se puede usar, el alta como principal y sin la línea.
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(boton("Dar de alta un mandato").className).toBe("");
    expect(within(seccion("Dar de alta un mandato")).getByLabelText("Tipo de mandato")).toBeTruthy();
    expect(screen.queryByText(ALTA_SIN_DECISION_USABLE)).toBeNull();
    expect(screen.queryByText(ALTA_SIN_DECISIONES)).toBeNull();
  });

  it("C8G-05: un alta de administración que choca con otra en esas fechas no ofrece «Dar de alta» y dice qué hacer", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador" } });
    // Estudio Sur administra del 1-2-2026 al 1-2-2027: el 1-11-2026 cae adentro.
    expect(
      within(alta).getByText(/^Ya hay una administración en esas fechas: Estudio Sur, desde el 1 de febrero de 2026 hasta el 1 de febrero de 2027\./),
    ).toBeTruthy();
    expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
    // Control: con fechas que no se pisan, el botón vuelve y el alta sale.
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-02-01" } });
    expect(within(alta).queryByText(/^Ya hay una administración/)).toBeNull();
    fireEvent.click(within(alta).getByRole("button", { name: "Dar de alta" }));
    await screen.findByText(LISTO_ALTA);
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
  });

  it("FL-MAN-01: con una administración sin fecha de fin, no se ofrece otra ni se piden sus datos; se dice y se abre su ficha", async () => {
    const sinFin = { ...en(MANDATOS.elementos, 1), vigente_hasta: null };
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}`]: () => Response.json({ elementos: [en(MANDATOS.elementos, 0), sinFin] }),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json({ ...DETALLE_AE, vigente_hasta: null }),
      }),
    );
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    // Solo el Consejo: las dos administraciones chocarían con cualquier «Desde».
    expect(within(within(alta).getByLabelText("Tipo de mandato")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "miembro_consejo",
    ]);
    expect(
      within(alta).getByText(
        "Ya hay una administración sin fecha de fin: Estudio Sur, desde el 1 de febrero de 2026. Para dar otra administración, antes hay que darla de baja desde su ficha.",
      ),
    ).toBeTruthy();
    fireEvent.click(within(alta).getByRole("button", { name: "Abrir su ficha" }));
    expect(screen.queryByRole("dialog", { name: "Dar de alta un mandato" })).toBeNull();
    expect(await esperarFicha("Estudio Sur, Administración externa")).toBeTruthy();
  });

  it("FL-MAN-01: con otra administración con fecha de fin, una administración pide primero las fechas; si chocan, nada más", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
    const etiquetas = () => [...alta.querySelectorAll("label")].map((l) => l.textContent);
    // Las fechas van antes que los datos de la administración.
    expect(etiquetas().indexOf("Desde")).toBeLessThan(etiquetas().indexOf("Razón social o nombre"));
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2026-11-01" } });
    expect(within(alta).getByText(/^Ya hay una administración en esas fechas: Estudio Sur/)).toBeTruthy();
    expect(within(alta).queryByLabelText("Razón social o nombre")).toBeNull();
    expect(within(alta).queryByLabelText("Decisión que lo respalda")).toBeNull();
    // Control: con fechas que no chocan, vuelven los datos.
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-02-01" } });
    expect(within(alta).getByLabelText("Razón social o nombre")).toBeTruthy();
  });

  it("C8G-05, control: el Consejo no choca con una administración", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await completarAlta();
    const alta = seccion("Dar de alta un mandato");
    expect(within(alta).queryByText(/^Ya hay una administración/)).toBeNull();
    expect(within(alta).getByRole("button", { name: "Dar de alta" })).toBeTruthy();
  });

  it("FL-MAN-05: con una sola decisión que se puede usar, ya va elegida; con dos, hay que elegirla", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    seccion("Dar de alta un mandato");
    expect(opciones("Decisión que lo respalda")).toEqual([D_EQUIPO]);
    expect((screen.getByLabelText("Decisión que lo respalda") as HTMLSelectElement).value).toBe(D_EQUIPO);
    cleanup();
    // Control: con dos, la persona elige.
    const otra = { id: D_OTRA, tipo: "asamblea", fecha: "2026-04-01", descripcion: "Renueva el Consejo", registrada_por_vos: false, del_equipo: false };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [...DECISIONES.elementos, otra] }) }));
    abrir();
    await cargado();
    seccion("Dar de alta un mandato");
    expect((screen.getByLabelText("Decisión que lo respalda") as HTMLSelectElement).value).toBe("");
  });

  it("decisión 4: el alta sin decisión ni fecha marca los dos campos y no manda nada", async () => {
    // Dos decisiones que se pueden usar: ninguna va elegida (FL-MAN-05).
    const otra = { id: D_OTRA, tipo: "asamblea", fecha: "2026-04-01", descripcion: "Renueva el Consejo", registrada_por_vos: false, del_equipo: false };
    const simulado = conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [...DECISIONES.elementos, otra] }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.click(within(alta).getByRole("button", { name: "Dar de alta" }));
    await waitFor(() => expect(within(alta).getByLabelText("Decisión que lo respalda").getAttribute("aria-invalid")).toBe("true"));
    expect(within(alta).getByText("Falta elegir la decisión que lo respalda.")).toBeTruthy();
    expect(within(alta).getByText("Falta la fecha «Desde».")).toBeTruthy();
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(0);
  });

  it("si la API igual niega el alta, es un error junto al formulario y lo cargado queda", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("sin_permiso", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    const aviso = await within(seccion("Dar de alta un mandato")).findByRole("alert");
    expect(aviso.textContent).toMatch(/^El mandato de esta cuenta no incluye dar este alta\./);
    expect((screen.getByLabelText("Decisión que lo respalda") as HTMLSelectElement).value).toBe(D_EQUIPO);
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
  });

  it("muestra el motivo de dominio por el código: persona sin vínculo", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("persona_sin_vinculo", 422) }));
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText("La persona no tiene un vínculo confirmado, vigente o futuro, en este edificio.")).toBeTruthy();
  });

  it("si se corta la red, el reintento usa la misma clave; un rechazo definitivo la renueva", async () => {
    let respuesta: () => Response = () => {
      throw new TypeError("sin red");
    };
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText(/No se pudo completar el pedido/)).toBeTruthy();
    respuesta = () => error("administracion_ocupada", 409);
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText("Ya hay un mandato de administración en esas fechas en este edificio.")).toBeTruthy();
    respuesta = () => Response.json({ id: NUEVO }, { status: 201 });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    const [primera, segunda, tercera] = pedidos(simulado, "POST", RUTA_MANDATOS).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(tercera).not.toBe(segunda);
  });

  it("G-49: un 422 de «Desde» nombra la fecha «Desde» y la marca, con su motivo al lado", async () => {
    const simulado = conConsejo({
      [`POST ${RUTA_MANDATOS}`]: () =>
        error("peticion_invalida", 422, { campos: ["body.desde"], mensajes: { "body.desde": "Escribir una fecha válida." } }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText("Hay que corregir la fecha «Desde»: el motivo está junto a cada campo.")).toBeTruthy();
    expect(screen.queryByText("Revisá los datos del formulario.")).toBeNull();
    const desde = screen.getByLabelText("Desde");
    expect(desde.getAttribute("aria-invalid")).toBe("true");
    expect(document.getElementById(desde.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Escribir una fecha válida.");
    // Control: «Hasta» no queda marcado y conserva su descripción; al corregir «Desde», su marca se va.
    const hasta = screen.getByLabelText(/^Hasta/);
    expect(hasta.getAttribute("aria-invalid")).toBeNull();
    expect(hasta.getAttribute("aria-describedby")).toMatch(/-vigencia-vacia$/);
    fireEvent.change(desde, { target: { value: "2026-11-02" } });
    expect(desde.getAttribute("aria-invalid")).toBeNull();
  });

  it("G-49: la fecha futura de una decisión se dice junto a la fecha del acto", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => error("fecha_futura", 422, { campos: ["body.fecha"] }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2099-01-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
    fireEvent.click(boton("Registrar decisión"));
    expect(await screen.findByText("Hay que corregir la fecha del acto: el motivo está junto a cada campo.")).toBeTruthy();
    const fecha = within(form).getByLabelText("Fecha del acto");
    expect(fecha.getAttribute("aria-invalid")).toBe("true");
    expect(document.getElementById(fecha.getAttribute("aria-describedby") ?? "")?.textContent).toMatch(/no puede ser posterior a hoy/);
    // «Cancelar» se lleva la marca: al volver a abrir, el campo empieza limpio.
    fireEvent.click(within(form).getByRole("button", { name: "Cancelar" }));
    const otra = seccion("Registrar una decisión");
    expect(within(otra).getByLabelText("Fecha del acto").getAttribute("aria-invalid")).toBeNull();
  });

  it("FL-DEC-04: la fecha del acto no ofrece días después de hoy", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    expect(within(form).getByLabelText("Fecha del acto").getAttribute("max")).toBe(hoyDelDispositivo());
  });

  it("G-49: los motivos de dominio no usan «facultad» ni «instrumento»", () => {
    expect(mensajeDeError(422, "facultad_fuera_de_paquete", "alta")).toBe("Algún permiso no corresponde a ese tipo de mandato.");
    expect(mensajeDeError(422, "respaldo_invalido", "baja")).toBe(
      "La remoción se respalda con una asamblea; cualquier otra causa, con el documento que lo prueba.",
    );
    expect(mensajeDeError(409, "codigo_desconocido", "alta")).not.toMatch(/choca/);
  });

  it("una fecha posterior a hoy en el consorcio (422 fecha_futura) se explica", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => error("fecha_futura", 422) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2099-01-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
    fireEvent.click(boton("Registrar decisión"));
    expect(await screen.findByText(/no puede ser posterior a hoy: se registran actos ya tomados/)).toBeTruthy();
    expect(screen.queryByText(LISTO_DECISION)).toBeNull();
  });

  it("un pedido en curso (409) conserva la clave", async () => {
    let enCurso = true;
    const simulado = conConsejo({
      [`POST ${RUTA_DECISIONES}`]: () => (enCurso ? error("idempotencia_en_curso", 409) : Response.json({ id: NUEVO }, { status: 201 })),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
    fireEvent.click(boton("Registrar decisión"));
    expect(await screen.findByText(/El pedido se está procesando/)).toBeTruthy();
    enCurso = false;
    fireEvent.click(boton("Registrar decisión"));
    await screen.findByText(LISTO_DECISION);
    const [primera, segunda] = pedidos(simulado, "POST", RUTA_DECISIONES).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("comentario 36: «Dar de baja…» abre su ventana sobre la ficha; pide confirmación y «Sí» manda el instrumento", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => Response.json({ id: M_AE, causa: "renuncia", vigente_hasta: "2026-10-19" }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const ficha = await esperarFicha("Estudio Sur, Administración externa");
    const ventana = await desplegarBaja();
    // El formulario no está dentro de la ficha, debajo de las facultades: es su propia ventana.
    expect(within(ficha).queryByLabelText("Por qué termina")).toBeNull();
    expect(within(ventana).getByRole("heading", { level: 2, name: "Dar de baja el mandato de Estudio Sur" })).toBeTruthy();
    fireEvent.change(within(ventana).getByLabelText("Por qué termina"), { target: { value: "Renuncia presentada" } });
    fireEvent.change(within(ventana).getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Dar de baja" }));
    expect(await within(ventana).findByText(/¿Dar de baja el mandato de Estudio Sur\? La baja no se puede deshacer./)).toBeTruthy();
    // «Cancelar» cierra la ventana sin mandar nada: la ficha queda.
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: VENTANA_BAJA })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Estudio Sur, Administración externa" })).toBeTruthy();
    expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`)).toHaveLength(0);
    const otra = await desplegarBaja();
    fireEvent.change(within(otra).getByLabelText("Por qué termina"), { target: { value: "Renuncia presentada" } });
    fireEvent.change(within(otra).getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
    fireEvent.click(within(otra).getByRole("button", { name: "Dar de baja" }));
    fireEvent.click(await within(otra).findByRole("button", { name: "Sí, dar de baja" }));
    const listo = await screen.findByText("Listo: el mandato de Estudio Sur quedó dado de baja.");
    // Se cierran la ventana y la ficha; el «Listo» queda en la página con el foco.
    await waitFor(() => expect(document.activeElement).toBe(listo));
    expect(screen.queryByRole("dialog")).toBeNull();
    const post = en(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`), 0);
    expect(post.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await post.json()).toEqual({
      causa: "renuncia",
      descripcion: "Renuncia presentada",
      decision_id: null,
      instrumento: "Nota del 10-10",
    });
  });

  it("decisión 4: la baja sin motivo ni instrumento marca los campos y no pregunta ni manda", async () => {
    const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const ventana = await desplegarBaja();
    fireEvent.click(within(ventana).getByRole("button", { name: "Dar de baja" }));
    expect(await within(ventana).findByText("Falta escribir por qué termina.")).toBeTruthy();
    expect(within(ventana).getByText("Falta escribir el instrumento.")).toBeTruthy();
    expect(within(ventana).queryByRole("button", { name: "Sí, dar de baja" })).toBeNull();
    expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`)).toHaveLength(0);
  });

  it("la remoción se respalda con una asamblea: solo ofrece asambleas, y ninguna propia", async () => {
    const asambleaPropia = { id: NUEVO, tipo: "asamblea", fecha: "2026-09-01", descripcion: "Remueve al estudio", registrada_por_vos: true, del_equipo: false };
    const simulado = conConsejo({
      [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [...DECISIONES.elementos, asambleaPropia] }),
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => Response.json({ id: M_AE, causa: "remocion", vigente_hasta: "2026-10-19" }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    await desplegarBaja();
    fireEvent.change(screen.getByLabelText("Causa"), { target: { value: "remocion" } });
    // FL-MAN-05: la única asamblea que se puede usar ya va elegida.
    expect(opciones("Asamblea que lo remueve")).toEqual([D_EQUIPO]);
    fireEvent.change(screen.getByLabelText("Asamblea que lo remueve"), { target: { value: D_EQUIPO } });
    fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Remoción" } });
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, dar de baja" }));
    await screen.findByText("Listo: el mandato de Estudio Sur quedó dado de baja.");
    const cuerpo = (await en(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`), 0).json()) as Record<string, unknown>;
    expect([cuerpo.causa, cuerpo.decision_id, cuerpo.instrumento]).toEqual(["remocion", D_EQUIPO, null]);
  });

  describe("la causa «Remoción por asamblea»", () => {
    const abrirElDetalle = async (decisiones: () => Response) => {
      vi.stubGlobal(
        "fetch",
        conConsejo({ [`GET ${RUTA_DECISIONES}`]: decisiones, [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }),
      );
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      await desplegarBaja();
    };

    it("sin una asamblea que quien mira pueda usar, no se ofrece y lo dice (C8G-03)", async () => {
      const soloPropia = { ...en(DECISIONES.elementos, 0), registrada_por_vos: true, del_equipo: false };
      await abrirElDetalle(() => Response.json({ elementos: [soloPropia] }));
      expect(opciones("Causa")).not.toContain("remocion");
      expect(opciones("Causa")).toContain("renuncia");
      // FL-MAN-08: la línea no va en la ventana de la baja, que se abre por otra causa, sino en la ficha.
      const ventana = screen.getByRole("dialog", { name: /^Dar de baja el mandato de/ });
      expect(within(ventana).queryByText(REMOCION_SIN_ASAMBLEA)).toBeNull();
      expect(within(screen.getByRole("dialog", { name: "Estudio Sur, Administración externa" })).getByText(REMOCION_SIN_ASAMBLEA)).toBeTruthy();
    });

    it("control: con una asamblea que puede usar, se ofrece, sin la línea", async () => {
      await abrirElDetalle(() => Response.json(DECISIONES));
      expect(opciones("Causa")).toContain("remocion");
      expect(screen.queryByText(REMOCION_SIN_ASAMBLEA)).toBeNull();
    });

    it("C8G-03: si las decisiones no llegaron, no se ofrece y una línea dice por qué", async () => {
      await abrirElDetalle(() => error("error_interno", 500));
      expect(opciones("Causa")).not.toContain("remocion");
      const ventana = screen.getByRole("dialog", { name: /^Dar de baja el mandato de/ });
      expect(within(ventana).queryByText(/^No se pudieron consultar las decisiones/)).toBeNull();
      expect(screen.getByText("No se pudieron consultar las decisiones: sin ellas no se ofrece la remoción por asamblea.")).toBeTruthy();
    });
  });

  it("un mandato terminado solo ofrece la baja por error de carga; uno dado de baja, ninguna", async () => {
    const terminado = { ...DETALLE_AE, estado: "terminado" };
    let detalle: object = terminado;
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(detalle) }));
    abrir();
    await cargado();
    const ver = () => fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    ver();
    // G-45: un terminado no ofrece «Dar de baja…»: solo anularlo por error de carga, sin elegir causa.
    await esperarFicha("Estudio Sur, Administración externa");
    expect(screen.queryByRole("button", { name: "Dar de baja…" })).toBeNull();
    const ventana = await desplegarBaja();
    expect(within(ventana).getByRole("heading", { level: 2, name: "Anular por error de carga" })).toBeTruthy();
    expect(within(ventana).queryByLabelText("Causa")).toBeNull();
    // MAN-13: la ayuda fija se fue; lo que importa va en la pregunta.
    expect(within(ventana).queryByText(/solo se anula si se cargó por error/)).toBeNull();
    fireEvent.change(within(ventana).getByLabelText("Por qué termina"), { target: { value: "Se cargó dos veces" } });
    fireEvent.change(within(ventana).getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
    fireEvent.click(within(ventana).getByRole("button", { name: "Anular" }));
    expect(
      await within(ventana).findByText("¿Anular el mandato de Estudio Sur por error de carga? Así consta que nunca valió. No se puede deshacer."),
    ).toBeTruthy();
    expect(boton("Sí, anular").className).toBe("peligro");
    fireEvent.click(within(ventana).getByRole("button", { name: "Cancelar" }));
    fireEvent.click(boton("Cerrar"));
    detalle = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "renuncia" };
    ver();
    const dada = await esperarFicha("Estudio Sur, Administración externa");
    expect(within(dada).getByText("Renuncia")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^(Dar de baja|Anular por error de carga)…$/ })).toBeNull();
  });

  it("G-45: la anulación de un terminado manda la causa «error de carga»", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json({ ...DETALLE_AE, estado: "terminado" }),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => Response.json({ id: M_AE, causa: "error_carga", vigente_hasta: null }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    await desplegarBaja();
    fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Se cargó dos veces" } });
    fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
    fireEvent.click(boton("Anular"));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, anular" }));
    await screen.findByText("Listo: el mandato de Estudio Sur quedó dado de baja.");
    expect(await en(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`), 0).json()).toEqual({
      causa: "error_carga",
      descripcion: "Se cargó dos veces",
      decision_id: null,
      instrumento: "Nota del 10-10",
    });
  });

  it("E62 y MAN-10: un mandato pendiente de revisión lo dice en una frase y admite cualquier baja", async () => {
    const pendiente = { ...DETALLE_AE, estado: "pendiente_revision" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(pendiente) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const ficha = await esperarFicha("Estudio Sur, Administración externa");
    expect(within(ficha).getByText("Pendiente de revisión: sin condición para integrar el Consejo")).toBeTruthy();
    // MAN-10: la explicación larga va en la ventana de mantenerlo, no en la ficha.
    expect(within(ficha).queryByText(CAUSA_REVISION)).toBeNull();
    expect(within(ficha).getByRole("button", { name: "Mantener el mandato…" })).toBeTruthy();
    const ventana = await desplegarBaja();
    expect(within(within(ventana).getByLabelText("Causa")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "remocion",
      "renuncia",
      "fallecimiento_incapacidad",
      "error_carga",
      "otro",
    ]);
  });

  it("un error en la baja deshace la pregunta: el reintento se vuelve a confirmar", async () => {
    let respuesta = () => error("sin_permiso", 403);
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => respuesta(),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, dar de baja" }));
    const rechazo = await screen.findByText(/El mandato de esta cuenta no incluye dar esta baja/);
    // El rechazo queda en la ventana.
    expect(screen.getByRole("dialog", { name: VENTANA_BAJA }).contains(rechazo)).toBe(true);
    expect(screen.queryByRole("button", { name: "Sí, dar de baja" })).toBeNull();
    respuesta = () => Response.json({ id: M_AE, causa: "renuncia", vigente_hasta: "2026-10-19" });
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, dar de baja" }));
    await screen.findByText("Listo: el mandato de Estudio Sur quedó dado de baja.");
    expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`)).toHaveLength(2);
  });

  it("cambiar un dato con la confirmación abierta la cierra", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    expect(await screen.findByRole("button", { name: "Sí, dar de baja" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Otra nota" } });
    expect(screen.queryByRole("button", { name: "Sí, dar de baja" })).toBeNull();
  });

  it("una baja sobre un mandato que ya tenía baja cierra la ficha y vuelve a pedir la lista", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => error("mandato_ya_dado_de_baja", 409),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, dar de baja" }));
    expect(await screen.findByText("El mandato ya tiene una baja registrada.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    // La ventana se fue con la ficha: el aviso quedó en la página, no sin lugar.
    expect(within(estadoDePantalla()).getByText("El mandato ya tiene una baja registrada.")).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", RUTA_MANDATOS)).toHaveLength(2));
  });

  it("lo escrito para un mandato no pasa al detalle de otro", async () => {
    const otro = { ...DETALLE_AE, id: M_MC, tipo: "miembro_consejo", nombre: "Ana Díaz", cuit: null };
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => Response.json(otro),
      }),
    );
    abrir();
    await abrirBaja();
    fireEvent.click(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }));
    await esperarFicha("Ana Díaz, Consejo de propietarios");
    // El otro detalle llega con la baja plegada (G-44): se despliega para mirar sus campos.
    expect(screen.queryByLabelText("Por qué termina")).toBeNull();
    await desplegarBaja();
    expect((screen.getByLabelText("Por qué termina") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Instrumento (nota, certificado u otro)") as HTMLInputElement).value).toBe("");
  });

  it("G-44: al terminar un alta el foco va al «Listo», sin abrir la ficha; al cerrar una ficha, el foco vuelve a su fila", async () => {
    const simulado = conConsejo({
      [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    boton("Dar de alta").focus();
    fireEvent.click(boton("Dar de alta"));
    const listo = await screen.findByText("Listo: el mandato de Beto Pérez quedó dado de alta y ya está en la lista.");
    await waitFor(() => expect(document.activeElement).toBe(listo));
    // Lo primero que se ve no es cómo deshacerlo: ni la ficha ni su baja.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByLabelText("Por qué termina")).toBeNull();
    expect(pedidos(simulado, "GET", `${RUTA_MANDATOS}/${NUEVO}`)).toHaveLength(0);
    const ver = screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") });
    ver.focus();
    fireEvent.click(ver);
    await esperarFicha("Estudio Sur, Administración externa");
    fireEvent.click(boton("Cerrar"));
    await waitFor(() => expect(document.activeElement).toBe(ver));
  });

  it("un 5xx conserva la clave; un intento reutilizado la renueva", async () => {
    let respuesta = () => error("error_interno", 502);
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    respuesta = () => error("idempotencia_reutilizada", 422);
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText(/Los datos cambiaron desde un intento anterior/)).toBeTruthy();
    respuesta = () => Response.json({ id: NUEVO }, { status: 201 });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    const [primera, segunda, tercera] = pedidos(simulado, "POST", RUTA_MANDATOS).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(tercera).not.toBe(segunda);
  });

  it("E34: el detalle con un 503 dice que el servicio no está disponible; sin respuesta, la conexión", async () => {
    let caida = false;
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => {
          if (caida) throw new TypeError("Failed to fetch");
          return error("servicio_no_disponible", 503);
        },
      }),
    );
    abrir();
    await cargado();
    const ver = screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") });
    fireEvent.click(ver);
    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Hay que revisar la conexión/)).toBeNull();
    caida = true;
    fireEvent.click(ver);
    expect(await screen.findByText(/Hay que revisar la conexión/)).toBeTruthy();
  });

  it("E34: la primera carga con un 503 de la aplicación dice servicio, no conexión", async () => {
    vi.stubGlobal("fetch", conConsejo({ "GET /api/v1/me/consorcios": () => error("servicio_no_disponible", 503) }));
    abrir();
    expect(await screen.findByText(/No se pudieron consultar los edificios. El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Hay que revisar la conexión/)).toBeNull();
  });

  describe("al terminar un envío", () => {
    const valor = (region: HTMLElement, etiqueta: string) => (within(region).getByLabelText(etiqueta) as HTMLInputElement).value;

    function completarDecision() {
      const form = seccion("Registrar una decisión");
      fireEvent.change(within(form).getByLabelText("Tipo"), { target: { value: "contrato_administracion" } });
      fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
      fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Contrata al estudio" } });
      fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Contrato del 01-10" } });
      return form;
    }

    it("registrar una decisión cierra su ventana, da el «Listo» con el foco y la deja vacía, sin poder repetirla", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      completarDecision();
      fireEvent.click(boton("Registrar decisión"));
      const listo = await screen.findByText(LISTO_DECISION);
      await waitFor(() => expect(document.activeElement).toBe(listo));
      // La decisión queda en la lista: la ficha no se abre sola encima del «Listo».
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(pedidos(simulado, "GET", `${RUTA_DECISIONES}/${NUEVO}`)).toHaveLength(0);
      const form = seccion("Registrar una decisión");
      expect(valor(form, "Tipo")).toBe("asamblea");
      expect(valor(form, "Fecha del acto")).toBe("");
      expect(valor(form, "Qué se decidió")).toBe("");
      expect(valor(form, "Dónde consta (libro, acta y folio)")).toBe("");
      // Vacía, volver a enviar solo marca lo que falta.
      fireEvent.click(within(form).getByRole("button", { name: "Registrar decisión" }));
      expect(await within(form).findByText("Falta la fecha del acto.")).toBeTruthy();
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(1);
    });

    it("control: una decisión rechazada no abre nada y deja lo escrito en la ventana para reintentar", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => error("fecha_futura", 422) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      const form = completarDecision();
      fireEvent.click(boton("Registrar decisión"));
      const aviso = await within(form).findByText(/no puede ser posterior a hoy/);
      await waitFor(() => expect(document.activeElement).toBe(aviso));
      expect(pedidos(simulado, "GET", `${RUTA_DECISIONES}/${NUEVO}`)).toHaveLength(0);
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      expect(valor(form, "Tipo")).toBe("contrato_administracion");
      expect(valor(form, "Fecha del acto")).toBe("2026-10-01");
      expect(valor(form, "Qué se decidió")).toBe("Contrata al estudio");
      expect(valor(form, "Dónde consta (libro, acta y folio)")).toBe("Contrato del 01-10");
    });

    it("dar de alta no abre la ficha, cierra su ventana y la deja vacía", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      const alta = seccion("Dar de alta un mandato");
      fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
      // MAN-13: sin la ayuda fija de la administración externa; los campos ya lo dicen.
      expect(within(alta).queryByText(/se carga por su razón social y su CUIT/)).toBeNull();
      // FL-MAN-01: con otra administración que ocupa fechas, primero las fechas.
      fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-03-01" } });
      fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
      fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
      fireEvent.click(boton("Dar de alta"));
      // MAN-09: el «Listo» no repite cómo se asocia la cuenta: la fila lo dice («Sin cuenta asociada»).
      expect(await screen.findByText("Listo: el mandato de Estudio Norte quedó dado de alta y ya está en la lista.")).toBeTruthy();
      expect(screen.queryByRole("dialog")).toBeNull();
      const despues = seccion("Dar de alta un mandato");
      expect(valor(despues, "Tipo de mandato")).toBe("miembro_consejo");
      expect(valor(despues, "Persona (con vínculo confirmado en el edificio)")).toBe(VECINA);
      // FL-MAN-05: con una sola decisión que se puede usar, vuelve a estar elegida.
      expect(valor(despues, "Decisión que lo respalda")).toBe(D_EQUIPO);
      expect(valor(despues, "Desde")).toBe("");
      expect(valor(despues, "Hasta (opcional; ese día ya no rige)")).toBe("");
      expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
    });

    it("control: un alta rechazada no abre nada y deja lo cargado para reintentar", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("mandato_superpuesto", 409) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      expect(await screen.findByText("La persona ya tiene un mandato de ese tipo en esas fechas.")).toBeTruthy();
      expect(pedidos(simulado, "GET", `${RUTA_MANDATOS}/${NUEVO}`)).toHaveLength(0);
      const alta = seccion("Dar de alta un mandato");
      expect(valor(alta, "Decisión que lo respalda")).toBe(D_EQUIPO);
      expect(valor(alta, "Desde")).toBe("2026-11-01");
    });

  });

  it("una lista caída se reintenta sin perder lo escrito; las que andan quedan a la vista", async () => {
    let caida = true;
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}`]: () => (caida ? error("error_interno", 500) : Response.json(MANDATOS)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText("No se pudieron consultar los mandatos.")).toBeTruthy();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "A medio escribir" } });
    fireEvent.click(within(form).getByRole("button", { name: "Cancelar" }));
    caida = false;
    fireEvent.click(within(seccion("Mandatos")).getByRole("button", { name: "Consultar de nuevo" }));
    // Mientras llega, la lista que andaba no se vacía.
    expect(within(seccion("Decisiones")).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
    expect(await cargado()).toBeTruthy();
    expect(pedidos(simulado, "GET", RUTA_MANDATOS)).toHaveLength(2);
    expect((within(seccion("Registrar una decisión")).getByLabelText("Qué se decidió") as HTMLTextAreaElement).value).toBe("A medio escribir");
    // Control: con las listas a la vista no hay nada que reintentar.
    expect(screen.queryByRole("button", { name: "Consultar de nuevo" })).toBeNull();
  });

  it("con varias listas caídas cada botón dice de qué sección es, y uno solo las trae todas", async () => {
    let caidas = true;
    const respuesta = (lista: unknown) => () => (caidas ? error("error_interno", 500) : Response.json(lista));
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}`]: respuesta(MANDATOS),
        [`GET ${RUTA_DECISIONES}`]: respuesta(DECISIONES),
        [`GET ${RUTA_CANDIDATOS}`]: respuesta(CANDIDATOS),
      }),
    );
    const { container } = abrir();
    // C8G-01: sin las listas no se ofrece el alta; la línea dice qué falta, con su reintento.
    expect(await screen.findByText(/^No se pudo consultar quiénes pueden recibir un mandato/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    expect(screen.getByRole("button", { name: "Consultar de nuevo", description: "Mandatos" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Consultar de nuevo", description: "Decisiones" })).toBeTruthy();
    const alta = screen.getByRole("button", { name: "Consultar de nuevo", description: /^No se pudo consultar quiénes/ });
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    caidas = false;
    fireEvent.click(alta);
    expect(await cargado()).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Dar de alta un mandato" })).toBeTruthy();
    expect(within(seccion("Decisiones")).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
  });

  it("C8G-01: sin las decisiones no hay alta; al traerlas, vuelve", async () => {
    let caida = true;
    vi.stubGlobal(
      "fetch",
      conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => (caida ? error("error_interno", 500) : Response.json(DECISIONES)) }),
    );
    abrir();
    await screen.findByText("No se pudieron consultar las decisiones.");
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    caida = false;
    fireEvent.click(within(seccion("Decisiones")).getByRole("button", { name: "Consultar de nuevo" }));
    expect(await screen.findByRole("button", { name: "Dar de alta un mandato" })).toBeTruthy();
  });

  it("los edificios que no se pudieron consultar se reintentan sin salir de la pantalla", async () => {
    let caido = true;
    const simulado = conConsejo({
      "GET /api/v1/me/consorcios": () => (caido ? error("servicio_no_disponible", 503) : Response.json(CONSORCIOS)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/No se pudieron consultar los edificios/)).toBeTruthy();
    // Control: la primera carga pidió una vez la pantalla y otra permisos.tsx, aun con StrictMode.
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(2);
    caido = false;
    fireEvent.click(boton("Consultar de nuevo"));
    expect(await cargado()).toBeTruthy();
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
    expect(screen.queryByText(/No se pudieron consultar los edificios/)).toBeNull();
  });

  describe("al volver la red", () => {
    const volvioLaRed = () => fireEvent(window, new Event("online"));

    it("una lista caída se repite sola, lo dice y no pierde lo escrito", async () => {
      let caida = true;
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}`]: () => (caida ? error("error_interno", 500) : Response.json(MANDATOS)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      expect(await screen.findByText("No se pudieron consultar los mandatos.")).toBeTruthy();
      seccion("Registrar una decisión");
      fireEvent.change(screen.getByLabelText("Qué se decidió"), { target: { value: "A medio escribir" } });
      caida = false;
      volvioLaRed();
      expect(await cargado()).toBeTruthy();
      expect(within(estadoDePantalla()).getByText(VOLVIO_LA_RED)).toBeTruthy();
      expect((screen.getByLabelText("Qué se decidió") as HTMLTextAreaElement).value).toBe("A medio escribir");
    });

    it("control: si la lista sigue caída no dice que actualizó", async () => {
      const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}`]: () => error("error_interno", 500) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await screen.findByText("No se pudieron consultar los mandatos.");
      const antes = pedidos(simulado, "GET", RUTA_MANDATOS).length;
      volvioLaRed();
      await waitFor(() => expect(pedidos(simulado, "GET", RUTA_MANDATOS).length).toBeGreaterThan(antes));
      expect(await screen.findByText("No se pudieron consultar los mandatos.")).toBeTruthy();
      await waitFor(() => expect(screen.queryByText(VOLVIO_LA_RED)).toBeNull());
    });

    it("control: nunca repite un envío, y con las listas a la vista no pide nada", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => error("servicio_no_disponible", 503) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      const form = seccion("Registrar una decisión");
      fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
      fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
      fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
      fireEvent.click(boton("Registrar decisión"));
      await within(form).findByRole("alert");
      const lecturas = pedidos(simulado, "GET", RUTA_MANDATOS).length;
      volvioLaRed();
      await new Promise((r) => setTimeout(r, 0));
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(1);
      expect(pedidos(simulado, "GET", RUTA_MANDATOS)).toHaveLength(lecturas);
      expect(screen.queryByText(VOLVIO_LA_RED)).toBeNull();
    });

    it("los edificios que no se pudieron consultar se repiten solos", async () => {
      let caido = true;
      vi.stubGlobal(
        "fetch",
        conConsejo({ "GET /api/v1/me/consorcios": () => (caido ? error("servicio_no_disponible", 503) : Response.json(CONSORCIOS)) }),
      );
      abrir();
      await screen.findByText(/No se pudieron consultar los edificios/);
      caido = false;
      volvioLaRed();
      expect(await cargado()).toBeTruthy();
      expect(screen.queryByText(/No se pudieron consultar los edificios/)).toBeNull();
    });
  });

  describe("a quién se le da el alta", () => {
    const YO = "99999999-9999-4999-8999-999999999999";
    const YO_CANDIDATO = { persona_id: YO, nombre: "Zoe Mira", unidades: ["3° C"], habilita_consejo: true, mandatos: [] };
    const PERSONA = "Persona (con vínculo confirmado en el edificio)";
    const opciones = () => within(screen.getByLabelText(PERSONA)).getAllByRole("option").map((o) => o.textContent);

    it("quien mira no se ofrece: lo elegido y lo enviado es otra persona", async () => {
      const simulado = conConsejo({
        "GET /api/v1/auth/sesion": () => Response.json({ persona_id: YO }),
        [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [YO_CANDIDATO, ...CANDIDATOS.elementos] }),
        [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      await waitFor(() => expect(opciones()).toEqual(["Beto Pérez (1° A)"]));
      expect((screen.getByLabelText(PERSONA) as HTMLSelectElement).value).toBe(VECINA);
      fireEvent.click(boton("Dar de alta"));
      await screen.findByText(LISTO_ALTA);
      const cuerpo = (await en(pedidos(simulado, "POST", RUTA_MANDATOS), 0).json()) as Record<string, unknown>;
      expect(cuerpo.persona_id).toBe(VECINA);
    });

    it("control: si no se pudo saber quién mira, no se saca a nadie y decide la API", async () => {
      const simulado = conConsejo({
        "GET /api/v1/auth/sesion": () => error("error_interno", 500),
        [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [YO_CANDIDATO, ...CANDIDATOS.elementos] }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      await waitFor(() => expect(pedidos(simulado, "GET", "/api/v1/auth/sesion").length).toBeGreaterThan(0));
      // Lo que se controla es que nada cambie: hay que dar tiempo a que la respuesta llegue a la pantalla.
      await new Promise((r) => setTimeout(r, 50));
      expect(opciones()).toEqual(["Zoe Mira (3° C)", "Beto Pérez (1° A)"]);
    });

    it("si el único candidato es quien mira, lo dice y el Consejo y la administración no se ofrecen (FL-MAN-09)", async () => {
      vi.stubGlobal(
        "fetch",
        conConsejo({
          "GET /api/v1/auth/sesion": () => Response.json({ persona_id: YO }),
          [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [YO_CANDIDATO] }),
        }),
      );
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      expect(await within(alta).findByText(/nadie se da un mandato a sí mismo/)).toBeTruthy();
      expect(within(alta).queryByLabelText("Persona (con vínculo confirmado en el edificio)")).toBeNull();
      expect((within(alta).getByLabelText("Tipo de mandato") as HTMLSelectElement).value).toBe("administrador_externo");
    });

    it("FL-MAN-09: sin candidatos, el alta abre en la administración externa, el único tipo posible, y dice por qué faltan los otros", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [] }) }));
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      // Antes se abría en «Consejo de propietarios», que ya se sabía imposible.
      expect(within(within(alta).getByLabelText("Tipo de mandato")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
        "administrador_externo",
      ]);
      expect(within(alta).getByText(ALTA_SIN_NADIE, { exact: false })).toBeTruthy();
      expect(within(alta).getByRole("link", { name: "Ir al Padrón" }).getAttribute("href")).toBe("/padron");
      // Los campos son los de la externa, el tipo que sí se puede dar.
      expect(within(alta).getByLabelText("Razón social o nombre")).toBeTruthy();
      expect(within(alta).getByRole("button", { name: "Dar de alta" })).toBeTruthy();
    });

    it("FL-MAN-09, control: con candidatos se ofrecen los tres tipos, sin la línea", async () => {
      vi.stubGlobal("fetch", conConsejo());
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      expect(within(within(alta).getByLabelText("Tipo de mandato")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
        "miembro_consejo",
        "administrador",
        "administrador_externo",
      ]);
      expect(within(alta).queryByText(ALTA_SIN_NADIE, { exact: false })).toBeNull();
    });

    it("pregunta 8: el Consejo se ofrece solo a quien cumple la condición (`habilita_consejo`)", async () => {
      const INQUILINA = { persona_id: YO.replace(/.$/, "f"), nombre: "Inés Inquilina", unidades: ["2° B"], habilita_consejo: false, mandatos: [] };
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [...CANDIDATOS.elementos, INQUILINA] }) }));
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      const personas = () => within(within(alta).getByLabelText(/^Persona/)).getAllByRole("option").map((o) => o.textContent);
      expect(personas()).toEqual(["Beto Pérez (1° A)"]);
      // La administración sí se le puede dar a cualquiera con vínculo confirmado.
      fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador" } });
      expect(personas()).toEqual(["Beto Pérez (1° A)", "Inés Inquilina (2° B)"]);
    });

    it("pregunta 8: si nadie cumple la condición del Consejo, no se ofrece y se dice por qué", async () => {
      const sinConsejo = { elementos: CANDIDATOS.elementos.map((c) => ({ ...c, habilita_consejo: false })) };
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => Response.json(sinConsejo) }));
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      const tipos = within(within(alta).getByLabelText("Tipo de mandato")).getAllByRole("option").map((o) => o.getAttribute("value"));
      expect(tipos).toEqual(["administrador", "administrador_externo"]);
      expect(within(alta).getByText(CONSEJO_SIN_CANDIDATOS, { exact: false })).toBeTruthy();
    });

    it("FL-MAN-09: sin candidatos y con una administración sin fecha de fin, no se ofrece el alta y se dice por qué", async () => {
      const sinFin = { ...en(MANDATOS.elementos, 1), vigente_hasta: null };
      vi.stubGlobal(
        "fetch",
        conConsejo({
          [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [] }),
          [`GET ${RUTA_MANDATOS}`]: () => Response.json({ elementos: [en(MANDATOS.elementos, 0), sinFin] }),
        }),
      );
      abrir();
      await cargado();
      expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
      expect(screen.getByText(ALTA_SIN_NADIE, { exact: false })).toBeTruthy();
      expect(screen.getByText(/^Ya hay una administración sin fecha de fin: Estudio Sur/)).toBeTruthy();
    });
  });

  describe("dónde queda el aviso", () => {
    it("el rechazo de un envío queda junto a su formulario, no en el estado de la página", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("mandato_superpuesto", 409) }));
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      const aviso = await within(seccion("Dar de alta un mandato")).findByRole("alert");
      expect(aviso.textContent).toBe("La persona ya tiene un mandato de ese tipo en esas fechas.");
      expect(estadoDePantalla().textContent).toBe("");
    });

    it("sin red también queda junto al formulario", async () => {
      vi.stubGlobal(
        "fetch",
        conConsejo({
          [`POST ${RUTA_MANDATOS}`]: () => {
            throw new TypeError("sin red");
          },
        }),
      );
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      expect((await within(seccion("Dar de alta un mandato")).findByRole("alert")).textContent).toMatch(/No se pudo completar el pedido/);
    });

    it("control: un «Listo» va al estado de la página, y arriba de las secciones", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) }));
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      const listo = await screen.findByText(LISTO_ALTA);
      expect(estadoDePantalla().contains(listo)).toBe(true);
      const titulo = screen.getByRole("heading", { level: 1, name: "Mandatos y decisiones" });
      const delante = seccion("Mandatos").compareDocumentPosition(estadoDePantalla()) & Node.DOCUMENT_POSITION_PRECEDING;
      expect(delante).not.toBe(0);
      expect(titulo.compareDocumentPosition(estadoDePantalla()) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    });
  });

  it("una lista que no se pudo consultar no arrastra a las otras", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => error("error_interno", 500) }));
    abrir();
    await cargado();
    expect(screen.getByText(/^No se pudo consultar quiénes pueden recibir un mandato/)).toBeTruthy();
    expect(within(seccion("Decisiones")).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
  });

  describe("con dos edificios", () => {
    const BASE2 = `/api/v1/consorcios/${C2}`;
    const DOS = [...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }];
    const MANDATO_C2 = { ...MANDATOS.elementos[0], id: NUEVO, nombre: "Eva Sosa" };

    function conDos(extra: Respuestas = {}) {
      return conConsejo({
        "GET /api/v1/me/consorcios": () => Response.json(DOS),
        [`GET ${BASE2}/mandatos`]: () => Response.json({ elementos: [MANDATO_C2] }),
        [`GET ${BASE2}/decisiones`]: () => Response.json({ elementos: [] }),
        [`GET ${BASE2}/mandatos/candidatos`]: () => Response.json({ elementos: [] }),
        [`GET ${BASE2}/decisiones/${NUEVO}`]: () => Response.json(DECISION_NUEVA),
        ...extra,
      });
    }

    it("al cambiar de edificio no queda nada del anterior y la clave es otra", async () => {
      let caida = true;
      const simulado = conDos({
        [`POST ${RUTA_DECISIONES}`]: () => {
          throw new TypeError("sin red");
        },
        [`POST ${BASE2}/decisiones`]: () => {
          if (caida) throw new TypeError("sin red");
          return Response.json({ id: NUEVO }, { status: 201 });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      const completar = () => {
        const form = seccion("Registrar una decisión");
        fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
        fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
        fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
      };
      completar();
      fireEvent.click(boton("Registrar decisión"));
      await screen.findByText(/No se pudo completar el pedido/);
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      expect(await screen.findByText("Eva Sosa, Consejo de propietarios")).toBeTruthy();
      expect(screen.queryByText("Ana Díaz, Consejo de propietarios")).toBeNull();
      expect(screen.queryByText(/No se pudo completar el pedido/)).toBeNull();
      expect(screen.getByText("Todavía no hay decisiones registradas.")).toBeTruthy();
      caida = false;
      seccion("Registrar una decisión");
      fireEvent.click(boton("Registrar decisión"));
      await screen.findByText(LISTO_DECISION);
      const enA = en(pedidos(simulado, "POST", RUTA_DECISIONES), 0).headers.get("Idempotency-Key");
      const enB = en(pedidos(simulado, "POST", `${BASE2}/decisiones`), 0).headers.get("Idempotency-Key");
      expect(enB).toMatch(UUID);
      expect(enB).not.toBe(enA);
    });

    it("un detalle del edificio anterior que llega tarde no se muestra en el nuevo", async () => {
      let soltar: () => void = () => undefined;
      const retenido = new Promise<void>((r) => {
        soltar = r;
      });
      const base = conDos({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) });
      const simulado = vi.fn(async (pedido: Request) => {
        if (new URL(pedido.url).pathname === `${RUTA_MANDATOS}/${M_AE}`) await retenido;
        return base(pedido);
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      await screen.findByText("Eva Sosa, Consejo de propietarios");
      soltar();
      await waitFor(() => expect(base).toHaveBeenCalledWith(expect.objectContaining({ url: expect.stringContaining(`${RUTA_MANDATOS}/${M_AE}`) })));
      await new Promise((r) => setTimeout(r, 0));
      expect(screen.queryByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" })).toBeNull();
    });

    it("un rechazo tardío del detalle del edificio anterior no muestra un error en el nuevo", async () => {
      let soltar: () => void = () => undefined;
      const retenido = new Promise<void>((r) => {
        soltar = r;
      });
      const base = conDos();
      let fallos = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (pedido: Request) => {
          if (new URL(pedido.url).pathname === `${RUTA_MANDATOS}/${M_AE}`) {
            await retenido;
            fallos += 1;
            throw new TypeError("sin red");
          }
          return base(pedido);
        }),
      );
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      await screen.findByText("Eva Sosa, Consejo de propietarios");
      soltar();
      await waitFor(() => expect(fallos).toBe(1));
      await new Promise((r) => setTimeout(r, 0));
      expect(screen.queryByText(/No se pudo completar el pedido/)).toBeNull();
    });
  });

  it("el detalle de una baja muestra cuándo se registró, en la hora del edificio", async () => {
    const dadoDeBaja = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "renuncia", finalizado_en: "2026-10-19T15:30:00Z" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(dadoDeBaja) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const titulo = await esperarFicha("Estudio Sur, Administración externa");
    const etiqueta = within(titulo.closest('[role="dialog"]') as HTMLElement).getByText("Baja registrada");
    expect(etiqueta.nextElementSibling?.textContent).toMatch(/19 de octubre de 2026.*12:30/);
  });

  it("al pedir otra ficha, la anterior (y su baja) se cierra aunque la nueva se niegue", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    await abrirBaja();
    fireEvent.click(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }));
    expect(screen.queryByRole("dialog", { name: VENTANA_BAJA })).toBeNull();
    expect(await screen.findByText("Esta cuenta no tiene permiso para ver ese mandato.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("el detalle de una remoción muestra la asamblea que la respaldó", async () => {
    const removido = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "remocion", baja_decision_id: D_EQUIPO, motivo_fin: "Remoción" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(removido) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const titulo = await esperarFicha("Estudio Sur, Administración externa");
    const detalle = titulo.closest('[role="dialog"]') as HTMLElement;
    const etiqueta = within(detalle).getByText("Asamblea que lo removió");
    expect(etiqueta.nextElementSibling?.textContent).toBe("Asamblea del 10 de enero de 2026: Designa al Consejo");
  });

  it("admite la vigencia vacía que acepta el dominio y avisa que nunca rige", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    const hasta = screen.getByLabelText("Hasta (opcional; ese día ya no rige)");
    expect(hasta.getAttribute("min")).toBe("2026-11-01");
    fireEvent.change(hasta, { target: { value: "2026-11-01" } });
    expect(screen.getByText(/el mandato nunca rige/)).toBeTruthy();
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    const cuerpo = (await en(pedidos(simulado, "POST", RUTA_MANDATOS), 0).json()) as Record<string, unknown>;
    expect([cuerpo.desde, cuerpo.hasta]).toEqual(["2026-11-01", "2026-11-01"]);
  });

  it("sin edificios lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => Response.json([]) }));
    abrir();
    expect(await screen.findByText("Esta cuenta todavía no participa de ningún edificio.")).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la ficha y la ventana de la baja abiertas", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    await esperarFicha("Estudio Sur, Administración externa");
    await desplegarBaja();
    const resultado = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
  });

  it("las rutas y el encabezado de reintento son los del contrato", () => {
    const rutas = contrato.paths as Record<string, Record<string, { parameters?: { name: string; required?: boolean }[] }> | undefined>;
    for (const ruta of [
      "/api/v1/consorcios/{consorcio_id}/mandatos",
      "/api/v1/consorcios/{consorcio_id}/decisiones",
      "/api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/baja",
    ]) {
      expect(rutas[ruta]?.post?.parameters?.find((p) => p.name === "Idempotency-Key")?.required, ruta).toBe(true);
    }
    expect(rutas["/api/v1/consorcios/{consorcio_id}/mandatos/candidatos"]?.get).toBeTruthy();
    expect(mensajeDeError(500, undefined, "alta")).toMatch(/El servicio no está disponible/);
    expect(mensajeDeError(403, "csrf_invalido", "baja")).toMatch(/Hay que recargarla/);
    expect(mensajeDeError(422, "codigo_desconocido", "decision")).toBe("Hay que revisar los datos del formulario.");
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

describe("página /mandatos: estados de pantalla mínima (documento 08, §13.1)", () => {
  it("carga: mientras consulta los edificios dice «Consultando…» y no muestra las listas", async () => {
    const { retenido, soltar } = retener(conConsejo(), "/api/v1/me/consorcios");
    vi.stubGlobal("fetch", retenido);
    abrir();

    expect(estadoDePantalla().textContent).toBe("Consultando…");
    expect(screen.queryByRole("region", { name: "Mandatos" })).toBeNull();
    soltar();

    await cargado();
    expect(within(estadoDePantalla()).queryByText("Consultando…")).toBeNull();
  });

  it("MAN-14: mientras llegan las listas, un solo «Consultando…» y ninguna sección", async () => {
    const { retenido, soltar } = retener(conConsejo(), RUTA_MANDATOS);
    vi.stubGlobal("fetch", retenido);
    abrir();

    // Las tres salen juntas (Promise.all en Mandatos.tsx): con una en vilo, ninguna se pinta.
    await waitFor(() => expect(pedidos(retenido, "GET", RUTA_CANDIDATOS)).toHaveLength(1));
    await waitFor(() => expect(estadoDePantalla().textContent).toBe("Consultando…"));
    expect(screen.getAllByText("Consultando…")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Mandatos" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Decisiones" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    soltar();

    await cargado();
    expect(screen.queryByText("Consultando…")).toBeNull();
    expect(screen.getByRole("region", { name: "Decisiones" })).toBeTruthy();
  });

  it("vacía: sin mandatos cargados lo dice", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}`]: () => Response.json({ elementos: [] }) }));
    abrir();

    const mandatos = await screen.findByRole("region", { name: "Mandatos" });
    expect(await within(mandatos).findByText("Todavía no hay mandatos cargados.")).toBeTruthy();
    expect(within(mandatos).queryByRole("list")).toBeNull();
  });

  it("sin red en la primera carga: habla de la conexión", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    abrir();

    expect(await screen.findByText("No se pudieron consultar los edificios. Hay que revisar la conexión y volver en un rato.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Mandatos" })).toBeNull();
  });
});

describe("los dos botones de arriba", () => {
  it("MAN-03: los formularios arrancan cerrados y se abren en su ventana, con el foco en el primer campo", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(screen.queryByRole("dialog")).toBeNull();
    const form = seccion("Registrar una decisión");
    await waitFor(() => expect(document.activeElement).toBe(within(form).getByLabelText("Tipo")));
    // El título está una vez en la ventana; no hay otro título adentro.
    expect(within(form).getAllByRole("heading")).toHaveLength(1);
    expect(within(form).getAllByRole("button", { name: "Cancelar" })).toHaveLength(1);
  });

  it("lo escrito en una ventana queda al cancelarla y volver a abrirla", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const decision = seccion("Registrar una decisión");
    fireEvent.change(within(decision).getByLabelText("Qué se decidió"), { target: { value: "A medio escribir" } });
    fireEvent.click(within(decision).getByRole("button", { name: "Cancelar" }));
    fireEvent.click(within(seccion("Dar de alta un mandato")).getByRole("button", { name: "Cancelar" }));
    expect((within(seccion("Registrar una decisión")).getByLabelText("Qué se decidió") as HTMLTextAreaElement).value).toBe("A medio escribir");
  });

  it("«Cancelar» la cierra y devuelve el foco al botón que la abrió", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    boton("Dar de alta un mandato").focus();
    fireEvent.click(within(seccion("Dar de alta un mandato")).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: "Dar de alta un mandato" })).toBeNull();
    expect(document.activeElement).toBe(boton("Dar de alta un mandato"));
  });
});

describe("lo que no se puede hacer no aparece en /mandatos", () => {
  const conAcciones = (...acciones: string[]) => conConsejo({ [`GET ${BASE}/mis-acciones`]: alcanza(...acciones) });
  const LECTURAS = ["mandatos.ver", "decisiones.ver", "mandatos.ver_candidatos"];
  const FORMULARIO_DECISION = { name: "Registrar una decisión" };
  const FORMULARIO_ALTA = { name: "Dar de alta un mandato" };

  it("quien solo lee no ve botones ni líneas de lo que no puede hacer", async () => {
    vi.stubGlobal("fetch", conAcciones(...LECTURAS));
    abrir();
    await cargado();
    expect(screen.queryByRole("button", { name: "Registrar una decisión" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    expect(screen.queryByText(ALTA_SIN_DECISION_USABLE)).toBeNull();
  });

  it("«Registrar una decisión» se ve con `decisiones.registrar`", async () => {
    vi.stubGlobal("fetch", conAcciones(...LECTURAS, "decisiones.registrar"));
    abrir();
    await cargado();
    expect(screen.getByRole("button", FORMULARIO_DECISION)).toBeTruthy();
    expect(screen.queryByRole("button", FORMULARIO_ALTA)).toBeNull();
  });

  it("«Dar de alta» se ve con `mandatos.alta`", async () => {
    vi.stubGlobal("fetch", conAcciones(...LECTURAS, "mandatos.alta"));
    abrir();
    await cargado();
    expect(await screen.findByRole("button", FORMULARIO_ALTA)).toBeTruthy();
    expect(screen.queryByRole("button", FORMULARIO_DECISION)).toBeNull();
  });

  it("control: «Dar de alta» sin poder elegir la decisión que la respalda no se ofrece", async () => {
    const simulado = conAcciones("mandatos.ver", "mandatos.ver_candidatos", "mandatos.alta");
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    expect(screen.queryByRole("button", FORMULARIO_ALTA)).toBeNull();
    expect(screen.queryByText(/no tiene permiso/i)).toBeNull();
  });

  it("«Dar de baja» se ve con `mandatos.baja`", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${BASE}/mis-acciones`]: alcanza(...LECTURAS, "mandatos.baja"),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      }),
    );
    abrir();
    await abrirBaja();
    expect(screen.getByRole("button", { name: "Dar de baja" })).toBeTruthy();
  });

  it.each([
    [true, 0],
    [false, 1],
  ])("con `es_propio` en %s el detalle se ve y «Dar de baja» aparece %i veces", async (propio, veces) => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${BASE}/mis-acciones`]: alcanza(...LECTURAS, "mandatos.baja"),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json({ ...DETALLE_AE, es_propio: propio }),
      }),
    );
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    const titulo = await esperarFicha("Estudio Sur, Administración externa");
    const detalle = titulo.closest('[role="dialog"]') as HTMLElement;
    // G-44: la baja es un solo botón que abre su ventana.
    expect(within(detalle).queryAllByRole("button", { name: "Dar de baja…" })).toHaveLength(veces);
    expect(screen.queryByRole("dialog", { name: VENTANA_BAJA })).toBeNull();
    // C8G-06: en el propio, en lugar del botón, quién la da.
    expect(within(detalle).queryAllByText(/^Este mandato es de esta cuenta: darlo de baja lo hace otra persona/)).toHaveLength(1 - veces);
    if (veces > 0) {
      fireEvent.click(within(detalle).getByRole("button", { name: "Dar de baja…" }));
      const ventana = await screen.findByRole("dialog", { name: VENTANA_BAJA });
      expect(within(ventana).getAllByRole("button", { name: "Dar de baja" })).toHaveLength(veces);
    }
    expect(within(detalle).getByRole("button", { name: "Cerrar" })).toBeTruthy();
  });

  it("control: sin `mandatos.baja` el detalle se ve y la baja no", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${BASE}/mis-acciones`]: alcanza(...LECTURAS),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      }),
    );
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    expect(await esperarFicha("Estudio Sur, Administración externa")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "Dar de baja" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de baja" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de baja…" })).toBeNull();
  });

  it.each([
    ["mandatos.ver", "Mandatos", RUTA_MANDATOS],
    ["decisiones.ver", "Decisiones", RUTA_DECISIONES],
  ])("sin `%s` su sección no aparece y su lista no se pide", async (falta, seccionQueFalta, ruta) => {
    const simulado = conAcciones(...LECTURAS.filter((a) => a !== falta));
    vi.stubGlobal("fetch", simulado);
    abrir();
    const otra = seccionQueFalta === "Mandatos" ? "Decisiones" : "Mandatos";
    expect(await screen.findByRole("region", { name: otra })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Consultando…")).toBeNull());
    expect(screen.queryByRole("region", { name: seccionQueFalta })).toBeNull();
    expect(pedidos(simulado, "GET", ruta)).toHaveLength(0);
    expect(screen.queryByText(/no tiene permiso/)).toBeNull();
  });

  it("con solo `decisiones.registrar` queda el formulario, sin listas y sin pedirlas", async () => {
    const simulado = conAcciones("decisiones.registrar");
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByRole("button", FORMULARIO_DECISION)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Mandatos" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Decisiones" })).toBeNull();
    for (const ruta of [RUTA_MANDATOS, RUTA_DECISIONES, RUTA_CANDIDATOS]) expect(pedidos(simulado, "GET", ruta)).toHaveLength(0);
  });

  it("un 403 de una sola lista saca esa sección y deja las otras", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => error("sin_permiso", 403) }));
    abrir();
    await cargado();
    expect(screen.queryByRole("region", { name: "Decisiones" })).toBeNull();
    expect(screen.getByRole("region", { name: "Mandatos" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
    expect(screen.queryByText(/no tiene permiso/)).toBeNull();
  });
});

describe("tanda 6 de fricción en /mandatos", () => {
  const RUTA_DOCUMENTOS = `${BASE}/documentos`;
  const SHA_ACTA = "c".repeat(64);
  const DOC_ACTA = "abababab-abab-4bab-8bab-abababababab";
  const V_ACTA = "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd";
  const version = (id: string, sha256: string, estado = "disponible") => ({
    id,
    estado,
    motivo_rechazo: null,
    tipo: "application/pdf",
    tamano: 100,
    sha256,
    subida_en: "2026-10-20T15:00:00Z",
  });
  const DOCUMENTOS = {
    documentos: [
      {
        id: DOC_ACTA,
        categoria: "actas_y_decisiones",
        titulo: "Acta 14 de asamblea",
        fecha: "2026-10-01",
        cargado_en: "2026-10-02T10:00:00Z",
        versiones: [version(V_ACTA, SHA_ACTA)],
      },
      // Sin archivo disponible, u otra categoría: no se ofrecen como acta.
      {
        id: NUEVO,
        categoria: "actas_y_decisiones",
        titulo: "Acta en revisión",
        fecha: "2026-10-03",
        cargado_en: "2026-10-03T10:00:00Z",
        versiones: [version(VECINA, "d".repeat(64), "en_cuarentena")],
      },
      {
        id: D_OTRA,
        categoria: "seguros",
        titulo: "Póliza",
        fecha: "2026-10-04",
        cargado_en: "2026-10-04T10:00:00Z",
        versiones: [version(M_MC, "e".repeat(64))],
      },
    ],
  };

  async function llenarDecision() {
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Designa al Consejo" } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 14, libro 3" } });
    return form;
  }

  it("G-15: el acta se elige de Documentos y la decisión la cita por la huella de su archivo", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_DOCUMENTOS}`]: () => Response.json(DOCUMENTOS),
      [`POST ${RUTA_DECISIONES}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const form = await llenarDecision();
    const acta = (await within(form).findByLabelText("Acta (opcional)")) as HTMLSelectElement;
    // Solo las actas con un archivo disponible.
    expect([...acta.options].map((o) => o.textContent)).toEqual(["Sin un acta de Documentos", "Acta 14 de asamblea (1 de octubre de 2026)"]);
    fireEvent.change(acta, { target: { value: DOC_ACTA } });
    fireEvent.click(boton("Registrar decisión"));
    await screen.findByText(LISTO_DECISION);
    const cuerpo = (await en(pedidos(simulado, "POST", RUTA_DECISIONES), 0).json()) as Record<string, unknown>;
    expect(cuerpo.sha256).toBe(SHA_ACTA);
  });

  it("G-15 y MAN-11: la ficha de la decisión abre su acta; sin el documento en Documentos dice «Registrada», nunca la huella", async () => {
    let sha = SHA_ACTA;
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_DOCUMENTOS}`]: () => Response.json(DOCUMENTOS),
        [`GET ${RUTA_DECISIONES}/${D_EQUIPO}`]: () => Response.json({ ...detalleDecision(D_EQUIPO, false), sha256: sha }),
      }),
    );
    abrir();
    await cargado();
    const ver = () => fireEvent.click(screen.getByRole("button", { name: fila("Asamblea del 10 de enero de 2026") }));
    ver();
    const ficha = await esperarFicha("Asamblea del 10 de enero de 2026");
    expect(await within(ficha).findByText("Acta 14 de asamblea")).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: "Ver el acta" })).toBeTruthy();
    expect(screen.queryByText("Huella del acta")).toBeNull();
    // Control: una huella sin documento vigente que la tenga: «Registrada», sin la huella ni «Ver el acta».
    fireEvent.click(boton("Cerrar"));
    sha = "f".repeat(64);
    ver();
    const otra = await esperarFicha("Asamblea del 10 de enero de 2026");
    expect(await within(otra).findByText("Registrada")).toBeTruthy();
    expect(screen.queryByText("f".repeat(64))).toBeNull();
    expect(screen.queryByText("Huella del acta")).toBeNull();
    expect(screen.queryByRole("button", { name: "Ver el acta" })).toBeNull();
  });

  it("G-15: un acta retirada de Documentos se dice junto al campo", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_DOCUMENTOS}`]: () => Response.json(DOCUMENTOS),
        [`POST ${RUTA_DECISIONES}`]: () => error("documento_invalido", 422, { campos: ["body.sha256"], mensajes: {} }),
      }),
    );
    abrir();
    const form = await llenarDecision();
    fireEvent.change(await within(form).findByLabelText("Acta (opcional)"), { target: { value: DOC_ACTA } });
    fireEvent.click(boton("Registrar decisión"));
    expect(await within(form).findByText("El acta elegida se retiró de Documentos: no respalda una decisión. Elegir otra.")).toBeTruthy();
    expect(within(form).getByLabelText("Acta (opcional)").getAttribute("aria-invalid")).toBe("true");
  });

  describe("G-40: la invitación de la administración externa se emite y se aprueba acá", () => {
    const INVITACIONES = `${RUTA_MANDATOS}/${M_AE}/invitaciones`;
    const INV = "efefefef-efef-4fef-8fef-efefefefefef";
    const invitacion = (extra: Record<string, unknown>) => ({
      id: INV,
      estado: "por_aprobar",
      correo: "admin@estudiosur.com",
      referencia: "Contrato del 01-10, cláusula 12",
      origen: "api",
      emitida_por_mi: false,
      creada_en: "2026-11-10T13:00:00Z",
      aprobada_en: null,
      vence_en: null,
      aceptada_en: null,
      revocada_en: null,
      ...extra,
    });
    const verExterno = async () => {
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      return esperarFicha("Estudio Sur, Administración externa");
    };

    it("sin invitación, «Invitar a la administración…» abre su ventana; queda por aprobar y la ficha lo dice una vez", async () => {
      let lista: unknown[] = [];
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${INVITACIONES}`]: () => Response.json({ elementos: lista }),
        [`POST ${INVITACIONES}`]: () => {
          lista = [invitacion({ emitida_por_mi: true })];
          return Response.json({ id: INV, mandato_id: M_AE, estado: "por_aprobar" }, { status: 201 });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const ficha = await verExterno();
      // MAN-06: el formulario no está desplegado en la ficha; lo abre su botón.
      expect(within(ficha).queryByLabelText("Correo de la administración")).toBeNull();
      expect(within(ficha).getByText("Sin cuenta asociada")).toBeTruthy();
      fireEvent.click(within(ficha).getByRole("button", { name: "Invitar a la administración…" }));
      const form = await screen.findByRole("dialog", { name: "Invitar a la administración" });
      fireEvent.change(within(form).getByLabelText("Correo de la administración"), { target: { value: " admin@estudiosur.com " } });
      fireEvent.change(within(form).getByLabelText("De dónde sale ese correo (contrato, nota u otro)"), {
        target: { value: "Contrato del 01-10, cláusula 12" },
      });
      fireEvent.click(within(form).getByRole("button", { name: "Invitar" }));
      expect(await within(ficha).findByText("Listo: la invitación quedó por aprobar.")).toBeTruthy();
      expect(screen.queryByRole("dialog", { name: "Invitar a la administración" })).toBeNull();
      const post = en(pedidos(simulado, "POST", INVITACIONES), 0);
      expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
      expect(await post.json()).toEqual({ correo: "admin@estudiosur.com", referencia: "Contrato del 01-10, cláusula 12" });
      // Quien la emitió no la aprueba (A7): el estado lo dice una vez, sin el botón (MAN-09).
      expect(await within(ficha).findByText("Invitación por aprobar: la aprueba otra persona del Consejo o de la administración.")).toBeTruthy();
      expect(within(ficha).getAllByText(/la aprueba otra persona/)).toHaveLength(1);
      expect(within(ficha).queryByRole("button", { name: "Aprobar la invitación" })).toBeNull();
      expect(within(ficha).queryByText("Emitida el")).toBeNull();
    });

    it("otra persona ve el correo y de dónde sale, y la aprueba: sale el correo", async () => {
      let lista = [invitacion({})];
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${INVITACIONES}`]: () => Response.json({ elementos: lista }),
        [`POST ${BASE}/invitaciones-mandato/${INV}/aprobar`]: () => {
          lista = [invitacion({ estado: "pendiente", correo: null, referencia: null, vence_en: "2026-11-20T13:00:00Z" })];
          return Response.json({ id: INV, estado: "pendiente", vence_en: "2026-11-20T13:00:00Z" });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const ficha = await verExterno();
      expect(within(ficha).getByText("admin@estudiosur.com")).toBeTruthy();
      expect(within(ficha).getByText("Contrato del 01-10, cláusula 12")).toBeTruthy();
      fireEvent.click(within(ficha).getByRole("button", { name: "Aprobar la invitación" }));
      expect(await within(ficha).findByText(/^Listo: la invitación quedó aprobada y salió el correo con el enlace\. Vence el/)).toBeTruthy();
      expect(await within(ficha).findByText(/^Invitación enviada, vence el .*Falta que la administración la acepte\.$/)).toBeTruthy();
      // Ningún botón invita a repetir lo hecho.
      expect(within(ficha).queryByRole("button", { name: "Aprobar la invitación" })).toBeNull();
      expect(pedidos(simulado, "POST", `${BASE}/invitaciones-mandato/${INV}/aprobar`)).toHaveLength(1);
    });

    it("retirar pide confirmación en su ventana y deja de nuevo la forma de invitar", async () => {
      let lista = [invitacion({ estado: "pendiente", correo: null, referencia: null, vence_en: "2026-11-20T13:00:00Z" })];
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${INVITACIONES}`]: () => Response.json({ elementos: lista }),
        [`POST ${BASE}/invitaciones-mandato/${INV}/revocar`]: () => {
          lista = [invitacion({ estado: "revocada", correo: null, referencia: null, revocada_en: "2026-11-12T13:00:00Z" })];
          return new Response(null, { status: 204 });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const ficha = await verExterno();
      fireEvent.click(within(ficha).getByRole("button", { name: "Retirar la invitación…" }));
      const ventana = await screen.findByRole("dialog", { name: "Retirar la invitación" });
      expect(pedidos(simulado, "POST", `${BASE}/invitaciones-mandato/${INV}/revocar`)).toHaveLength(0);
      fireEvent.click(within(ventana).getByRole("button", { name: "Sí, retirarla" }));
      expect(await within(ficha).findByText("Listo: la invitación se retiró.")).toBeTruthy();
      expect(await within(ficha).findByText("Sin cuenta asociada: la invitación anterior se retiró.")).toBeTruthy();
      expect(within(ficha).getByRole("button", { name: "Invitar a la administración…" })).toBeTruthy();
    });

    it("control: con cuenta asociada, o sin la facultad, no hay invitación que mostrar ni que pedir", async () => {
      const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json({ ...DETALLE_AE, con_cuenta: true }) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      const ficha = await esperarFicha("Estudio Sur, Administración externa");
      expect(within(ficha).getByText("Con cuenta asociada")).toBeTruthy();
      expect(within(ficha).queryByRole("button", { name: "Invitar a la administración…" })).toBeNull();
      expect(pedidos(simulado, "GET", INVITACIONES)).toHaveLength(0);
      cleanup();
      const sinFacultad = conConsejo({
        [`GET ${BASE}/mis-acciones`]: alcanza("mandatos.ver", "decisiones.ver"),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      });
      vi.stubGlobal("fetch", sinFacultad);
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
      const otra = await esperarFicha("Estudio Sur, Administración externa");
      expect(within(otra).queryByRole("button", { name: "Invitar a la administración…" })).toBeNull();
      expect(pedidos(sinFacultad, "GET", INVITACIONES)).toHaveLength(0);
    });
  });

  describe("G-41: un mandato pendiente de revisión dice por qué y se puede mantener", () => {
    const PENDIENTE = {
      ...DETALLE_AE,
      id: M_MC,
      tipo: "miembro_consejo",
      nombre: "Ana Díaz",
      cuit: null,
      con_cuenta: true,
      estado: "pendiente_revision",
      condicion_cumplida_hoy: true,
    };

    it("C8G-04: si la persona hoy no cumple la condición, no ofrece mantenerlo y dice dónde se resuelve", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => Response.json({ ...PENDIENTE, condicion_cumplida_hoy: false }) }));
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }));
      const ficha = await esperarFicha("Ana Díaz, Consejo de propietarios");
      const linea = await within(ficha).findByText(new RegExp(`^${SIN_CONDICION_PARA_MANTENER.slice(0, 40)}`));
      expect(within(linea).getByRole("link", { name: "Ir al Padrón" }).getAttribute("href")).toBe("/padron");
      expect(within(ficha).queryByRole("button", { name: "Mantener el mandato…" })).toBeNull();
    });

    it("«Mantener el mandato…» abre su ventana con el porqué; con una asamblea y una fecha, lo mantiene", async () => {
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => Response.json(PENDIENTE),
        [`POST ${RUTA_MANDATOS}/${M_MC}/revision`]: () =>
          Response.json({ id: NUEVO, mandato_id: M_MC, eficaz_desde: "2026-11-20" }, { status: 201 }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      fireEvent.click(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }));
      const ficha = await esperarFicha("Ana Díaz, Consejo de propietarios");
      // Control C8G-04: con la condición cumplida no hay línea.
      expect(within(ficha).queryByText(new RegExp(`^${SIN_CONDICION_PARA_MANTENER.slice(0, 40)}`))).toBeNull();
      // MAN-06: no está desplegado en la ficha.
      expect(within(ficha).queryByLabelText("Asamblea que lo mantiene")).toBeNull();
      fireEvent.click(within(ficha).getByRole("button", { name: "Mantener el mandato…" }));
      const ventana = await screen.findByRole("dialog", { name: "Mantener el mandato" });
      expect(within(ventana).getByText(CAUSA_REVISION)).toBeTruthy();
      // FL-DEC-04: «Desde cuándo vuelve a operar» no ofrece días antes de hoy.
      expect(within(ventana).getByLabelText("Desde cuándo vuelve a operar").getAttribute("min")).toBe(hoyDelDispositivo());
      fireEvent.change(within(ventana).getByLabelText("Asamblea que lo mantiene"), { target: { value: D_EQUIPO } });
      fireEvent.change(within(ventana).getByLabelText("Desde cuándo vuelve a operar"), { target: { value: "2026-11-20" } });
      fireEvent.click(within(ventana).getByRole("button", { name: "Mantener el mandato" }));
      expect(
        await screen.findByText("Listo: el mandato de Ana Díaz se mantiene y vuelve a operar desde el 20 de noviembre de 2026."),
      ).toBeTruthy();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(await en(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_MC}/revision`), 0).json()).toEqual({
        decision_id: D_EQUIPO,
        eficaz_desde: "2026-11-20",
      });
    });

    it("C8G-06: el propio no ofrece mantenerlo ni darlo de baja y dice quién lo hace; uno vigente no ofrece mantenerlo", async () => {
      let detalle: object = { ...PENDIENTE, es_propio: true };
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => Response.json(detalle) }));
      abrir();
      await cargado();
      const ver = () => fireEvent.click(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }));
      ver();
      const propio = await esperarFicha("Ana Díaz, Consejo de propietarios");
      expect(within(propio).queryByRole("button", { name: "Mantener el mandato…" })).toBeNull();
      expect(within(propio).queryByRole("button", { name: "Dar de baja…" })).toBeNull();
      expect(
        within(propio).getByText(
          "Este mandato es de esta cuenta: mantenerlo o darlo de baja lo hace otra persona del Consejo o de la administración.",
        ),
      ).toBeTruthy();
      fireEvent.click(boton("Cerrar"));
      detalle = { ...PENDIENTE, estado: "vigente" };
      ver();
      await screen.findByRole("button", { name: "Dar de baja…" });
      expect(screen.queryByRole("button", { name: "Mantener el mandato…" })).toBeNull();
      expect(screen.queryByText(/^Este mandato es de esta cuenta/)).toBeNull();
    });
  });
});

describe("D-34: los mandatos que ya no rigen van plegados en «Mandatos anteriores»", () => {
  const anterior = (id: string, nombre: string, estado: string) => ({
    id, tipo: "miembro_consejo", nombre, cuit: null, vigente_desde: "2025-01-01", vigente_hasta: "2026-01-01", estado, con_cuenta: false,
  });
  const CON_ANTERIORES = {
    elementos: [
      ...MANDATOS.elementos,
      anterior("e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1", "Ciro Baja", "dado_de_baja"),
      anterior("e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2", "Dora Fin", "terminado"),
      anterior("e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3", "Eloy Error", "error_carga"),
      { ...anterior("e4e4e4e4-e4e4-4e4e-8e4e-e4e4e4e4e4e4", "Fede Futuro", "futuro"), vigente_desde: "2027-01-01", vigente_hasta: null },
    ],
  };

  it("los vigentes y los que van a regir quedan a la vista; los demás, plegados con su cantidad", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}`]: () => Response.json(CON_ANTERIORES) }));
    abrir();
    await cargado();
    const lista = screen.getByRole("list", { name: "Mandatos" });
    expect(within(lista).getByRole("button", { name: fila("Fede Futuro, Consejo de propietarios") })).toBeTruthy();
    expect(within(lista).getByRole("button", { name: fila("Estudio Sur, Administración externa") })).toBeTruthy();
    const anteriores = screen.getByRole("list", { name: "Mandatos anteriores" });
    const plegable = anteriores.closest("details");
    expect(plegable?.open).toBe(false);
    expect(plegable?.querySelector("summary")?.textContent).toBe("Mandatos anteriores (3)");
    for (const nombre of ["Ciro Baja", "Dora Fin", "Eloy Error"]) {
      expect(within(lista).queryByText(new RegExp(nombre))).toBeNull();
      expect(within(anteriores).getByRole("button", { name: fila(`${nombre}, Consejo de propietarios`) })).toBeTruthy();
    }
  });

  it("control: sin mandatos anteriores no hay plegable", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(screen.queryByText(/^Mandatos anteriores/)).toBeNull();
  });
});

describe("tanda 11 de fricción en /mandatos", () => {
  const RUTA_DOCUMENTOS = `${BASE}/documentos`;
  const DOC_NUEVO = "12121212-1212-4212-8212-121212121212";
  const V_NUEVA = "34343434-3434-4434-8434-343434343434";
  const SHA_NUEVA = "a".repeat(64);
  const ESTADO_VERSION = `${RUTA_DOCUMENTOS}/${DOC_NUEVO}/versiones/${V_NUEVA}/estado`;
  /** Voseo o tuteo en los textos de la pantalla (regla del propietario: impersonal con infinitivo). */
  const VOSEO = /(^|[^\p{L}])(subí|elegí|cargá|escribí|completá|tu|tus|vos|podés|tenés|querés)(?=$|[^\p{L}])/iu;
  const verExterno = async () => {
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    return esperarFicha("Estudio Sur, Administración externa");
  };

  describe("G-40: desasociar la cuenta de la administración externa", () => {
    const CON_CUENTA = { ...DETALLE_AE, con_cuenta: true };
    const DESASOCIAR = `${RUTA_MANDATOS}/${M_AE}/desasociar-persona`;
    const LISTO = "Listo: la cuenta quedó desasociada del mandato.";

    it("se abre en su ventana; con el motivo la desasocia y la ficha vuelve sin cuenta y con la invitación", async () => {
      let detalle: object = CON_CUENTA;
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(detalle),
        [`POST ${DESASOCIAR}`]: () => {
          detalle = DETALLE_AE;
          return Response.json({ mandato_id: M_AE, sesiones_revocadas: 2 });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const ficha = await verExterno();
      expect(within(ficha).getByText("Con cuenta asociada")).toBeTruthy();
      // Al abrir la ficha no está desplegada.
      expect(screen.queryByLabelText("Por qué esa cuenta no es la de la administración")).toBeNull();
      const desplegar = boton("Desasociar la cuenta…");
      expect(desplegar.className).toBe("secundario peligro");
      fireEvent.click(desplegar);
      const ventana = await screen.findByRole("dialog", { name: "Desasociar la cuenta" });
      const campo = within(ventana).getByLabelText("Por qué esa cuenta no es la de la administración");
      await waitFor(() => expect(document.activeElement).toBe(campo));
      expect(within(ventana).getByText(AYUDA_DESASOCIAR)).toBeTruthy();
      expect(ventana.textContent).not.toMatch(VOSEO);
      fireEvent.change(campo, { target: { value: " La cuenta es de un empleado que ya no está " } });
      fireEvent.click(within(ventana).getByRole("button", { name: "Desasociar la cuenta" }));
      expect(await within(ficha).findByText(LISTO)).toBeTruthy();
      const post = en(pedidos(simulado, "POST", DESASOCIAR), 0);
      expect(post.headers.get("X-CSRF-Token")).toBe("abc123");
      expect(post.headers.get("Idempotency-Key")).toMatch(UUID);
      expect(await post.json()).toEqual({ referencia: "La cuenta es de un empleado que ya no está" });
      // La ficha se vuelve a pedir: sin cuenta, con la invitación y sin otra desasociación.
      expect(await within(ficha).findByText("Sin cuenta asociada")).toBeTruthy();
      expect(await within(ficha).findByRole("button", { name: "Invitar a la administración…" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Desasociar la cuenta…" })).toBeNull();
      expect(within(ficha).getByText(LISTO)).toBeTruthy();
      expect(pedidos(simulado, "GET", `${RUTA_MANDATOS}/${M_AE}`)).toHaveLength(2);
      expect(pedidos(simulado, "POST", DESASOCIAR)).toHaveLength(1);
    });

    it("«Cancelar» cierra su ventana sin mandar nada", async () => {
      const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(CON_CUENTA) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await verExterno();
      fireEvent.click(boton("Desasociar la cuenta…"));
      fireEvent.change(await screen.findByLabelText("Por qué esa cuenta no es la de la administración"), { target: { value: "Otra" } });
      fireEvent.click(boton("Cancelar"));
      expect(screen.queryByLabelText("Por qué esa cuenta no es la de la administración")).toBeNull();
      expect(boton("Desasociar la cuenta…")).toBeTruthy();
      expect(boton("Dar de baja…")).toBeTruthy();
      expect(pedidos(simulado, "POST", DESASOCIAR)).toHaveLength(0);
    });

    it("control: no se ofrece sin la facultad, sin cuenta, en el propio ni en un mandato del Consejo", async () => {
      const todas = accionesDeLasPoliticas();
      const casos = [
        { acciones: todas.filter((a) => a !== "mandatos.desasociar_persona"), id: M_AE, detalle: CON_CUENTA, titulo: "Estudio Sur, Administración externa" },
        { acciones: todas, id: M_AE, detalle: DETALLE_AE, titulo: "Estudio Sur, Administración externa" },
        { acciones: todas, id: M_AE, detalle: { ...CON_CUENTA, es_propio: true }, titulo: "Estudio Sur, Administración externa" },
        {
          acciones: todas,
          id: M_MC,
          detalle: { ...CON_CUENTA, id: M_MC, tipo: "miembro_consejo", nombre: "Ana Díaz", cuit: null },
          titulo: "Ana Díaz, Consejo de propietarios",
        },
      ];
      for (const caso of casos) {
        const simulado = conConsejo({
          [`GET ${BASE}/mis-acciones`]: () => Response.json({ acciones: caso.acciones }),
          [`GET ${RUTA_MANDATOS}/${caso.id}`]: () => Response.json(caso.detalle),
        });
        vi.stubGlobal("fetch", simulado);
        abrir();
        await cargado();
        fireEvent.click(screen.getByRole("button", { name: fila(caso.titulo) }));
        await esperarFicha(caso.titulo);
        expect(screen.queryByRole("button", { name: /^Desasociar la cuenta/ })).toBeNull();
        expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${caso.id}/desasociar-persona`)).toHaveLength(0);
        cleanup();
      }
    });

    it("un 409 «sin cuenta» lo dice en la ficha y la vuelve a pedir; la separación dice el motivo de la API en la ventana", async () => {
      let detalle: object = CON_CUENTA;
      let respuesta = () => {
        detalle = DETALLE_AE;
        return error("mandato_sin_persona", 409);
      };
      const simulado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(detalle),
        [`POST ${DESASOCIAR}`]: () => respuesta(),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const ficha = await verExterno();
      fireEvent.click(boton("Desasociar la cuenta…"));
      fireEvent.change(await screen.findByLabelText("Por qué esa cuenta no es la de la administración"), { target: { value: "No es de ellos" } });
      fireEvent.click(boton("Desasociar la cuenta"));
      expect(await within(ficha).findByText("El mandato ya no tiene una cuenta asociada: la ficha ya está actualizada.")).toBeTruthy();
      expect(await within(ficha).findByText("Sin cuenta asociada")).toBeTruthy();
      expect(pedidos(simulado, "GET", `${RUTA_MANDATOS}/${M_AE}`)).toHaveLength(2);
      cleanup();

      const SEPARACION = "El mandato es de esta cuenta: lo hace otra persona.";
      detalle = CON_CUENTA;
      respuesta = () =>
        Response.json(
          { codigo: "ya_participo", mensaje: SEPARACION, detalle: { separacion: "no_es_su_propio_mandato" }, id_correlacion: "c" },
          { status: 403 },
        );
      const separado = conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(detalle),
        [`POST ${DESASOCIAR}`]: () => respuesta(),
      });
      vi.stubGlobal("fetch", separado);
      abrir();
      await verExterno();
      fireEvent.click(boton("Desasociar la cuenta…"));
      fireEvent.change(await screen.findByLabelText("Por qué esa cuenta no es la de la administración"), { target: { value: "No es de ellos" } });
      fireEvent.click(boton("Desasociar la cuenta"));
      const ventana = screen.getByRole("dialog", { name: "Desasociar la cuenta" });
      expect(await within(ventana).findByText(SEPARACION)).toBeTruthy();
      // Lo escrito queda para corregir o cancelar; la ficha no se vuelve a pedir.
      expect(within(ventana).getByLabelText("Por qué esa cuenta no es la de la administración")).toBeTruthy();
      expect(pedidos(separado, "GET", `${RUTA_MANDATOS}/${M_AE}`)).toHaveLength(1);
    });

    it("los mensajes propios de la desasociación, y la invitación sigue diciendo lo suyo", () => {
      expect(mensajeDeError(403, "sin_permiso", "desasociar")).toBe("El mandato de esta cuenta no incluye desasociar la cuenta de la administración.");
      expect(mensajeDeError(404, "no_encontrado", "desasociar")).toBe("No se encontró ese mandato en este edificio.");
      expect(mensajeDeError(409, "mandato_no_asociable", "desasociar")).toBe("Solo se desasocia así la cuenta de una administración externa.");
      expect(mensajeDeError(409, "mandato_no_asociable", "invitar")).toBe("Solo la cuenta de una administración externa se asocia por invitación.");
      expect(AYUDA_DESASOCIAR).not.toMatch(VOSEO);
    });
  });

  describe("G-15: subir el acta desde «Registrar una decisión»", () => {
    const version = (estado: string) => ({
      id: V_NUEVA,
      estado,
      motivo_rechazo: estado === "rechazado" ? "infectado" : null,
      tipo: "application/pdf",
      tamano: 9,
      sha256: SHA_NUEVA,
      subida_en: "2026-11-14T15:00:00Z",
    });
    const documento = (estado: string) => ({
      id: DOC_NUEVO,
      categoria: "actas_y_decisiones",
      titulo: "Acta 15 de asamblea",
      fecha: "2026-10-01",
      cargado_en: "2026-11-14T15:00:00Z",
      versiones: [version(estado)],
    });
    /** Un archivo como el del navegador: el `File` de Node, que `Request` manda tal cual (ver Documentos.test). */
    const pdf = (nombre: string) => new ArchivoNode(["%PDF-1.7\n"], nombre, { type: "application/pdf" }) as unknown as File;
    const LISTO_ELEGIDA = /^Listo: el acta «Acta 15 de asamblea» pasó la revisión del antivirus y quedó elegida\.$/;
    /** MAN-04: el aviso de la subida dice solo lo que hace falta para seguir. */
    const LISTO_SUBIDA = "Listo: el acta se subió. Se elige sola cuando termine la revisión del antivirus.";

    /** Documentos sin actas hasta que se sube una; `estadoEnLista` es el de su archivo en la lista. */
    function conSubida(estadoEnLista: () => string, extra: Respuestas = {}) {
      let subida = false;
      return conConsejo({
        [`GET ${RUTA_DOCUMENTOS}`]: () => Response.json({ documentos: subida ? [documento(estadoEnLista())] : [] }),
        [`POST ${RUTA_DOCUMENTOS}`]: () => Response.json({ id: DOC_NUEVO }, { status: 201 }),
        [`POST ${RUTA_DOCUMENTOS}/${DOC_NUEVO}/versiones`]: () => {
          subida = true;
          return Response.json({ id: V_NUEVA, estado: "en_cuarentena", sha256: SHA_NUEVA, tamano: 9, tipo: "application/pdf" }, { status: 201 });
        },
        [`POST ${RUTA_DECISIONES}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
        ...extra,
      });
    }

    async function llenarDecision() {
      await cargado();
      const form = seccion("Registrar una decisión");
      fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
      fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Designa al Consejo" } });
      fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 15, libro 3" } });
      await within(form).findByLabelText("Acta (opcional)");
      return form;
    }

    /** Abre la ventana del acta (MAN-04), elige el PDF y devuelve la ventana. */
    function elegirActa(form: HTMLElement) {
      fireEvent.click(within(form).getByRole("button", { name: "Subir el acta" }));
      const subida = screen.getByRole("dialog", { name: "Subir el acta" });
      fireEvent.change(within(subida).getByLabelText("Archivo del acta"), { target: { files: [pdf("Acta 15 de asamblea.pdf")] } });
      return subida;
    }

    it("MAN-04: «Subir el acta» abre su ventana sobre la decisión; disponible, queda elegida y la decisión la cita", async () => {
      const simulado = conSubida(() => "disponible");
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      expect(screen.queryByLabelText("Archivo del acta")).toBeNull();
      // MAN-04: una sola ayuda, sin anunciar el botón que está debajo.
      expect(within(form).queryByText(/se puede subir desde acá/)).toBeNull();
      const subida = elegirActa(form);
      expect(subida.textContent).not.toMatch(VOSEO);
      // Dentro del formulario de la decisión no hay otro formulario.
      expect(form.contains(subida)).toBe(false);
      expect(within(subida).getAllByRole("button", { name: "Cancelar" })).toHaveLength(1);
      // El nombre del archivo propone el título.
      expect((within(subida).getByLabelText("Título del acta") as HTMLInputElement).value).toBe("Acta 15 de asamblea");
      fireEvent.click(within(subida).getByRole("button", { name: "Subir el acta" }));
      expect(await within(form).findByText(LISTO_ELEGIDA)).toBeTruthy();
      expect((within(form).getByLabelText("Acta (opcional)") as HTMLSelectElement).value).toBe(DOC_NUEVO);
      expect(screen.queryByRole("dialog", { name: "Subir el acta" })).toBeNull();
      const creacion = en(pedidos(simulado, "POST", RUTA_DOCUMENTOS), 0);
      expect(creacion.headers.get("X-CSRF-Token")).toBe("abc123");
      expect(creacion.headers.get("Idempotency-Key")).toMatch(UUID);
      // En «Actas y decisiones», con la fecha del acto.
      expect(await creacion.json()).toEqual({ categoria: "actas_y_decisiones", titulo: "Acta 15 de asamblea", fecha: "2026-10-01" });
      const archivo = en(pedidos(simulado, "POST", `${RUTA_DOCUMENTOS}/${DOC_NUEVO}/versiones`), 0);
      expect(archivo.headers.get("Content-Type")).toBe("application/pdf");
      expect(archivo.headers.get("Idempotency-Key")).toMatch(UUID);
      expect(await archivo.text()).toBe("%PDF-1.7\n");
      // Subir el acta no registra la decisión: la registra su propio botón, con la huella del acta.
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(0);
      fireEvent.click(boton("Registrar decisión"));
      await screen.findByText(LISTO_DECISION);
      const cuerpo = (await en(pedidos(simulado, "POST", RUTA_DECISIONES), 0).json()) as Record<string, unknown>;
      expect(cuerpo.sha256).toBe(SHA_NUEVA);
    });

    it("en revisión lo dice y no la ofrece; cuando el antivirus la aprueba, queda elegida", async () => {
      let estado = "en_cuarentena";
      const simulado = conSubida(() => estado, {
        [`GET ${ESTADO_VERSION}`]: () => {
          estado = "disponible";
          return Response.json({ estado: "disponible", motivo_rechazo: null });
        },
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      const listo = await within(form).findByText(LISTO_SUBIDA);
      await waitFor(() => expect(document.activeElement).toBe(listo));
      // MAN-05: subida el acta, no se ofrece subir otra junto al «Listo».
      expect(within(form).queryByRole("button", { name: "Subir el acta" })).toBeNull();
      const acta = within(form).getByLabelText("Acta (opcional)") as HTMLSelectElement;
      await waitFor(() => expect(pedidos(simulado, "GET", RUTA_DOCUMENTOS)).toHaveLength(2));
      expect(acta.value).toBe("");
      expect([...acta.options].map((o) => o.value)).toEqual([""]);
      // La consulta del antivirus corre sola cada pocos segundos.
      expect(await within(form).findByText(LISTO_ELEGIDA)).toBeTruthy();
      expect((within(form).getByLabelText("Acta (opcional)") as HTMLSelectElement).value).toBe(DOC_NUEVO);
      expect(pedidos(simulado, "GET", ESTADO_VERSION).length).toBeGreaterThan(0);
      // MAN-05: tampoco con el acta ya elegida.
      expect(within(form).queryByRole("button", { name: "Subir el acta" })).toBeNull();
    });

    it("al cambiar de edificio, la respuesta del antivirus que estaba en camino no trae las actas del anterior (Codex en #245)", async () => {
      let soltar: (r: Response) => void = () => undefined;
      const C2_BASE = `/api/v1/consorcios/${C2}`;
      const simulado = conSubida(() => "en_cuarentena", {
        "GET /api/v1/me/consorcios": () => Response.json([...CONSORCIOS, { id: C2, nombre: "Edificio Verde", vinculos: [] }]),
        [`GET ${C2_BASE}/mandatos`]: () => Response.json({ elementos: [] }),
        [`GET ${C2_BASE}/decisiones`]: () => Response.json({ elementos: [] }),
        [`GET ${C2_BASE}/mandatos/candidatos`]: () => Response.json({ elementos: [] }),
        [`GET ${C2_BASE}/documentos`]: () => Response.json({ documentos: [] }),
        [`GET ${ESTADO_VERSION}`]: () => new Promise<Response>((r) => (soltar = r)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      await within(form).findByText(LISTO_SUBIDA);
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_VERSION).length).toBeGreaterThan(0));
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      const antes = pedidos(simulado, "GET", RUTA_DOCUMENTOS).length;
      soltar(Response.json({ estado: "disponible", motivo_rechazo: null }));
      // Control de que la respuesta llegó: se espera un ciclo y no se pidió la lista del anterior.
      await new Promise((r) => setTimeout(r, 50));
      expect(pedidos(simulado, "GET", RUTA_DOCUMENTOS)).toHaveLength(antes);
      expect(screen.queryByText(LISTO_ELEGIDA)).toBeNull();
    });

    it("FL-DEC-01: con el acta en revisión, la decisión no se manda sin ella: espera y se registra sola con el acta", async () => {
      let soltar: (r: Response) => void = () => undefined;
      let estado = "en_cuarentena";
      const simulado = conSubida(() => estado, {
        [`GET ${ESTADO_VERSION}`]: () => new Promise<Response>((r) => (soltar = r)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      await within(form).findByText(LISTO_SUBIDA);
      // Ya no se invita a registrarla sin el acta.
      expect(within(form).queryByText(/se puede registrar sin el acta/)).toBeNull();
      fireEvent.click(boton("Registrar decisión"));
      expect(await within(form).findByText(DECISION_ESPERA_EL_ACTA)).toBeTruthy();
      // Esperando, no hay un botón que la registre de nuevo; queda «Cancelar».
      expect(within(form).queryByRole("button", { name: "Registrar decisión" })).toBeNull();
      expect(within(form).getByRole("button", { name: "Cancelar" })).toBeTruthy();
      await new Promise((r) => setTimeout(r, 20));
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(0);
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_VERSION).length).toBeGreaterThan(0));
      estado = "disponible";
      soltar(Response.json({ estado: "disponible", motivo_rechazo: null }));
      expect(await screen.findByText(LISTO_DECISION_CON_ACTA)).toBeTruthy();
      expect(screen.queryByRole("dialog", { name: "Registrar una decisión" })).toBeNull();
      const decisiones = pedidos(simulado, "POST", RUTA_DECISIONES);
      expect(decisiones).toHaveLength(1);
      const cuerpo = (await en(decisiones, 0).json()) as Record<string, unknown>;
      expect(cuerpo.sha256).toBe(SHA_NUEVA);
    });

    it("FL-DEC-01: si el antivirus rechaza el acta que la decisión esperaba, no se registra, se dice y vuelve el botón", async () => {
      let soltar: (r: Response) => void = () => undefined;
      const simulado = conSubida(() => "en_cuarentena", {
        [`GET ${ESTADO_VERSION}`]: () => new Promise<Response>((r) => (soltar = r)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      await within(form).findByText(LISTO_SUBIDA);
      fireEvent.click(boton("Registrar decisión"));
      await within(form).findByText(DECISION_ESPERA_EL_ACTA);
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_VERSION).length).toBeGreaterThan(0));
      soltar(Response.json({ estado: "rechazado", motivo_rechazo: "infectado" }));
      expect(await within(form).findByText(/no pasó la revisión del antivirus y no se puede usar: hay que subir otro\. La decisión no se registró\.$/)).toBeTruthy();
      expect(within(form).queryByText(DECISION_ESPERA_EL_ACTA)).toBeNull();
      expect(within(form).getByRole("button", { name: "Registrar decisión" })).toBeTruthy();
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(0);
    });

    it("FL-DEC-01: cambiar un dato mientras espera deja de esperar, y cerrar la ventana no registra nada", async () => {
      let soltar: (r: Response) => void = () => undefined;
      let estado = "en_cuarentena";
      const simulado = conSubida(() => estado, {
        [`GET ${ESTADO_VERSION}`]: () => new Promise<Response>((r) => (soltar = r)),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      await within(form).findByText(LISTO_SUBIDA);
      fireEvent.click(boton("Registrar decisión"));
      await within(form).findByText(DECISION_ESPERA_EL_ACTA);
      fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Otra cosa" } });
      expect(within(form).queryByText(DECISION_ESPERA_EL_ACTA)).toBeNull();
      expect(within(form).getByRole("button", { name: "Registrar decisión" })).toBeTruthy();
      fireEvent.click(boton("Registrar decisión"));
      await within(form).findByText(DECISION_ESPERA_EL_ACTA);
      // La consulta del antivirus sale mientras la ventana está abierta; su respuesta llega cerrada.
      await waitFor(() => expect(pedidos(simulado, "GET", ESTADO_VERSION).length).toBeGreaterThan(0));
      fireEvent.click(within(form).getByRole("button", { name: "Cancelar" }));
      estado = "disponible";
      soltar(Response.json({ estado: "disponible", motivo_rechazo: null }));
      await new Promise((r) => setTimeout(r, 50));
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(0);
      expect(screen.queryByText(LISTO_DECISION_CON_ACTA)).toBeNull();
    });

    it("FL-DEC-02: «Subir el acta» sin la fecha del acto marca la fecha y no abre la ventana", async () => {
      const simulado = conSubida(() => "disponible");
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "" } });
      fireEvent.click(within(form).getByRole("button", { name: "Subir el acta" }));
      expect(screen.queryByRole("dialog", { name: "Subir el acta" })).toBeNull();
      const fecha = within(form).getByLabelText("Fecha del acto");
      expect(fecha.getAttribute("aria-invalid")).toBe("true");
      expect(within(form).getByText("Falta la fecha del acto.")).toBeTruthy();
      expect(document.activeElement).toBe(fecha);
      // Control: con la fecha, se abre.
      fireEvent.change(fecha, { target: { value: "2026-10-01" } });
      fireEvent.click(within(form).getByRole("button", { name: "Subir el acta" }));
      expect(screen.getByRole("dialog", { name: "Subir el acta" })).toBeTruthy();
    });

    it("un archivo rechazado por el antivirus lo dice, no se ofrece, y recién ahí se puede subir otro (MAN-05)", async () => {
      const simulado = conSubida(() => "rechazado", {
        [`GET ${ESTADO_VERSION}`]: () => Response.json({ estado: "rechazado", motivo_rechazo: "infectado" }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(elegirActa(form)).getByRole("button", { name: "Subir el acta" }));
      expect(
        await within(form).findByText(
          "El archivo del acta «Acta 15 de asamblea» no pasó la revisión del antivirus y no se puede usar: hay que subir otro.",
        ),
      ).toBeTruthy();
      const acta = within(form).getByLabelText("Acta (opcional)") as HTMLSelectElement;
      expect(acta.value).toBe("");
      expect([...acta.options].map((o) => o.value)).toEqual([""]);
      expect(within(form).getByRole("button", { name: "Subir el acta" })).toBeTruthy();
    });

    it("si el archivo no entra, el documento creado no se repite: el reintento sube solo el archivo", async () => {
      let intentos = 0;
      const simulado = conSubida(() => "disponible", {
        [`POST ${RUTA_DOCUMENTOS}/${DOC_NUEVO}/versiones`]: () => {
          intentos += 1;
          if (intentos === 1) return error("tipo_no_admitido", 415);
          return Response.json({ id: V_NUEVA, estado: "en_cuarentena", sha256: SHA_NUEVA, tamano: 9, tipo: "application/pdf" }, { status: 201 });
        },
        [`GET ${RUTA_DOCUMENTOS}`]: () => Response.json({ documentos: intentos > 1 ? [documento("disponible")] : [] }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      const subida = elegirActa(form);
      fireEvent.click(within(subida).getByRole("button", { name: "Subir el acta" }));
      // El rechazo queda en la ventana del acta.
      expect(
        await within(subida).findByText(/^El acta «Acta 15 de asamblea» quedó creada en Documentos, pero el archivo no se subió\. Solo se aceptan/),
      ).toBeTruthy();
      expect((within(subida).getByLabelText("Título del acta") as HTMLInputElement).disabled).toBe(true);
      fireEvent.click(within(subida).getByRole("button", { name: "Subir el acta" }));
      expect(await within(form).findByText(LISTO_ELEGIDA)).toBeTruthy();
      expect(pedidos(simulado, "POST", RUTA_DOCUMENTOS)).toHaveLength(1);
      expect(pedidos(simulado, "POST", `${RUTA_DOCUMENTOS}/${DOC_NUEVO}/versiones`)).toHaveLength(2);
    });

    it("sin archivo elegido lo dice en su ventana y no manda nada", async () => {
      const simulado = conSubida(() => "disponible");
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      fireEvent.click(within(form).getByRole("button", { name: "Subir el acta" }));
      const subida = await screen.findByRole("dialog", { name: "Subir el acta" });
      fireEvent.click(within(subida).getByRole("button", { name: "Subir el acta" }));
      expect(await within(subida).findByText("Falta elegir el archivo del acta.")).toBeTruthy();
      expect(pedidos(simulado, "POST", RUTA_DOCUMENTOS)).toHaveLength(0);
    });

    it("control: sin poder cargar documentos, el acta se elige pero no se ofrece subirla", async () => {
      const simulado = conSubida(() => "disponible", {
        [`GET ${BASE}/mis-acciones`]: () =>
          Response.json({ acciones: accionesDeLasPoliticas().filter((a) => a !== "documentos.cargar" && a !== "documentos.cargar_operativos") }),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      const form = await llenarDecision();
      expect(within(form).getByLabelText("Acta (opcional)")).toBeTruthy();
      expect(within(form).queryByRole("button", { name: "Subir el acta" })).toBeNull();
    });
  });

  it("FL-MAN-06: el «Listo» del alta de una administración externa trae «Invitar a la administración», que abre su invitación", async () => {
    const externa = {
      ...DETALLE_AE,
      id: NUEVO,
      nombre: "Estudio Norte",
      vigente_desde: "2027-03-01",
      vigente_hasta: null,
      estado: "futuro",
      con_cuenta: false,
    };
    const simulado = conConsejo({
      [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
      [`GET ${RUTA_MANDATOS}/${NUEVO}`]: () => Response.json(externa),
      [`GET ${RUTA_MANDATOS}/${NUEVO}/invitaciones`]: () => Response.json({ elementos: [] }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
    fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2027-03-01" } });
    fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
    fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText("Listo: el mandato de Estudio Norte quedó dado de alta y ya está en la lista.");
    fireEvent.click(screen.getByRole("button", { name: "Invitar a la administración" }));
    const ventana = await screen.findByRole("dialog", { name: "Invitar a la administración" });
    expect(within(ventana).getByLabelText("Correo de la administración")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Estudio Norte, Administración externa" })).toBeTruthy();
  });

  it("FL-MAN-06, control: el «Listo» del alta del Consejo no trae la invitación", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) }));
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    expect(screen.queryByRole("button", { name: "Invitar a la administración" })).toBeNull();
  });

  it("FL-MAN-10: «Sin cuenta asociada» se destaca solo para quien puede invitar; para el resto va en el resumen", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${BASE}/mis-acciones`]: () =>
          Response.json({ acciones: accionesDeLasPoliticas().filter((a) => a !== "mandatos.asociar_persona") }),
      }),
    );
    abrir();
    await cargado();
    const externo = screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") });
    expect(externo.querySelector(".fila-registro__estado")).toBeNull();
    expect(externo.querySelector(".fila-registro__resumen")?.textContent).toBe("Vigente · Sin cuenta asociada");
  });

  it("G-44: la fila del mandato nuevo queda resaltada, a la vista y dicha en palabras hasta el próximo paso", async () => {
    let lista = MANDATOS;
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}`]: () => Response.json(lista),
      [`POST ${RUTA_MANDATOS}`]: () => {
        lista = {
          elementos: [
            ...MANDATOS.elementos,
            {
              id: NUEVO,
              tipo: "miembro_consejo",
              nombre: "Beto Pérez",
              cuit: null,
              vigente_desde: "2026-11-01",
              vigente_hasta: null,
              estado: "vigente",
              con_cuenta: true,
            },
          ],
        };
        return Response.json({ id: NUEVO }, { status: 201 });
      },
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    // Control: antes del alta ninguna fila está resaltada.
    expect(document.querySelector(".fila-nueva")).toBeNull();
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText(LISTO_ALTA);
    const nueva = (await screen.findByRole("button", { name: fila("Beto Pérez, Consejo de propietarios") })).closest("li");
    if (nueva === null) throw new Error("la fila nueva no está en una lista");
    expect(nueva.className).toBe("fila-nueva");
    expect(within(nueva).getByText(RECIEN_DADO_DE_ALTA)).toBeTruthy();
    // Se lleva a la vista la fila nueva, no otra.
    const desplazar = vi.mocked(Element.prototype.scrollIntoView);
    await waitFor(() => expect(desplazar.mock.contexts).toContain(nueva));
    // Las demás filas no se marcan.
    expect(screen.getByRole("button", { name: fila("Ana Díaz, Consejo de propietarios") }).closest("li")?.className).toBe("");
    expect(screen.getAllByText(RECIEN_DADO_DE_ALTA)).toHaveLength(1);
    // El próximo paso (abrir otro detalle) le saca la marca.
    fireEvent.click(screen.getByRole("button", { name: fila("Estudio Sur, Administración externa") }));
    await esperarFicha("Estudio Sur, Administración externa");
    expect(document.querySelector(".fila-nueva")).toBeNull();
    expect(screen.queryByText(RECIEN_DADO_DE_ALTA)).toBeNull();
  });
});
