import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { CampoContrasena } from "./CampoContrasena";

afterEach(cleanup);

function abrir() {
  return render(
    <form>
      <label htmlFor="clave">Contraseña</label>
      <CampoContrasena id="clave" autoComplete="current-password" defaultValue="secreta-12345" />
    </form>,
  );
}

describe("CampoContrasena", () => {
  it("empieza oculta y el botón la muestra y la vuelve a ocultar", () => {
    abrir();
    const campo = screen.getByLabelText("Contraseña") as HTMLInputElement;
    expect(campo.type).toBe("password");

    fireEvent.click(screen.getByRole("button", { name: "Mostrar lo escrito" }));
    expect(campo.type).toBe("text");
    expect(campo.value).toBe("secreta-12345");
    const ocultar = screen.getByRole("button", { name: "Ocultar lo escrito" });
    expect(ocultar.textContent).toBe("Ocultar");

    fireEvent.click(ocultar);
    expect(campo.type).toBe("password");
  });

  it("el botón no envía el formulario y conserva lo demás del campo", () => {
    abrir();
    const boton = screen.getByRole("button", { name: "Mostrar lo escrito" });
    expect(boton.getAttribute("type")).toBe("button");
    expect(boton.getAttribute("aria-controls")).toBe("clave");
    expect(screen.getByLabelText("Contraseña").getAttribute("autocomplete")).toBe("current-password");
  });

  it("no tiene faltas de accesibilidad detectables por axe, oculta ni visible", async () => {
    const { container } = abrir();
    const reglas = { rules: { "color-contrast": { enabled: false } } };
    expect((await axe.run(container, reglas)).violations).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Mostrar lo escrito" }));
    expect((await axe.run(container, reglas)).violations).toEqual([]);
  });
});
