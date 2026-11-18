import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import type { components } from "../api/tipos";
import { hoyDelDispositivo } from "../archivos";
import { ContextoVersion } from "../version";
import {
  DOS_FIRMAS,
  EMITE_AL_FIRMAR,
  FALTA_ADMINISTRADOR,
  FIRMAR,
  FIRMAR_Y_EMITIR,
  IR_A_MANDATOS,
  PREPARAR,
  REGISTRAR_ENVIO,
  RESPONDER,
  REGISTRAR_RESPUESTA,
  RETIRAR,
  SIN_ADMINISTRADOR_AL_PREPARAR,
  SIN_SOLICITUDES,
  TEXTO_ESTADO,
  textoSituacion,
  TITULO_SOLICITUDES,
} from "./Solicitudes";

// Solicitudes formales (RF49, app 0108). Datos ficticios: ningún edificio, persona ni documento real.

type Solicitud = components["schemas"]["SolicitudFormal"];
type Lista = components["schemas"]["ListaSolicitudesFormales"];
type Respuestas = Record<string, (pedido: Request) => Response | Promise<Response>>;

const SIN_VERSION_NUEVA = { hayVersionNueva: false, actualizar: () => undefined };
const C1 = "11111111-1111-4111-8111-111111111111";
const S1 = "22222222-2222-4222-8222-222222222222";
const S2 = "33333333-3333-4333-8333-333333333333";
const S3 = "44444444-4444-4444-8444-444444444444";
const BASE = `/api/v1/consorcios/${C1}/solicitudes-formales`;
const LISTA = `GET ${BASE}`;

const BORRADOR: Solicitud = {
  id: S1,
  numero: 3,
  objeto: "periodo",
  referencia: "Octubre de 2026 (ficticio)",
  documentacion: "Extractos del período.",
  plazo_dias: 15,
  estado: "borrador",
  preparada_por_nombre: "Marta Ficticia",
  preparada_en: "2026-11-10T12:00:00Z",
  firmas: [],
  destinatario_nombre: null,
  destinatario_con_cuenta: null,
  emitida_en: null,
  texto_emitido: null,
  huella_texto: null,
  envio: null,
  respuesta: null,
  retiro: null,
  acciones: ["firmar", "retirar"],
  falta_administrador_vigente: false,
};
const PENDIENTE: Solicitud = {
  ...BORRADOR,
  id: S2,
  numero: 2,
  estado: "envio_pendiente",
  firmas: [
    { nombre: "Marta Ficticia", firmada_en: "2026-11-09T12:00:00Z", propia: true },
    { nombre: "Eva Ficticia", firmada_en: "2026-11-09T13:00:00Z", propia: false },
  ],
  destinatario_nombre: "Estudio Ficticio",
  destinatario_con_cuenta: false,
  emitida_en: "2026-11-09T13:00:00Z",
  texto_emitido: "Solicitud formal de documentación N.º 2\nConsorcio: Edificio Ficticio",
  huella_texto: "a".repeat(64),
  acciones: ["registrar_envio"],
};
const ENVIADA: Solicitud = {
  ...PENDIENTE,
  id: S3,
  numero: 1,
  estado: "enviada",
  destinatario_con_cuenta: true,
  envio: {
    medio: "aplicacion",
    detalle: null,
    fecha_envio: "2026-11-01",
    vence_el: "2026-11-16",
    registrado_por_nombre: "Eva Ficticia",
    registrado_en: "2026-11-01T13:00:00Z",
    documento_id: null,
    version_documento_id: null,
  },
  acciones: ["responder"],
};
const DOC_RESPUESTA = "99999999-9999-4999-8999-999999999999";
const VERSION_RESPUESTA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RESPONDIDA: Solicitud = {
  ...ENVIADA,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  numero: 5,
  estado: "respondida",
  respuesta: {
    texto: "Se adjuntan los extractos.", medio: "aplicacion", respondida_el: "2026-11-14", fuera_de_plazo: false,
    registrada_por_nombre: "Estudio Ficticio", registrada_en: "2026-11-14T10:00:00Z", documento_id: DOC_RESPUESTA,
    version_documento_id: VERSION_RESPUESTA,
  },
  acciones: [],
};
const RETIRADA: Solicitud = {
  ...BORRADOR,
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  numero: 6,
  estado: "retirada",
  retiro: { motivo: "Se pidió por otra vía.", retirada_por_nombre: "Marta Ficticia", retirada_en: "2026-11-12T10:00:00Z" },
  acciones: [],
};
const LLENA: Lista = {
  elementos: [BORRADOR, PENDIENTE, ENVIADA],
  puede_preparar: true,
  plazo_por_defecto_dias: 15,
  plazo_minimo_dias: 5,
  plazo_maximo_dias: 60,
};

