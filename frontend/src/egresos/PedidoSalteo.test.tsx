import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import { SALTEO_VENCIDO_SIN_USAR, textoConSalteo } from "../pagos/FormularioOrden";
import { ContextoVersion } from "../version";
import {
  ENVIAR_PEDIDO,
  FALTA_MOTIVO_PEDIDO,
  IMPORTE_PEDIDO,
  MOTIVO_PEDIDO,
  PEDIDO_ENVIADO,
  PEDIR_SALTEO,
  type PedidoSalteo,
  RECHAZO_DEL_PEDIDO,
  textoPedidoAlConsejo,
  textoPedidoPropio,
} from "./PedidoSalteo";
import { MOTIVO_SALTEO, SALTEAR } from "./SalteoDocumentacion";

// El pedido de salteo al Consejo (pregunta 3 del propietario, 18-11-2026; flujos 18-11, tanda 9).
// Datos ficticios.

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const P1 = "22222222-2222-4222-8222-222222222222";
const BASE = `/api/v1/consorcios/${C1}`;
const RUTA_PEDIDOS = `${BASE}/pedidos-salteo`;
const RUTA_PEDIR = `${BASE}/proveedores/${P1}/pedidos-salteo`;

const PROVEEDOR = {
  id: P1, cuit: "20000000001", razon_social: "Gas Ficticio SRL", rubro: "gas", contacto: null, telefono: null, whatsapp: null,
  correo: null, domicilio: null, atiende_emergencias: false, cuenta_ultimos_digitos: "1234", documento_id: null, estado: "activo",
  version: 1, huella: "a".repeat(64), alta_por: "66666666-6666-4666-8666-666666666666", creado_en: "2026-10-20T15:00:00Z",
  actualizado_en: "2026-10-20T15:00:00Z",
} as const;
const FALTANTE = { codigo: "matricula_gas", descripcion: "Matrícula de gasista", proveedor_id: P1, rubro_legal: "gas", tipo: "habilitacion" };
const CONFIGURACION = { dias_por_vencer: 30, modo_exigencia: "bloquear", admite_no_vence: true, autoriza_por_persona: false };
const LISTA = {
  elementos: [PROVEEDOR], documentacion_faltante: [P1], documentacion_vencida: [], documentacion_por_vencer: [],
  modo_exigencia: "bloquear", con_salteo: [], salteo_de_quien_prepara: [], vence_salteo: [], salteo_vencido_sin_usar: [],
  autoriza_por_persona: false,
};
const FICHA = {
  proveedor: PROVEEDOR, versiones: [], documentacion: [], puede_cargar_documentacion: true, rubros_legales: ["gas"],
  faltantes: [FALTANTE], requisitos: [], configuracion: CONFIGURACION, salteos: [], retiros_con_motivo: [],
};

function pedido(extra: Partial<PedidoSalteo> = {}): PedidoSalteo {
  return {
    numero: 7, proveedor_id: P1, razon_social: "Gas Ficticio SRL", motivo: "Pérdida de gas en el palier", importe_centavos: 1_500_000,
    moneda: "ARS", pedido_en: "2026-11-18T12:00:00Z", pedido_por_nombre: "LAB Admin", propio: true, estado: "pendiente",
    salteo_numero: null, salteo_vence_en: null, ...extra,
  };
}

/** El texto como lo deja la búsqueda de Testing Library: los espacios de la fecha y el importe, comunes. */
const normal = (texto: string) => texto.replace(/\s+/g, " ");

type Respuestas = Record<string, () => Response | Promise<Response>>;

function backend(respuestas: Respuestas) {
  const base: Respuestas = {
    "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS"] }]),
    "GET /api/v1/auth/totp": () => Response.json({ activo: true, llaves: 0, codigos_recuperacion_restantes: 9, verificado_en: null }),
    [`GET ${BASE}/proveedores`]: () => Response.json(LISTA),
    [`GET ${BASE}/contratos`]: () => Response.json({ elementos: [] }),
    [`GET ${BASE}/proveedores/${P1}`]: () => Response.json(FICHA),
    [`GET ${RUTA_PEDIDOS}`]: () => Response.json({ elementos: [] }),
  };
  return vi.fn(async (p: Request) => {
    const clave = `${p.method} ${new URL(p.url).pathname}`;
    const respuesta = respuestas[clave] ?? base[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta();
  });
}

