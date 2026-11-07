import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { alcanza, alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { VOLVIO_LA_RED } from "./egresos/comun";
import { mensajeDeError, presentacion } from "./Mandatos";
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

const CANDIDATOS = { elementos: [{ persona_id: VECINA, nombre: "Beto Pérez", unidades: ["1° A"] }] };

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

function error(codigo: string, status: number) {
  return Response.json({ codigo, mensaje: "x", id_correlacion: "c" }, { status });
}

type Respuestas = Record<string, () => Response>;

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
const FORMULARIOS = ["Dar de alta un mandato", "Registrar una decisión"];
/** Una sección de la pantalla; los dos formularios se abren con su botón. */
function seccion(nombre: string) {
  if (FORMULARIOS.includes(nombre) && screen.queryByRole("region", { name: nombre }) === null) fireEvent.click(boton(nombre));
  return screen.getByRole("region", { name: nombre });
}
const boton = (nombre: string) => screen.getByRole("button", { name: nombre });

async function completarAlta(decision = D_EQUIPO) {
  await cargado();
  const alta = seccion("Dar de alta un mandato");
  fireEvent.change(within(alta).getByLabelText("Decisión que lo respalda"), { target: { value: decision } });
  fireEvent.change(within(alta).getByLabelText("Desde"), { target: { value: "2026-11-01" } });
}

/** Abre el detalle del externo y completa la baja por renuncia, sin confirmar. */
async function abrirBaja() {
  await cargado();
  fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
  await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
  fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Renuncia presentada" } });
  fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
}

beforeEach(() => {
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

  it("lista lo mínimo: el CUIT solo si la API lo manda, y las decisiones sin referencia", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const mandatos = seccion("Mandatos");
    const consejo = within(mandatos).getByText("Ana Díaz, Consejo de propietarios").closest("tr") as HTMLElement;
    expect(within(consejo).queryByText(/^CUIT/)).toBeNull();
    const externo = within(mandatos).getByText("Estudio Sur, Administración externa").closest("tr") as HTMLElement;
    expect(within(externo).getByText(`CUIT ${CUIT}`)).toBeTruthy();
    expect(within(externo).getByText(/1 de febrero de 2027 \(ese día ya no rige\)/)).toBeTruthy();
    const decisiones = seccion("Decisiones");
    expect(within(decisiones).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
    expect(within(decisiones).queryByText(/Acta 14/)).toBeNull();
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
    expect(await screen.findByRole("heading", { level: 1, name: "Tu consorcio, en tu mano" })).toBeTruthy();
    expect(screen.queryByText(/No tenés permiso/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeNull();
  });

  it("el detalle de un mandato suma lo autorizado y lleva el foco a su título", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    await waitFor(() => expect(document.activeElement).toBe(titulo));
    const detalle = titulo.closest("section") as HTMLElement;
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    const detalle = titulo.closest("section") as HTMLElement;
    expect(within(detalle).getByText("Reunión del Consejo del 5 de marzo de 2026: Contrata al estudio")).toBeTruthy();
  });

  it("el detalle de una decisión dice si la registraste vos, sin decir quién", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Reunión del Consejo del 5 de marzo de 2026" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Reunión del Consejo del 5 de marzo de 2026" });
    const detalle = titulo.closest("section") as HTMLElement;
    expect(within(detalle).getByText("Acta 14, libro 3, folio 52")).toBeTruthy();
    expect(within(detalle).getByText(/Vos: no podés usarla para dar de alta ni para remover/)).toBeTruthy();
  });

  it("un detalle que ya no está (404) lo dice y vuelve a pedir la lista", async () => {
    const simulado = conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => error("no_encontrado", 404) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
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
    expect(await screen.findByText("Listo: registramos la decisión.")).toBeTruthy();
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

  it("«Qué se decidió» y «Dónde consta» son textarea y la huella, una línea", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    const que = within(form).getByLabelText("Qué se decidió");
    const donde = within(form).getByLabelText("Dónde consta (libro, acta y folio)");
    const huella = within(form).getByLabelText("Huella SHA-256 del acta escaneada (opcional)");
    expect(que.tagName).toBe("TEXTAREA");
    expect(que.getAttribute("rows")).toBe("4");
    expect(que.getAttribute("maxlength")).toBe("500");
    expect(donde.tagName).toBe("TEXTAREA");
    expect(donde.getAttribute("rows")).toBe("2");
    expect(donde.getAttribute("maxlength")).toBe("300");
    expect(huella.tagName).toBe("INPUT");
    expect(huella.getAttribute("type")).toBe("text");
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
    expect(que.validationMessage).toBe("Escribí algo más que espacios.");
    fireEvent.change(que, { target: { value: " Algo " } });
    expect(que.validity.valid).toBe(true);
  });

  it("con un texto de solo espacios el formulario de decisión no se envía", async () => {
    const simulado = conConsejo();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    const form = seccion("Registrar una decisión");
    fireEvent.change(within(form).getByLabelText("Fecha del acto"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "   " } });
    fireEvent.change(within(form).getByLabelText("Dónde consta (libro, acta y folio)"), { target: { value: "Acta 1" } });
    const formulario = within(form).getByRole("button", { name: "Registrar decisión" }).closest("form") as HTMLFormElement;
    expect(formulario.checkValidity()).toBe(false);
    fireEvent.change(within(form).getByLabelText("Qué se decidió"), { target: { value: "Algo" } });
    expect(formulario.checkValidity()).toBe(true);
    expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toEqual([]);
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
    expect(await screen.findByText("Listo: el mandato quedó dado de alta.")).toBeTruthy();
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
    expect(within(alta).queryByLabelText("Persona (con vínculo confirmado en el edificio)")).toBeNull();
    fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
    fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
    fireEvent.change(within(alta).getByLabelText("Matrícula (opcional)"), { target: { value: "RPA 99" } });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText("Listo: el mandato quedó dado de alta.");
    const cuerpo = (await en(pedidos(simulado, "POST", RUTA_MANDATOS), 0).json()) as Record<string, unknown>;
    expect([cuerpo.persona_id, cuerpo.nombre, cuerpo.cuit, cuerpo.matricula]).toEqual([null, "Estudio Norte", CUIT, "RPA 99"]);
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Asamblea del 10 de enero de 2026" }));
    await waitFor(() => expect(detalles()).toHaveLength(1));
  });

  it("si todas las decisiones son propias lo dice y no hay selector ni «Dar de alta», tampoco para un externo", async () => {
    const propias = { elementos: [en(DECISIONES.elementos, 1)] };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json(propias) }));
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    expect(within(alta).getByText(/las registraste vos, y quien registra una decisión no la usa/)).toBeTruthy();
    expect(within(alta).queryByLabelText("Decisión que lo respalda")).toBeNull();
    expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
    fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
    expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
  });

  it("control: sin ninguna decisión lo dice sin atribuírselas a quien mira", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [] }) }));
    abrir();
    await cargado();
    const alta = seccion("Dar de alta un mandato");
    expect(within(alta).getByText(/Todavía no hay decisiones registradas: sin una que lo respalde/)).toBeTruthy();
    expect(within(alta).queryByText(/las registraste vos/)).toBeNull();
    expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
  });

  it("si la API igual niega el alta, es un error junto al formulario y lo cargado queda", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("sin_permiso", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta();
    fireEvent.click(boton("Dar de alta"));
    const aviso = await within(seccion("Dar de alta un mandato")).findByRole("alert");
    expect(aviso.textContent).toMatch(/^No tenés permiso para dar este alta\./);
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
    expect(await screen.findByText(/No pudimos completar el pedido/)).toBeTruthy();
    respuesta = () => error("administracion_ocupada", 409);
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText("Ya hay un mandato de administración en esas fechas en este edificio.")).toBeTruthy();
    respuesta = () => Response.json({ id: NUEVO }, { status: 201 });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText("Listo: el mandato quedó dado de alta.");
    const [primera, segunda, tercera] = pedidos(simulado, "POST", RUTA_MANDATOS).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
    expect(tercera).not.toBe(segunda);
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
    expect(screen.queryByText("Listo: registramos la decisión.")).toBeNull();
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
    await screen.findByText("Listo: registramos la decisión.");
    const [primera, segunda] = pedidos(simulado, "POST", RUTA_DECISIONES).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("dar de baja pide confirmación; «No» no manda nada y «Sí» manda el instrumento", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => Response.json({ id: M_AE, causa: "renuncia", vigente_hasta: "2026-10-19" }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Renuncia presentada" } });
    fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Nota del 10-10" } });
    fireEvent.click(boton("Dar de baja"));
    expect(screen.getByText(/¿Dar de baja el mandato de Estudio Sur\? La baja no se puede deshacer./)).toBeTruthy();
    fireEvent.click(boton("No"));
    expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`)).toHaveLength(0);
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    expect(await screen.findByText("Listo: dimos de baja el mandato de Estudio Sur.")).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    fireEvent.change(screen.getByLabelText("Causa"), { target: { value: "remocion" } });
    expect(opciones("Asamblea que lo remueve")).toEqual(["", D_EQUIPO]);
    fireEvent.change(screen.getByLabelText("Asamblea que lo remueve"), { target: { value: D_EQUIPO } });
    fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Remoción" } });
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    await screen.findByText("Listo: dimos de baja el mandato de Estudio Sur.");
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
      fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
      await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    };

    it("sin una asamblea que quien mira pueda usar, no se ofrece", async () => {
      const soloPropia = { ...en(DECISIONES.elementos, 0), registrada_por_vos: true, del_equipo: false };
      await abrirElDetalle(() => Response.json({ elementos: [soloPropia] }));
      expect(opciones("Causa")).not.toContain("remocion");
      expect(opciones("Causa")).toContain("renuncia");
    });

    it("control: con una asamblea que puede usar, se ofrece", async () => {
      await abrirElDetalle(() => Response.json(DECISIONES));
      expect(opciones("Causa")).toContain("remocion");
    });

    it("control: si las decisiones no llegaron no se sabe, y sigue a la vista", async () => {
      await abrirElDetalle(() => error("error_interno", 500));
      expect(opciones("Causa")).toContain("remocion");
    });
  });

  it("un mandato terminado solo ofrece la baja por error de carga; uno dado de baja, ninguna", async () => {
    const terminado = { ...DETALLE_AE, estado: "terminado" };
    let detalle: object = terminado;
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(detalle) }));
    abrir();
    await cargado();
    const ver = () => fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    ver();
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    expect(within(screen.getByLabelText("Causa")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["error_carga"]);
    detalle = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "renuncia" };
    ver();
    expect(await screen.findByText("Renuncia")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "Dar de baja" })).toBeNull();
  });

  it("E62: un mandato pendiente de revisión lo dice y admite cualquier baja", async () => {
    const pendiente = { ...DETALLE_AE, estado: "pendiente_revision" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(pendiente) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    expect(screen.getByText("Pendiente de revisión: sin condición para integrar el Consejo")).toBeTruthy();
    expect(within(screen.getByLabelText("Causa")).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "remocion",
      "renuncia",
      "fallecimiento_incapacidad",
      "error_carga",
      "otro",
    ]);
  });

  it("un error en la baja cierra la confirmación: el reintento se vuelve a confirmar", async () => {
    let respuesta = () => error("sin_permiso", 403);
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => respuesta(),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    expect(await screen.findByText(/No tenés permiso para dar esta baja/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sí, dar de baja" })).toBeNull();
    respuesta = () => Response.json({ id: M_AE, causa: "renuncia", vigente_hasta: "2026-10-19" });
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    await screen.findByText("Listo: dimos de baja el mandato de Estudio Sur.");
    expect(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`)).toHaveLength(2);
  });

  it("cambiar un dato con la confirmación abierta la cierra", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    expect(screen.getByRole("button", { name: "Sí, dar de baja" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Instrumento (nota, certificado u otro)"), { target: { value: "Otra nota" } });
    expect(screen.queryByRole("button", { name: "Sí, dar de baja" })).toBeNull();
  });

  it("una baja sobre un mandato que ya tenía baja cierra el detalle y vuelve a pedir la lista", async () => {
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => error("mandato_ya_dado_de_baja", 409),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await abrirBaja();
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    expect(await screen.findByText("El mandato ya tiene una baja registrada.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "Dar de baja" })).toBeNull();
    // El formulario se fue con el detalle: el aviso subió, no quedó sin lugar.
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Ana Díaz, Consejo de propietarios" }));
    await screen.findByRole("heading", { level: 2, name: "Ana Díaz, Consejo de propietarios" });
    expect((screen.getByLabelText("Por qué termina") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Instrumento (nota, certificado u otro)") as HTMLInputElement).value).toBe("");
  });

  it("al terminar un alta el foco va a su detalle y al cerrarlo vuelve al formulario; con «Ver detalle», a ese botón", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      }),
    );
    abrir();
    await completarAlta();
    boton("Dar de alta").focus();
    fireEvent.click(boton("Dar de alta"));
    const creado = await screen.findByRole("heading", { level: 2, name: "Beto Pérez, Consejo de propietarios" });
    await waitFor(() => expect(document.activeElement).toBe(creado));
    fireEvent.click(boton("Cerrar el detalle"));
    expect(document.activeElement).toBe(boton("Dar de alta un mandato"));
    const ver = screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" });
    ver.focus();
    fireEvent.click(ver);
    await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    fireEvent.click(boton("Cerrar el detalle"));
    expect(document.activeElement).toBe(ver);
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
    expect(await screen.findByText(/Cambiaste los datos de un intento anterior/)).toBeTruthy();
    respuesta = () => Response.json({ id: NUEVO }, { status: 201 });
    fireEvent.click(boton("Dar de alta"));
    await screen.findByText("Listo: el mandato quedó dado de alta.");
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
    const ver = screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" });
    fireEvent.click(ver);
    expect(await screen.findByText(/El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
    caida = true;
    fireEvent.click(ver);
    expect(await screen.findByText(/Revisá la conexión/)).toBeTruthy();
  });

  it("E34: la primera carga con un 503 de la aplicación dice servicio, no conexión", async () => {
    vi.stubGlobal("fetch", conConsejo({ "GET /api/v1/me/consorcios": () => error("servicio_no_disponible", 503) }));
    abrir();
    expect(await screen.findByText(/No pudimos consultar tus edificios. El servicio no está disponible/)).toBeTruthy();
    expect(screen.queryByText(/Revisá la conexión/)).toBeNull();
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

    it("registrar una decisión abre su detalle y deja el formulario vacío, sin poder repetirla", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      completarDecision();
      boton("Registrar decisión").focus();
      fireEvent.click(boton("Registrar decisión"));
      const titulo = await screen.findByRole("heading", { level: 2, name: "Contrato de administración del 1 de octubre de 2026" });
      await waitFor(() => expect(document.activeElement).toBe(titulo));
      expect(within(titulo.closest("section") as HTMLElement).getByText("Contrato del 01-10")).toBeTruthy();
      expect(screen.getByText("Listo: registramos la decisión.")).toBeTruthy();
      expect(screen.queryByRole("region", { name: "Registrar una decisión" })).toBeNull();
      const form = seccion("Registrar una decisión");
      expect(valor(form, "Tipo")).toBe("asamblea");
      expect(valor(form, "Fecha del acto")).toBe("");
      expect(valor(form, "Qué se decidió")).toBe("");
      expect(valor(form, "Dónde consta (libro, acta y folio)")).toBe("");
      expect((within(form).getByRole("button", { name: "Registrar decisión" }).closest("form") as HTMLFormElement).checkValidity()).toBe(false);
      expect(pedidos(simulado, "POST", RUTA_DECISIONES)).toHaveLength(1);
    });

    it("control: una decisión rechazada no abre nada y deja lo escrito para reintentar", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_DECISIONES}`]: () => error("fecha_futura", 422) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await cargado();
      const form = completarDecision();
      boton("Registrar decisión").focus();
      fireEvent.click(boton("Registrar decisión"));
      const aviso = await screen.findByText(/no puede ser posterior a hoy/);
      await waitFor(() => expect(document.activeElement).toBe(aviso));
      expect(pedidos(simulado, "GET", `${RUTA_DECISIONES}/${NUEVO}`)).toHaveLength(0);
      expect(screen.queryByRole("button", { name: "Cerrar el detalle" })).toBeNull();
      expect(valor(form, "Tipo")).toBe("contrato_administracion");
      expect(valor(form, "Fecha del acto")).toBe("2026-10-01");
      expect(valor(form, "Qué se decidió")).toBe("Contrata al estudio");
      expect(valor(form, "Dónde consta (libro, acta y folio)")).toBe("Contrato del 01-10");
    });

    it("dar de alta abre el mandato creado y deja el formulario vacío, sin poder repetirla", async () => {
      const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      const alta = seccion("Dar de alta un mandato");
      fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
      fireEvent.change(within(alta).getByLabelText("Razón social o nombre"), { target: { value: "Estudio Norte" } });
      fireEvent.change(within(alta).getByLabelText("CUIT (11 números, sin guiones)"), { target: { value: CUIT } });
      fireEvent.change(within(alta).getByLabelText("Hasta (opcional; ese día ya no rige)"), { target: { value: "2027-11-01" } });
      fireEvent.click(boton("Dar de alta"));
      expect(await screen.findByRole("heading", { level: 2, name: "Beto Pérez, Consejo de propietarios" })).toBeTruthy();
      expect(screen.getByText("Listo: el mandato quedó dado de alta.")).toBeTruthy();
      const despues = seccion("Dar de alta un mandato");
      expect(valor(despues, "Tipo de mandato")).toBe("miembro_consejo");
      expect(valor(despues, "Persona (con vínculo confirmado en el edificio)")).toBe(VECINA);
      expect(valor(despues, "Decisión que lo respalda")).toBe("");
      expect(valor(despues, "Desde")).toBe("");
      expect(valor(despues, "Hasta (opcional; ese día ya no rige)")).toBe("");
      expect((within(despues).getByRole("button", { name: "Dar de alta" }).closest("form") as HTMLFormElement).checkValidity()).toBe(false);
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

    it("si el detalle de lo creado no llega, el «Listo» queda, dice dónde verlo y no se repite el envío", async () => {
      const simulado = conConsejo({
        [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }),
        [`GET ${RUTA_MANDATOS}/${NUEVO}`]: () => error("error_interno", 500),
      });
      vi.stubGlobal("fetch", simulado);
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      const aviso = await screen.findByText("Listo: el mandato quedó dado de alta. No pudimos abrir su detalle: buscalo en la lista.");
      expect(aviso.className).toBe("aviso-ok");
      expect(screen.queryByRole("button", { name: "Cerrar el detalle" })).toBeNull();
      expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
    });
  });

  it("una lista caída se reintenta sin perder lo escrito; las que andan quedan a la vista", async () => {
    let caida = true;
    const simulado = conConsejo({
      [`GET ${RUTA_MANDATOS}`]: () => (caida ? error("error_interno", 500) : Response.json(MANDATOS)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText("No pudimos consultar los mandatos.")).toBeTruthy();
    seccion("Registrar una decisión");
    const decision = screen.getByLabelText("Qué se decidió") as HTMLTextAreaElement;
    fireEvent.change(decision, { target: { value: "A medio escribir" } });
    caida = false;
    fireEvent.click(within(seccion("Mandatos")).getByRole("button", { name: "Consultar de nuevo" }));
    // Mientras llega, la lista que andaba no se vacía.
    expect(within(seccion("Decisiones")).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
    expect(await cargado()).toBeTruthy();
    expect(pedidos(simulado, "GET", RUTA_MANDATOS)).toHaveLength(2);
    expect((screen.getByLabelText("Qué se decidió") as HTMLTextAreaElement).value).toBe("A medio escribir");
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
    await screen.findByRole("button", { name: "Dar de alta un mandato" });
    seccion("Dar de alta un mandato");
    expect(await screen.findByText("No pudimos consultar quiénes pueden recibir un mandato.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Consultar de nuevo", description: "Mandatos" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Consultar de nuevo", description: "Decisiones" })).toBeTruthy();
    const alta = screen.getByRole("button", { name: "Consultar de nuevo", description: "Dar de alta un mandato" });
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    caidas = false;
    fireEvent.click(alta);
    expect(await cargado()).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Dar de alta" })).toBeTruthy();
    expect(within(seccion("Decisiones")).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
  });

  it("sin decisiones no hay alta, y el aviso del alta también deja reintentar", async () => {
    let caida = true;
    vi.stubGlobal(
      "fetch",
      conConsejo({ [`GET ${RUTA_DECISIONES}`]: () => (caida ? error("error_interno", 500) : Response.json(DECISIONES)) }),
    );
    abrir();
    await screen.findByText("No pudimos consultar las decisiones.");
    expect(screen.queryByRole("button", { name: "Dar de alta un mandato" })).toBeTruthy();
    seccion("Dar de alta un mandato");
    expect(await screen.findByText(/no pudimos consultarlas/)).toBeTruthy();
    caida = false;
    fireEvent.click(within(seccion("Dar de alta un mandato")).getByRole("button", { name: "Consultar de nuevo" }));
    expect(await screen.findByRole("button", { name: "Dar de alta" })).toBeTruthy();
  });

  it("los edificios que no se pudieron consultar se reintentan sin salir de la pantalla", async () => {
    let caido = true;
    const simulado = conConsejo({
      "GET /api/v1/me/consorcios": () => (caido ? error("servicio_no_disponible", 503) : Response.json(CONSORCIOS)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/No pudimos consultar tus edificios/)).toBeTruthy();
    // Control: la primera carga pidió una vez la pantalla y otra permisos.tsx, aun con StrictMode.
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(2);
    caido = false;
    fireEvent.click(boton("Consultar de nuevo"));
    expect(await cargado()).toBeTruthy();
    expect(pedidos(simulado, "GET", "/api/v1/me/consorcios")).toHaveLength(3);
    expect(screen.queryByText(/No pudimos consultar tus edificios/)).toBeNull();
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
      expect(await screen.findByText("No pudimos consultar los mandatos.")).toBeTruthy();
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
      await screen.findByText("No pudimos consultar los mandatos.");
      const antes = pedidos(simulado, "GET", RUTA_MANDATOS).length;
      volvioLaRed();
      await waitFor(() => expect(pedidos(simulado, "GET", RUTA_MANDATOS).length).toBeGreaterThan(antes));
      expect(await screen.findByText("No pudimos consultar los mandatos.")).toBeTruthy();
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
      await screen.findByText(/No pudimos consultar tus edificios/);
      caido = false;
      volvioLaRed();
      expect(await cargado()).toBeTruthy();
      expect(screen.queryByText(/No pudimos consultar tus edificios/)).toBeNull();
    });
  });

  describe("a quién se le da el alta", () => {
    const YO = "99999999-9999-4999-8999-999999999999";
    const YO_CANDIDATO = { persona_id: YO, nombre: "Zoe Mira", unidades: ["3° C"] };
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
      await screen.findByText("Listo: el mandato quedó dado de alta.");
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

    it("si el único candidato es quien mira, lo dice y no hay botón", async () => {
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
      expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
    });

    it("sin candidatos no hay «Dar de alta» para el Consejo; la administración externa lo conserva", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => Response.json({ elementos: [] }) }));
      abrir();
      await cargado();
      const alta = seccion("Dar de alta un mandato");
      expect(await within(alta).findByText(/Nadie tiene todavía un vínculo confirmado/)).toBeTruthy();
      expect(within(alta).queryByRole("button", { name: "Dar de alta" })).toBeNull();
      fireEvent.change(within(alta).getByLabelText("Tipo de mandato"), { target: { value: "administrador_externo" } });
      expect(within(alta).getByRole("button", { name: "Dar de alta" })).toBeTruthy();
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
      expect((await within(seccion("Dar de alta un mandato")).findByRole("alert")).textContent).toMatch(/No pudimos completar el pedido/);
    });

    it("control: un «Listo» va al estado de la página, y arriba de las secciones", async () => {
      vi.stubGlobal("fetch", conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => Response.json({ id: NUEVO }, { status: 201 }) }));
      abrir();
      await completarAlta();
      fireEvent.click(boton("Dar de alta"));
      const listo = await screen.findByText("Listo: el mandato quedó dado de alta.");
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
    seccion("Dar de alta un mandato");
    expect(screen.getByText("No pudimos consultar quiénes pueden recibir un mandato.")).toBeTruthy();
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
      await screen.findByText(/No pudimos completar el pedido/);
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      expect(await screen.findByText("Eva Sosa, Consejo de propietarios")).toBeTruthy();
      expect(screen.queryByText("Ana Díaz, Consejo de propietarios")).toBeNull();
      expect(screen.queryByText(/No pudimos completar el pedido/)).toBeNull();
      expect(screen.getByText("Todavía no hay decisiones registradas.")).toBeTruthy();
      caida = false;
      seccion("Registrar una decisión");
      fireEvent.click(boton("Registrar decisión"));
      await screen.findByText("Listo: registramos la decisión.");
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
      fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
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
      fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      await screen.findByText("Eva Sosa, Consejo de propietarios");
      soltar();
      await waitFor(() => expect(fallos).toBe(1));
      await new Promise((r) => setTimeout(r, 0));
      expect(screen.queryByText(/No pudimos completar el pedido/)).toBeNull();
    });
  });

  it("el detalle de una baja muestra cuándo se registró, en la hora del edificio", async () => {
    const dadoDeBaja = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "renuncia", finalizado_en: "2026-10-19T15:30:00Z" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(dadoDeBaja) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    const etiqueta = within(titulo.closest("section") as HTMLElement).getByText("Baja registrada");
    expect(etiqueta.nextElementSibling?.textContent).toMatch(/19 de octubre de 2026.*12:30/);
  });

  it("al pedir otro detalle, el anterior (y su baja) se cierra aunque el nuevo se niegue", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
        [`GET ${RUTA_MANDATOS}/${M_MC}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    await abrirBaja();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Ana Díaz, Consejo de propietarios" }));
    expect(screen.queryByRole("heading", { level: 3, name: "Dar de baja" })).toBeNull();
    expect(await screen.findByText("No tenés permiso para ver ese mandato.")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" })).toBeNull();
  });

  it("el detalle de una remoción muestra la asamblea que la respaldó", async () => {
    const removido = { ...DETALLE_AE, estado: "dado_de_baja", causa_baja: "remocion", baja_decision_id: D_EQUIPO, motivo_fin: "Remoción" };
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(removido) }));
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    const detalle = titulo.closest("section") as HTMLElement;
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
    await screen.findByText("Listo: el mandato quedó dado de alta.");
    const cuerpo = (await en(pedidos(simulado, "POST", RUTA_MANDATOS), 0).json()) as Record<string, unknown>;
    expect([cuerpo.desde, cuerpo.hasta]).toEqual(["2026-11-01", "2026-11-01"]);
  });

  it("sin edificios lo dice", async () => {
    vi.stubGlobal("fetch", backend({ "GET /api/v1/me/consorcios": () => Response.json([]) }));
    abrir();
    expect(await screen.findByText("Todavía no participás de ningún edificio.")).toBeTruthy();
  });

  it("no tiene faltas de accesibilidad detectables por axe, con el detalle abierto", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    const { container } = abrir();
    await completarAlta();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
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
    expect(mensajeDeError(403, "csrf_invalido", "baja")).toMatch(/Recargá la página/);
    expect(mensajeDeError(422, "codigo_desconocido", "decision")).toBe("Revisá los datos del formulario.");
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

  it("carga: las listas del edificio dicen «Consultando…» hasta que llegan las tres", async () => {
    const { retenido, soltar } = retener(conConsejo(), RUTA_MANDATOS);
    vi.stubGlobal("fetch", retenido);
    abrir();

    await screen.findByRole("region", { name: "Mandatos" });
    // Las tres salen juntas (Promise.all en Mandatos.tsx): con una en vilo, ninguna se pinta.
    await waitFor(() => expect(pedidos(retenido, "GET", RUTA_CANDIDATOS)).toHaveLength(1));
    for (const nombre of ["Mandatos", "Decisiones", "Dar de alta un mandato"])
      expect(within(seccion(nombre)).getByText("Consultando…")).toBeTruthy();
    soltar();

    await cargado();
    for (const nombre of ["Mandatos", "Decisiones", "Dar de alta un mandato"])
      expect(within(seccion(nombre)).queryByText("Consultando…")).toBeNull();
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

    expect(await screen.findByText("No pudimos consultar tus edificios. Revisá la conexión y volvé en un rato.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Mandatos" })).toBeNull();
  });
});

