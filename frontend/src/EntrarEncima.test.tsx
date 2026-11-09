import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api/cliente";
import { type Copia, guardarCopia, leerCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { EntrarEncima } from "./EntrarEncima";
import { ENCABEZADO_IDEMPOTENCIA } from "./egresos/comun";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { exigirSesion, SinSesion } from "./egresos/useConsorcio";
import { recordarLlave } from "./llaveAcceso";
import { recordarMetodo } from "./metodoConfirmar";
import { activarEntrarEncima, comprobarSesion, faltaConocerSesion, pidiendoEntrar, salidas, useAEntrar } from "./sesionEncima";

const PERSONA = "99999999-9999-4999-8999-999999999999";
const CONSORCIO = "c1c1c1c1-0000-4000-8000-000000000001";
const RUTA = `/api/v1/consorcios/${CONSORCIO}/proveedores`;
const SESION = { persona_id: PERSONA, nombre: "Ana", correo: "ana@ejemplo.com", sesion_id: "s1", vence_en: "2026-12-01T00:00:00Z" };
const NUEVA = { ...SESION, sesion_id: "s2" };
const HOJA = "Tu sesión venció";
const LISTA = { elementos: [], actualizada_en: null, zona_horaria: "America/Argentina/Buenos_Aires" } as unknown as Copia["lista"];

const error = (codigo: string, estado: number, detalle?: object) =>
  Response.json({ codigo, mensaje: "x", id_correlacion: "c", detalle }, { status: estado });

type Respuesta = (pedido: Request) => Response;

/** Un backend que cambia: `sesion` es la de la cookie y `rutas` responde lo demás. */
function backend(rutas: Record<string, Respuesta>) {
  const estado = { sesion: SESION as typeof SESION | null };
  const simulado = vi.fn(async (pedido: Request) => {
    const { pathname } = new URL(pedido.url);
    if (pathname === "/api/v1/auth/sesion")
      return estado.sesion === null ? error("no_autenticado", 401) : Response.json(estado.sesion);
    const respuesta = rutas[`${pedido.method} ${pathname}`];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${pedido.method} ${pathname}`);
    return respuesta(pedido);
  });
  vi.stubGlobal("fetch", simulado);
  return { estado, simulado };
}

const veces = (simulado: ReturnType<typeof backend>["simulado"], metodo: string, ruta: string) =>
  simulado.mock.calls.filter(([p]) => p.method === metodo && new URL(p.url).pathname === ruta).length;

function Formulario() {
  const [texto, setTexto] = useState("");
  const [lectura, setLectura] = useState("sin leer");
  const clave = useRef(crypto.randomUUID());
  const aEntrar = useAEntrar();
  return (
    <>
      <label htmlFor="escrito">Escrito</label>
      <input id="escrito" value={texto} onChange={(e) => setTexto(e.target.value)} />
      <button
        type="button"
        onClick={() =>
          void api
            .GET("/api/v1/consorcios/{consorcio_id}/proveedores", { params: { path: { consorcio_id: CONSORCIO } } })
            .then(({ data }) => setLectura(data === undefined ? "falló" : "leído"))
        }
      >
        Leer
      </button>
      <button
        type="button"
        onClick={() =>
          void api
            .POST("/api/v1/consorcios/{consorcio_id}/proveedores", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: CONSORCIO }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
              body: { cuit: "30000000007", razon_social: "Proveedor SINTETICO", atiende_emergencias: false },
            })
            .then(({ response }) => exigirSesion(response))
            .catch((e: unknown) => {
              if (e instanceof SinSesion) aEntrar();
            })
        }
      >
        Guardar
      </button>
      <p>{lectura}</p>
    </>
  );
}

async function montar() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/egresos/proveedores"]}>
        <EntrarEncima />
        <Formulario />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // La hoja compara con la sesión que conocía: hasta acá no se conoce ninguna.
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(0));
  await Promise.resolve();
}

const tocar = (nombre: string) => fireEvent.click(screen.getByRole("button", { name: nombre }));

async function entrarConCodigo() {
  fireEvent.click(await screen.findByRole("button", { name: "Entrar con un código" }));
  fireEvent.change(await screen.findByLabelText(/Código de/), { target: { value: "042917" } });
  tocar("Entrar con el código");
}

describe("entrar encima", () => {
  let apagar: () => void;

  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    apagar = activarEntrarEncima();
    vi.spyOn(salidas, "aInicio").mockImplementation(() => undefined);
    vi.spyOn(salidas, "aEntrar").mockImplementation(() => undefined);
  });

  afterEach(() => {
    cleanup();
    apagar();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("si la sesión vence confirmando con la llave, la hoja de entrar sigue arriba y se puede usar", async () => {
    const bytes = () => Uint8Array.from([1, 2, 3]).buffer;
    const credencial = {
      id: "AQID",
      rawId: bytes(),
      type: "public-key",
      getClientExtensionResults: () => ({}),
      response: { clientDataJSON: bytes(), authenticatorData: bytes(), signature: bytes(), userHandle: null },
    };
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", { value: { get: async () => credencial }, configurable: true });
    window.localStorage.clear();
    recordarLlave("AQID");
    recordarMetodo("llave");
    const { estado } = backend({
      "GET /api/v1/auth/totp": () =>
        Response.json({ activo: true, llaves: 1, codigos_recuperacion_restantes: 5, verificado_en: null }),
      "GET /api/v1/auth/llave": () =>
        Response.json([{ id: "1", nombre: "Mi PC", credencial_id: "AQID", creada_en: "2026-11-04T10:00:00Z", ultimo_uso_en: null }]),
      "POST /api/v1/auth/llave/verificar/opciones": () =>
        Response.json({ opciones: { challenge: "AAEC", allowCredentials: [{ id: "AwQF", type: "public-key" }] } }),
      "POST /api/v1/auth/llave/verificar": () => error("no_autenticado", 401),
      "POST /api/v1/auth/codigo": () => {
        estado.sesion = NUEVA;
        return Response.json({ persona_id: PERSONA });
      },
    });
    await montar();
    render(<PedirCodigo acto="aprobar la orden" alVerificar={vi.fn()} alCancelar={vi.fn()} />);

    const entrar = (await screen.findByRole("heading", { name: HOJA })).closest<HTMLElement>(".hoja-fondo")!;
    const fallo = (await screen.findByRole("heading", { name: "No se pudo confirmar" })).closest<HTMLElement>(".hoja-fondo")!;
    expect(entrar.inert).toBeFalsy();
    await waitFor(() => expect(fallo.inert).toBe(true));
    expect(entrar.contains(document.activeElement)).toBe(true);

    await entrarConCodigo();
    await screen.findByText("Seguís donde estabas.");
    expect(screen.queryByRole("heading", { name: HOJA })).toBeNull();
    expect(fallo.inert).toBeFalsy();
    expect(fallo.contains(document.activeElement)).toBe(true);
  });

  it("una lectura de la sesión que da 401 abre la hoja, espera y se repite sola, sin ir a /entrar", async () => {
    const { estado } = backend({
      "POST /api/v1/auth/codigo": () => {
        estado.sesion = NUEVA;
        return Response.json({ persona_id: PERSONA });
      },
    });
    await montar();
    fireEvent.change(screen.getByLabelText("Escrito"), { target: { value: "lo que escribí" } });

    estado.sesion = null;
    const lectura = api.GET("/api/v1/auth/sesion");

    await screen.findByRole("heading", { name: HOJA });
    await entrarConCodigo();
    const { data } = await lectura;
    expect(data?.sesion_id).toBe(NUEVA.sesion_id);
    expect(screen.queryByRole("heading", { name: HOJA })).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("Escrito").value).toBe("lo que escribí");
  });

  it("control: comprobar la sesión con un 401 no abre la hoja ni queda esperando", async () => {
    const { estado } = backend({});
    await montar();

    estado.sesion = null;
    const { response } = await api.GET("/api/v1/auth/sesion", { fetch: comprobarSesion });

    expect(response.status).toBe(401);
    expect(pidiendoEntrar()).toBe(false);
  });

  it("si la lectura que da a conocer la sesión falló, se repite al volver a la ventana", async () => {
    const { estado, simulado } = backend({});
    simulado.mockRejectedValueOnce(new TypeError("sin red"));
    await montar();
    await waitFor(() => expect(veces(simulado, "GET", "/api/v1/auth/sesion")).toBe(1));

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(veces(simulado, "GET", "/api/v1/auth/sesion")).toBe(2));
    await waitFor(() => expect(faltaConocerSesion()).toBe(false));

    estado.sesion = null;
    void api.GET("/api/v1/auth/sesion");
    await screen.findByRole("heading", { name: HOJA });
  });

  it("si salir falló, la sesión sigue conocida y al vencer abre la hoja", async () => {
    const { estado } = backend({ "POST /api/v1/auth/salir": () => error("servicio_no_disponible", 503) });
    await montar();

    await api.POST("/api/v1/auth/salir", { headers: encabezadoCsrf() });
    expect(faltaConocerSesion()).toBe(false);
    estado.sesion = null;
    void api.GET("/api/v1/auth/sesion");

    await screen.findByRole("heading", { name: HOJA });
  });

  it("una lectura con la sesión vencida espera en la hoja y se repite sola al entrar, sin perder lo escrito", async () => {
    let vencida = true;
    const { estado, simulado } = backend({
      [`GET ${RUTA}`]: () => (vencida ? error("no_autenticado", 401) : Response.json({ elementos: [] })),
      "POST /api/v1/auth/codigo": () => {
        vencida = false;
        estado.sesion = NUEVA;
        return Response.json({ persona_id: PERSONA });
      },
    });
    await montar();
    fireEvent.change(screen.getByLabelText("Escrito"), { target: { value: "lo que escribí" } });

    estado.sesion = null;
    tocar("Leer");
    expect(await screen.findByRole("heading", { name: HOJA })).toBeTruthy();
    expect(screen.getByText("sin leer")).toBeTruthy();
    expect((screen.getByLabelText("Correo") as HTMLInputElement).value).toBe(SESION.correo);

    await entrarConCodigo();

    expect(await screen.findByText("Seguís donde estabas.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: HOJA })).toBeNull();
    expect(await screen.findByText("leído")).toBeTruthy();
    expect(veces(simulado, "GET", RUTA)).toBe(2);
    expect((screen.getByLabelText("Escrito") as HTMLInputElement).value).toBe("lo que escribí");
  });

  it("un pedido que escribe no se manda solo: lo repite la persona, con la misma clave", async () => {
    let vencida = true;
    const { estado, simulado } = backend({
      [`POST ${RUTA}`]: () => (vencida ? error("no_autenticado", 401) : Response.json({ id: "p", version: 1 }, { status: 201 })),
      "POST /api/v1/auth/codigo": () => {
        vencida = false;
        estado.sesion = NUEVA;
        return Response.json({ persona_id: PERSONA });
      },
    });
    await montar();

    tocar("Guardar");
    await screen.findByRole("heading", { name: HOJA });
    await entrarConCodigo();
    await screen.findByText("Seguís donde estabas.");
    expect(veces(simulado, "POST", RUTA)).toBe(1); // el control: no salió de nuevo

    tocar("Guardar");
    await waitFor(() => expect(veces(simulado, "POST", RUTA)).toBe(2));
    const claves = simulado.mock.calls
      .filter(([p]) => p.method === "POST" && new URL(p.url).pathname === RUTA)
      .map(([p]) => p.headers.get(ENCABEZADO_IDEMPOTENCIA));
    expect(claves[0]).toBeTruthy();
    expect(claves[0]).toBe(claves[1]);
  });

  it("la sesión que ya no sirve para el acto también pide entrar encima, y la sesión vieja no la cierra", async () => {
    const { estado } = backend({
      [`POST ${RUTA}`]: () => error("sesion_reciente_requerida", 403),
      "POST /api/v1/auth/codigo": () => {
        estado.sesion = NUEVA;
        return Response.json({ persona_id: PERSONA });
      },
    });
    await montar();

    tocar("Guardar");
    await screen.findByRole("heading", { name: HOJA });
    // Control: la sesión vieja sigue viva y responde 200; volver a la pestaña no da por entrado.
    fireEvent.focus(window);
    await new Promise((listo) => setTimeout(listo));
    expect(screen.getByRole("heading", { name: HOJA })).toBeTruthy();

    await entrarConCodigo();
    expect(await screen.findByText("Seguís donde estabas.")).toBeTruthy();
  });

  it("si entró por el enlace en otra pestaña, al volver se cierra sola", async () => {
    const { estado } = backend({ [`POST ${RUTA}`]: () => error("no_autenticado", 401) });
    await montar();

    tocar("Guardar");
    await screen.findByRole("heading", { name: HOJA });
    estado.sesion = NUEVA;
    fireEvent.focus(window);

    expect(await screen.findByText("Seguís donde estabas.")).toBeTruthy();
  });

  it("si entra otra persona, no ve la pantalla de la anterior: carga el inicio de cero y borra las copias", async () => {
    guardarCopia({ consorcio_id: CONSORCIO, nombre: "Edificio SINTETICO", guardada_en: "2026-11-06T00:00:00Z", lista: LISTA });
    expect(leerCopias()).toHaveLength(1);
    const { estado } = backend({
      [`POST ${RUTA}`]: () => error("no_autenticado", 401),
      "POST /api/v1/auth/codigo": () => {
        estado.sesion = { ...NUEVA, persona_id: "11111111-1111-4111-8111-111111111111" };
        return Response.json({});
      },
    });
    await montar();

    tocar("Guardar");
    await screen.findByRole("heading", { name: HOJA });
    await entrarConCodigo();

    await waitFor(() => expect(salidas.aInicio).toHaveBeenCalledOnce());
    expect(screen.queryByText("Seguís donde estabas.")).toBeNull();
    expect(leerCopias()).toEqual([]);
  });

  it("deja salir de la cuenta", async () => {
    backend({
      [`POST ${RUTA}`]: () => error("no_autenticado", 401),
      "POST /api/v1/auth/salir": () => new Response(null, { status: 204 }),
    });
    await montar();

    tocar("Guardar");
    await screen.findByRole("heading", { name: HOJA });
    window.sessionStorage.setItem("cem.entrar.adonde_iba", "/pagos");
    tocar("Salir de la cuenta");

    await waitFor(() => expect(salidas.aEntrar).toHaveBeenCalledOnce());
    expect(window.sessionStorage.getItem("cem.entrar.adonde_iba")).toBeNull();
  });

  it.each([
    ["sin_permiso", 403, undefined],
    ["reautenticacion_requerida", 403, { falta: "segundo_factor" }],
    ["reautenticacion_requerida", 403, { falta: "contrasena" }],
    ["servicio_no_disponible", 503, undefined],
  ])("control: %s %s no abre la hoja", async (codigo, estado, detalle) => {
    backend({ [`POST ${RUTA}`]: () => error(codigo, estado, detalle) });
    await montar();

    tocar("Guardar");
    await new Promise((listo) => setTimeout(listo));
    expect(pidiendoEntrar()).toBe(false);
    expect(screen.queryByRole("heading", { name: HOJA })).toBeNull();
  });

  it("control: pedir entrar de nuevo sin detalle sí la abre", async () => {
    backend({ [`POST ${RUTA}`]: () => error("reautenticacion_requerida", 403, { falta: "entrar_de_nuevo" }) });
    await montar();

    tocar("Guardar");
    expect(await screen.findByRole("heading", { name: HOJA })).toBeTruthy();
  });

  it("un código equivocado al entrar no se toma por una sesión vencida", async () => {
    backend({ "POST /api/v1/auth/codigo": () => error("no_autenticado", 401) });
    await montar();

    await api.POST("/api/v1/auth/codigo", { body: { correo: "ana@ejemplo.com", codigo: "000000" } });
    expect(pidiendoEntrar()).toBe(false);
  });

  it("sin una sesión conocida, un 401 sigue a /entrar como siempre", async () => {
    const { estado } = backend({ [`GET ${RUTA}`]: () => error("no_autenticado", 401) });
    estado.sesion = null;
    await montar();

    const { response } = await api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", {
      params: { path: { consorcio_id: CONSORCIO } },
    });
    expect(response.status).toBe(401);
    expect(pidiendoEntrar()).toBe(false);
  });

  it("después de salir, un 401 ya no abre la hoja", async () => {
    backend({
      [`GET ${RUTA}`]: () => error("no_autenticado", 401),
      "POST /api/v1/auth/salir": () => new Response(null, { status: 204 }),
    });
    await montar();

    await api.POST("/api/v1/auth/salir", { headers: encabezadoCsrf() });
    const { response } = await api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", {
      params: { path: { consorcio_id: CONSORCIO } },
    });
    expect(response.status).toBe(401);
    expect(pidiendoEntrar()).toBe(false);
  });
});
