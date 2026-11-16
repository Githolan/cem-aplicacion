import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alcanza, alcanzaTodo } from "../accionesDePrueba";
import { App } from "../App";
import type { components } from "../api/tipos";
import { ContextoVersion } from "../version";
import {
  FIRMAR,
  PREPARAR,
  REGISTRAR_ENVIO,
  RESPONDER,
  RETIRAR,
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

async function ficha(numero: number) {
  const titulo = await screen.findByRole("heading", { name: new RegExp(`^N\\.º ${numero} ·`) });
  const articulo = titulo.closest("article");
  if (articulo === null) throw new Error("sin ficha");
  return within(articulo as HTMLElement);
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
  it("las agrupa por estado y ofrece solo lo que la API dice", async () => {
    vi.stubGlobal("fetch", backend({ [LISTA]: () => Response.json(LLENA) }));
    const { container } = abrir();
    expect(await screen.findByRole("heading", { level: 1, name: TITULO_SOLICITUDES })).toBeTruthy();
    const grupos = await screen.findAllByRole("region");
    expect(grupos.map((g) => g.getAttribute("aria-label"))).toEqual([TEXTO_ESTADO.borrador, TEXTO_ESTADO.envio_pendiente, TEXTO_ESTADO.enviada]);
    const borrador = await ficha(3);
    expect(borrador.getByRole("button", { name: FIRMAR })).toBeTruthy();
    expect(borrador.getByRole("button", { name: RETIRAR })).toBeTruthy();
    expect(borrador.queryByRole("button", { name: RESPONDER })).toBeNull();
    const pendiente = await ficha(2);
    expect(pendiente.getByRole("button", { name: REGISTRAR_ENVIO })).toBeTruthy();
    expect(pendiente.queryByRole("button", { name: FIRMAR })).toBeNull();
    const enviada = await ficha(1);
    expect(enviada.getByRole("button", { name: RESPONDER })).toBeTruthy();
    expect(screen.getByRole("button", { name: PREPARAR })).toBeTruthy();
    const resultado = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
  });

  it("sin solicitudes ni facultad de preparar, solo lo dice", async () => {
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json({ ...LLENA, elementos: [], puede_preparar: false }),
      [`GET /api/v1/consorcios/${C1}/mis-acciones`]: alcanza("solicitud_formal.responder"),
    }));  // prettier-ignore
    abrir();
    expect(await screen.findByText(SIN_SOLICITUDES)).toBeTruthy();
    expect(screen.queryByRole("button", { name: PREPARAR })).toBeNull();
  });

  it("prepara con el plazo que propone la API y la suma a la lista", async () => {
    let cuerpo: unknown = null;
    const nueva: Solicitud = { ...BORRADOR, id: "55555555-5555-4555-8555-555555555555", numero: 4 };
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json({ ...LLENA, plazo_por_defecto_dias: 21 }),
      [`POST ${BASE}`]: async (p) => {
        cuerpo = await p.json();
        expect(p.headers.get("Idempotency-Key")).toBeTruthy();
        return Response.json(nueva, { status: 201 });
      },
    }));  // prettier-ignore
    abrir();
    fireEvent.click(await screen.findByRole("button", { name: PREPARAR }));
    const formulario = screen.getByRole("form", { name: PREPARAR });
    expect((within(formulario).getByLabelText(/Plazo en días/) as HTMLInputElement).value).toBe("21");
    fireEvent.change(within(formulario).getByLabelText(/^Cuál/), { target: { value: " Noviembre (ficticio) " } });
    fireEvent.change(within(formulario).getByLabelText(/Documentación o aclaración/), { target: { value: "Facturas." } });
    fireEvent.click(within(formulario).getByRole("button", { name: "Guardar el borrador" }));
    expect(await screen.findByText(/Quedó preparada la solicitud N\.º 4/)).toBeTruthy();
    expect(cuerpo).toEqual({ objeto: "periodo", referencia: "Noviembre (ficticio)", documentacion: "Facturas.", plazo_dias: 21 });
    expect(await ficha(4)).toBeTruthy();
  });

  it("firma; sin administrador vigente dice por qué y la solicitud queda como estaba", async () => {
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
    const borrador = await ficha(3);
    fireEvent.click(borrador.getByRole("button", { name: FIRMAR }));
    expect(await borrador.findByText(/no tiene un administrador vigente/)).toBeTruthy();
    fireEvent.click(borrador.getByRole("button", { name: FIRMAR }));
    expect(await screen.findByText("Quedó la firma. Falta la de otra persona.")).toBeTruthy();
    expect((await ficha(3)).getByText("Borrador: 1 de 2 firmas.")).toBeTruthy();
  });

  it("registra el envío: sube el comprobante y lo cita", async () => {
    let envio: unknown = null;
    const adjunto = "66666666-6666-4666-8666-666666666666";
    vi.stubGlobal("fetch", backend({
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
    }));  // prettier-ignore
    abrir();
    const pendiente = await ficha(2);
    fireEvent.click(pendiente.getByRole("button", { name: REGISTRAR_ENVIO }));
    const boton = pendiente.getByRole("button", { name: "Registrar el envío" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true); // sin comprobante no se registra
    const campo = pendiente.getByLabelText("Archivo del comprobante del envío", { selector: "input" });
    fireEvent.change(campo, { target: { files: [new File(["%PDF"], "carta.pdf", { type: "application/pdf" })] } });
    fireEvent.change(pendiente.getByLabelText("Fecha del envío"), { target: { value: "2026-11-10" } });
    await waitFor(() => expect(boton.disabled).toBe(false));
    fireEvent.click(boton);
    expect(await screen.findByText(/Quedó registrado el envío de la solicitud N\.º 2/)).toBeTruthy();
    expect(envio).toEqual({ medio: "carta_documento", fecha_envio: "2026-11-10", adjunto_id: adjunto });
  });

  it("el destinatario responde en la aplicación", async () => {
    let cuerpo: unknown = null;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => Response.json(LLENA),
      [`POST ${BASE}/${S3}/respuesta`]: async (p) => {
        cuerpo = await p.json();
        return Response.json({ ...ENVIADA, estado: "respondida", acciones: [] });
      },
    }));  // prettier-ignore
    abrir();
    const enviada = await ficha(1);
    fireEvent.click(enviada.getByRole("button", { name: RESPONDER }));
    fireEvent.change(enviada.getByLabelText("Respuesta"), { target: { value: "Se adjuntan los extractos." } });
    fireEvent.click(enviada.getByRole("button", { name: "Enviar la respuesta" }));
    expect(await screen.findByText(/Quedó registrada la respuesta de la solicitud N\.º 1/)).toBeTruthy();
    expect(cuerpo).toEqual({ texto: "Se adjuntan los extractos." });
  });

  it("si otra persona ya la cambió, avisa y vuelve a leer", async () => {
    let lecturas = 0;
    vi.stubGlobal("fetch", backend({
      [LISTA]: () => {
        lecturas += 1;
        return Response.json(LLENA);
      },
      [`POST ${BASE}/${S1}/retiro`]: () => error("solicitud_no_admite", 409),
    }));  // prettier-ignore
    abrir();
    const borrador = await ficha(3);
    const antes = lecturas;
    fireEvent.click(borrador.getByRole("button", { name: RETIRAR }));
    fireEvent.change(borrador.getByLabelText("Motivo del retiro"), { target: { value: "Se pidió por otra vía." } });
    fireEvent.click(borrador.getByRole("button", { name: "Confirmar el retiro" }));
    expect(await screen.findByText(/ya no admite eso en su estado/)).toBeTruthy();
    await waitFor(() => expect(lecturas).toBeGreaterThan(antes));
  });
});
