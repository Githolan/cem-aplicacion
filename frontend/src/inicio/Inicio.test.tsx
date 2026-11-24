import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { ContextoVersion } from "../version";
import { NADA_PENDIENTE } from "./Inicio";
import {
  DETALLE_SIN_LECTURA,
  PREPARAR_LA_ORDEN,
  textoImportaciones,
  textoPagos,
  textoReparaciones,
  textoSalteoHasta,
  textoSolicitudes,
  TITULO_PEDIDO_SALTEO,
} from "./pendientes";

// El inicio como única bandeja (decisión 9 del 18-11-2026; FL-PAD-01, FL-AVI-01 a FL-AVI-05,
// FL-INI-01, FL-SAL-01 parte 1): una fila por cada pendiente que le toca a quien mira y por edificio,
// sin selector; sin nada, «No hay nada pendiente.».

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "abababab-abab-4bab-8bab-abababababab";
const AZUL = { id: C1, nombre: "Edificio Azul", vinculos: [] };
const VERDE = { id: C2, nombre: "Edificio Verde", vinculos: [] };
const P1 = "99999999-9999-4999-8999-999999999991";
const P2 = "99999999-9999-4999-8999-999999999992";
const CONFIRMACION_ACTIVA = { activo: true, llaves: 0, codigos_recuperacion_restantes: 8, verificado_en: "2026-11-01T00:00:00Z" };
const SESION = {
  persona_id: "0199e3a2-1234-7abc-8def-0123456789ab",
  nombre: "Ana",
  correo: "ana@ejemplo.com",
  sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac",
  vence_en: "2026-11-13T00:00:00Z",
};
const SALUD = { estado: "ok", version: "0.1.0", sha_imagen: "x", sha_contratos: "", version_contrato: "0.1.0" };
const PREPARA = ["egresos.preparar_orden", "contactos.ver"];
const REVISA = ["egresos.revisar_orden", "egresos.conciliar_pago", "contactos.ver"];
const VECINO = ["contactos.ver"];
const CONFIRMA = ["padron.ver", "padron.confirmar_vinculo"];
const DESTINATARIO = ["solicitud_formal.responder", "contactos.ver"];
const CONSEJO_SOLICITUDES = ["fiscalizacion.ver_panel", "solicitud_formal.responder"];

const ruta = (c: string, resto: string) => `/api/v1/consorcios/${c}/${resto}`;

function orden(n: number, pendiente: boolean) {
  return {
    orden_id: `3333333${n}-3333-4333-8333-333333333333`,
    proveedor: `Proveedor ${n}`,
    concepto: "Abono",
    importe_centavos: 100_000,
    moneda: "ARS",
    paso: "aprobar",
    grupo: pendiente ? "para_aprobar" : "esperando",
    le_toca_a: pendiente ? "vos" : "consejo",
    pendiente,
    situacion: "en_revision",
    estado_visible: "esperando_aprobacion",
    principal: pendiente ? "aprobar" : null,
    creada_en: "2026-11-09T15:00:00Z",
    actualizada_en: "2026-11-09T15:00:00Z",
    posible_duplicada_de: null,
  };
}

const bandeja = (pendientes: number, { rechazadas = 0, esperando = 0 } = {}) =>
  ({
    elementos: [...Array.from({ length: pendientes }, (_, i) => orden(i, true)), ...Array.from({ length: esperando }, (_, i) => orden(i + 5, false))],
    avisos_rechazo: Array.from({ length: rechazadas }, () => ({})),
    por_cotejar: 0,
    sin_explicar: 0,
  }) as const;

const vinculo = (confirmable: boolean, confirmado = false) => ({ confirmado, confirmable_por_vos: confirmable });

function solicitud(acciones: string[], estado = "enviada", vence = "2026-11-30", porVencer = false) {
  return { acciones, estado, envio: estado === "borrador" ? null : { vence_el: vence }, por_vencer: porVencer };
}

function proveedor(id: string, razon_social: string, estado = "activo") {
  return { id, razon_social, estado };
}

