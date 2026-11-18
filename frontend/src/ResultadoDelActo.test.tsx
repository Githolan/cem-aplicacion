import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { type Resultado, ResultadoDelActo } from "./ResultadoDelActo";

// El resultado sale donde se actuó, una vez, y se va con el acto siguiente (tanda 1, punto 7).

function Tarjeta() {
  const [resultado, setResultado] = useState<Resultado | null>(null);
  return (
    <main>
      <section aria-label="Contacto">
        <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
        <button type="button" onClick={() => setResultado({ texto: "Listo: quedó corregido.", ok: true })}>
          Corregir
        </button>
      </section>
      <button type="button" className="secundario">
        Otra cosa
      </button>
    </main>
  );
}

afterEach(() => cleanup());

describe("ResultadoDelActo", () => {
  it("aparece donde se actuó, con el foco, y se va al tocar el botón siguiente", () => {
    render(<Tarjeta />);
    expect(screen.getByRole("status").textContent).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Corregir" }));
    const listo = screen.getByText("Listo: quedó corregido.");
    expect(document.activeElement).toBe(listo);
    expect(listo.closest("section")?.getAttribute("aria-label")).toBe("Contacto");
    fireEvent.click(screen.getByRole("button", { name: "Otra cosa" }));
    expect(screen.queryByText("Listo: quedó corregido.")).toBeNull();
    // Control: el mismo acto otra vez lo vuelve a dar, una sola vez.
    fireEvent.click(screen.getByRole("button", { name: "Corregir" }));
    expect(screen.getAllByText("Listo: quedó corregido.")).toHaveLength(1);
  });
});
