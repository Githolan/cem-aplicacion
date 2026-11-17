import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { ComandoNoDisponible } from "./ComandoNoDisponible";

// En lugar de un botón que no se puede usar: una línea con qué falta y dónde se resuelve (criterio 8).

afterEach(() => cleanup());

describe("ComandoNoDisponible", () => {
  it("dice qué falta y lleva adonde se resuelve, sin ningún botón", () => {
    const { container } = render(
      <MemoryRouter>
        <ComandoNoDisponible motivo="Falta el seguro vigente del proveedor." resolver={{ texto: "Abrir su ficha", a: "/egresos/proveedores" }} />
      </MemoryRouter>,
    );
    const linea = container.querySelector("p.comando-no-disponible");
    expect(linea?.textContent).toBe("Falta el seguro vigente del proveedor. Abrir su ficha");
    expect(screen.getByRole("link", { name: "Abrir su ficha" }).getAttribute("href")).toBe("/egresos/proveedores");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("sin enlace, solo el motivo; en un renglón, como span", () => {
    const { container } = render(
      <MemoryRouter>
        <ComandoNoDisponible motivo="Lo coteja otra persona del Consejo." enLinea />
      </MemoryRouter>,
    );
    expect(container.querySelector("span.comando-no-disponible")?.textContent).toBe("Lo coteja otra persona del Consejo.");
    expect(screen.queryByRole("link")).toBeNull();
    // Control: no queda un párrafo.
    expect(container.querySelector("p")).toBeNull();
  });
});
