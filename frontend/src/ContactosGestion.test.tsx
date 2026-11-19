import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import contrato from "../../contratos/openapi.json";
import type { components } from "./api/tipos";
import { alcanzaTodo } from "./accionesDePrueba";
import { App } from "./App";
import { LARGO_TELEFONO, MESES_DE_VALIDEZ_DE_LA_COMPROBACION, mensajeDeError, ofreceComprobar, PATRON_TELEFONO } from "./ContactosGestion";
import { PREFIJO_COPIA } from "./copiaContactos";
import { RECARGAR, SIN_CONEXION } from "./fallas";
import { ContextoVersion } from "./version";

// La gestión de los contactos, dentro de /contactos (G-52 y G-53): quien alcanza
// `contactos.gestionar` (acá, todo el catálogo con `alcanzaTodo`) ve la lista de gestión y sus actos en
// la misma pantalla que la lista general. Lo de quien no gestiona está en Contactos.test.tsx.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const K1 = "33333333-3333-4333-8333-333333333333";
const K2 = "44444444-4444-4444-8444-444444444444";
const P1 = "55555555-5555-4555-8555-555555555555";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const CONSORCIOS = [{ id: C1, nombre: "Edificio Azul", vinculos: [] }];
const BASE = `/api/v1/consorcios/${C1}/contactos`;
const GESTION = `${BASE}/gestion`;

type Contacto = components["schemas"]["ContactoGestion"];
type ContactoGeneral = components["schemas"]["Contacto"];

/** Un contacto del contrato: si le falta un campo, el tipo no compila. */
function contacto(id: string, nombre: string, extra: Partial<Contacto> = {}): Contacto {
  return {
    id,
    funcion: "emergencias",
    naturaleza: "servicio",
    nombre,
    telefono: "0800-555-1234",
    indicaciones: null,
    vigente_desde: "2026-10-01",
    vigente_hasta: null,
    actualizado_en: "2026-10-20T15:00:00Z",
    verificado_en: null,
    observado: false,
    consentimiento_referencia: null,
    observacion: null,
    actualizado_por: P1,
    verificado_por: null,
    observado_por: null,
    ...extra,
  };
}

const LISTA = {
  elementos: [
    contacto(K1, "Plomería Rápida", { indicaciones: "De 8 a 20; fuera de hora, el número de guardia" }),
    contacto(K2, "Marta Ruiz", {
      funcion: "encargado",
      naturaleza: "profesional",
      telefono: "11 4000-0000",
      consentimiento_referencia: "Nota firmada del 20-10",
      observado: true,
      observacion: "El teléfono no atiende",
      observado_por: P1,
    }),
  ],
};

/** Lo que publica la lista general de un contacto: sin la constancia, la observación ni quién cambió qué. */
function general({ id, funcion, naturaleza, nombre, telefono, indicaciones, vigente_desde, vigente_hasta, actualizado_en, verificado_en, observado }: Contacto): ContactoGeneral {
  return { id, funcion, naturaleza, nombre, telefono, indicaciones, vigente_desde, vigente_hasta, actualizado_en, verificado_en, observado };
}

/** La lista general del edificio, la que se guarda sin conexión. */
const listaGeneral = (elementos: readonly Contacto[] = LISTA.elementos) => ({
  elementos: elementos.map(general),
  actualizada_en: null,
  zona_horaria: "America/Argentina/Buenos_Aires",
});

/** Hace `meses` meses, en ISO 8601. */
function haceMeses(meses: number): string {
  const fecha = new Date();
  fecha.setMonth(fecha.getMonth() - meses);
  return fecha.toISOString();
}

function error(codigo: string, status: number, detalle: unknown = null) {
  return Response.json({ codigo, mensaje: "x", detalle, id_correlacion: "c" }, { status });
}

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

