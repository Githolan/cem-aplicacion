import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CargandoPagos } from "./CargandoPagos";

describe("mientras cargan los pagos", () => {
  it("avisa al lector de pantalla y muestra tres bloques", () => {
    const { container } = render(<CargandoPagos />);
    expect(screen.getByRole("status").textContent).toContain("Cargando los pagos…");
    expect(container.querySelectorAll(".bloques-carga__bloque[aria-hidden='true']")).toHaveLength(3);
  });
});