const llamadas = (api: ReturnType<typeof backend>, metodo: string, camino: string) =>
  api.mock.calls.map((c) => (c as unknown as [Request])[0]).filter((p) => p.method === metodo && new URL(p.url).pathname === camino);

function abrir(ruta: string | { pathname: string; state: unknown }) {
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

async function elegirElBloqueado() {
  fireEvent.change(await screen.findByLabelText("Proveedor"), { target: { value: P1 } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("el pedido desde el formulario de la orden", () => {
  it("se pide con motivo e importe; enviado, se dice y queda el estado, sin otro botón", async () => {
    const api = backend({ [`POST ${RUTA_PEDIR}`]: () => Response.json(pedido(), { status: 201 }) });
    vi.stubGlobal("fetch", api);
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    fireEvent.click(await screen.findByRole("button", { name: PEDIR_SALTEO }));
    const ventana = await screen.findByRole("dialog");
    // Lo obligatorio se marca sin llegar al servidor.
    fireEvent.click(within(ventana).getByRole("button", { name: ENVIAR_PEDIDO }));
    expect(await within(ventana).findByText(FALTA_MOTIVO_PEDIDO)).toBeTruthy();
    expect(llamadas(api, "POST", RUTA_PEDIR)).toHaveLength(0);
    fireEvent.change(within(ventana).getByLabelText(MOTIVO_PEDIDO), { target: { value: "Pérdida de gas en el palier" } });
    fireEvent.change(within(ventana).getByLabelText(IMPORTE_PEDIDO), { target: { value: "15000" } });
    fireEvent.click(within(ventana).getByRole("button", { name: ENVIAR_PEDIDO }));
    expect(await screen.findByText(PEDIDO_ENVIADO)).toBeTruthy();
    const [enviado] = llamadas(api, "POST", RUTA_PEDIR);
    expect(await (enviado as Request).json()).toEqual({ motivo: "Pérdida de gas en el palier", importe_centavos: 1_500_000, moneda: "ARS" });
    expect(screen.getByText(normal(textoPedidoPropio(pedido())))).toBeTruthy();
    expect(screen.queryByRole("button", { name: PEDIR_SALTEO })).toBeNull();
  });

  it("con un pedido propio que espera, no se ofrece otro: se ve su estado", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${RUTA_PEDIDOS}`]: () => Response.json({ elementos: [pedido()] }) }));
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    expect(await screen.findByText(normal(textoPedidoPropio(pedido())))).toBeTruthy();
    expect(screen.queryByRole("button", { name: PEDIR_SALTEO })).toBeNull();
  });

  it("control: el pedido de otra persona, o uno propio ya atendido, no quita el botón", async () => {
    const otros = [pedido({ propio: false }), pedido({ numero: 6, estado: "atendido" })];
    vi.stubGlobal("fetch", backend({ [`GET ${RUTA_PEDIDOS}`]: () => Response.json({ elementos: otros }) }));
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    expect(await screen.findByRole("button", { name: PEDIR_SALTEO })).toBeTruthy();
  });

  it("si ya había uno que espera (`pedido_salteo_pendiente`), la ventana se cierra, se dice y se ve el estado", async () => {
    let leidos: object[] = [];
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${RUTA_PEDIDOS}`]: () => Response.json({ elementos: leidos }),
        [`POST ${RUTA_PEDIR}`]: () => {
          leidos = [pedido()];
          return Response.json({ codigo: "pedido_salteo_pendiente", mensaje: "x", detalle: null, id_correlacion: "c" }, { status: 409 });
        },
      }),
    );
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    fireEvent.click(await screen.findByRole("button", { name: PEDIR_SALTEO }));
    const ventana = await screen.findByRole("dialog");
    fireEvent.change(within(ventana).getByLabelText(MOTIVO_PEDIDO), { target: { value: "Urgencia" } });
    fireEvent.change(within(ventana).getByLabelText(IMPORTE_PEDIDO), { target: { value: "100" } });
    fireEvent.click(within(ventana).getByRole("button", { name: ENVIAR_PEDIDO }));
    expect(await screen.findByText(RECHAZO_DEL_PEDIDO.pedido_salteo_pendiente as string)).toBeTruthy();
    expect(await screen.findByText(normal(textoPedidoPropio(pedido())))).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("el pedido desde la ficha del proveedor", () => {
  it("quien prepara lo pide desde la ficha del bloqueado", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden") }));
    abrir({ pathname: "/egresos/proveedores", state: { consorcio: C1, abrir: P1 } });
    const ficha = await screen.findByRole("dialog", { name: "Gas Ficticio SRL" });
    expect(await within(ficha).findByRole("button", { name: PEDIR_SALTEO })).toBeTruthy();
  });

  it("control: sin documentación que falte no se ofrece pedir", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${BASE}/mis-acciones`]: alcanza("egresos.preparar_orden"),
        [`GET ${BASE}/proveedores/${P1}`]: () => Response.json({ ...FICHA, faltantes: [] }),
      }),
    );
    abrir({ pathname: "/egresos/proveedores", state: { consorcio: C1, abrir: P1 } });
    const ficha = await screen.findByRole("dialog", { name: "Gas Ficticio SRL" });
    await within(ficha).findByText("Cambiar los datos");
    expect(within(ficha).queryByRole("button", { name: PEDIR_SALTEO })).toBeNull();
  });

  it("al Consejo, el inicio lo lleva a la ficha de consulta: ve el pedido y el motivo ya escrito en el salteo", async () => {
    const consulta = { ...FICHA, versiones: undefined, puede_cargar_documentacion: undefined };
    vi.stubGlobal(
      "fetch",
      backend({
        [`GET ${BASE}/mis-acciones`]: alcanza("proveedores.ver", "egresos.saltear_documentacion"),
        [`GET ${BASE}/consulta-proveedores`]: () => Response.json(LISTA),
        [`GET ${BASE}/consulta-proveedores/${P1}`]: () => Response.json(consulta),
        [`GET ${RUTA_PEDIDOS}`]: () => Response.json({ elementos: [pedido({ propio: false })] }),
      }),
    );
    abrir({ pathname: "/proveedores", state: { consorcio: C1, abrir: P1 } });
    const ficha = await screen.findByRole("dialog", { name: "Gas Ficticio SRL" });
    expect(await within(ficha).findByText(normal(textoPedidoAlConsejo(pedido({ propio: false }))))).toBeTruthy();
    fireEvent.click(within(ficha).getByRole("button", { name: SALTEAR }));
    await waitFor(() => expect((screen.getByLabelText(MOTIVO_SALTEO) as HTMLTextAreaElement).value).toBe("Pérdida de gas en el palier"));
  });
});

describe("el salteo en el formulario de la orden (FL-SAL-01, FL-SAL-04, tanda 9)", () => {
  it("con un salteo que sirve, el aviso dice hasta cuándo", async () => {
    const VENCE = "2026-11-19T15:00:00Z";
    const lista = { ...LISTA, con_salteo: [P1], vence_salteo: [{ proveedor_id: P1, vence_en: VENCE }] };
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/proveedores`]: () => Response.json(lista) }));
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    expect(await screen.findByText(normal(textoConSalteo(VENCE)))).toBeTruthy();
    expect(textoConSalteo(VENCE)).toMatch(/Sirve hasta el .+\.$/);
  });

  it("el último salteo que venció sin usarse se dice junto al bloqueo", async () => {
    vi.stubGlobal("fetch", backend({ [`GET ${BASE}/proveedores`]: () => Response.json({ ...LISTA, salteo_vencido_sin_usar: [P1] }) }));
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    expect(await screen.findByText(SALTEO_VENCIDO_SIN_USAR)).toBeTruthy();
  });

  it("control: sin un salteo vencido, no se dice", async () => {
    vi.stubGlobal("fetch", backend({}));
    abrir("/pagos/nueva");
    await elegirElBloqueado();
    await screen.findByRole("button", { name: PEDIR_SALTEO });
    expect(screen.queryByText(SALTEO_VENCIDO_SIN_USAR)).toBeNull();
  });
});