function conLista(extra: Respuestas = {}) {
  return backend({
    "GET /api/v1/me/consorcios": () => Response.json(CONSORCIOS),
    [`GET ${BASE}`]: () => Response.json(listaGeneral()),
    [`GET ${GESTION}`]: () => Response.json(LISTA),
    ...extra,
  });
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

function abrir(ruta = "/contactos") {
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

/** La fila del contacto: un botón cuyo nombre empieza con el título. */
async function filaDe(titulo: string): Promise<HTMLElement> {
  return screen.findByRole("button", { name: new RegExp(`^${titulo}( |$)`) });
}

/** Abre la ficha del contacto desde su fila (con el foco en la fila, como al tocarla) y la devuelve. */
async function abrirFicha(titulo: string): Promise<HTMLElement> {
  const fila = await filaDe(titulo);
  fila.focus();
  fireEvent.click(fila);
  return screen.getByRole("dialog", { name: titulo });
}

/** CON-04: el botón que envía la ventana de agregar. */
const enviarAgregar = () => within(screen.getByRole("dialog", { name: "Agregar un contacto" })).getByRole("button", { name: "Agregar" });

function escribir(etiqueta: string | RegExp, valor: string) {
  fireEvent.change(screen.getByLabelText(etiqueta), { target: { value: valor } });
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

describe("gestión de los contactos en /contactos", () => {
  it("G-52: una sola pantalla, con «Agregar un contacto» arriba de la lista, que abre el alta ahí mismo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    const agregar = await screen.findByRole("button", { name: "Agregar un contacto" });
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
    // Va antes de la lista (G-60).
    const lista = await screen.findByRole("list", { name: "Contactos de emergencia" });
    expect(agregar.compareDocumentPosition(lista) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Una sola lista: la de gestión reemplaza a la general, no se suma.
    expect(screen.getAllByRole("list", { name: "Contactos de emergencia" })).toHaveLength(1);
    // El teléfono, a un toque al lado de la fila, como para todos.
    expect(screen.getAllByRole("link", { name: "0800-555-1234" })[0]?.getAttribute("href")).toBe("tel:08005551234");
    fireEvent.click(agregar);
    // CON-04: el alta se abre en su ventana, con solo sus campos y sus dos botones.
    const ventana = await screen.findByRole("dialog", { name: "Agregar un contacto" });
    expect(within(ventana).getByLabelText("Nombre")).toBeTruthy();
    expect(within(ventana).getAllByRole("button").map((b) => b.textContent)).toEqual(["Agregar", "Cancelar"]);
    // CON-08: sin ayudas fijas; las etiquetas, cortas.
    expect(within(ventana).queryByText(/^Sin teléfonos ni correos/)).toBeNull();
    expect(within(ventana).queryByText(/^Solo números de empresas/)).toBeNull();
    expect(within(ventana).getByLabelText("Desde (opcional)")).toBeTruthy();
    expect(within(within(ventana).getByLabelText("¿De quién es el número?")).getByRole("option", { name: "Profesional" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
  });

  it("control: la pantalla abierta sin tocar nada no abre ningún formulario", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await filaDe("Encargado/a: Marta Ruiz");
    expect(screen.queryByRole("heading", { name: "Agregar un contacto" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    expect(screen.queryByText(/¿Retirar el contacto/)).toBeNull();
  });

  it("/contactos/gestion, la dirección de antes, lleva a /contactos con la gestión", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir("/contactos/gestion");
    expect(await screen.findByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Gestionar los contactos de emergencia" })).toBeNull();
    // Control: es la pantalla de /contactos, que lee las dos listas.
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(1);
  });

  it("la ficha ofrece corregir, comprobar, señalar y retirar, en botones, y «Retirar» en rojo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    const acciones = plomeria.querySelector(".ficha-registro__acciones")!;
    expect(within(acciones as HTMLElement).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Corregir",
      "Marcar como comprobado",
      "Señalar un error",
      "Retirar",
    ]);
    expect(within(plomeria).getByRole("button", { name: "Retirar" }).className).toBe("secundario peligro");
  });

  it("«Retirar» abre la pregunta de ese contacto, y «Corregir» sus datos", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    fireEvent.click(within(await abrirFicha("Encargado/a: Marta Ruiz")).getByRole("button", { name: "Retirar" }));
    expect(await screen.findByText("¿Retirar el contacto Marta Ruiz? No se puede deshacer.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sí, retirar" }).className).toBe("peligro");
    // Solo ese: el otro contacto sigue sin nada abierto.
    expect(screen.queryByText(/¿Retirar el contacto Plomería Rápida/)).toBeNull();
    // CON-05: la pregunta va en su ventana, encima de la ficha, y «Cancelar» vuelve a la ficha.
    expect(within(screen.getByRole("dialog", { name: "Retirar el contacto" })).queryByText("Teléfono")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cerrar" }));

    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Corregir" }));
    expect(await screen.findByRole("heading", { name: "Corregir el contacto" })).toBeTruthy();
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida");
  });

  it("G-53: retirar termina en /contactos con el «Listo» y vuelve a leer las dos listas", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Retirar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sí, retirar" }));
    expect(await screen.findByText("Listo: el contacto Plomería Rápida se retiró.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(1);
    // La lista general también: la copia sin conexión queda sin el retirado.
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await waitFor(() => expect(pedidos(simulado, "GET", BASE)).toHaveLength(2));
  });

  it("G-53: «Marcar como comprobado» termina con el «Listo» en la misma pantalla; «Cancelar» en señalar vuelve sin nada", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/verificar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Marcar como comprobado" }));
    expect(await screen.findByText("Listo: quedó la constancia de que el contacto Plomería Rápida se comprobó.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/verificar`)).toHaveLength(1);

    const ficha = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(ficha).getByRole("button", { name: "Señalar un error" }));
    expect(await screen.findByLabelText("Motivo")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    // De vuelta en la ficha, con sus actos, sin enviar nada.
    expect(within(ficha).getByRole("button", { name: "Señalar un error" })).toBeTruthy();
    expect(screen.queryByLabelText("Motivo")).toBeNull();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(0);
  });

  it("CON-06: «Marcar como comprobado» se manda con su botón, sin un paso intermedio", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/verificar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Marcar como comprobado" }));
    // Ninguna ventana con «Lo comprobé»: el pedido sale con el toque.
    expect(screen.queryByRole("dialog", { name: "Dejar constancia de la comprobación" })).toBeNull();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/verificar`)).toHaveLength(1));
    const aviso = await screen.findByText("Listo: quedó la constancia de que el contacto Plomería Rápida se comprobó.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("CON-06: si comprobar se rechaza, el motivo queda en la ficha, que sigue abierta", async () => {
    vi.stubGlobal("fetch", conLista({ [`POST ${BASE}/${K1}/verificar`]: () => error("sin_permiso", 403) }));
    abrir();
    const ficha = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(ficha).getByRole("button", { name: "Marcar como comprobado" }));
    expect((await within(ficha).findByRole("alert")).textContent).toMatch(/^Esta cuenta no tiene permiso para gestionar los contactos/);
    expect(screen.getByRole("dialog", { name: "Emergencias: Plomería Rápida" })).toBeTruthy();
    // Control: otro acto se lleva el rechazo.
    fireEvent.click(within(ficha).getByRole("button", { name: "Señalar un error" }));
    expect(within(ficha).queryByRole("alert")).toBeNull();
  });

  it("G-54: un contacto ya señalado no ofrece «Señalar un error»; uno sin señalar, sí", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    expect(within(marta).queryByRole("button", { name: "Señalar un error" })).toBeNull();
    expect(within(marta).getByRole("button", { name: "Marcar como comprobado" })).toBeTruthy();
    fireEvent.click(within(marta).getByRole("button", { name: "Cerrar" }));
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    expect(within(plomeria).getByRole("button", { name: "Señalar un error" })).toBeTruthy();
  });

  describe("G-54: «Marcar como comprobado» solo cuando hace falta", () => {
    const conComprobacion = (verificado_en: string | null, observado = false) =>
      conLista({
        [`GET ${GESTION}`]: () =>
          Response.json({ elementos: [contacto(K1, "Plomería Rápida", { verificado_en, observado, verificado_por: verificado_en === null ? null : P1 })] }),
      });

    it("comprobado hace 2 meses: no lo ofrece, y la fecha va una sola vez", async () => {
      const cuando = haceMeses(2);
      vi.stubGlobal("fetch", conComprobacion(cuando));
      abrir();
      const ficha = await abrirFicha("Emergencias: Plomería Rápida");
      expect(within(ficha).queryByRole("button", { name: "Marcar como comprobado" })).toBeNull();
      // CON-07: la fecha la dice una sola vez «Última comprobación»; no hay otro «Comprobado el …».
      expect(within(ficha).getByText("Última comprobación")).toBeTruthy();
      expect(within(ficha).queryByText(/^Comprobado el /)).toBeNull();
      expect(within(ficha).queryByText("Última modificación")).toBeNull();
      expect(cuando).toBeTruthy();
      // Control: los demás actos siguen.
      expect(within(ficha).getByRole("button", { name: "Corregir" })).toBeTruthy();
      expect(within(ficha).getByRole("button", { name: "Señalar un error" })).toBeTruthy();
    });

    it("comprobado hace 13 meses: lo ofrece otra vez", async () => {
      vi.stubGlobal("fetch", conComprobacion(haceMeses(13)));
      abrir();
      const ficha = await abrirFicha("Emergencias: Plomería Rápida");
      expect(within(ficha).getByRole("button", { name: "Marcar como comprobado" })).toBeTruthy();
      expect(within(ficha).queryByText(/^Comprobado el /)).toBeNull();
    });

    it("comprobado hace 2 meses pero con un dato señalado: lo ofrece, porque comprobar lo resuelve", async () => {
      vi.stubGlobal("fetch", conComprobacion(haceMeses(2), true));
      abrir();
      const ficha = await abrirFicha("Emergencias: Plomería Rápida");
      expect(within(ficha).getByRole("button", { name: "Marcar como comprobado" })).toBeTruthy();
    });

    it("sin comprobar nunca: lo ofrece", async () => {
      vi.stubGlobal("fetch", conComprobacion(null));
      abrir();
      const ficha = await abrirFicha("Emergencias: Plomería Rápida");
      expect(within(ficha).getByRole("button", { name: "Marcar como comprobado" })).toBeTruthy();
    });

    it("el plazo es el de la constante, contado en meses de calendario", () => {
      expect(MESES_DE_VALIDEZ_DE_LA_COMPROBACION).toBe(12);
      const ahora = new Date("2026-11-14T12:00:00-03:00");
      const sinSenalar = (verificado_en: string) => ({ observado: false, verificado_en });
      expect(ofreceComprobar(sinSenalar("2025-11-15T12:00:00-03:00"), ahora)).toBe(false);
      expect(ofreceComprobar(sinSenalar("2025-11-13T12:00:00-03:00"), ahora)).toBe(true);
      expect(ofreceComprobar({ observado: true, verificado_en: "2026-11-13T12:00:00-03:00" }, ahora)).toBe(true);
      expect(ofreceComprobar({ observado: false, verificado_en: null }, ahora)).toBe(true);
    });
  });

  // Con dos edificios, el selector de /contactos elige también dónde se gestiona.
  const C2 = "22222222-2222-4222-8222-222222222222";
  const BASE2 = `/api/v1/consorcios/${C2}/contactos`;
  const DOS_EDIFICIOS = [...CONSORCIOS, { id: C2, nombre: "Edificio Bordó", vinculos: [] }];
  const K3 = "66666666-6666-4666-8666-666666666666";
  const GAS = contacto(K3, "Gas Ficticio");

  it("en el segundo edificio, la lista y lo que se agrega son de ese edificio", async () => {
    const simulado = conLista({
      "GET /api/v1/me/consorcios": () => Response.json(DOS_EDIFICIOS),
      [`GET ${BASE2}`]: () => Response.json(listaGeneral([GAS])),
      [`GET ${BASE2}/gestion`]: () => Response.json({ elementos: [GAS] }),
      [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }),
      [`POST ${BASE2}`]: () => Response.json({ id: K3 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await filaDe("Encargado/a: Marta Ruiz");
    fireEvent.change(screen.getByLabelText("Edificio"), { target: { value: C2 } });
    await filaDe("Emergencias: Gas Ficticio");
    expect(screen.queryByRole("button", { name: /^Encargado\/a: Marta Ruiz/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "Seguros del Plata");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(enviarAgregar());
    await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    expect(pedidos(simulado, "POST", BASE2)).toHaveLength(1);
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(0);
  });

  it("con un pedido en curso no se puede cambiar de edificio; al terminar, sí", async () => {
    let soltar: () => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      conLista({
        "GET /api/v1/me/consorcios": () => Response.json(DOS_EDIFICIOS),
        [`POST ${BASE}/${K1}/verificar`]: () =>
          new Promise<Response>((listo) => {
            soltar = () => listo(Response.json({ id: K1 }));
          }),
      }),
    );
    abrir();
    fireEvent.click(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Marcar como comprobado" }));
    const edificio = screen.getByLabelText("Edificio") as HTMLSelectElement;
    await waitFor(() => expect(edificio.disabled).toBe(true));
    soltar();
    await screen.findByText("Listo: quedó la constancia de que el contacto Plomería Rápida se comprobó.");
    await waitFor(() => expect((screen.getByLabelText("Edificio") as HTMLSelectElement).disabled).toBe(false));
  });

  it("sin sesión (401) en la lista de gestión manda a /entrar y borra las copias de /contactos", async () => {
    localStorage.setItem(`${PREFIJO_COPIA}${C1}`, "{}");
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => error("no_autenticado", 401) }));
    abrir();
    expect(await screen.findByRole("heading", { level: 1, name: "Entrar a Consorcio en Mano" })).toBeTruthy();
    expect(localStorage.length).toBe(0);
  });

  it("muestra cada contacto con lo de gestión, y en el dispositivo queda solo la copia de la lista general", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    expect(within(plomeria).getByText("Servicio")).toBeTruthy();
    expect(within(plomeria).getByRole("link", { name: "0800-555-1234" }).getAttribute("href")).toBe("tel:08005551234");
    expect(within(plomeria).getByText("Desde el 1 de octubre de 2026, sin fecha de fin")).toBeTruthy();
    expect(within(plomeria).getByText("Sin comprobar")).toBeTruthy();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    expect(within(marta).getByText("Nota firmada del 20-10")).toBeTruthy();
    expect(within(marta).getByText("El teléfono no atiende")).toBeTruthy();
    // Nada de la lista de gestión queda en el almacenamiento (§5): solo la copia de la lista general.
    expect(Object.keys(localStorage)).toEqual([`${PREFIJO_COPIA}${C1}`]);
    const guardada = localStorage.getItem(`${PREFIJO_COPIA}${C1}`) ?? "";
    expect((JSON.parse(guardada) as { lista: unknown }).lista).toEqual(listaGeneral());
    // Control: la búsqueda encuentra un dato de la lista general, y ninguno de la de gestión.
    expect(guardada).toContain("Marta Ruiz");
    for (const deGestion of ["Nota firmada del 20-10", "El teléfono no atiende", "consentimiento_referencia", "observacion", P1])
      expect(guardada).not.toContain(deGestion);
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(1);
    expect(pedidos(simulado, "GET", BASE)).toHaveLength(1);
  });

  it("un edificio sin contactos lo dice y deja agregar el primero", async () => {
    vi.stubGlobal(
      "fetch",
      conLista({
        [`GET ${BASE}`]: () => Response.json(listaGeneral([])),
        [`GET ${GESTION}`]: () => Response.json({ elementos: [] }),
      }),
    );
    abrir();
    expect(await screen.findByText("El edificio no tiene contactos de emergencia cargados.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Contactos de emergencia" })).toBeNull();
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
  });

  it("sin conexión al pedir la lista de gestión lo dice, deja la lista general sin actos y el botón para agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => Promise.reject(new TypeError("Failed to fetch")) }));
    abrir();
    expect(await screen.findByText(/^No se pudo consultar la lista para gestionar los contactos\./)).toBeTruthy();
    // CON-10: el motivo, una sola vez, junto al botón.
    expect(screen.getAllByText((_, e) => e?.tagName === "P" && (e.textContent ?? "").includes(SIN_CONEXION))).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    // Son teléfonos de emergencia: la lista general sigue a la vista, y su ficha es solo de lectura.
    const ficha = await abrirFicha("Emergencias: Plomería Rápida");
    expect(within(ficha).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cerrar"]);
  });

  it("con la lista caída se agrega igual, y lo escrito sigue mientras se vuelve a consultar", async () => {
    let caida = true;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => (caida ? Promise.reject(new TypeError("Failed to fetch")) : Response.json(LISTA)),
      [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    const { container } = abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations.map((v) => v.id)).toEqual([]);
    const nombre = screen.getByLabelText("Nombre") as HTMLInputElement;
    escribir("Nombre", "Bomberos");
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    await waitFor(() => expect(screen.queryByText(/^No se pudo consultar la lista para gestionar los contactos\./)).toBeNull());
    await filaDe("Emergencias: Plomería Rápida");
    // El mismo campo: el formulario no se desarmó mientras llegaba la lista.
    expect(screen.getByLabelText("Nombre")).toBe(nombre);
    expect(nombre.value).toBe("Bomberos");
    fireEvent.click(enviarAgregar());
    expect(await screen.findByText("Listo: el contacto Bomberos quedó agregado.")).toBeTruthy();
    expect(pedidos(simulado, "POST", BASE)).toHaveLength(1);
  });

  it("la lista que no se pudo consultar se reintenta en la misma pantalla y el error se va", async () => {
    let caida = true;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => (caida ? Promise.reject(new TypeError("Failed to fetch")) : Response.json(LISTA)),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(/^No se pudo consultar la lista para gestionar los contactos\./)).toBeTruthy();
    caida = false;
    fireEvent.click(screen.getByRole("button", { name: "Consultar de nuevo" }));
    await waitFor(() => expect(screen.queryByText(/^No se pudo consultar la lista para gestionar los contactos\./)).toBeNull());
    expect(within(await abrirFicha("Emergencias: Plomería Rápida")).getByRole("button", { name: "Corregir" })).toBeTruthy();
    expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2);
    expect(screen.queryByText(SIN_CONEXION)).toBeNull();
    // Control: con la lista a la vista no hay nada que reintentar.
    expect(screen.queryByRole("button", { name: "Consultar de nuevo" })).toBeNull();
  });

  it("un 403 al leer la lista de gestión deja la lista general, sin actos ni botón para agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`GET ${GESTION}`]: () => error("sin_permiso", 403) }));
    abrir();
    const ficha = await abrirFicha("Emergencias: Plomería Rápida");
    expect(within(ficha).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cerrar"]);
    expect(screen.getByRole("heading", { level: 1, name: "Contactos de emergencia" })).toBeTruthy();
    expect(screen.queryByText(/no tiene permiso/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar un contacto" })).toBeNull();
  });

  it("agrega con CSRF, una clave de reintento y el cuerpo del contrato (lo vacío va nulo), y vuelve a pedir la lista", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Función"));
    expect(screen.queryByLabelText(/Dónde consta que aceptó figurar/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "  Seguros del Plata  ");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(enviarAgregar());

    const aviso = await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    const [pedido] = pedidos(simulado, "POST", BASE);
    expect(pedido?.headers.get("X-CSRF-Token")).toBe("abc123");
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.json()).toEqual({
      funcion: "aseguradora",
      naturaleza: "empresa",
      nombre: "Seguros del Plata",
      telefono: "0810-222-3333",
      consentimiento_referencia: null,
      indicaciones: null,
      vigente_desde: null,
    });
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
  });

  it("un profesional pide la constancia y el teléfono; otra naturaleza manda la constancia nula", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => Response.json({ id: K1 }, { status: 201 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "profesional" } });
    const constancia = screen.getByLabelText(/Dónde consta que aceptó figurar/);
    expect(constancia.hasAttribute("required")).toBe(true);
    expect(screen.getByLabelText("Teléfono").hasAttribute("required")).toBe(true);
    escribir(/Dónde consta que aceptó figurar/, "Nota firmada del 20-10");
    // Si cambia de idea, la constancia escrita no viaja con otra naturaleza.
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "organismo" } });
    expect(screen.queryByLabelText(/Dónde consta que aceptó figurar/)).toBeNull();
    expect(screen.getByLabelText(/^Teléfono/).hasAttribute("required")).toBe(false);
    escribir("Nombre", "Bomberos");
    escribir(/^Teléfono/, "100");
    fireEvent.click(enviarAgregar());
    await screen.findByText("Listo: el contacto Bomberos quedó agregado.");
    const cuerpo = (await pedidos(simulado, "POST", BASE)[0]?.json()) as Record<string, unknown>;
    expect([cuerpo.naturaleza, cuerpo.consentimiento_referencia]).toEqual(["organismo", null]);
  });

  it("corregir arranca con los datos del contacto y manda un PUT con todos los campos", async () => {
    const simulado = conLista({ [`PUT ${BASE}/${K2}`]: () => Response.json({ id: K2 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Marta Ruiz");
    expect((screen.getByLabelText(/Dónde consta que aceptó figurar/) as HTMLInputElement).value).toBe("Nota firmada del 20-10");
    expect(screen.queryByLabelText(/^Desde/)).toBeNull();
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: el contacto Marta Ruiz quedó corregido.");
    expect(await pedidos(simulado, "PUT", `${BASE}/${K2}`)[0]?.json()).toEqual({
      funcion: "encargado",
      naturaleza: "profesional",
      nombre: "Marta Ruiz",
      telefono: "11 4000-0001",
      consentimiento_referencia: "Nota firmada del 20-10",
      indicaciones: null,
    });
  });

  it("corregir y señalar: con lo obligatorio vacío, el botón marca el campo y no manda nada (decisión 4)", async () => {
    const simulado = conLista();
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "  ");
    escribir(/Dónde consta que aceptó figurar/, "");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Falta escribir el nombre.")).toBeTruthy();
    expect(screen.getByText("Falta escribir dónde consta que aceptó figurar.")).toBeTruthy();
    expect(screen.getByLabelText("Nombre").getAttribute("aria-invalid")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Nombre")));
    expect(pedidos(simulado, "PUT", `${BASE}/${K2}`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.click(within(marta).getByRole("button", { name: "Cerrar" }));

    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    expect(await screen.findByText("Falta escribir el motivo.")).toBeTruthy();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(0);
  });

  it("mientras se agrega un contacto, las filas no abren otra ficha; al terminar, sí", async () => {
    let soltar: () => void = () => undefined;
    const simulado = conLista({
      [`POST ${BASE}`]: () =>
        new Promise<Response>((listo) => {
          soltar = () => listo(Response.json({ id: K1 }, { status: 201 }));
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("Función"), { target: { value: "aseguradora" } });
    escribir("Nombre", "Seguros del Plata");
    escribir(/^Teléfono/, "0810-222-3333");
    fireEvent.click(enviarAgregar());
    const fila = await screen.findByRole("button", { name: /Encargado\/a: Marta Ruiz/ });
    await waitFor(() => expect((fila as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(fila);
    // Solo la ventana de agregar, que sigue abierta mientras viaja el pedido: ninguna ficha.
    expect(screen.queryByRole("dialog", { name: "Encargado/a: Marta Ruiz" })).toBeNull();
    expect(enviarAgregar()).toBeTruthy();
    soltar();
    await screen.findByText("Listo: el contacto Seguros del Plata quedó agregado.");
    // Control: terminado el pedido, la fila vuelve a abrir la ficha.
    expect(await abrirFicha("Encargado/a: Marta Ruiz")).toBeTruthy();
  });

  it("CON-09: después de un cambio, la lista vieja queda a la vista pero no se puede usar hasta que llega la nueva", async () => {
    let soltar: () => void = () => undefined;
    let vueltas = 0;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => {
        vueltas += 1;
        if (vueltas === 1) return Response.json(LISTA);
        return new Promise<Response>((listo) => {
          soltar = () => listo(Response.json(LISTA));
        });
      },
      [`PUT ${BASE}/${K2}`]: () => Response.json({ id: K2 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    fireEvent.click(within(marta).getByRole("button", { name: "Corregir" }));
    escribir("Teléfono", "11 4000-0001");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    await screen.findByText("Listo: el contacto Marta Ruiz quedó corregido.");
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    // La lista vieja queda a la vista, sin «Consultando…», pero sus filas no abren la ficha.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryAllByRole("button", { name: "Corregir" })).toHaveLength(0);
    expect(screen.getByRole("list", { name: "Contactos de emergencia" })).toBeTruthy();
    expect(screen.queryByText("Consultando…")).toBeNull();
    const fila = await filaDe("Encargado/a: Marta Ruiz");
    expect((fila as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(fila);
    expect(screen.queryByRole("dialog")).toBeNull();
    // «Agregar un contacto» sigue en su lugar.
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
    soltar();
    await waitFor(() => expect((fila as HTMLButtonElement).disabled).toBe(false));
    // Control: con la lista nueva, las filas vuelven y la ficha ofrece corregir otra vez.
    expect(within(await abrirFicha("Encargado/a: Marta Ruiz")).getByRole("button", { name: "Corregir" })).toBeTruthy();
  });

  it("retirar, desde la ficha, pregunta de una en dos toques; «Cancelar» no envía y «Sí, retirar» sí", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => Response.json({ id: K1 }) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    expect(screen.getByText("¿Retirar el contacto Plomería Rápida? No se puede deshacer.")).toBeTruthy();
    // El foco queda en la pregunta, no en «Sí, retirar»: dos Enter seguidos no retiran (P3 de #125).
    expect(document.activeElement).toBe(screen.getByRole("dialog", { name: "Retirar el contacto" }));
    // Control: el paso intermedio ya no existe, y abrir la pregunta no mandó nada.
    expect(screen.queryByRole("button", { name: "Retirar el contacto" })).toBeNull();
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/retirar`)).toHaveLength(0);
    // «Cancelar» deja la ficha como estaba.
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    await screen.findByText("Listo: el contacto Plomería Rápida se retiró.");
    const [pedido] = pedidos(simulado, "POST", `${BASE}/${K1}/retirar`);
    expect(pedido?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await pedido?.text()).toBe("");
  });

  it("si retirar falla, la pregunta sigue para reintentar con la misma clave", async () => {
    const ruta = `${BASE}/${K1}/retirar`;
    const simulado = conLista({ [`POST ${ruta}`]: () => error("servicio_no_disponible", 503) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    const si = screen.getByRole("button", { name: "Sí, retirar" }) as HTMLButtonElement;
    fireEvent.click(si);
    const aviso = await screen.findByText(/El servicio no está disponible/);
    await waitFor(() => expect(document.activeElement).toBe(aviso));
    // El rechazo va dentro de la ventana abierta, encima de la ficha: lo de atrás queda inerte.
    expect(screen.getByRole("dialog", { name: "Retirar el contacto" }).contains(aviso)).toBe(true);
    expect(plomeria.contains(aviso)).toBe(false);
    expect(aviso.getAttribute("role")).toBe("alert");
    expect(si.disabled).toBe(false);
    fireEvent.click(si);
    await waitFor(() => expect(pedidos(simulado, "POST", ruta)).toHaveLength(2));
    const [primera, segunda] = pedidos(simulado, "POST", ruta).map((p) => p.headers.get("Idempotency-Key"));
    expect(segunda).toBe(primera);
  });

  it("retirar un contacto que ya terminó lo explica, cierra la pregunta y recarga la lista", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/retirar`]: () => error("contacto_terminado", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Retirar" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    expect(await screen.findByText("Ese contacto ya no está vigente: para cambiarlo, hay que agregar uno nuevo.")).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Emergencias: Plomería Rápida");
    expect(screen.queryByRole("button", { name: "Sí, retirar" })).toBeNull();
    // No había nada escrito: retirar no abre Agregar.
    expect(screen.queryByLabelText("Nombre")).toBeNull();
  });

  it("comprobar y señalar mandan sus pedidos; señalar lleva el motivo", async () => {
    const simulado = conLista({
      [`POST ${BASE}/${K1}/verificar`]: () => Response.json({ id: K1 }),
      [`POST ${BASE}/${K1}/senalar`]: () => Response.json({ id: K1 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    let plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Marcar como comprobado" }));
    await screen.findByText("Listo: quedó la constancia de que el contacto Plomería Rápida se comprobó.");
    expect(pedidos(simulado, "POST", `${BASE}/${K1}/verificar`)).toHaveLength(1);
    plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Motivo"));
    escribir("Motivo", " Cambió de dirección ");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    await screen.findByText("Listo: quedó señalado el dato incorrecto del contacto Plomería Rápida.");
    expect(await pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)[0]?.json()).toEqual({ motivo: "Cambió de dirección" });
  });

  it("G-58: un 422 del nombre se dice en una frase corta y el motivo va junto al nombre", async () => {
    const simulado = conLista({
      [`PUT ${BASE}/${K1}`]: () =>
        error("peticion_invalida", 422, {
          campos: ["body.nombre"],
          mensajes: { "body.nombre": "Sin teléfonos ni correos acá: el número va solo en el teléfono." },
        }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Llamar al 11 2345 6789");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    const resumen = await screen.findByText(/^Hay que corregir el nombre/);
    expect((resumen.textContent ?? "").length).toBeLessThan(100);
    const nombre = screen.getByLabelText("Nombre");
    expect(nombre.getAttribute("aria-invalid")).toBe("true");
    const descripciones = (nombre.getAttribute("aria-describedby") ?? "").split(" ").map((i) => document.getElementById(i)?.textContent);
    expect(descripciones).toContain("Sin teléfonos ni correos acá: el número va solo en el teléfono.");
    // Control: el teléfono no queda marcado.
    expect(screen.getByLabelText(/^Teléfono/).getAttribute("aria-invalid")).toBeNull();
  });

  it("G-57: el teléfono mal escrito lo dice con su propio texto, junto al campo", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    const telefono = screen.getByLabelText(/^Teléfono/) as HTMLInputElement;
    fireEvent.change(telefono, { target: { value: "abc" } });
    expect(telefono.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("El teléfono va solo con números, espacios, paréntesis o guiones.")).toBeTruthy();
    expect(telefono.validationMessage).toBe("El teléfono va solo con números, espacios, paréntesis o guiones.");
    // Control: bien escrito, el aviso se va.
    fireEvent.change(telefono, { target: { value: "011 4567-8901" } });
    expect(telefono.getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText("El teléfono va solo con números, espacios, paréntesis o guiones.")).toBeNull();
    expect(telefono.validationMessage).toBe("");
  });

  it("G-59: «Cancelar» en «Agregar un contacto» se lleva el error", async () => {
    vi.stubGlobal("fetch", conLista({ [`POST ${BASE}`]: () => error("peticion_invalida", 422, { campos: ["body.nombre"] }) }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    escribir("Nombre", "Cerrajería Ficticia");
    fireEvent.click(enviarAgregar());
    // Control: el error se ve mientras el formulario está abierto.
    expect(await screen.findByText(/^Hay que corregir el nombre/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog", { name: "Agregar un contacto" })).toBeNull();
    expect(screen.queryByText(/^Hay que corregir el nombre/)).toBeNull();
  });

  it("un 422 dice qué campo revisar y por qué; un 409 contacto_terminado lo explica, recarga la lista y lo escrito pasa a Agregar", async () => {
    let respuesta = () => error("peticion_invalida", 422, { campos: ["body.nombre"] });
    const simulado = conLista({
      [`PUT ${BASE}/${K1}`]: () => respuesta(),
      [`POST ${BASE}`]: () => Response.json({ id: K2 }, { status: 201 }),
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Llamar al 11 2345 6789");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Hay que corregir el nombre: el motivo está junto a cada campo.")).toBeTruthy();
    // El formulario sigue abierto para corregir.
    expect(screen.getByRole("button", { name: "Guardar la corrección" })).toBeTruthy();
    respuesta = () => error("contacto_terminado", 409);
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    // CON-04: lo escrito pasa a la ventana de agregar, que dice por qué.
    const ventana = await screen.findByRole("dialog", { name: "Agregar un contacto" });
    expect(within(ventana).getByRole("alert").textContent).toBe("Ese contacto ya no está vigente: para cambiarlo, hay que agregar uno nuevo.");
    expect(screen.queryByRole("button", { name: "Guardar la corrección" })).toBeNull();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    // Lo escrito sigue, ahora en Agregar, y sale como un pedido nuevo y no como otro PUT.
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida SRL");
    fireEvent.click(enviarAgregar());
    expect(await screen.findByText("Listo: el contacto Plomería Rápida SRL quedó agregado.")).toBeTruthy();
    const correcciones = pedidos(simulado, "PUT", `${BASE}/${K1}`);
    expect(correcciones).toHaveLength(2);
    const [alta] = pedidos(simulado, "POST", BASE);
    expect(alta?.headers.get("Idempotency-Key")).not.toBe(correcciones[1]?.headers.get("Idempotency-Key"));
    expect(await alta?.json()).toEqual({
      funcion: "emergencias",
      naturaleza: "servicio",
      nombre: "Plomería Rápida SRL",
      telefono: "0800-555-1234",
      consentimiento_referencia: null,
      indicaciones: "De 8 a 20; fuera de hora, el número de guardia",
      vigente_desde: null,
    });
  });

  it("corregir un contacto que ya no está (404) también deja lo escrito en Agregar", async () => {
    vi.stubGlobal("fetch", conLista({ [`PUT ${BASE}/${K1}`]: () => error("no_encontrado", 404) }));
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText("Ese contacto ya no está en este edificio.")).toBeTruthy();
    expect((await screen.findByLabelText("Nombre") as HTMLInputElement).value).toBe("Plomería Rápida SRL");
    expect(enviarAgregar()).toBeTruthy();
  });

  it("agregar con un 404 (el edificio ya no se ve) pide recargar, no habla de un contacto y deja lo escrito", async () => {
    const simulado = conLista({ [`POST ${BASE}`]: () => error("no_encontrado", 404) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: "Agregar un contacto" }));
    escribir("Nombre", "Seguros del Plata");
    fireEvent.click(enviarAgregar());
    const ventana = screen.getByRole("dialog", { name: "Agregar un contacto" });
    expect(await within(ventana).findByText(RECARGAR)).toBeTruthy();
    expect(screen.queryByText("Ese contacto ya no está en este edificio.")).toBeNull();
    expect((within(ventana).getByLabelText("Nombre") as HTMLInputElement).value).toBe("Seguros del Plata");
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    // Control: el 404 de un acto sobre un contacto sigue diciendo que el contacto ya no está.
    expect(mensajeDeError(404, "no_encontrado", "retirar")).toBe("Ese contacto ya no está en este edificio.");
  });

  it("control: otro 409 al corregir cierra el formulario, porque un PUT viejo desharía el cambio de otro", async () => {
    const simulado = conLista({ [`PUT ${BASE}/${K1}`]: () => error("conflicto", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Corregir" }));
    escribir("Nombre", "Plomería Rápida SRL");
    fireEvent.click(screen.getByRole("button", { name: "Guardar la corrección" }));
    expect(await screen.findByText(/Otra persona cambió este dato recién/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Emergencias: Plomería Rápida");
    expect(screen.queryByRole("button", { name: "Guardar la corrección" })).toBeNull();
    expect(screen.queryByLabelText("Nombre")).toBeNull();
  });

  it("si señalar choca con la lista, el motivo sigue cuando la lista nueva trae el contacto", async () => {
    const simulado = conLista({ [`POST ${BASE}/${K1}/senalar`]: () => error("conflicto", 409) });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    expect(await screen.findByText(/Otra persona cambió este dato recién/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    // La ficha y la ventana de señalar vuelven con la lista nueva, con lo escrito.
    await screen.findByRole("dialog", { name: "Emergencias: Plomería Rápida" });
    const nueva = await screen.findByRole("dialog", { name: "Señalar un dato incorrecto" });
    expect((within(nueva).getByLabelText("Motivo") as HTMLInputElement).value).toBe("No atiende");
  });

  it("control: si la lista nueva ya no trae el contacto, el formulario de señalar se va", async () => {
    let terminado = false;
    const simulado = conLista({
      [`GET ${GESTION}`]: () => Response.json(terminado ? { elementos: LISTA.elementos.slice(1) } : LISTA),
      [`POST ${BASE}/${K1}/senalar`]: () => {
        terminado = true;
        return error("contacto_terminado", 409);
      },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    expect(await screen.findByText(/^Ese contacto ya no está vigente/)).toBeTruthy();
    await waitFor(() => expect(pedidos(simulado, "GET", GESTION)).toHaveLength(2));
    await filaDe("Encargado/a: Marta Ruiz");
    expect(screen.queryByRole("heading", { level: 3, name: "Emergencias: Plomería Rápida" })).toBeNull();
    expect(screen.queryByLabelText("Motivo")).toBeNull();
    // Un motivo no alcanza para un contacto nuevo: señalar no abre Agregar.
    expect(screen.getByRole("button", { name: "Agregar un contacto" })).toBeTruthy();
  });

  it("un 5xx conserva la clave para el reintento; un 4xx definitivo la renueva; cambiar los datos también", async () => {
    let respuesta = () => error("servicio_no_disponible", 503);
    const simulado = conLista({ [`POST ${BASE}/${K1}/senalar`]: () => respuesta() });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    escribir("Motivo", "No atiende");
    const enviar = () => fireEvent.click(screen.getByRole("button", { name: "Señalar el error" }));
    enviar();
    await screen.findByText(/El servicio no está disponible/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(2));
    respuesta = () => error("peticion_invalida", 422, { campos: ["body.motivo"] });
    enviar();
    await screen.findByText(/^Hay que corregir el motivo/);
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(4));
    escribir("Motivo", "No atiende nunca");
    enviar();
    await waitFor(() => expect(pedidos(simulado, "POST", `${BASE}/${K1}/senalar`)).toHaveLength(5));
    const claves = pedidos(simulado, "POST", `${BASE}/${K1}/senalar`).map((p) => p.headers.get("Idempotency-Key"));
    expect(claves[0]).toBe(claves[1]);
    expect(claves[1]).toBe(claves[2]);
    expect(claves[3]).not.toBe(claves[2]);
    expect(claves[4]).not.toBe(claves[3]);
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la lista, la ficha y cada formulario abierto", async () => {
    vi.stubGlobal("fetch", conLista());
    abrir();
    await filaDe("Encargado/a: Marta Ruiz");
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    // La ficha va en un portal, fuera del contenedor: se revisa la página entera.
    const faltas = async () => (await axe.run(document.body, reglas)).violations.map((v) => v.id);
    expect(await faltas()).toEqual([]);
    const marta = await abrirFicha("Encargado/a: Marta Ruiz");
    expect(await faltas()).toEqual([]);
    // «Marcar como comprobado» no abre nada (CON-06): se manda con su botón.
    for (const [boton, cierre] of [
      ["Corregir", "Cancelar"],
      ["Retirar", "Cancelar"],
    ] as const) {
      fireEvent.click(within(marta).getByRole("button", { name: boton }));
      expect(await faltas()).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: cierre }));
    }
    fireEvent.click(within(marta).getByRole("button", { name: "Cerrar" }));
    // «Señalar un error» solo se ofrece en un contacto sin señalar (G-54): Marta ya lo está.
    const plomeria = await abrirFicha("Emergencias: Plomería Rápida");
    fireEvent.click(within(plomeria).getByRole("button", { name: "Señalar un error" }));
    expect(await faltas()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    fireEvent.click(within(plomeria).getByRole("button", { name: "Cerrar" }));
    fireEvent.click(screen.getByRole("button", { name: "Agregar un contacto" }));
    fireEvent.change(screen.getByLabelText("¿De quién es el número?"), { target: { value: "profesional" } });
    expect(await faltas()).toEqual([]);
  });

  it("las rutas, el encabezado de reintento, los cuerpos y los topes son los del contrato", () => {
    type Operacion = {
      parameters?: { name: string; required?: boolean }[];
      requestBody?: { content: { "application/json"?: { schema: { $ref: string } } } };
    };
    const rutas = contrato.paths as Record<string, Record<string, Operacion> | undefined>;
    expect(rutas["/api/v1/consorcios/{consorcio_id}/contactos/gestion"]?.get).toBeTruthy();
    const conClave = [
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos"]?.post, "ContactoNuevo"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}"]?.put, "DatosContacto"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/senalar"]?.post, "Senalamiento"],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/retirar"]?.post, null],
      [rutas["/api/v1/consorcios/{consorcio_id}/contactos/{contacto_id}/verificar"]?.post, null],
    ] as const;
    for (const [op, esquema] of conClave) {
      expect(op?.parameters?.find((p) => p.name === "Idempotency-Key")?.required).toBe(true);
      expect(op?.requestBody?.content["application/json"]?.schema.$ref ?? null).toBe(
        esquema === null ? null : `#/components/schemas/${esquema}`,
      );
    }
    const esquemas = contrato.components.schemas as Record<
      string,
      { properties?: Record<string, { maxLength?: number; anyOf?: { maxLength?: number; pattern?: string }[] }> }
    >;
    expect(esquemas.DatosContacto?.properties?.nombre?.maxLength).toBe(120);
    expect(esquemas.Senalamiento?.properties?.motivo?.maxLength).toBe(300);
    const telefono = esquemas.DatosContacto?.properties?.telefono?.anyOf?.find((t) => t.pattern !== undefined);
    expect(telefono?.pattern).toBe("^[+0-9][0-9 ()-]{2,29}$");
    // El navegador compila `pattern` con la marca `v`, donde los paréntesis y el guion sin escapar
    // son inválidos y el atributo se ignora sin validar nada. El de la página compila y acepta lo
    // mismo que el contrato; control: el del contrato tal cual no compila con `v`.
    const pagina = new RegExp(`^(?:${PATRON_TELEFONO})$`, "v");
    const delContrato = new RegExp(telefono?.pattern ?? "");
    for (const valor of ["0800-555-1234", "+54 11 4000 0000", "(011) 4321-9876", "11x", "1"])
      expect(pagina.test(valor)).toBe(delContrato.test(valor));
    expect(() => new RegExp(`^(?:${(telefono?.pattern ?? "").slice(1, -1)})$`, "v")).toThrow();
    // El tope del campo es el largo máximo del formato: un primer carácter y el máximo de la repetición.
    expect(LARGO_TELEFONO).toBe(1 + Number(/\{\d+,(\d+)\}\$$/.exec(telefono?.pattern ?? "")?.[1]));
    expect(mensajeDeError(500, undefined, "agregar")).toMatch(/No pudimos completar|servicio/);
  });
});
