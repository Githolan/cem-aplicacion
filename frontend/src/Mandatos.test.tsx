import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import { App } from "./App";
import { mensajeDeError } from "./Mandatos";
import { ContextoVersion } from "./version";

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "88888888-8888-4888-8888-888888888888";
const M_MC = "22222222-2222-4222-8222-222222222222";
const M_AE = "33333333-3333-4333-8333-333333333333";
const D_EQUIPO = "44444444-4444-4444-8444-444444444444";
const D_PROPIA = "55555555-5555-4555-8555-555555555555";
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
    { id: D_EQUIPO, tipo: "asamblea", fecha: "2026-01-10", descripcion: "Designa al Consejo" },
    { id: D_PROPIA, tipo: "consejo", fecha: "2026-03-05", descripcion: "Contrata al estudio" },
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
  matricula: "RPA 1234",
  decision_id: D_EQUIPO,
  facultades: ["documentos.cargar"],
  causa_baja: null,
  baja_decision_id: null,
  instrumento_referencia: null,
  motivo_fin: null,
  finalizado_en: null,
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

/** El Consejo: lee las tres listas. */
function conConsejo(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET ${RUTA_MANDATOS}`]: () => Response.json(MANDATOS),
    [`GET ${RUTA_DECISIONES}`]: () => Response.json(DECISIONES),
    [`GET ${RUTA_CANDIDATOS}`]: () => Response.json(CANDIDATOS),
    [`GET ${RUTA_DECISIONES}/${D_EQUIPO}`]: () => Response.json(detalleDecision(D_EQUIPO, false)),
    [`GET ${RUTA_DECISIONES}/${D_PROPIA}`]: () => Response.json(detalleDecision(D_PROPIA, true)),
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
const seccion = (nombre: string) => screen.getByRole("region", { name: nombre });
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("página /mandatos", () => {
  it("el inicio tiene el enlace a mandatos y decisiones", () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ estado: "ok" })));
    abrir("/");
    expect(screen.getByRole("link", { name: "Mandatos y decisiones" }).getAttribute("href")).toBe("/mandatos");
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
    const consejo = within(mandatos).getByText("Ana Díaz, Consejo de propietarios").closest("li") as HTMLElement;
    expect(within(consejo).queryByText("CUIT")).toBeNull();
    const externo = within(mandatos).getByText("Estudio Sur, Administración externa").closest("li") as HTMLElement;
    expect(within(externo).getByText(CUIT)).toBeTruthy();
    expect(within(externo).getByText(/1 de febrero de 2027 \(ese día ya no rige\)/)).toBeTruthy();
    const decisiones = seccion("Decisiones");
    expect(within(decisiones).getByText("Asamblea del 10 de enero de 2026")).toBeTruthy();
    expect(within(decisiones).queryByText(/Acta 14/)).toBeNull();
  });

  it("sin permiso de lectura (403) lo explica y no muestra el alta", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
        [`GET ${RUTA_MANDATOS}`]: () => error("sin_permiso", 403),
        [`GET ${RUTA_DECISIONES}`]: () => error("sin_permiso", 403),
        [`GET ${RUTA_CANDIDATOS}`]: () => error("sin_permiso", 403),
      }),
    );
    abrir();
    expect(await screen.findByText("No tenés permiso para ver los mandatos de este edificio.")).toBeTruthy();
    expect(screen.getByText("No tenés permiso para ver las decisiones de este edificio.")).toBeTruthy();
    expect(screen.getByText("No tenés permiso para dar de alta mandatos en este edificio.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dar de alta" })).toBeNull();
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
    expect(within(detalle).getByText("documentos.cargar")).toBeTruthy();
    expect(within(detalle).getByText("Asamblea del 10 de enero de 2026: Designa al Consejo")).toBeTruthy();
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

  it("avisa A15 si la decisión la registraste vos, pero no impide el envío: decide la API", async () => {
    const simulado = conConsejo({ [`POST ${RUTA_MANDATOS}`]: () => error("sin_permiso", 403) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta(D_PROPIA);
    expect(await screen.findByText(/Esa decisión la registraste vos: no podés usarla para dar el alta/)).toBeTruthy();
    const decision = screen.getByLabelText("Decisión que lo respalda");
    // El aviso es la descripción del selector, en una región que el lector anuncia al llegar.
    const descripcion = document.getElementById(decision.getAttribute("aria-describedby") ?? "");
    expect(descripcion?.textContent).toMatch(/la registraste vos/);
    expect(descripcion?.getAttribute("aria-live")).toBe("polite");
    fireEvent.click(boton("Dar de alta"));
    expect(await screen.findByText(/No tenés permiso para dar este alta. Puede ser .*la decisión la hayas registrado vos/)).toBeTruthy();
    expect(pedidos(simulado, "POST", RUTA_MANDATOS)).toHaveLength(1);
  });

  it("con la decisión del equipo no hay aviso A15 (después de que la API respondió)", async () => {
    const simulado = conConsejo();
    vi.stubGlobal("fetch", simulado);
    abrir();
    await completarAlta(D_EQUIPO);
    await waitFor(() => expect(pedidos(simulado, "GET", `${RUTA_DECISIONES}/${D_EQUIPO}`)).toHaveLength(1));
    // Control: la misma espera con la decisión propia sí muestra el aviso.
    fireEvent.change(screen.getByLabelText("Decisión que lo respalda"), { target: { value: D_PROPIA } });
    expect(await screen.findByText(/Esa decisión la registraste vos/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Decisión que lo respalda"), { target: { value: D_EQUIPO } });
    expect(screen.queryByText(/Esa decisión la registraste vos/)).toBeNull();
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

  it("la remoción se respalda con una asamblea: solo ofrece asambleas y avisa A15", async () => {
    const asambleaPropia = { id: NUEVO, tipo: "asamblea", fecha: "2026-09-01", descripcion: "Remueve al estudio" };
    const simulado = conConsejo({
      [`GET ${RUTA_DECISIONES}`]: () => Response.json({ elementos: [...DECISIONES.elementos, asambleaPropia] }),
      [`GET ${RUTA_DECISIONES}/${NUEVO}`]: () =>
        Response.json({ ...asambleaPropia, referencia: "Acta 20", sha256: null, registrada_por_vos: true, del_equipo: false }),
      [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE),
      [`POST ${RUTA_MANDATOS}/${M_AE}/baja`]: () => Response.json({ id: M_AE, causa: "remocion", vigente_hasta: "2026-10-19" }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await cargado();
    fireEvent.click(screen.getByRole("button", { name: "Ver detalle", description: "Estudio Sur, Administración externa" }));
    await screen.findByRole("heading", { level: 3, name: "Dar de baja" });
    fireEvent.change(screen.getByLabelText("Causa"), { target: { value: "remocion" } });
    const asambleas = within(screen.getByLabelText("Asamblea que lo remueve")).getAllByRole("option");
    expect(asambleas.map((o) => o.getAttribute("value"))).toEqual(["", D_EQUIPO, NUEVO]);
    fireEvent.change(screen.getByLabelText("Asamblea que lo remueve"), { target: { value: NUEVO } });
    expect(await screen.findByText(/Esa decisión la registraste vos: no podés usarla para remover/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Asamblea que lo remueve"), { target: { value: D_EQUIPO } });
    fireEvent.change(screen.getByLabelText("Por qué termina"), { target: { value: "Remoción" } });
    fireEvent.click(boton("Dar de baja"));
    fireEvent.click(boton("Sí, dar de baja"));
    await screen.findByText("Listo: dimos de baja el mandato de Estudio Sur.");
    const cuerpo = (await en(pedidos(simulado, "POST", `${RUTA_MANDATOS}/${M_AE}/baja`), 0).json()) as Record<string, unknown>;
    expect([cuerpo.causa, cuerpo.decision_id, cuerpo.instrumento]).toEqual(["remocion", D_EQUIPO, null]);
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

  it("al terminar un envío el foco va al aviso, y al cerrar el detalle vuelve a «Ver detalle»", async () => {
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
    const aviso = await screen.findByText("Listo: el mandato quedó dado de alta.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
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

  it("una lista que no se pudo consultar no arrastra a las otras", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_CANDIDATOS}`]: () => error("error_interno", 500) }));
    abrir();
    await cargado();
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

    it("un rechazo tardío de la consulta A15 del edificio anterior no muestra nada en el nuevo", async () => {
      let soltar: () => void = () => undefined;
      const retenido = new Promise<void>((r) => {
        soltar = r;
      });
      const base = conDos();
      let fallos = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (pedido: Request) => {
          if (new URL(pedido.url).pathname === `${RUTA_DECISIONES}/${D_PROPIA}`) {
            await retenido;
            fallos += 1;
            throw new TypeError("sin red");
          }
          return base(pedido);
        }),
      );
      abrir();
      await completarAlta(D_PROPIA);
      fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
      await screen.findByText("Eva Sosa, Consejo de propietarios");
      soltar();
      await waitFor(() => expect(fallos).toBe(1));
      await new Promise((r) => setTimeout(r, 0));
      expect(screen.queryByText(/No pudimos consultar quién registró esta decisión/)).toBeNull();
    });
  });

  it("si la consulta A15 falla en el mismo edificio, lo dice sin bloquear el envío", async () => {
    vi.stubGlobal(
      "fetch",
      conConsejo({
        [`GET ${RUTA_DECISIONES}/${D_PROPIA}`]: () => {
          throw new TypeError("sin red");
        },
      }),
    );
    abrir();
    await completarAlta(D_PROPIA);
    expect(await screen.findByText(/No pudimos consultar quién registró esta decisión/)).toBeTruthy();
    expect(boton("Dar de alta").hasAttribute("disabled")).toBe(false);
    // El aviso es de esa decisión: al elegir otra, que sí se pudo consultar, desaparece.
    fireEvent.change(screen.getByLabelText("Decisión que lo respalda"), { target: { value: D_EQUIPO } });
    expect(screen.queryByText(/No pudimos consultar quién registró esta decisión/)).toBeNull();
    // Y no queda como error de la página.
    expect(within(screen.getByRole("status")).queryByText(/No pudimos consultar/)).toBeNull();
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

  it("no tiene faltas de accesibilidad detectables por axe, con el detalle y el aviso A15 abiertos", async () => {
    vi.stubGlobal("fetch", conConsejo({ [`GET ${RUTA_MANDATOS}/${M_AE}`]: () => Response.json(DETALLE_AE) }));
    const { container } = abrir();
    await completarAlta(D_PROPIA);
    await screen.findByText(/Esa decisión la registraste vos/);
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
    expect(mensajeDeError(500, undefined, "alta")).toMatch(/No pudimos completar/);
    expect(mensajeDeError(403, "csrf_invalido", "baja")).toMatch(/Recargá la página/);
    expect(mensajeDeError(422, "codigo_desconocido", "decision")).toBe("Revisá los datos del formulario.");
  });
});