describe("los dos botones de arriba", () => {
  it("los formularios arrancan cerrados y arriba de las listas al abrirse", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(screen.queryByRole("region", { name: "Registrar una decisión" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Dar de alta un mandato" })).toBeNull();
    const form = seccion("Registrar una decisión");
    expect(form.compareDocumentPosition(seccion("Mandatos")) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(document.activeElement).toBe(within(form).getByRole("heading", { level: 2 }));
    expect(boton("Registrar una decisión").getAttribute("aria-expanded")).toBe("true");
  });

  it("se abre uno por vez, y lo escrito en el otro queda", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    fireEvent.change(within(seccion("Registrar una decisión")).getByLabelText("Qué se decidió"), { target: { value: "A medio escribir" } });
    seccion("Dar de alta un mandato");
    expect(screen.queryByRole("region", { name: "Registrar una decisión" })).toBeNull();
    expect((within(seccion("Registrar una decisión")).getByLabelText("Qué se decidió") as HTMLTextAreaElement).value).toBe("A medio escribir");
  });

  it("«Cancelar» lo cierra y devuelve el foco al botón que lo abrió", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    fireEvent.click(within(seccion("Dar de alta un mandato")).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("region", { name: "Dar de alta un mandato" })).toBeNull();
    expect(document.activeElement).toBe(boton("Dar de alta un mandato"));
  });
});

describe("lo que no se puede hacer no aparece en /mandatos", () => {
  const conAcciones = (...acciones: string[]) => conConsejo({ [`GET ${BASE}/mis-acciones`]: alcanza(...acciones) });
  const LECTURAS = ["mandatos.ver", "decisiones.ver", "mandatos.ver_candidatos"];
  const FORMULARIO_DECISION = { name: "Registrar una decisión" };
  const FORMULARIO_ALTA = { name: "Dar de alta un mandato" };

  it("la presentación dice solo lo que la persona alcanza", () => {
    const nada = { veMandatos: false, veDecisiones: false, registra: false, daAltas: false, daBajas: false };
    expect(presentacion({ ...nada, veMandatos: true })).toBe("Acá ves los mandatos del Consejo y de la administración del edificio.");
    expect(presentacion({ ...nada, veDecisiones: true, registra: true })).toBe("Acá ves las decisiones del edificio. Podés registrar decisiones.");
    expect(presentacion({ veMandatos: true, veDecisiones: true, registra: true, daAltas: true, daBajas: true })).toBe(
      "Acá ves los mandatos del Consejo y de la administración del edificio y las decisiones que los respaldan. Podés registrar decisiones, dar de alta un mandato y dar de baja un mandato.",
    );
  });

  it("quien solo lee no ve la promesa de registrar ni de dar altas o bajas", async () => {
    vi.stubGlobal("fetch", conAcciones(...LECTURAS));
    abrir();
    await cargado();
    expect(screen.getByText(/^Acá ves los mandatos/)).toBeTruthy();
    expect(screen.queryByText(/Podés (registrar|dar)/)).toBeNull();
    expect(screen.queryByText(/lo decide la plataforma/)).toBeNull();
  });

  it("control: con todas las facultades la presentación las nombra", async () => {
    vi.stubGlobal("fetch", conConsejo());
    abrir();
    await cargado();
    expect(screen.getByText(/Podés registrar decisiones, dar de alta un mandato y dar de baja un mandato\.$/)).toBeTruthy();
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
    expect(screen.queryByText(/no tenés permiso/i)).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    const titulo = await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" });
    const detalle = titulo.closest("section") as HTMLElement;
    expect(within(detalle).queryAllByRole("heading", { level: 3, name: "Dar de baja" })).toHaveLength(veces);
    expect(within(detalle).queryAllByRole("button", { name: "Dar de baja" })).toHaveLength(veces);
    expect(within(detalle).getByRole("button", { name: "Cerrar el detalle" })).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 3, name: "Dar de baja" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dar de baja" })).toBeNull();
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
    expect(screen.queryByText(/No tenés permiso/)).toBeNull();
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
    expect(screen.queryByText(/No tenés permiso/)).toBeNull();
  });
});