const listaProveedores = (extra: object = {}) => ({
  elementos: [],
  con_salteo: [],
  documentacion_vencida: [],
  documentacion_faltante: [],
  documentacion_por_vencer: [],
  salteo_de_quien_prepara: [],
  modo_exigencia: "bloquear",
  vence_salteo: [],
  salteo_vencido_sin_usar: [],
  autoriza_por_persona: true,
  ...extra,
});

type Respuesta = () => Response;

/** Responde por «MÉTODO camino»; lo que no está previsto falla la prueba. */
function backend({
  consorcios = [AZUL],
  acciones = {},
  invitaciones = [],
  otras = {},
}: {
  consorcios?: object[];
  acciones?: Record<string, readonly string[]>;
  invitaciones?: object[];
  otras?: Record<string, Respuesta>;
}) {
  return vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    const otra = otras[camino];
    if (otra !== undefined) return otra();
    if (camino === "/api/v1/auth/sesion") return Response.json(SESION);
    // La confirmación ya activada: el aviso de la decisión 10 no aparece salvo que la prueba lo pida.
    if (camino === "/api/v1/auth/totp") return Response.json(CONFIRMACION_ACTIVA);
    if (camino === "/health") return Response.json(SALUD);
    if (camino === "/api/v1/me/consorcios") return Response.json(consorcios);
    if (camino === "/api/v1/me/invitaciones") return Response.json({ elementos: invitaciones });
    if (camino === "/api/v1/pagos-por-aclarar") return Response.json({ elementos: [] });
    const deAcciones = /^\/api\/v1\/consorcios\/([^/]+)\/mis-acciones$/.exec(camino);
    if (deAcciones !== null) return Response.json({ acciones: acciones[deAcciones[1] ?? ""] ?? [] });
    // Lo que no se configuró en la prueba responde vacío: sin pendientes.
    if (camino.endsWith("/pagos/bandeja")) return Response.json(bandeja(0));
    if (camino.endsWith("/padron")) return Response.json({ elementos: [] });
    if (camino.endsWith("/solicitudes-formales")) return Response.json({ elementos: [] });
    if (camino.endsWith("/proveedores")) return Response.json(listaProveedores());
    if (camino.endsWith("/mandatos")) return Response.json({ elementos: [] });
    if (camino.endsWith("/contactos/gestion")) return Response.json({ elementos: [] });
    if (camino.endsWith("/pedidos-salteo")) return Response.json({ elementos: [] });
    if (camino.endsWith("/liquidaciones-importadas")) return Response.json([]);
    if (camino.endsWith("/reclamos-liquidacion")) return Response.json({ elementos: [] });
    if (camino.endsWith("/reparaciones")) return Response.json({ elementos: [] });
    // Los pagos informados (app 0129 a 0134): nada por confirmar, por revisar ni por completar.
    if (camino.endsWith("/pagos-informados")) return Response.json({ elementos: [], largo_numero_operacion: 60, largo_motivo: 500, por_confirmar: 0, unidades: [] });
    if (camino.endsWith("/pagos-informados/cotejo")) return Response.json({ liquidacion: null });
    if (camino.endsWith("/pagos-informados/por-correo")) return Response.json({ elementos: [], direccion: null });
    throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
  });
}

function pedidosA(simulado: ReturnType<typeof backend>, camino: string): number {
  return simulado.mock.calls.filter((c) => new URL((c[0] as Request).url).pathname === camino).length;
}

function abrir() {
  const consultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <ContextoVersion.Provider value={SIN_VERSION_NUEVA}>
        <QueryClientProvider client={consultas}>
          <MemoryRouter initialEntries={["/"]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>
      </ContextoVersion.Provider>
    </StrictMode>,
  );
}

