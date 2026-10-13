import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Salud } from "./api/cliente";
import { renderizarEn } from "./pruebas/renderizar";

const SALUD: Salud = {
  estado: "ok",
  version: "0.1.0",
  sha_imagen: "0123456789abcdef0123456789abcdef01234567",
  sha_contratos: "fedcba9876543210fedcba9876543210fedcba98",
  version_contrato: "0.1.0",
};

function respuestaJson(cuerpo: unknown, estado: number): Response {
  return new Response(JSON.stringify(cuerpo), { status: estado, headers: { "Content-Type": "application/json" } });
}

describe("pantalla de inicio", () => {
  it("muestra el nombre del producto y el estado leído de /health", async () => {
    const fetchDePrueba = vi.fn<typeof globalThis.fetch>(async () => respuestaJson(SALUD, 200));
    renderizarEn("/", fetchDePrueba);

    expect(screen.getByRole("heading", { level: 1, name: "Consorcio en Mano" })).toBeInTheDocument();
    expect(await screen.findByText(/Servicio en línea/)).toHaveTextContent("versión 0.1.0");
    expect(screen.getByText("0123456")).toBeInTheDocument();

    const pedido = fetchDePrueba.mock.calls[0]?.[0];
    expect(pedido).toBeInstanceOf(Request);
    expect(new URL((pedido as Request).url).pathname).toBe("/health");
  });

  it("avisa cuando el backend no responde bien", async () => {
    const fetchDePrueba = vi.fn<typeof globalThis.fetch>(async () =>
      respuestaJson({ codigo: "no_disponible", mensaje: "caído" }, 503),
    );
    renderizarEn("/", fetchDePrueba);

    expect(await screen.findByText(/El servicio no responde/)).toHaveTextContent("503");
    expect(screen.queryByText(/Servicio en línea/)).not.toBeInTheDocument();
  });
});

describe("rutas", () => {
  it("una ruta inexistente muestra la página de no encontrada", () => {
    const fetchDePrueba = vi.fn<typeof globalThis.fetch>(async () => respuestaJson(SALUD, 200));
    renderizarEn("/no/existe", fetchDePrueba);

    expect(screen.getByRole("heading", { name: "No encontramos esa página" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver al inicio" })).toHaveAttribute("href", "/");
    expect(fetchDePrueba).not.toHaveBeenCalled();
  });
});
