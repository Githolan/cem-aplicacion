import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { esDeEsteDispositivo, nombreDelDispositivo, recordarLlave } from "./llaveAcceso";
import { SeccionLlave } from "./SeccionLlave";

const LLAVE = {
  id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
  credencial_id: "Y3JlZGVuY2lhbA",
  nombre: "Mi PC",
  creada_en: "2026-11-04T10:00:00Z",
  ultimo_uso_en: null,
};
const VERIFICADO = {
  verificado_en: "2026-11-04T10:00:00Z",
  vale_hasta: "2026-11-04T10:10:00Z",
  codigos_recuperacion_restantes: 9,
};
const OPCIONES_DE_ALTA = { challenge: "AAAA", user: { id: "AAAA", name: "a", displayName: "a" } };
const FALTA = () =>
  Response.json({ codigo: "segundo_factor_requerido", mensaje: "x", id_correlacion: "c" }, { status: 403 });

function mostrar(alTerminar: (aviso: string) => void = () => undefined) {
  return render(<SeccionLlave alCambiar={() => undefined} alCodigos={() => undefined} alTerminar={alTerminar} />);
}

const TOTP = () => Response.json({ activo: false, llaves: 1, codigos_recuperacion_restantes: 5, verificado_en: null });

function backend(respuestas: Record<string, () => Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (pedido: Request) => {
      const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
      const respuesta = { "GET /api/v1/auth/totp": TOTP, ...respuestas }[clave];
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    }),
  );
}

/** «Quitar» abre la hoja de confirmar (AC-14); el pedido sale con «Quitar la llave». */
async function quitarConfirmando() {
  fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));
  fireEvent.click(await screen.findByRole("button", { name: "Quitar la llave" }));
}