function conAncho(pc: boolean) {
  vi.stubGlobal("matchMedia", (consulta: string) => ({
    matches: pc && consulta === "(min-width: 64.0625em)",
    media: consulta,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

const paraHacer = async () => within(await screen.findByRole("region", { name: "Para hacer" }));
const fila = async (nombre: string | RegExp, detalle: string | RegExp) => (await paraHacer()).findByRole("link", { name: nombre, description: detalle });

beforeEach(() => conAncho(false));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Pagos: una fila por edificio, sin selector (FL-AVI-03)", () => {
  it("una fila por cada edificio con algo para hacer, con su nombre; el que no tiene nada no aparece", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        consorcios: [AZUL, VERDE],
        acciones: { [C1]: PREPARA, [C2]: REVISA },
        otras: {
          [ruta(C1, "pagos/bandeja")]: () => Response.json(bandeja(0, { esperando: 2 })),
          [ruta(C2, "pagos/bandeja")]: () => Response.json(bandeja(3)),
        },
      }),
    );
    abrir();
    const verde = await fila("Pagos · Edificio Verde", "3 cosas para hacer");
    expect(verde.getAttribute("href")).toBe("/pagos");
    expect(screen.queryByRole("link", { name: "Pagos · Edificio Azul" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Edificio" })).toBeNull();
    // Quien revisa también tiene una fila: no hay tarjeta aparte.
    expect(screen.queryByRole("region", { name: "Pagos" })).toBeNull();
  });

  it("la fila abre la bandeja del edificio de la fila", async () => {
    const simulado = backend({
      consorcios: [AZUL, VERDE],
      acciones: { [C1]: REVISA, [C2]: REVISA },
      otras: { [ruta(C2, "pagos/bandeja")]: () => Response.json(bandeja(1)) },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    const verde = await fila("Pagos · Edificio Verde", "1 cosa para hacer");
    const antesAzul = pedidosA(simulado, ruta(C1, "pagos/bandeja"));
    const antesVerde = pedidosA(simulado, ruta(C2, "pagos/bandeja"));
    fireEvent.click(verde);
    await screen.findByRole("heading", { level: 1, name: "Pagos" });
    await waitFor(() => expect(pedidosA(simulado, ruta(C2, "pagos/bandeja"))).toBeGreaterThan(antesVerde));
    expect(pedidosA(simulado, ruta(C1, "pagos/bandeja"))).toBe(antesAzul);
  });

  it("con un solo edificio la fila no lo nombra (C1)", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: REVISA }, otras: { [ruta(C1, "pagos/bandeja")]: () => Response.json(bandeja(2)) } }));
    abrir();
    expect(await fila("Pagos", "2 cosas para hacer")).toBeTruthy();
  });

  it("FL-AVI-04: la orden rechazada sin ver cuenta en la fila de Pagos", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ acciones: { [C1]: PREPARA }, otras: { [ruta(C1, "pagos/bandeja")]: () => Response.json(bandeja(0, { rechazadas: 1 })) } }),
    );
    abrir();
    expect(await fila("Pagos", "1 orden rechazada")).toBeTruthy();
  });

  it("textoPagos junta lo que hay para hacer y las rechazadas, en singular o plural", () => {
    expect(textoPagos(0, 0)).toBeNull();
    expect(textoPagos(1, 0)).toBe("1 cosa para hacer");
    expect(textoPagos(2, 2)).toBe("2 cosas para hacer · 2 órdenes rechazadas");
  });

  it("si un edificio no se pudo leer, su fila lo dice y un solo «Reintentar» lo vuelve a pedir", async () => {
    let caido = true;
    const caida = () =>
      caido ? Response.json({ codigo: "error", mensaje: "x", id_correlacion: "c" }, { status: 500 }) : Response.json(bandeja(1));
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: REVISA }, otras: { [ruta(C1, "pagos/bandeja")]: caida } }));
    abrir();
    expect(await fila("Pagos", DETALLE_SIN_LECTURA)).toBeTruthy();
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
    caido = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await fila("Pagos", "1 cosa para hacer")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Padrón: solo lo que esa cuenta puede confirmar (FL-PAD-01)", () => {
  it("no cuenta los vínculos sin confirmar que confirma otra persona", async () => {
    const padron = () => Response.json({ elementos: [vinculo(true), vinculo(false), vinculo(false), vinculo(false, true)] });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: CONFIRMA }, otras: { [ruta(C1, "padron")]: padron } }));
    abrir();
    expect(await fila("Padrón del edificio", "1 vínculo espera confirmación")).toBeTruthy();
  });

  it("control: si ninguno es suyo para confirmar, no hay fila y el aviso no queda para siempre", async () => {
    const simulado = backend({ acciones: { [C1]: CONFIRMA }, otras: { [ruta(C1, "padron")]: () => Response.json({ elementos: [vinculo(false)] }) } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Para hacer" })).toBeNull();
  });

  it("una fila por edificio, y no pide el padrón donde solo lo ve", async () => {
    const simulado = backend({
      consorcios: [AZUL, VERDE],
      acciones: { [C1]: ["padron.ver"], [C2]: CONFIRMA },
      otras: { [ruta(C2, "padron")]: () => Response.json({ elementos: [vinculo(true), vinculo(true)] }) },
    });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await fila("Padrón del edificio · Edificio Verde", "2 vínculos esperan confirmación")).toBeTruthy();
    expect(pedidosA(simulado, ruta(C1, "padron"))).toBe(0);
  });
});

