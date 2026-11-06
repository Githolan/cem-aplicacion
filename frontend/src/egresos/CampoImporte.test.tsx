import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CampoImporte } from "./CampoImporte";

afterEach(cleanup);

function Campo({ inicial, alCambiar }: { inicial: string; alCambiar: (texto: string) => void }) {
  const [valor, setValor] = useState(inicial);
  return (
    <>
      <label htmlFor="importe">Importe</label>
      <CampoImporte
        id="importe"
        valor={valor}
        deshabilitado={false}
        alCambiar={(texto) => {
          setValor(texto);
          alCambiar(texto);
        }}
      />
    </>
  );
}

const campo = () => screen.getByLabelText("Importe") as HTMLInputElement;

describe("campo de importe", () => {
  it("abre el teclado numérico del dispositivo", () => {
    render(<Campo inicial="" alCambiar={vi.fn()} />);
    expect(campo().inputMode).toBe("decimal");
  });

  it("al salir muestra los centavos en el formato del idioma", () => {
    render(<Campo inicial="" alCambiar={vi.fn()} />);
    fireEvent.focus(campo());
    fireEvent.change(campo(), { target: { value: "120000" } });
    expect(campo().value).toBe("120000");
    fireEvent.blur(campo());
    expect(campo().value).toBe("120.000,00");
  });

  it("el punto del teclado numérico también sirve de coma", () => {
    render(<Campo inicial="" alCambiar={vi.fn()} />);
    fireEvent.focus(campo());
    fireEvent.change(campo(), { target: { value: "1234.5" } });
    fireEvent.blur(campo());
    expect(campo().value).toBe("1.234,50");
  });

  it("deja ver cómo leyó un punto de miles", () => {
    render(<Campo inicial="" alCambiar={vi.fn()} />);
    fireEvent.focus(campo());
    fireEvent.change(campo(), { target: { value: "1.234" } });
    fireEvent.blur(campo());
    expect(campo().value).toBe("1.234,00");
  });

  it("al volver al campo se sigue escribiendo sobre lo escrito", () => {
    render(<Campo inicial="120000" alCambiar={vi.fn()} />);
    expect(campo().value).toBe("120.000,00");
    fireEvent.focus(campo());
    expect(campo().value).toBe("120000");
  });

  it("salir del campo no cambia lo escrito: el formulario conserva la clave del pedido", () => {
    const alCambiar = vi.fn();
    render(<Campo inicial="120000" alCambiar={alCambiar} />);
    fireEvent.focus(campo());
    fireEvent.blur(campo());
    expect(alCambiar).not.toHaveBeenCalled();
  });

  it("control: lo que no es un importe queda como se escribió, para corregirlo", () => {
    render(<Campo inicial="" alCambiar={vi.fn()} />);
    fireEvent.focus(campo());
    fireEvent.change(campo(), { target: { value: "12,345,6" } });
    fireEvent.blur(campo());
    expect(campo().value).toBe("12,345,6");
  });
});
