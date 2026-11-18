import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BOTON_ENTRAR_PARA_ACTIVAR, ENTRAR_PARA_ACTIVAR } from "./altaDeConfirmacion";
import { FALTA_ACTIVAR, FaltaConfirmacion } from "./FaltaConfirmacion";

const C1 = "c1c1c1c1-0000-4000-8000-000000000001";
const SIN_NADA = { activo: false, llaves: 0, codigos_recuperacion_restantes: 0, verificado_en: null };
/** La sesión, con el plazo para activar la confirmación (FL-CTA-01, app 0110). */
const sesion = (minutos: number) => () =>
  Response.json({
    persona_id: "0199e3a2-1234-7abc-8def-0123456789ab", nombre: "Ana", correo: "ana@ejemplo.com",
    sesion_id: "0199e3a2-1234-7abc-8def-0123456789ac", vence_en: "2099-01-01T00:00:00Z",
    alta_confirmacion_hasta: new Date(Date.now() + minutos * 60_000).toISOString(),
  });

type Respuestas = Record<string, () => Response>;

function backend(acciones: readonly string[], respuestas: Respuestas = {}) {
  const simulado = vi.fn(async (pedido: Request) => {
    const camino = new URL(pedido.url).pathname;
    if (camino === "/api/v1/me/consorcios") return Response.json([{ id: C1, nombre: "Edificio SINTETICO", vinculos: [], monedas: ["ARS"] }]);
    if (camino === `/api/v1/consorcios/${C1}/mis-acciones`) return Response.json({ acciones });
    const lista: Respuestas = { "GET /api/v1/auth/totp": () => Response.json(SIN_NADA), "GET /api/v1/auth/sesion": sesion(5), ...respuestas };
    const respuesta = lista[`${pedido.method} ${camino}`];
    if (respuesta === undefined) throw new Error(`pedido no previsto: ${pedido.method} ${camino}`);
    return respuesta();
  });
  vi.stubGlobal("fetch", simulado);
  return simulado;
}

const leyoLaConfirmacion = (simulado: ReturnType<typeof backend>) =>
  simulado.mock.calls.some(([p]) => new URL(p.url).pathname === "/api/v1/auth/totp");

function mostrar() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FaltaConfirmacion />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("activar la confirmación desde el inicio (FL-CTA-01, pregunta 10)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it.each([["egresos.revisar_orden"], ["egresos.ejecutar_pago"], ["egresos.conciliar_pago"]])(
    "a quien puede %s y no tiene ningún método se lo ofrece, para activarla ahí mismo",
    async (accion) => {
      backend(["consorcios.mis", accion]);
      mostrar();

      expect(await screen.findByText(FALTA_ACTIVAR)).toBeTruthy();
      // Sin llave en este navegador, la aplicación de códigos es la acción principal.
      const usar = screen.getByRole("button", { name: "Usar una aplicación de códigos" });
      expect(usar.className).not.toContain("secundario");
    },
  );

  it("control: a quien no aprueba, ni paga, ni concilia, no se lo ofrece ni pregunta", async () => {
    const simulado = backend(["consorcios.mis", "egresos.preparar_orden", "padron.ver"]);
    mostrar();

    await waitFor(() => expect(simulado.mock.calls.some(([p]) => new URL(p.url).pathname.endsWith("/mis-acciones"))).toBe(true));
    await new Promise((listo) => setTimeout(listo));
    expect(screen.queryByText(FALTA_ACTIVAR)).toBeNull();
    expect(leyoLaConfirmacion(simulado)).toBe(false);
  });

  it.each([
    ["con la aplicación", { ...SIN_NADA, activo: true }],
    ["con una llave", { ...SIN_NADA, llaves: 1 }],
  ])("control: %s ya activada, no se ofrece", async (_caso, estado) => {
    const simulado = backend(["egresos.revisar_orden"], { "GET /api/v1/auth/totp": () => Response.json(estado) });
    mostrar();

    await waitFor(() => expect(leyoLaConfirmacion(simulado)).toBe(true));
    await new Promise((listo) => setTimeout(listo));
    expect(screen.queryByText(FALTA_ACTIVAR)).toBeNull();
  });

  it("activa la aplicación en una ventana encima, muestra los códigos y después dice que quedó activada", async () => {
    backend(["egresos.revisar_orden"], {
      "POST /api/v1/auth/totp/alta": () => Response.json({ secreto: "ABCDEFGHIJKLMNOP", uri: "otpauth://totp/CEM:ana?secret=ABCDEFGHIJKLMNOP" }),
      "POST /api/v1/auth/totp/confirmar": () => Response.json({ codigos: ["k7m2q-x9p4t", "a2b3c-d4e5f"] }),
    });
    mostrar();

    fireEvent.click(await screen.findByRole("button", { name: "Usar una aplicación de códigos" }));
    expect(await screen.findByRole("heading", { name: "Cargar la cuenta en la aplicación" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Código de la aplicación"), { target: { value: "123456" } });

    expect(await screen.findByRole("heading", { name: "Guardar los códigos de recuperación" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Códigos guardados: seguir" }));
    expect(await screen.findByText("Listo: la confirmación con código quedó activada.")).toBeTruthy();
    expect(screen.queryByText(FALTA_ACTIVAR)).toBeNull();
  });

  it("FL-CTA-01 (tanda 9): con el plazo de la sesión vencido, dice que primero hay que entrar de nuevo, sin los botones de activar", async () => {
    backend(["egresos.revisar_orden"], { "GET /api/v1/auth/sesion": sesion(-1) });
    mostrar();

    expect(await screen.findByText(ENTRAR_PARA_ACTIVAR)).toBeTruthy();
    expect(screen.getByText(FALTA_ACTIVAR)).toBeTruthy();
    expect(screen.getByRole("button", { name: BOTON_ENTRAR_PARA_ACTIVAR })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Usar una aplicación de códigos" })).toBeNull();
  });

  it("FL-CTA-01, control: dentro del plazo, se ofrece activar", async () => {
    backend(["egresos.revisar_orden"], { "GET /api/v1/auth/sesion": sesion(5) });
    mostrar();

    expect(await screen.findByRole("button", { name: "Usar una aplicación de códigos" })).toBeTruthy();
    expect(screen.queryByText(ENTRAR_PARA_ACTIVAR)).toBeNull();
  });

  it("si no se pudo saber, lo dice y deja reintentar", async () => {
    let falla = true;
    backend(["egresos.revisar_orden"], {
      "GET /api/v1/auth/totp": () =>
        falla
          ? Response.json({ codigo: "servicio_no_disponible", mensaje: "x", id_correlacion: "c" }, { status: 503 })
          : Response.json(SIN_NADA),
    });
    mostrar();

    expect(await screen.findByText(/No se pudo saber si la confirmación/)).toBeTruthy();
    falla = false;
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(FALTA_ACTIVAR)).toBeTruthy();
  });
});