describe("sección de la llave", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", { value: {}, configurable: true });
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("quien perdió el dispositivo quita la llave con un código de recuperación", async () => {
    const borrar = vi
      .fn<() => Response>()
      .mockImplementationOnce(FALTA)
      .mockImplementationOnce(() => new Response(null, { status: 204 }));
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: borrar,
      "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO),
    });
    mostrar();

    await quitarConfirmando();
    fireEvent.click(await screen.findByRole("button", { name: "Usar otro método" }));
    fireEvent.click(await screen.findByRole("button", { name: "Código de recuperación" }));
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "k7m2q-x9p4t" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    expect(await screen.findByText("Listo: la llave se quitó.")).not.toBeNull();
    expect(borrar).toHaveBeenCalledTimes(2);
  });

  it("con la sesión ya verificada, quitar no pide nada más", async () => {
    const borrar = vi.fn(() => new Response(null, { status: 204 }));
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: borrar,
    });
    mostrar();

    await quitarConfirmando();

    expect(await screen.findByText("Listo: la llave se quitó.")).not.toBeNull();
    expect(borrar).toHaveBeenCalledOnce();
  });

  it("si cancela la confirmación no da nada por terminado", async () => {
    const borrar = vi.fn(FALTA);
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: borrar,
    });
    mostrar();

    await quitarConfirmando();
    // PedirCodigo dispara la llave al leer lo activo: se espera a que falle antes de cancelar.
    await screen.findByRole("button", { name: "Probar de nuevo" });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(await screen.findByRole("button", { name: "Quitar" })).not.toBeNull();
    expect(screen.queryByText("Listo: la llave se quitó.")).toBeNull();
    expect(borrar).toHaveBeenCalledOnce();
  });

  it("con la llave de este dispositivo dice que no falta nada y no ofrece agregar", async () => {
    recordarLlave(LLAVE.credencial_id);
    backend({ "GET /api/v1/auth/llave": () => Response.json([LLAVE]) });
    mostrar();

    expect(await screen.findByText(/La llave ya funciona en este dispositivo/)).not.toBeNull();
    expect(screen.getByRole("list", { name: "Llaves de la cuenta" }).textContent).toContain("Mi PC (este dispositivo)");
    expect(screen.queryByRole("button", { name: "Agregar este dispositivo" })).toBeNull();
  });

  it("control: con la llave de otro dispositivo ofrece agregar este y no dice que está listo", async () => {
    recordarLlave("otra-credencial");
    backend({ "GET /api/v1/auth/llave": () => Response.json([LLAVE]) });
    mostrar();

    expect(await screen.findByRole("button", { name: "Agregar este dispositivo" })).not.toBeNull();
    expect(screen.getByText("Si este dispositivo no está en la lista, agregarlo.")).not.toBeNull();
    expect(screen.queryByText(/La llave ya funciona en este dispositivo/)).toBeNull();
    expect(screen.getByRole("list", { name: "Llaves de la cuenta" }).textContent).not.toContain("(este dispositivo)");
  });

  function dispositivoConLlave(nombre: string) {
    Object.defineProperty(navigator, "credentials", {
      value: { create: () => Promise.reject(new DOMException("ya está", "InvalidStateError")) },
      configurable: true,
    });
    backend({
      "GET /api/v1/auth/llave": () => Response.json([{ ...LLAVE, nombre }]),
      "POST /api/v1/auth/llave/alta/opciones": () => Response.json({ opciones: OPCIONES_DE_ALTA }),
    });
    mostrar();
  }

  it("si el navegador avisa que ya tiene llave, reconoce la de su mismo nombre", async () => {
    dispositivoConLlave(nombreDelDispositivo(navigator.userAgent));

    fireEvent.click(await screen.findByRole("button", { name: "Es otro equipo: agregarlo" }));

    expect(await screen.findByText(/La llave ya funciona en este dispositivo/)).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Es otro equipo: agregarlo" })).toBeNull();
    expect(esDeEsteDispositivo(LLAVE.credencial_id)).toBe(true);
  });

  it("control: si no hay ninguna con su nombre, lo dice y no marca ninguna", async () => {
    dispositivoConLlave("Otro nombre");

    fireEvent.click(await screen.findByRole("button", { name: "Agregar este dispositivo" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Este dispositivo ya tiene una llave en esta cuenta.");
    // AC-35: el error se ve como error, no como texto común.
    expect(screen.getByRole("alert").className).toContain("aviso-error");
    expect(esDeEsteDispositivo(LLAVE.credencial_id)).toBe(false);
  });

  it.each([
    ["sin red", () => Promise.reject(new TypeError("sin red")), SIN_CONEXION],
    [
      "con un 503",
      async () => Response.json({ codigo: "x", mensaje: "x", id_correlacion: "c" }, { status: 503 }),
      SERVICIO_NO_DISPONIBLE,
    ],
  ])("%s al leer las llaves lo dice y deja reintentar", async (_, falla, mensaje) => {
    let intentos = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        intentos += 1;
        return intentos === 1 ? falla() : Response.json([LLAVE]);
      }),
    );
    mostrar();

    expect((await screen.findByRole("alert")).textContent).toBe(mensaje);
    expect(screen.getByRole("alert").className).toContain("aviso-error");
    expect(screen.queryByRole("button", { name: "Agregar este dispositivo" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByRole("button", { name: "Agregar este dispositivo" })).not.toBeNull();
    // Control: con la lectura bien, ni el error ni el botón.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("confirmar no es un paso aparte: no hay botón, con llaves o sin ellas", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([]) });
    const sinLlaves = mostrar();

    expect(await screen.findByRole("button", { name: "Activar con PIN, huella o cara" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar que soy yo" })).toBeNull();
    sinLlaves.unmount();

    backend({ "GET /api/v1/auth/llave": () => Response.json([LLAVE]) });
    mostrar();

    expect(await screen.findByRole("button", { name: "Quitar" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar que soy yo" })).toBeNull();
  });
});

describe("informe de fricción, tanda 7", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", { value: {}, configurable: true });
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("AC-14: «Quitar» no borra la llave hasta confirmarlo en la hoja, y «Volver» no borra nada", async () => {
    const borrar = vi.fn(() => new Response(null, { status: 204 }));
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: borrar,
    });
    mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));
    expect(await screen.findByRole("dialog", { name: "¿Quitar la llave de Mi PC?" })).not.toBeNull();
    expect(borrar).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Volver" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(borrar).not.toHaveBeenCalled();

    await quitarConfirmando();
    expect(await screen.findByText("Listo: la llave se quitó.")).not.toBeNull();
    expect(borrar).toHaveBeenCalledOnce();
  });

  it("AC-12: con una llave del mismo nombre que este equipo y sin su marca, no invita a agregarlo", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([{ ...LLAVE, nombre: nombreDelDispositivo(navigator.userAgent) }]) });
    mostrar();

    expect(await screen.findByText(/no hace falta hacer nada: la llave se confirma al usarla/)).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Agregar este dispositivo" })).toBeNull();
    // Si de verdad es otro equipo, se puede agregar, pero no como la acción principal.
    expect(screen.getByRole("button", { name: "Es otro equipo: agregarlo" }).className).toBe("secundario");
  });

  it("AC-12, control: con una llave de otro nombre sigue ofreciendo agregar este dispositivo", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([{ ...LLAVE, nombre: "Otro nombre" }]) });
    mostrar();

    expect((await screen.findByRole("button", { name: "Agregar este dispositivo" })).className).toBe("");
    expect(screen.queryByText(/no hace falta hacer nada/)).toBeNull();
  });
});