const INICIO: Respuestas = {
  "GET /api/v1/auth/sesion": () => Response.json({}),
  "GET /health": () =>
    Response.json({ estado: "ok", version: "0.1.0", sha_imagen: "a".repeat(40), sha_contratos: "", version_contrato: "0.1.0" }),
  "GET /api/v1/pagos-por-aclarar": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/invitaciones": () => Response.json({ elementos: [] }),
  "GET /api/v1/me/consorcios": () => Response.json([{ id: C1, nombre: "Edificio Ficticio", vinculos: [], monedas: ["ARS"] }]),
};

function backend(respuestas: Respuestas) {
  return vi.fn(async (pedido: Request) => {
    const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
    const respuesta = respuestas[clave] ?? INICIO[clave] ?? alcanzaTodo(clave);
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
    return respuesta(pedido);
  });
}

function error(codigo: string, status: number, mensaje = "x") {
  return Response.json({ codigo, mensaje, detalle: null, id_correlacion: "c" }, { status });
}

function abrir(ruta = "/solicitudes") {
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

/** La fila de una solicitud en la lista. */
const fila = (numero: number) => screen.findByRole("button", { name: new RegExp(`^N\\.º ${numero} ·`) });

/** Toca la fila y devuelve su ficha (SOL-03). */
async function abrirFicha(numero: number) {
  fireEvent.click(await fila(numero));
  return within(await screen.findByRole("dialog", { name: new RegExp(`^N\\.º ${numero} ·`) }));
}

function pedidos(simulado: ReturnType<typeof vi.fn>, metodo: string, camino: string): Request[] {
  return simulado.mock.calls
    .map((c) => (c as unknown as [Request])[0])
    .filter((p) => p.method === metodo && new URL(p.url).pathname === camino);
}

async function sinViolaciones(contenedor: HTMLElement) {
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations.map((v) => v.id)).toEqual([]);
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

describe("textos", () => {
  it("dice en qué está cada una, sin calificar (RN33)", () => {
    expect(textoSituacion(BORRADOR)).toBe("Borrador: 0 de 2 firmas.");
    expect(textoSituacion(PENDIENTE)).toMatch(/No vence mientras no se registre el envío/);
    expect(textoSituacion(ENVIADA)).toBe("Enviada, esperando respuesta. Vence el 16/11/2026.");
    const fuera = { ...ENVIADA, estado: "respondida" as const, respuesta: {
      texto: "x", medio: "aplicacion" as const, respondida_el: "2026-11-20", fuera_de_plazo: true,
      registrada_por_nombre: null, registrada_en: "2026-11-20T10:00:00Z", documento_id: null, version_documento_id: null } };
    expect(textoSituacion(fuera)).toBe("Respondida el 20/11/2026, fuera de plazo.");
  });
});

describe("solicitudes formales", () => {
  it("SOL-03 y SOL-04: las agrupa por estado en filas; la ficha ofrece solo lo que la API dice", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    const { container } = abrir();
    expect(await screen.findByRole("heading", { level: 1, name: TITULO_SOLICITUDES })).toBeTruthy();
    const grupos = await screen.findAllByRole("region");
    expect(grupos.map((g) => g.getAttribute("aria-label"))).toEqual([TEXTO_ESTADO.borrador, TEXTO_ESTADO.envio_pendiente, TEXTO_ESTADO.enviada]);
    // La fila dice solo lo que su sección no dice.
    expect((await fila(3)).textContent).toBe("N.º 3 · Período: Octubre de 2026 (ficticio) 0 de 2 firmas");
    expect((await fila(1)).textContent).toMatch(/Vence el 16\/11\/2026$/);
    // Ningún botón de acto en la lista: se abre la ficha.
    expect(screen.queryByRole("button", { name: FIRMAR })).toBeNull();
    // SOL-01: lo legal no está en la página.
    expect(screen.queryByText(/art\. 2064/)).toBeNull();
    expect(screen.getByRole("button", { name: PREPARAR })).toBeTruthy();
    await sinViolaciones(container);

    const borrador = await abrirFicha(3);
    expect(borrador.getByRole("button", { name: FIRMAR })).toBeTruthy();
    expect(borrador.getByRole("button", { name: RETIRAR })).toBeTruthy();
    expect(borrador.queryByRole("button", { name: RESPONDER })).toBeNull();
    await sinViolaciones(document.body);
    fireEvent.click(borrador.getByRole("button", { name: "Cerrar" }));
    const pendiente = await abrirFicha(2);
    expect(pendiente.getByRole("button", { name: REGISTRAR_ENVIO })).toBeTruthy();
    expect(pendiente.queryByRole("button", { name: FIRMAR })).toBeNull();
    fireEvent.click(pendiente.getByRole("button", { name: "Cerrar" }));
    const enviada = await abrirFicha(1);
    expect(enviada.getByRole("button", { name: RESPONDER })).toBeTruthy();
  });

  it("sin solicitudes ni facultad de preparar, solo lo dice", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json({ ...LLENA, elementos: [], puede_preparar: false }),
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("solicitud_formal.responder"),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText(SIN_SOLICITUDES)).toBeTruthy();
    expect(screen.queryByRole("button", { name: PREPARAR })).toBeNull();
    expect(screen.queryByText(DOS_FIRMAS)).toBeNull();
  });

  it("SOL-06: las respondidas y las retiradas quedan plegadas, fuera de la lista de trabajo", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [BORRADOR, RESPONDIDA, RETIRADA] }) }));
    abrir();
    await fila(3);
    expect(screen.queryByRole("region", { name: TEXTO_ESTADO.respondida })).toBeNull();
    const plegadas = screen.getByText("Ver respondidas y retiradas (2)").closest("details")!;
    expect(plegadas.open).toBe(false);
    expect(within(plegadas).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "N.º 5 · Período: Octubre de 2026 (ficticio) Respondida el 14/11/2026.",
      "N.º 6 · Período: Octubre de 2026 (ficticio) Retirada.",
    ]);
  });

  it("SOL-02, SOL-01 y C8P-24: prepara en su ventana, marca lo que falta y abre la ficha del borrador nuevo", async () => {
    let cuerpo: unknown = null;
    const nueva: Solicitud = { ...BORRADOR, id: "55555555-5555-4555-8555-555555555555", numero: 4 };
    const api = backend({
      [LISTA]: () => Response.json({ ...LLENA, plazo_por_defecto_dias: 21 }),
      [`POST ${BASE}`]: async (p) => {
        cuerpo = await p.json();
        expect(p.headers.get("Idempotency-Key")).toBeTruthy();
        return Response.json(nueva, { status: 201 });
      },
    });
    vi.stubGlobal("fetch", api);
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: PREPARAR }));
    const ventana = within(await screen.findByRole("dialog", { name: PREPARAR }));
    expect(ventana.getByText(DOS_FIRMAS)).toBeTruthy();
    expect((ventana.getByLabelText(/Plazo en días/) as HTMLInputElement).value).toBe("21");
    const guardar = ventana.getByRole("button", { name: "Guardar el borrador" }) as HTMLButtonElement;
    expect(guardar.disabled).toBe(false);
    fireEvent.click(guardar);
    expect(await ventana.findByText("Falta decir cuál.")).toBeTruthy();
    expect(ventana.getByText("Falta escribir la documentación que se pide.")).toBeTruthy();
    expect(pedidos(api, "POST", BASE)).toHaveLength(0);
    fireEvent.change(ventana.getByLabelText(/^Cuál/), { target: { value: " Noviembre (ficticio) " } });
    fireEvent.change(ventana.getByLabelText(/Documentación o aclaración/), { target: { value: "Facturas." } });
    fireEvent.click(guardar);
    const ficha = await screen.findByRole("dialog", { name: /^N\.º 4 ·/ });
    expect(within(ficha).getByText(/Quedó preparada la solicitud N\.º 4/)).toBeTruthy();
    expect(within(ficha).getByRole("button", { name: FIRMAR })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: PREPARAR })).toBeNull();
    expect(cuerpo).toEqual({ objeto: "periodo", referencia: "Noviembre (ficticio)", documentacion: "Facturas.", plazo_dias: 21 });
  });

  it("firma; sin administrador vigente lo dice en la ficha, y firmada sale de ella con el aviso", async () => {
    let veces = 0;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json(LLENA),
      [`POST ${BASE}/${S1}/firma`]: () => {
        veces += 1;
        return veces === 1
          ? error("sin_administrador_vigente", 409)
          : Response.json({ ...BORRADOR, firmas: [{ nombre: "Yo Ficticia", firmada_en: "2026-11-16T10:00:00Z", propia: true }] });
      },
    }));  // prettier-ignore
    abrir();
    const borrador = await abrirFicha(3);
    fireEvent.click(borrador.getByRole("button", { name: FIRMAR }));
    expect(await borrador.findByText(/no tiene un administrador vigente/)).toBeTruthy();
    fireEvent.click(borrador.getByRole("button", { name: FIRMAR }));
    expect(await screen.findByText("Quedó la firma. Falta la de otra persona.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((await fila(3)).textContent).toMatch(/1 de 2 firmas$/);
  });

  it("C8P-08: sin administrador vigente no ofrece «Firmar»: dice qué falta y dónde se resuelve", async () => {
    const espera: Solicitud = {
      ...BORRADOR, firmas: [{ nombre: "Eva Ficticia", firmada_en: "2026-11-16T10:00:00Z", propia: false }],
      acciones: ["retirar"], falta_administrador_vigente: true,
    };  // prettier-ignore
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [espera] }) }));
    abrir();
    const ficha = await abrirFicha(3);
    const linea = await ficha.findByText(new RegExp(`^${FALTA_ADMINISTRADOR.slice(0, 40)}`));
    expect(within(linea).getByRole("link", { name: IR_A_MANDATOS }).getAttribute("href")).toBe("/mandatos");
    expect(ficha.queryByRole("button", { name: FIRMAR })).toBeNull();
    expect(ficha.getByRole("button", { name: RETIRAR })).toBeTruthy();
  });

  it("control C8P-08: con administrador vigente no hay línea y se ofrece «Firmar»", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    abrir();
    const ficha = await abrirFicha(3);
    expect(await ficha.findByRole("button", { name: FIRMAR })).toBeTruthy();
    expect(ficha.queryByText(new RegExp(`^${FALTA_ADMINISTRADOR.slice(0, 40)}`))).toBeNull();
  });

  it("C8P-26: registra el envío en su ventana: sin comprobante lo marca; con él lo sube y lo cita", async () => {
    let envio: unknown = null;
    const adjunto = "66666666-6666-4666-8666-666666666666";
    const api = backend({
      [LISTA]: () => Response.json(LLENA),
      [`POST ${BASE}/${S2}/adjuntos`]: (p) => {
        const url = new URL(p.url);
        expect(url.searchParams.get("para")).toBe("envio");
        expect(p.headers.get("Content-Type")).toBe("application/pdf");
        return Response.json({ adjunto_id: adjunto, documento_id: "77777777-7777-4777-8777-777777777777",
          archivo: { id: "88888888-8888-4888-8888-888888888888", estado: "en_cuarentena", tipo: "application/pdf", tamano: 4, sha256: "b".repeat(64) } },
          { status: 201 });
      },
      [`POST ${BASE}/${S2}/envio`]: async (p) => {
        envio = await p.json();
        return Response.json({ ...PENDIENTE, estado: "enviada", acciones: [], envio: { ...ENVIADA.envio, medio: "carta_documento" } });
      },
    });  // prettier-ignore
    vi.stubGlobal("fetch", api);
    abrir();
    const pendiente = await abrirFicha(2);
    fireEvent.click(pendiente.getByRole("button", { name: REGISTRAR_ENVIO }));
    const ventana = within(await screen.findByRole("dialog", { name: REGISTRAR_ENVIO }));
    // SOL-07: sin repetir desde cuándo corre el plazo.
    expect(ventana.queryByText(/corre el plazo/)).toBeNull();
    const boton = ventana.getByRole("button", { name: "Registrar el envío" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(false);
    fireEvent.click(boton);
    expect(await ventana.findByText("Falta el comprobante del envío.")).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/${S2}/adjuntos`)).toHaveLength(0);
    const campo = ventana.getByLabelText("Archivo del comprobante del envío", { selector: "input" });
    fireEvent.change(campo, { target: { files: [new File(["%PDF"], "carta.pdf", { type: "application/pdf" })] } });
    expect(ventana.queryByText("Falta el comprobante del envío.")).toBeNull();
    fireEvent.change(ventana.getByLabelText("Fecha del envío"), { target: { value: "2026-11-10" } });
    fireEvent.click(boton);
    expect(await screen.findByText("Quedó registrado el envío de la solicitud N.º 2.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(envio).toEqual({ medio: "carta_documento", fecha_envio: "2026-11-10", adjunto_id: adjunto });
  });

  it("si el envío se hizo y su respuesta se perdió, el reintento cita el mismo comprobante sin subirlo de nuevo", async () => {
    const adjunto = "66666666-6666-4666-8666-666666666666";
    let subidas = 0;
    const claves: (string | null)[] = [];
    let intentos = 0;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json(LLENA),
      [`POST ${BASE}/${S2}/adjuntos`]: () => {
        subidas += 1;
        return Response.json({ adjunto_id: adjunto, documento_id: "77777777-7777-4777-8777-777777777777",
          archivo: { id: "88888888-8888-4888-8888-888888888888", estado: "en_cuarentena", tipo: "application/pdf", tamano: 4, sha256: "b".repeat(64) } },
          { status: 201 });
      },
      [`POST ${BASE}/${S2}/envio`]: (p) => {
        claves.push(p.headers.get("Idempotency-Key"));
        intentos += 1;
        if (intentos === 1) throw new TypeError("Failed to fetch"); // se hizo, pero no llegó la respuesta
        return Response.json({ ...PENDIENTE, estado: "enviada", acciones: [], envio: { ...ENVIADA.envio, medio: "carta_documento" } });
      },
    }));  // prettier-ignore
    abrir();
    const pendiente = await abrirFicha(2);
    fireEvent.click(pendiente.getByRole("button", { name: REGISTRAR_ENVIO }));
    const ventana = within(await screen.findByRole("dialog", { name: REGISTRAR_ENVIO }));
    const boton = ventana.getByRole("button", { name: "Registrar el envío" }) as HTMLButtonElement;
    const campo = ventana.getByLabelText("Archivo del comprobante del envío", { selector: "input" });
    fireEvent.change(campo, { target: { files: [new File(["%PDF"], "carta.pdf", { type: "application/pdf" })] } });
    fireEvent.change(ventana.getByLabelText("Fecha del envío"), { target: { value: "2026-11-10" } });
    fireEvent.click(boton);
    // La falla queda en la ventana, con lo elegido.
    expect(await ventana.findByRole("alert")).toBeTruthy();
    await waitFor(() => expect(boton.disabled).toBe(false));
    fireEvent.click(boton);
    expect(await screen.findByText(/Quedó registrado el envío de la solicitud N\.º 2/)).toBeTruthy();
    expect(subidas).toBe(1);
    expect(claves).toHaveLength(2);
    expect(claves[1]).toBe(claves[0]); // el mismo intento lógico: la API devuelve lo guardado
  });

  it("C8P-27: el destinatario responde en su ventana; sin texto se marca lo que falta", async () => {
    let cuerpo: unknown = null;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json(LLENA),
      [`POST ${BASE}/${S3}/respuesta`]: async (p) => {
        cuerpo = await p.json();
        return Response.json({ ...ENVIADA, estado: "respondida", acciones: [] });
      },
    }));  // prettier-ignore
    abrir();
    const enviada = await abrirFicha(1);
    fireEvent.click(enviada.getByRole("button", { name: RESPONDER }));
    const ventana = within(await screen.findByRole("dialog", { name: RESPONDER }));
    fireEvent.click(ventana.getByRole("button", { name: "Enviar la respuesta" }));
    expect(await ventana.findByText("Falta escribir la respuesta.")).toBeTruthy();
    expect(cuerpo).toBeNull();
    fireEvent.change(ventana.getByLabelText("Respuesta"), { target: { value: "Se adjuntan los extractos." } });
    fireEvent.click(ventana.getByRole("button", { name: "Enviar la respuesta" }));
    expect(await screen.findByText(/Quedó registrada la respuesta de la solicitud N\.º 1/)).toBeTruthy();
    expect(cuerpo).toEqual({ texto: "Se adjuntan los extractos." });
  });

  it("C8P-25 y si otra persona ya la cambió: el retiro marca el motivo que falta; el rechazo cierra la ficha, avisa y relee", async () => {
    let lecturas = 0;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => {
        lecturas += 1;
        return Response.json(LLENA);
      },
      [`POST ${BASE}/${S1}/retiro`]: () => error("solicitud_no_admite", 409),
    }));  // prettier-ignore
    abrir();
    const borrador = await abrirFicha(3);
    const antes = lecturas;
    fireEvent.click(borrador.getByRole("button", { name: RETIRAR }));
    const ventana = within(await screen.findByRole("dialog", { name: RETIRAR }));
    fireEvent.click(ventana.getByRole("button", { name: RETIRAR }));
    expect(await ventana.findByText("Falta escribir el motivo del retiro.")).toBeTruthy();
    fireEvent.change(ventana.getByLabelText("Motivo del retiro"), { target: { value: "Se pidió por otra vía." } });
    fireEvent.click(ventana.getByRole("button", { name: RETIRAR }));
    expect(await screen.findByText(/ya no admite eso en su estado/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(lecturas).toBeGreaterThan(antes));
  });

  it("SOL-08, SOL-09 y SOL-10: la ficha sin datos técnicos, con el adjunto a mano y la copia que no se repite", async () => {
    const escribir = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: escribir }, configurable: true });
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [PENDIENTE, RESPONDIDA] }) }));
    abrir();
    const pendiente = await abrirFicha(2);
    // La huella del texto emitido sigue a la vista (decisión del propietario, 17-11-2026).
    expect(pendiente.getByText("Huella SHA-256")).toBeTruthy();
    expect(pendiente.getByText("a".repeat(64))).toBeTruthy();
    expect(pendiente.getByText("Marta Ficticia (esta cuenta), Eva Ficticia")).toBeTruthy();
    // El plazo, solo en días: desde cuándo corre ya lo dice la situación (SOL-07).
    expect(pendiente.getByText("15 días")).toBeTruthy();
    fireEvent.click(pendiente.getByRole("button", { name: "Copiar el texto" }));
    expect(await pendiente.findByText("Copiado.")).toBeTruthy();
    expect(pendiente.queryByRole("button", { name: "Copiar el texto" })).toBeNull();
    expect(escribir).toHaveBeenCalledWith(PENDIENTE.texto_emitido);
    fireEvent.click(pendiente.getByRole("button", { name: "Cerrar" }));

    fireEvent.click(screen.getByText(/^Ver respondidas y retiradas/));
    const respondida = await abrirFicha(5);
    const enlace = respondida.getByRole("link", { name: "Descargar el adjunto" });
    expect(enlace.getAttribute("href")).toBe(`/api/v1/consorcios/${C1}/documentos/${DOC_RESPUESTA}/versiones/${VERSION_RESPUESTA}/descargar`);
    expect(respondida.queryByText(/Documentos del edificio, «Otros»/)).toBeNull();
    // Control: un borrador sin firmas no muestra el renglón de firmas.
    fireEvent.click(respondida.getByRole("button", { name: "Cerrar" }));
    cleanup();
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    abrir();
    const borrador = await abrirFicha(3);
    expect(borrador.queryByText("Firmas")).toBeNull();
    expect(borrador.getByText("Preparó")).toBeTruthy();
  });
});

describe("Flujos 18-11, tanda 7", () => {
  const CON_UNA_FIRMA: Solicitud = {
    ...BORRADOR,
    firmas: [{ nombre: "Eva Ficticia", firmada_en: "2026-11-16T10:00:00Z", propia: false }],
  };
  const manana = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return d;
  };

  it("FL-SOL-01: con una firma puesta, el botón dice «Firmar y emitir» y avisa que no se puede retirar", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [CON_UNA_FIRMA] }) }));
    abrir();
    const ficha = await abrirFicha(3);
    expect(ficha.getByRole("button", { name: FIRMAR_Y_EMITIR })).toBeTruthy();
    expect(ficha.getByText(EMITE_AL_FIRMAR)).toBeTruthy();
    expect(ficha.queryByRole("button", { name: FIRMAR })).toBeNull();
  });

  it("FL-SOL-01, control: la primera firma sigue diciendo «Firmar», sin el aviso", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    abrir();
    const ficha = await abrirFicha(3);
    expect(ficha.getByRole("button", { name: FIRMAR })).toBeTruthy();
    expect(ficha.queryByText(EMITE_AL_FIRMAR)).toBeNull();
  });

  it("FL-SOL-02: emitida con el envío pendiente, la ficha queda abierta con el resultado, el texto y «Registrar el envío»", async () => {
    const emitida: Solicitud = { ...PENDIENTE, id: S1, numero: 3, firmas: [...CON_UNA_FIRMA.firmas, { nombre: "Yo Ficticia", firmada_en: "2026-11-17T10:00:00Z", propia: true }] };
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json({ ...LLENA, elementos: [CON_UNA_FIRMA] }),
      [`POST ${BASE}/${S1}/firma`]: () => Response.json(emitida),
    }));  // prettier-ignore
    abrir();
    const ficha = await abrirFicha(3);
    fireEvent.click(ficha.getByRole("button", { name: FIRMAR_Y_EMITIR }));
    expect(await ficha.findByText(/^Quedó emitida\. El administrador no tiene cuenta/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /^N\.º 3 ·/ })).toBeTruthy();
    expect(ficha.getByText("Texto emitido")).toBeTruthy();
    expect(ficha.getByRole("button", { name: REGISTRAR_ENVIO })).toBeTruthy();
  });

  it("FL-SOL-02, control: emitida a quien tiene cuenta, la ficha se cierra y el resultado queda en la lista", async () => {
    const enviada: Solicitud = { ...ENVIADA, id: S1, numero: 3, acciones: [] };
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json({ ...LLENA, elementos: [CON_UNA_FIRMA] }),
      [`POST ${BASE}/${S1}/firma`]: () => Response.json(enviada),
    }));  // prettier-ignore
    abrir();
    const ficha = await abrirFicha(3);
    fireEvent.click(ficha.getByRole("button", { name: FIRMAR_Y_EMITIR }));
    expect(await screen.findByText(/^Quedó emitida y enviada/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("FL-SOL-06: la fecha del envío va de la emisión a hoy; fuera de eso se marca sin mandar nada", async () => {
    const api = backend({ [LISTA]: () => Response.json(LLENA) });
    vi.stubGlobal("fetch", api);
    abrir();
    const pendiente = await abrirFicha(2);
    fireEvent.click(pendiente.getByRole("button", { name: REGISTRAR_ENVIO }));
    const ventana = within(await screen.findByRole("dialog", { name: REGISTRAR_ENVIO }));
    const fecha = ventana.getByLabelText("Fecha del envío") as HTMLInputElement;
    expect(fecha.min).toBe(hoyDelDispositivo(new Date(PENDIENTE.emitida_en!)));
    expect(fecha.max).toBe(hoyDelDispositivo());
    fireEvent.change(ventana.getByLabelText("Archivo del comprobante del envío", { selector: "input" }), {
      target: { files: [new File(["%PDF"], "carta.pdf", { type: "application/pdf" })] },
    });
    fireEvent.change(fecha, { target: { value: "2026-11-01" } });
    fireEvent.click(ventana.getByRole("button", { name: "Registrar el envío" }));
    expect(await ventana.findByText(/^La fecha va desde la emisión \(.+\) hasta hoy\.$/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/${S2}/adjuntos`)).toHaveLength(0);
  });

  it("FL-SOL-06: la respuesta recibida va del envío a hoy; un día futuro se marca sin mandar nada", async () => {
    const vencida: Solicitud = {
      ...ENVIADA, estado: "vencida", destinatario_con_cuenta: false,
      envio: { ...ENVIADA.envio!, medio: "carta_documento" }, acciones: ["registrar_respuesta"],
    };  // prettier-ignore
    const api = backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [vencida] }) });
    vi.stubGlobal("fetch", api);
    abrir();
    const ficha = await abrirFicha(1);
    fireEvent.click(ficha.getByRole("button", { name: REGISTRAR_RESPUESTA }));
    const ventana = within(await screen.findByRole("dialog", { name: REGISTRAR_RESPUESTA }));
    const fecha = ventana.getByLabelText("Fecha en que llegó") as HTMLInputElement;
    expect(fecha.min).toBe("2026-11-01");
    expect(fecha.max).toBe(hoyDelDispositivo());
    fireEvent.change(ventana.getByLabelText("Lo que respondió la administración"), { target: { value: "Llegó por carta." } });
    fireEvent.change(fecha, { target: { value: hoyDelDispositivo(manana()) } });
    fireEvent.click(ventana.getByRole("button", { name: "Registrar la respuesta" }));
    expect(await ventana.findByText(/^La fecha va desde el envío \(01\/11\/2026\) hasta hoy\.$/)).toBeTruthy();
    expect(pedidos(api, "POST", `${BASE}/${S3}/respuesta-recibida`)).toHaveLength(0);
  });

  it("decisión 15: sin administrador vigente, la ventana de preparar lo dice y deja guardar el borrador", async () => {
    const espera: Solicitud = { ...CON_UNA_FIRMA, acciones: ["retirar"], falta_administrador_vigente: true };
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json({ ...LLENA, elementos: [espera] }) }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: PREPARAR }));
    const ventana = within(await screen.findByRole("dialog", { name: PREPARAR }));
    const linea = ventana.getByText(new RegExp(`^${SIN_ADMINISTRADOR_AL_PREPARAR.slice(0, 40)}`));
    expect(within(linea).getByRole("link", { name: IR_A_MANDATOS }).getAttribute("href")).toBe("/mandatos");
    expect(ventana.getByRole("button", { name: "Guardar el borrador" })).toBeTruthy();
  });

  it("decisión 15, control: con administrador vigente, la ventana no lo menciona", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: PREPARAR }));
    const ventana = within(await screen.findByRole("dialog", { name: PREPARAR }));
    expect(ventana.queryByText(new RegExp(`^${SIN_ADMINISTRADOR_AL_PREPARAR.slice(0, 40)}`))).toBeNull();
  });
});
