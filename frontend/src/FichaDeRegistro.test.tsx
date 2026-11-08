import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { FichaDeRegistro, FilaDeRegistro, useFichas } from "./FichaDeRegistro";

// La ficha de un registro y la fila que la abre, en una lista mínima: lo que cada pantalla arma
// con ellas (Padrón, Contactos, Invitaciones) se prueba en su propia página.

type Registro = { id: string; nombre: string; unidad: string };

const REGISTROS: Registro[] = [
  { id: "a", nombre: "Ana Ruiz", unidad: "1° A" },
  { id: "b", nombre: "Beto Pérez", unidad: "2° B" },
];

function Lista({ conAccion = false, ocupada = false }: { conAccion?: boolean; ocupada?: boolean }) {
  const fichas = useFichas<string>();
  const [hecho, setHecho] = useState<string | null>(null);
  const abierta = REGISTROS.find((r) => r.id === fichas.abierta);
  return (
    <main>
      <h1>Registros</h1>
      <p role="status">{hecho}</p>
      <ul className="filas-registro" aria-label="Registros">
        {REGISTROS.map((r) => (
          <li key={r.id}>
            <FilaDeRegistro
              titulo={r.nombre}
              resumen={r.unidad}
              alAbrir={() => fichas.abrir(r.id)}
              refFila={fichas.refFila(r.id)}
              abierta={fichas.abierta === r.id}
            />
          </li>
        ))}
      </ul>
      {abierta !== undefined && (
        <FichaDeRegistro
          titulo={abierta.nombre}
          alCerrar={() => fichas.cerrar()}
          ocupada={ocupada}
          datos={[
            { etiqueta: "Nombre", valor: abierta.nombre },
            { etiqueta: "Unidad", valor: abierta.unidad },
          ]}
        >
          {conAccion && (
            <p className="ficha-registro__acciones">
              <button
                type="button"
                onClick={() => {
                  setHecho(`Listo: ${abierta.nombre}.`);
                  fichas.cerrar(false);
                }}
              >
                Hacer
              </button>
            </p>
          )}
        </FichaDeRegistro>
      )}
    </main>
  );
}

const fila = (nombre: string) => screen.getByRole("button", { name: new RegExp(`^${nombre}( |$)`) });

/** Toca la fila como lo hace un navegador que no le da el foco al botón (Safari): sin `focus()`. */
function tocarSinFoco(nombre: string): HTMLElement {
  fireEvent.click(fila(nombre));
  return screen.getByRole("dialog", { name: nombre });
}

afterEach(() => cleanup());

describe("FilaDeRegistro y FichaDeRegistro", () => {
  it("la fila es un solo botón con el título y el resumen en su nombre", () => {
    render(<Lista />);
    const ana = fila("Ana Ruiz");
    expect(ana.tagName).toBe("BUTTON");
    expect(ana.getAttribute("type")).toBe("button");
    expect(ana.textContent).toBe("Ana Ruiz 1° A");
    expect(within(ana).queryAllByRole("button")).toEqual([]);
    expect(ana.getAttribute("aria-current")).toBeNull();
  });

  it("tocar la fila abre la ficha con todos sus datos, marca la fila y lleva el foco adentro", () => {
    render(<Lista />);
    const ana = fila("Ana Ruiz");
    ana.focus();
    fireEvent.click(ana);
    const ficha = screen.getByRole("dialog", { name: "Ana Ruiz" });
    expect(ficha.getAttribute("aria-modal")).toBe("true");
    const datos = [...ficha.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(datos).toEqual([
      ["Nombre", "Ana Ruiz"],
      ["Unidad", "1° A"],
    ]);
    expect(ficha.contains(document.activeElement)).toBe(true);
    expect(ana.getAttribute("aria-current")).toBe("true");
    // Sin acciones, solo se cierra.
    expect(within(ficha).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cerrar"]);
  });

  it("«Cerrar» la cierra y el foco vuelve a la fila, aunque el navegador no la hubiera enfocado", () => {
    render(<Lista />);
    const ficha = tocarSinFoco("Beto Pérez");
    fireEvent.click(within(ficha).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(fila("Beto Pérez"));
    expect(fila("Beto Pérez").getAttribute("aria-current")).toBeNull();
  });

  it("Escape la cierra y el foco vuelve a la fila", () => {
    render(<Lista />);
    const ficha = tocarSinFoco("Ana Ruiz");
    fireEvent.keyDown(within(ficha).getByRole("button", { name: "Cerrar" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(fila("Ana Ruiz"));
  });

  it("con un pedido en curso no se cierra: «Cerrar» deshabilitado y Escape sin efecto", () => {
    render(<Lista ocupada />);
    const ficha = tocarSinFoco("Ana Ruiz");
    expect((within(ficha).getByRole("button", { name: "Cerrar" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(ficha, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Ana Ruiz" })).toBeTruthy();
  });

  it("cerrada por un resultado (`cerrar(false)`), no le devuelve el foco a la fila", () => {
    render(<Lista conAccion />);
    const ficha = tocarSinFoco("Ana Ruiz");
    fireEvent.click(within(ficha).getByRole("button", { name: "Hacer" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("Listo: Ana Ruiz.");
    // Control: el foco no quedó en la fila; la pantalla decide adónde va (el aviso del resultado).
    expect(document.activeElement).not.toBe(fila("Ana Ruiz"));
  });

  it("lo de atrás queda inerte mientras la ficha está abierta, y vuelve al cerrarla", () => {
    const { container } = render(<Lista />);
    const ficha = tocarSinFoco("Ana Ruiz");
    expect(container.inert).toBe(true);
    fireEvent.click(within(ficha).getByRole("button", { name: "Cerrar" }));
    expect(container.inert).toBe(false);
  });

  it("no tiene faltas de accesibilidad detectables por axe, con la lista y con la ficha abierta", async () => {
    render(<Lista conAccion />);
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(document.body, reglas)).violations.map((v) => v.id)).toEqual([]);
    tocarSinFoco("Beto Pérez");
    expect((await axe.run(document.body, reglas)).violations.map((v) => v.id)).toEqual([]);
  });
});
