import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PedirCodigo } from "./PedirCodigo";

const LLAVE = { id: "1", nombre: "Mi PC", creada_en: "2026-11-04T10:00:00Z", ultimo_uso_en: null };
const OPCIONES = { opciones: { challenge: "AAEC", allowCredentials: [{ id: "AwQF", type: "public-key" }] } };
const VERIFICADO = {
  verificado_en: "2026-11-04T10:00:00Z",
  vale_hasta: "2026-11-04T10:10:00Z",
  codigos_recuperacion_restantes: 10,
};
const bytes = () => Uint8Array.from([1, 2, 3]).buffer;
const CREDENCIAL = {
  id: "AQID",
  rawId: bytes(),
  type: "public-key",
  getClientExtensionResults: () => ({}),
  response: { clientDataJSON: bytes(), authenticatorData: bytes(), signature: bytes(), userHandle: null },
};

type Respuestas = Record<string, () => Response>;

function backend(respuestas: Respuestas) {
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

function dispositivo(get: () => Promise<unknown>) {
  vi.stubGlobal("PublicKeyCredential", class {});
  Object.defineProperty(navigator, "credentials", { value: { get }, configurable: true });
}

function mostrar() {
  const alVerificar = vi.fn();
  render(<PedirCodigo acto="aprobar la orden" alVerificar={alVerificar} alCancelar={() => undefined} />);
  return alVerificar;
}

describe("confirmar con la llave", () => {
  beforeEach(() => {
    document.cookie = "__Host-cem_csrf=x";
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("con llave ofrece el gesto y sigue con el mismo pedido", async () => {
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      "POST /api/v1/auth/llave/verificar/opciones": () => Response.json(OPCIONES),
      "POST /api/v1/auth/llave/verificar": () => Response.json(VERIFICADO),
    });
    const get = vi.fn(async () => CREDENCIAL);
    dispositivo(get);
    const alVerificar = mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Confirmar con tu PIN o huella" }));

    await vi.waitFor(() => expect(alVerificar).toHaveBeenCalledOnce());
    expect(get).toHaveBeenCalledOnce();
  });

  it("si la persona cancela, lo dice y no sigue", async () => {
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      "POST /api/v1/auth/llave/verificar/opciones": () => Response.json(OPCIONES),
    });
    dispositivo(async () => {
      throw new DOMException("cancelado", "NotAllowedError");
    });
    const alVerificar = mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Confirmar con tu PIN o huella" }));

    expect((await screen.findByRole("alert")).textContent).toContain("No se confirmó");
    expect(alVerificar).not.toHaveBeenCalled();
  });

  it("si el backend rechaza la firma, no sigue", async () => {
    backend({
      "GET /api/v1/auth/llave": () => Response.json([LLAVE]),
      "POST /api/v1/auth/llave/verificar/opciones": () => Response.json(OPCIONES),
      "POST /api/v1/auth/llave/verificar": () =>
        Response.json({ codigo: "segundo_factor_invalido", mensaje: "x", id_correlacion: "c" }, { status: 400 }),
    });
    dispositivo(async () => CREDENCIAL);
    const alVerificar = mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Confirmar con tu PIN o huella" }));

    expect((await screen.findByRole("alert")).textContent).toContain("No pudimos confirmar tu llave");
    expect(alVerificar).not.toHaveBeenCalled();
  });

  it("deja usar un código en su lugar", async () => {
    backend({ "GET /api/v1/auth/llave": () => Response.json([LLAVE]) });
    dispositivo(async () => CREDENCIAL);
    mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Usar un código" }));

    expect(screen.getByLabelText("Código")).not.toBeNull();
  });

  it("sin llaves muestra el código, como siempre", async () => {
    const consulta = vi.fn(() => Response.json([]));
    backend({ "GET /api/v1/auth/llave": consulta });
    dispositivo(async () => CREDENCIAL);
    mostrar();

    await vi.waitFor(() => expect(consulta).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Código")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar con tu PIN o huella" })).toBeNull();
  });

  function Alta() {
    return <p>Alta con vuelta a {(useLocation().state as { volver: string }).volver}</p>;
  }

  function mostrarEnRuta() {
    const pantalla = <PedirCodigo acto="aprobar la orden" alVerificar={() => undefined} alCancelar={() => undefined} />;
    render(
      <MemoryRouter initialEntries={["/egresos/revision"]}>
        <Routes>
          <Route path="/egresos/revision" element={pantalla} />
          <Route path="/cuenta/segundo-factor" element={<Alta />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  function mandarCodigo() {
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verificar y seguir" }));
  }

  it("sin alta del segundo factor lleva a darla de alta y a volver acá", async () => {
    backend({
      "POST /api/v1/auth/totp/verificar": () =>
        Response.json({ codigo: "segundo_factor_sin_alta", mensaje: "x", id_correlacion: "c" }, { status: 409 }),
    });
    mostrarEnRuta();
    mandarCodigo();

    fireEvent.click(await screen.findByRole("link", { name: "Activala ahora" }));

    expect(await screen.findByText("Alta con vuelta a /egresos/revision")).not.toBeNull();
  });

  it("un código equivocado no manda a ninguna parte", async () => {
    backend({
      "POST /api/v1/auth/totp/verificar": () =>
        Response.json({ codigo: "segundo_factor_invalido", mensaje: "x", id_correlacion: "c" }, { status: 400 }),
    });
    mostrarEnRuta();
    mandarCodigo();

    expect((await screen.findByRole("alert")).textContent).toContain("El código no es válido");
    expect(screen.queryByRole("link", { name: "Activala ahora" })).toBeNull();
  });
});