describe("Solicitudes formales (FL-AVI-01, FL-AVI-02)", () => {
  it("el destinatario ve la que espera respuesta, con el vencimiento", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ acciones: { [C1]: DESTINATARIO }, otras: { [ruta(C1, "solicitudes-formales")]: () => Response.json({ elementos: [solicitud(["responder"])] }) } }),
    );
    abrir();
    const enlace = await fila("Solicitudes formales", "1 espera respuesta, vence el 30/11/2026");
    expect(enlace.getAttribute("href")).toBe("/solicitudes");
  });

  it("textoSolicitudes cuenta lo que ofrece cada solicitud; las vencidas, solo para quien ve el panel", () => {
    const lista = [
      solicitud(["firmar", "retirar"], "borrador"),
      solicitud(["registrar_envio"], "envio_pendiente"),
      solicitud(["registrar_respuesta"], "vencida"),
      solicitud([], "respondida"),
    ];
    expect(textoSolicitudes(lista, true)).toBe("1 espera la firma · 1 con el envío sin registrar · 1 con la respuesta por registrar · 1 vencida");
    expect(textoSolicitudes(lista, false)).toBe("1 espera la firma · 1 con el envío sin registrar · 1 con la respuesta por registrar");
    expect(textoSolicitudes([solicitud([], "respondida"), solicitud(["retirar"], "enviada")], true)).toBeNull();
  });

  it("el Consejo ve la que espera su firma", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        acciones: { [C1]: CONSEJO_SOLICITUDES },
        otras: { [ruta(C1, "solicitudes-formales")]: () => Response.json({ elementos: [solicitud(["firmar"], "borrador")] }) },
      }),
    );
    abrir();
    expect(await fila("Solicitudes formales", "1 espera la firma")).toBeTruthy();
  });

  it("FL-AVI-02: en el teléfono, el destinatario sin panel tiene la entrada en «Gestión», como en el menú", async () => {
    // Una respondida: ya no le toca nada, pero la pantalla tiene sus solicitudes.
    const lista = () => Response.json({ elementos: [solicitud([], "respondida")] });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: DESTINATARIO }, otras: { [ruta(C1, "solicitudes-formales")]: lista } }));
    abrir();
    const gestion = within(await screen.findByRole("region", { name: "Gestión" }));
    expect((await gestion.findByRole("link", { name: "Solicitudes formales" })).getAttribute("href")).toBe("/solicitudes");
  });

  it("control: sin ninguna solicitud dirigida, no hay entrada", async () => {
    const simulado = backend({ acciones: { [C1]: DESTINATARIO } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Solicitudes formales" })).toBeNull();
  });
});

