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
import { LISTO } from "./FichaReparacion";
import { pedidoListo } from "./NuevaGestion";
import {
  CERRADAS,
  ENLAZAR_GASTO,
  GASTO_PENDIENTE,
  NUEVA_GESTION,
  PRESUPUESTAR,
  RECHAZADO,
  REGISTRAR_GESTION,
  RETIRAR,
  RUTA_REPARACIONES,
  TERMINAR,
  TEXTO_CON_CUENTA,
  TITULO_REPARACIONES,
} from "./textos";

// «Seguimiento», «Nueva gestión» y el expediente de las reparaciones (RF19, app 0124). Datos
// SINTETICOS: ningún edificio, unidad ni persona real.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "22222222-2222-4222-8222-222222222222";
const MC = "33333333-3333-4333-8333-333333333333";
const OFICIAL = "44444444-4444-4444-8444-444444444444";
const A1 = "55555555-5555-4555-8555-555555555555";
const R1 = "77777777-7777-4777-8777-777777777777";
const R2 = "88888888-8888-4888-8888-888888888888";
const BASE = `/api/v1/consorcios/${C1}`;
const REPARACIONES = `${BASE}/reparaciones`;
const DESCRIPCION = "Pierde agua la canilla del baño. PRUEBA SINTETICA.";

type Reparacion = components["schemas"]["Reparacion"];
type Paso = Reparacion["historia"][number];

const paso = (estado: Paso["estado"], cambios: Partial<Paso> = {}): Paso => ({
  estado, motivo: null, adjunto_id: null, proveedor_nombre: null, proveedor_cuit: null, aprobado_por: null, facultad: null,
  decision_id: null, decision_descripcion: null, decision_fecha: null, gasto: null, registrado_por_nombre: "Ana SINTETICA",
  registrado_en: "2026-11-22T10:00:00Z", ...cambios,
});  // prettier-ignore
const PEDIDA: Reparacion = {
  id: R1, numero: 7, estado: "pedida", unidad_id: U1, designacion: "1-A", lugar: null, gestion: null,
  pedida_por_nombre: "Ana SINTETICA", pedida_en: "2026-11-22T10:00:00Z", propia: true, descripcion: DESCRIPCION,
  historia: [paso("pedida")], adjuntos: [], acciones: ["agregar_foto", "retirar"],
  responsables_posibles: [], decisiones_citables: [], gastos_enlazables: [],
};  // prettier-ignore
const CERRADA: Reparacion = { ...PEDIDA, id: R2, numero: 3, estado: "cerrada", acciones: [], historia: [paso("pedida"), paso("cerrada")] };
const resumen = ({ id, numero, estado, unidad_id, designacion, lugar, gestion, pedida_por_nombre, pedida_en, propia }: Reparacion) =>
  ({ id, numero, estado, unidad_id, designacion, lugar, gestion, pedida_por_nombre, pedida_en, propia });  // prettier-ignore
const LISTA = {
  elementos: [PEDIDA, CERRADA].map(resumen), gestiona: false, pide: true, unidades: [{ id: U1, designacion: "1-A" }],
  largo_descripcion: 2000, largo_lugar: 120, largo_motivo: 500, largo_proveedor: 120, largo_aprobado_por: 120, fotos_maximo: 4,
};  // prettier-ignore
const GASTO = { liquidacion_id: OFICIAL, periodo: "2026-10-01", clave: "gasto:1:2", concepto: "Plomería SINTETICA", total: "45000.00" };

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
const VECINA = alcanza("reparaciones.ver", "reparaciones.pedir");
const CONSEJO = alcanza("reparaciones.ver", "reparaciones.pedir", "reparaciones.gestionar");
const error = (estado: number, codigo: string) => Response.json({ codigo, mensaje: "x", detalle: null, id_correlacion: "c" }, { status: estado });
const subida = (adjunto: string) => () =>
  Response.json({ adjunto_id: adjunto, documento_id: A1, archivo: { id: A1, estado: "en_cuarentena", tipo: "application/pdf", tamano: 4, sha256: "x" } }, { status: 201 });
