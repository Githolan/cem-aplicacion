import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { SeccionLlave } from "./SeccionLlave";

const LLAVE = {
  id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
  nombre: "Mi PC",
  creada_en: "2026-11-04T10:00:00Z",
  ultimo_uso_en: null,
};
const VERIFICADO = {
  verificado_en: "2026-11-04T10:00:00Z",
  vale_hasta: "2026-11-04T10:10:00Z",
  codigos_recuperacion_restantes: 9,
};
const FALTA = () =>
  Response.json({ codigo: "segundo_factor_requerido", mensaje: "x", id_correlacion: "c" }, { status: 403 });

function mostrar(alTerminar: (aviso: string) => void = () => undefined) {
  return render(<SeccionLlave alCambiar={() => undefined} alCodigos={() => undefined} alTerminar={alTerminar} />);
}

function backend(respuestas: Record<string, () => Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (pedido: Request) => {
      const clave = `${pedido.method} ${new URL(pedido.url).pathname}`;
      const respuesta = respuestas[clave];
      if (respuesta === undefined) throw new Error(`pedido no previsto: ${clave}`);
      return respuesta();
    }),
  );
}

describe("sección de la llave", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
    vi.stubGlobal("PublicKeyCredential", class {});
    Object.defineProperty(navigator, "credentials", { value: {}, configurable: true });
  });

  afterEach(() => {
    cleanup();
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

    fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Usar un código" }));
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "k7m2q-x9p4t" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    expect(await screen.findByText("Listo: quitaste la llave.")).not.toBeNull();
    expect(borrar).toHaveBeenCalledTimes(2);
  });

  it("con la sesión ya verificada, quitar no pide nada más", async () => {
    const borrar = vi.fn(() => new Response(null, { status: 204 }));
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      [`DELETE /api/v1/auth/llave/${LLAVE.id}`]: borrar,
    });
    mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Quitar" }));

    expect(await screen.findByText("Listo: quitaste la llave.")).not.toBeNull();
    expect(borrar).toHaveBeenCalledOnce();
  });

  it("quien solo tiene llave puede confirmar con un código de recuperación", async () => {
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      "POST /api/v1/auth/totp/verificar": () => Response.json(VERIFICADO),
    });
    const alTerminar = vi.fn();
    mostrar(alTerminar);

    fireEvent.click(await screen.findByRole("button", { name: "Confirmar que soy yo" }));
    fireEvent.click(await screen.findByRole("button", { name: "Usar un código" }));
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "k7m2q-x9p4t" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));

    await waitFor(() => expect(alTerminar).toHaveBeenCalledWith("Listo: confirmaste que sos vos, por 10 minutos."));
  });

  it("si cancela la confirmación no da nada por terminado", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([LLAVE]) });
    const alTerminar = vi.fn();
    mostrar(alTerminar);

    fireEvent.click(await screen.findByRole("button", { name: "Confirmar que soy yo" }));
    // PedirCodigo cambia a la vista de la llave al leerlas: se espera a que esté antes de cancelar.
    await screen.findByRole("button", { name: "Usar un código" });
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(await screen.findByRole("button", { name: "Confirmar que soy yo" })).not.toBeNull();
    expect(alTerminar).not.toHaveBeenCalled();
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
    expect(screen.queryByRole("button", { name: "Agregar este dispositivo" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByRole("button", { name: "Agregar este dispositivo" })).not.toBeNull();
    // Control: con la lectura bien, ni el error ni el botón.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("sin llaves no ofrece confirmar", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([]) });
    mostrar();

    expect(await screen.findByRole("button", { name: "Activar en este dispositivo" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar que soy yo" })).toBeNull();
  });
});