describe("Proveedores de quien prepara (FL-SAL-01 parte 1, FL-AVI-05)", () => {
  it("el salteo que espera su orden (el pedido atendido) tiene su fila, que abre «Preparar la orden» y dice hasta cuándo", async () => {
    const VENCE = "2026-11-19T15:00:00Z";
    const lista = () =>
      Response.json(
        listaProveedores({
          elementos: [proveedor(P1, "Ascensores Sur"), proveedor(P2, "Limpieza Norte")],
          con_salteo: [P1],
          documentacion_faltante: [P1],
          vence_salteo: [{ proveedor_id: P1, vence_en: VENCE }],
        }),
      );
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { [ruta(C1, "proveedores")]: lista } }));
    abrir();
    const salteo = await fila(PREPARAR_LA_ORDEN, `Ascensores Sur: ${textoSalteoHasta(VENCE)}`);
    expect(salteo.getAttribute("href")).toBe("/pagos/nueva");
    expect(await fila("Proveedores", "1 proveedor con documentación vencida o faltante")).toBeTruthy();
  });

  it("al Consejo que saltea, cada pedido pendiente de otra persona le aparece y abre la ficha del proveedor", async () => {
    const pedido = (numero: number, estado: string, propio: boolean) => ({
      numero, proveedor_id: P1, razon_social: "Ascensores Sur", motivo: "Pérdida de gas", importe_centavos: 1_500_000, moneda: "ARS",
      pedido_en: "2026-11-18T12:00:00Z", pedido_por_nombre: "LAB Admin", propio, estado, salteo_numero: null, salteo_vence_en: null,
    });
    const pedidos = () => Response.json({ elementos: [pedido(3, "pendiente", false), pedido(2, "pendiente", true), pedido(1, "atendido", false)] });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["egresos.saltear_documentacion", "proveedores.ver"] }, otras: { [ruta(C1, "pedidos-salteo")]: pedidos } }));
    abrir();
    const enlace = await fila(TITULO_PEDIDO_SALTEO, /^Ascensores Sur: .+15\.000,00 · LAB Admin$/);
    expect(enlace.getAttribute("href")).toBe("/proveedores");
    // Uno solo: el propio y el atendido no le tocan.
    expect((await paraHacer()).getAllByRole("link", { name: TITULO_PEDIDO_SALTEO })).toHaveLength(1);
  });

  it("control: sin pedidos que esperen, el Consejo no tiene filas de salteo", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["egresos.saltear_documentacion", "proveedores.ver"] } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
  });

  it("control: sin salteo ni documentación vencida o faltante no hay filas de proveedores", async () => {
    const lista = () => Response.json(listaProveedores({ elementos: [proveedor(P1, "Ascensores Sur")], documentacion_por_vencer: [P1] }));
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { [ruta(C1, "proveedores")]: lista } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
  });

  it("un «sin permiso» del servidor no es una falla: no hay fila ni «Reintentar»", async () => {
    const prohibido = () => Response.json({ codigo: "sin_permiso", mensaje: "x", id_correlacion: "c" }, { status: 403 });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { [ruta(C1, "proveedores")]: prohibido } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("control: un error del servidor sí es una falla, con su fila y «Reintentar»", async () => {
    const caida = () => Response.json({ codigo: "error_interno", mensaje: "x", id_correlacion: "c" }, { status: 500 });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { [ruta(C1, "proveedores")]: caida } }));
    abrir();
    expect(await fila("Proveedores", DETALLE_SIN_LECTURA)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("quien no prepara órdenes no pide la lista de proveedores", async () => {
    const simulado = backend({ acciones: { [C1]: REVISA } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(NADA_PENDIENTE);
    expect(pedidosA(simulado, ruta(C1, "proveedores"))).toBe(0);
  });
});

describe("otros pendientes (FL-AVI-05)", () => {
  it("el mandato en revisión, para quien lo puede resolver", async () => {
    const mandatos = () => Response.json({ elementos: [{ estado: "pendiente_revision" }, { estado: "vigente" }] });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["mandatos.ver", "mandatos.resolver_revision"] }, otras: { [ruta(C1, "mandatos")]: mandatos } }));
    abrir();
    expect((await fila("Mandatos y decisiones", "1 mandato en revisión")).getAttribute("href")).toBe("/mandatos");
  });

  it("FL-MAN-04: la invitación de la administración por aprobar, para quien la aprueba y no la emitió", async () => {
    const mandatos = () =>
      Response.json({
        elementos: [
          { estado: "vigente", invitacion: "por_aprobar", invitacion_emitida_por_mi: false },
          { estado: "vigente", invitacion: "por_aprobar", invitacion_emitida_por_mi: true },
          { estado: "vigente", invitacion: "pendiente", invitacion_emitida_por_mi: false },
        ],
      });
    const aprueba = ["mandatos.ver", "mandatos.ver_asociaciones", "mandatos.aprobar_asociacion"];
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: aprueba }, otras: { [ruta(C1, "mandatos")]: mandatos } }));
    abrir();
    expect((await fila("Mandatos y decisiones", "1 invitación espera aprobación")).getAttribute("href")).toBe("/mandatos");
  });

  it("FL-MAN-04, control: sin aprobar invitaciones, la por aprobar no cuenta", async () => {
    const mandatos = () => Response.json({ elementos: [{ estado: "pendiente_revision", invitacion: "por_aprobar", invitacion_emitida_por_mi: false }] });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["mandatos.ver", "mandatos.resolver_revision"] }, otras: { [ruta(C1, "mandatos")]: mandatos } }));
    abrir();
    expect(await fila("Mandatos y decisiones", "1 mandato en revisión")).toBeTruthy();
  });

  it("control: quien solo ve los mandatos no pide la lista para el inicio", async () => {
    const simulado = backend({ acciones: { [C1]: ["mandatos.ver"] } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    await screen.findByText(NADA_PENDIENTE);
    expect(pedidosA(simulado, ruta(C1, "mandatos"))).toBe(0);
  });

  it("el contacto con un dato señalado, para quien gestiona los contactos", async () => {
    const contactos = () => Response.json({ elementos: [{ observado: true }, { observado: true }, { observado: false }] });
    vi.stubGlobal(
      "fetch",
      backend({ acciones: { [C1]: ["contactos.ver", "contactos.gestionar"] }, otras: { [ruta(C1, "contactos/gestion")]: contactos } }),
    );
    abrir();
    expect(await fila("Contactos de emergencia", "2 contactos con un dato señalado como incorrecto")).toBeTruthy();
  });
});