const pdf = () => new File(["%PDF"], "presupuesto.pdf", { type: "application/pdf" });

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={[RUTA_REPARACIONES]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

async function abrirFicha(nombre: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name: nombre }));
  return within(await screen.findByRole("dialog"));
}

beforeEach(() => {
  vi.spyOn(document, "cookie", "get").mockReturnValue("__Host-cem_csrf=abc123");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("pantalla /reparaciones", () => {
  it("quien pidió ve las suyas, las terminadas plegadas, y la ficha ofrece solo sus acciones", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        "GET /api/v1/me/consorcios": CONSORCIOS,
        [`GET ${BASE}/mis-acciones`]: VECINA,
        [`GET ${REPARACIONES}`]: () => Response.json(LISTA),
        [`GET ${REPARACIONES}/${R1}`]: () => Response.json(PEDIDA),
      }),
    );
    const { container } = abrir();
    const abiertas = await screen.findByRole("list", { name: TITULO_REPARACIONES });
    expect(within(abiertas).getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText(`${CERRADAS} (1)`)).toBeTruthy();
    expect(screen.getByRole("button", { name: NUEVA_GESTION })).toBeTruthy();
    expect((await axe.run(container)).violations).toEqual([]);
    const ficha = await abrirFicha(/Reparación N\.º 7/);
    expect(ficha.getByText(DESCRIPCION)).toBeTruthy();
    expect(ficha.getByRole("button", { name: RETIRAR })).toBeTruthy();
    // Ni la gestión ni el presupuesto: no los ofrece la API.
    expect(ficha.queryByRole("button", { name: REGISTRAR_GESTION })).toBeNull();
    expect(ficha.queryByRole("button", { name: PRESUPUESTAR })).toBeNull();
  });

  it("«Nueva gestión» marca lo que falta, rechaza una cuenta y, enviado el pedido, sube las fotos", async () => {
    let intento = 0;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: VECINA,
      [`GET ${REPARACIONES}`]: () => Response.json(LISTA),
      [`POST ${REPARACIONES}`]: () => (++intento === 1 ? error(422, "texto_con_cuenta") : Response.json(PEDIDA, { status: 201 })),
      [`POST ${REPARACIONES}/${R1}/adjuntos`]: subida(A1),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: NUEVA_GESTION }));
    const ventana = within(await screen.findByRole("dialog", { name: NUEVA_GESTION }));
    // Un espacio común sin decir cuál, y sin texto: se marcan los dos.
    fireEvent.change(ventana.getByLabelText("Dónde"), { target: { value: "" } });
    fireEvent.click(ventana.getByRole("button", { name: "Enviar el pedido" }));
    expect(await ventana.findByText("Falta contar qué hay que reparar.")).toBeTruthy();
    expect(ventana.getByText("Falta decir en qué espacio común.")).toBeTruthy();
    expect(pedidos(api, "POST", REPARACIONES)).toHaveLength(0);
    fireEvent.change(ventana.getByLabelText("Dónde"), { target: { value: U1 } });
    fireEvent.change(ventana.getByLabelText("Qué hay que reparar"), { target: { value: DESCRIPCION } });
    fireEvent.change(ventana.getByLabelText("Archivo de la foto", { selector: "input" }), {
      target: { files: [new File(["\x89PNG"], "canilla.png", { type: "image/png" })] },
    });
    expect(within(ventana.getByRole("list", { name: "Fotos elegidas" })).getByRole("button", { name: "Quitar canilla.png" })).toBeTruthy();
    fireEvent.click(ventana.getByRole("button", { name: "Enviar el pedido" }));
    expect(await ventana.findByText(TEXTO_CON_CUENTA, { selector: ".error-de-campo" })).toBeTruthy();
    // Corregido, sale el pedido (un 422 no dejó nada: otra clave) y después sube la foto.
    fireEvent.click(ventana.getByRole("button", { name: "Enviar el pedido" }));
    expect(await screen.findByText(pedidoListo(7))).toBeTruthy();
    const [, segundo] = pedidos(api, "POST", REPARACIONES);
    expect(await segundo?.json()).toEqual({ descripcion: DESCRIPCION, unidad_id: U1, lugar: null });
    const fotos = pedidos(api, "POST", `${REPARACIONES}/${R1}/adjuntos`);
    expect(fotos).toHaveLength(1);
    expect(new URL(fotos[0]?.url ?? "").searchParams.get("tipo")).toBe("foto");
  });

  it("quien gestiona asigna el responsable desde la ficha de una pedida", async () => {
    const pedidaAjena: Reparacion = { ...PEDIDA, propia: false, acciones: ["gestionar", "no_realizada"], responsables_posibles: [{ id: MC, nombre: "Ana Consejo" }] };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${REPARACIONES}`]: () => Response.json({ ...LISTA, gestiona: true, elementos: [resumen(pedidaAjena)] }),
      [`GET ${REPARACIONES}/${R1}`]: () => Response.json(pedidaAjena),
      [`POST ${REPARACIONES}/${R1}/gestion`]: () => Response.json({ ...pedidaAjena, estado: "en_gestion" }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const ficha = await abrirFicha(/Reparación N\.º 7/);
    expect(ficha.getByText("Ana SINTETICA")).toBeTruthy(); // quién la pidió, para quien gestiona
    fireEvent.click(ficha.getByRole("button", { name: REGISTRAR_GESTION }));
    const ventana = within(await screen.findByRole("dialog", { name: REGISTRAR_GESTION }));
    fireEvent.change(ventana.getByLabelText("Prioridad"), { target: { value: "urgente" } });
    fireEvent.click(ventana.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText(LISTO.gestionar)).toBeTruthy();
    const [envio] = pedidos(api, "POST", `${REPARACIONES}/${R1}/gestion`);
    expect(await envio?.json()).toEqual({ responsable_id: MC, prioridad: "urgente", fecha_comprometida: null });
  });

  it("el presupuesto se sube, espera al antivirus y recién disponible se registra", async () => {
    const enGestion: Reparacion = { ...PEDIDA, estado: "en_gestion", propia: false, acciones: ["presupuestar", "gestionar", "no_realizada"] };
    let subido = false;
    let leidoTrasSubir = false;
    let leidoAlRegistrar = false;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${REPARACIONES}`]: () => Response.json({ ...LISTA, gestiona: true, elementos: [resumen(enGestion)] }),
      [`GET ${REPARACIONES}/${R1}`]: () => {
        leidoTrasSubir = subido;
        return Response.json({
          ...enGestion,
          adjuntos: subido ? [{ id: A1, tipo: "presupuesto", estado: "disponible", tipo_archivo: "application/pdf", subido_en: "2026-11-22T11:00:00Z", propio: true, usado: false }] : [],
        });
      },
      [`POST ${REPARACIONES}/${R1}/adjuntos`]: () => {
        subido = true;
        return subida(A1)();
      },
      [`POST ${REPARACIONES}/${R1}/presupuesto`]: () => {
        leidoAlRegistrar = leidoTrasSubir;
        return Response.json({ ...enGestion, estado: "presupuestada" });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const ficha = await abrirFicha(/Reparación N\.º 7/);
    fireEvent.click(ficha.getByRole("button", { name: PRESUPUESTAR }));
    const ventana = within(await screen.findByRole("dialog", { name: PRESUPUESTAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Registrar" }));
    expect(await ventana.findByText("Falta el archivo del presupuesto.")).toBeTruthy();
    expect(ventana.getByText("Falta el nombre del proveedor.")).toBeTruthy();
    fireEvent.change(ventana.getByLabelText("Proveedor"), { target: { value: "Plomería SINTETICA" } });
    fireEvent.change(ventana.getByLabelText("Archivo del presupuesto", { selector: "input" }), { target: { files: [pdf()] } });
    fireEvent.click(ventana.getByRole("button", { name: "Registrar" }));
    expect(await screen.findByText(LISTO.presupuestar)).toBeTruthy();
    const [envio] = pedidos(api, "POST", `${REPARACIONES}/${R1}/presupuesto`);
    expect(await envio?.json()).toEqual({ adjunto_id: A1, proveedor_nombre: "Plomería SINTETICA", proveedor_cuit: null });
    // El acto salió después de leer, ya subido, que el archivo estaba disponible; no antes.
    expect(leidoAlRegistrar).toBe(true);
    expect(new URL(pedidos(api, "POST", `${REPARACIONES}/${R1}/adjuntos`)[0]?.url ?? "").searchParams.get("tipo")).toBe("presupuesto");
  });

  it("una factura que el antivirus rechaza no se cita: la ventana lo dice y no manda el acto", async () => {
    const aprobada: Reparacion = { ...PEDIDA, estado: "aprobada", propia: false, acciones: ["terminar", "gestionar", "no_realizada"] };
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${REPARACIONES}`]: () => Response.json({ ...LISTA, gestiona: true, elementos: [resumen(aprobada)] }),
      [`GET ${REPARACIONES}/${R1}`]: () =>
        Response.json({ ...aprobada, adjuntos: [{ id: A1, tipo: "factura", estado: "rechazado", tipo_archivo: "application/pdf", subido_en: "2026-11-22T11:00:00Z", propio: true, usado: false }] }),
      [`POST ${REPARACIONES}/${R1}/adjuntos`]: subida(A1),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    const ficha = await abrirFicha(/Reparación N\.º 7/);
    fireEvent.click(ficha.getByRole("button", { name: TERMINAR }));
    const ventana = within(await screen.findByRole("dialog", { name: TERMINAR }));
    fireEvent.click(ventana.getByRole("button", { name: "Registrar" }));
    expect(await ventana.findByText("Falta la factura: sin ella no se registra que se hizo.")).toBeTruthy();
    fireEvent.change(ventana.getByLabelText("Archivo de la factura", { selector: "input" }), { target: { files: [pdf()] } });
    fireEvent.click(ventana.getByRole("button", { name: "Registrar" }));
    expect(await ventana.findByText(RECHAZADO)).toBeTruthy();
    expect(pedidos(api, "POST", `${REPARACIONES}/${R1}/terminacion`)).toHaveLength(0);
  });

  it("terminada sin gasto: solo el aviso; con un gasto para enlazar, la ventana lo elige y cierra", async () => {
    const terminada: Reparacion = { ...PEDIDA, estado: "terminada_gasto_pendiente", propia: false, acciones: ["gestionar", "no_realizada"] };
    let conGasto = false;
    const api = backend({
      "GET /api/v1/me/consorcios": CONSORCIOS,
      [`GET ${BASE}/mis-acciones`]: CONSEJO,
      [`GET ${REPARACIONES}`]: () => Response.json({ ...LISTA, gestiona: true, elementos: [resumen(terminada)] }),
      [`GET ${REPARACIONES}/${R1}`]: () =>
        Response.json(conGasto ? { ...terminada, acciones: ["enlazar_gasto", "gestionar", "no_realizada"], gastos_enlazables: [GASTO] } : terminada),
      [`POST ${REPARACIONES}/${R1}/gasto`]: () => Response.json({ ...terminada, estado: "cerrada", acciones: [] }),
    });
    vi.stubGlobal("fetch", api);
    abrir();
    let ficha = await abrirFicha(/Reparación N\.º 7/);
    expect(ficha.getByText(GASTO_PENDIENTE)).toBeTruthy();
    expect(ficha.queryByRole("button", { name: ENLAZAR_GASTO })).toBeNull();
    fireEvent.click(ficha.getByRole("button", { name: "Cerrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    conGasto = true;
    ficha = await abrirFicha(/Reparación N\.º 7/);
    expect(ficha.queryByText(GASTO_PENDIENTE)).toBeNull();
    fireEvent.click(ficha.getByRole("button", { name: ENLAZAR_GASTO }));
    const ventana = within(await screen.findByRole("dialog", { name: ENLAZAR_GASTO }));
    expect(ventana.getByLabelText(/Plomería SINTETICA · \$ 45\.000,00/)).toBeTruthy();
    fireEvent.click(ventana.getByRole("button", { name: "Enlazar y cerrar" }));
    expect(await screen.findByText(LISTO.enlazar_gasto)).toBeTruthy();
    const [envio] = pedidos(api, "POST", `${REPARACIONES}/${R1}/gasto`);
    expect(await envio?.json()).toEqual({ liquidacion_id: OFICIAL, clave: "gasto:1:2" });
  });
});