// --- Bandeja del Consejo (RF40, P0 S6): lo que necesita al Consejo, cada fila a su pantalla ---------

const CONSEJO = [
  "fiscalizacion.ver_panel",
  "solicitud_formal.responder",
  "liquidacion.importar",
  "reclamos_liquidacion.atender",
  "reparaciones.ver",
  "reparaciones.gestionar",
  "contactos.ver",
];
const importacion = (estado: string) => ({ estado });
const reparacion = (estado: string) => ({ estado });

describe("Bandeja del Consejo (RF40, P0 S6)", () => {
  it("cada pendiente del Consejo tiene su fila, que abre su pantalla en el edificio", async () => {
    vi.stubGlobal(
      "fetch",
      backend({
        acciones: { [C1]: CONSEJO },
        otras: {
          [ruta(C1, "liquidaciones-importadas")]: () =>
            Response.json([importacion("para_confirmar"), importacion("confirmada"), importacion("leyendo"), importacion("no_reconocida")]),
          [ruta(C1, "solicitudes-formales")]: () =>
            Response.json({ elementos: [solicitud([], "enviada", "2026-11-25", true), solicitud([], "enviada", "2026-12-20"), solicitud(["registrar_respuesta"], "vencida", "2026-11-01")] }),
          [ruta(C1, "reclamos-liquidacion")]: () => Response.json({ elementos: [{ estado: "presentado" }, { estado: "respondido" }] }),
          [ruta(C1, "reparaciones")]: () =>
            Response.json({ elementos: ["pedida", "en_gestion", "aprobada", "terminada_gasto_pendiente", "cerrada"].map(reparacion) }),
        },
      }),
    );
    abrir();
    const importar = await fila("Liquidación del administrador", "1 liquidación espera confirmación");
    expect(importar.getAttribute("href")).toBe("/importar-liquidacion");
    await fila("Solicitudes formales", "1 con la respuesta por registrar · 1 por vencer, vence el 25/11/2026 · 1 vencida");
    await fila("Reclamos sobre la liquidación", "1 reclamo espera respuesta");
    await fila("Reparaciones", "1 pedido de reparación espera responsable · 2 reparaciones en curso");
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("con todo al día, el Consejo lee «No hay nada pendiente.»", async () => {
    const simulado = backend({ acciones: { [C1]: CONSEJO } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Para hacer" })).toBeNull();
    // Control: preguntó por cada cosa.
    for (const parte of ["liquidaciones-importadas", "solicitudes-formales", "reclamos-liquidacion", "reparaciones"])
      expect(pedidosA(simulado, ruta(C1, parte))).toBeGreaterThan(0);
  });

  it("los pagos informados: lo que espera al Consejo, el cotejo y lo que falta completar del correo", async () => {
    const vacia = { largo_numero_operacion: 60, largo_motivo: 500, unidades: [] };
    vi.stubGlobal(
      "fetch",
      backend({
        acciones: { [C1]: ["pagos_informados.ver", "pagos_informados.confirmar", "fiscalizacion.ver_panel", "pagos_informados.informar"] },
        otras: {
          [ruta(C1, "pagos-informados")]: () => Response.json({ ...vacia, elementos: [], por_confirmar: 2 }),
          [ruta(C1, "pagos-informados/cotejo")]: () =>
            Response.json({ liquidacion: { importacion_id: C1, periodo: "2026-11-01", pagos_desde: "2026-10-01", pagos_hasta: "2026-10-31", unidades: [], para_revisar: 1 } }),
          [ruta(C1, "pagos-informados/por-correo")]: () => Response.json({ elementos: [{ id: "x" }], direccion: null }),
        },
      }),
    );
    abrir();
    const confirmar = await fila("Pagos informados", "2 pagos informados esperan confirmación");
    expect(confirmar.getAttribute("href")).toBe("/pagos-informados");
    const cotejo = await fila("Cotejo con la liquidación", "1 unidad para revisar");
    expect(cotejo.getAttribute("href")).toBe("/pagos-informados/cotejo");
    await fila("Pagos informados", "1 comprobante llegó por correo y falta completarlo");
  });

  it("quien no confirma pagos no pide el cotejo para el inicio", async () => {
    const simulado = backend({ acciones: { [C1]: ["pagos_informados.ver", "pagos_informados.informar"] } });
    vi.stubGlobal("fetch", simulado);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(pedidosA(simulado, ruta(C1, "pagos-informados/cotejo"))).toBe(0);
    expect(pedidosA(simulado, ruta(C1, "pagos-informados/por-correo"))).toBeGreaterThan(0);
  });

  it("quien no importa no pide la lista de liquidaciones; quien importa, sí", async () => {
    const sinImportar = backend({ acciones: { [C1]: VECINO } });
    vi.stubGlobal("fetch", sinImportar);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(pedidosA(sinImportar, ruta(C1, "liquidaciones-importadas"))).toBe(0);
    cleanup();
    const importa = backend({ acciones: { [C1]: ["liquidacion.importar", ...VECINO] } });
    vi.stubGlobal("fetch", importa);
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(pedidosA(importa, ruta(C1, "liquidaciones-importadas"))).toBeGreaterThan(0);
  });

  it("si la lista de liquidaciones no se pudo leer, la fila lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ acciones: { [C1]: ["liquidacion.importar", ...VECINO] }, otras: { [ruta(C1, "liquidaciones-importadas")]: () => new Response(null, { status: 500 }) } }),
    );
    abrir();
    await fila("Liquidación del administrador", DETALLE_SIN_LECTURA);
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("las por vencer solo para quien ve el panel; el destinatario ve su vencimiento", () => {
    const lista = [solicitud(["responder"], "enviada", "2026-11-25", true), solicitud([], "enviada", "2026-11-24", true)];
    expect(textoSolicitudes(lista, true)).toBe("1 espera respuesta, vence el 25/11/2026 · 2 por vencer, la primera vence el 24/11/2026");
    expect(textoSolicitudes(lista, false)).toBe("1 espera respuesta, vence el 25/11/2026");
    expect(textoSolicitudes([solicitud([], "enviada", "2026-12-30")], true)).toBeNull();
  });

  it("textoImportaciones y textoReparaciones, en singular, plural o nada", () => {
    expect(textoImportaciones(0)).toBeNull();
    expect(textoImportaciones(2)).toBe("2 liquidaciones esperan confirmación");
    expect(textoReparaciones(0, 0)).toBeNull();
    expect(textoReparaciones(0, 1)).toBe("1 reparación en curso");
    expect(textoReparaciones(2, 0)).toBe("2 pedidos de reparación esperan responsable");
  });
});

describe("sin nada pendiente (FL-INI-01)", () => {
  it("dice «No hay nada pendiente.», sin «Para hacer»", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: VECINO } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Para hacer" })).toBeNull();
  });

  it("también en la PC, para quien administra al día", async () => {
    conAncho(true);
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: [...PREPARA, ...CONFIRMA] } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
  });

  it("control: con algo pendiente no lo dice", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: REVISA }, otras: { [ruta(C1, "pagos/bandeja")]: () => Response.json(bandeja(1)) } }));
    abrir();
    await fila("Pagos", "1 cosa para hacer");
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("control: con una invitación pendiente no lo dice", async () => {
    const invitacion = {
      id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
      consorcio: "Edificio Azul",
      unidad: "1° A",
      tipo: "inquilino",
      nombre: "Ana",
      invitada_por: null,
      vence_en: "2026-11-20T00:00:00Z",
    };
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: VECINO }, invitaciones: [invitacion] }));
    abrir();
    await screen.findByRole("button", { name: "Aceptar la invitación" });
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("control: sin edificios dice cómo sumarse, no que no hay nada pendiente", async () => {
    vi.stubGlobal("fetch", backend({ consorcios: [] }));
    abrir();
    await screen.findByText(/todavía no participa de ningún edificio/);
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("no tiene faltas de accesibilidad detectables por axe", async () => {
    vi.stubGlobal(
      "fetch",
      backend({ consorcios: [AZUL, VERDE], acciones: { [C1]: REVISA, [C2]: REVISA }, otras: { [ruta(C2, "pagos/bandeja")]: () => Response.json(bandeja(1)) } }),
    );
    const { container } = abrir();
    await fila("Pagos · Edificio Verde", "1 cosa para hacer");
    const { violations } = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(violations.map((v) => v.id)).toEqual([]);
  });
});

describe("Flujos 18-11, tanda 7: el inicio del habitante autorizado", () => {
  it("FL-VER-04: con solo la consulta de proveedores no hay sección «Gestión»: «Proveedores» está en el menú", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["proveedores.ver", "documentos.ver"] } }));
    abrir();
    const menu = await screen.findByRole("navigation", { name: "Secciones" });
    const proveedores = await within(menu).findByRole("link", { name: "Proveedores" });
    expect(proveedores.classList.contains("navegacion__item--secundario")).toBe(false);
    expect(screen.queryByRole("region", { name: "Gestión" })).toBeNull();
    expect(within(screen.getByRole("main")).queryByRole("link", { name: "Proveedores" })).toBeNull();
  });

  it("FL-VER-04, control: con otra pantalla de gestión, «Proveedores» sigue en la sección «Gestión» del inicio", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: ["proveedores.ver", "padron.ver"] } }));
    abrir();
    const gestion = within(await screen.findByRole("region", { name: "Gestión" }));
    expect(await gestion.findByRole("link", { name: "Proveedores" })).toBeTruthy();
  });
});

describe("integración de las tandas 2 y 3: la confirmación en el inicio", () => {
  const PREPARA = ["egresos.revisar_orden"];

  it("decisión 10: sin la confirmación activada, el aviso está y no se dice «No hay nada pendiente.»", async () => {
    const sinConfirmacion = () => Response.json({ ...CONFIRMACION_ACTIVA, activo: false });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { "/api/v1/auth/totp": sinConfirmacion } }));
    abrir();
    expect(await screen.findByRole("region", { name: "Confirmación con PIN, huella o código" })).toBeTruthy();
    expect(screen.queryByText(NADA_PENDIENTE)).toBeNull();
  });

  it("control: con la confirmación activada, sin el aviso y con «No hay nada pendiente.»", async () => {
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA } }));
    abrir();
    expect(await screen.findByText(NADA_PENDIENTE)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Confirmación con PIN, huella o código" })).toBeNull();
  });

  it("AC-08: si no se puede leer la confirmación y falla otro pendiente, hay un solo «Reintentar»", async () => {
    const falla = () => new Response(null, { status: 500 });
    vi.stubGlobal("fetch", backend({ acciones: { [C1]: PREPARA }, otras: { "/api/v1/auth/totp": falla, [ruta(C1, "pagos/bandeja")]: falla } }));
    abrir();
    expect(await screen.findByText(/No se pudo saber si la confirmación/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Reintentar" })).toHaveLength(1);
  });
});
