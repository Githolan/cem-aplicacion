import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { CampoCuit, CUIT_INVALIDO, cuitConError, cuitValido } from "./CampoCuit";

afterEach(cleanup);

describe("dígito verificador del CUIT (comentario 27)", () => {
  it("acepta los CUIT con el verificador bien, incluido el que da 11 y pasa a 0", () => {
    // 20123456786: el verificador da 6. 30000000007: da 7. 20000000001: da 1.
    for (const cuit of ["20123456786", "30000000007", "20000000001", "30712345671"]) expect(cuitValido(cuit)).toBe(true);
    // 2300000000 da resto 11: el verificador es 0.
    expect(cuitValido("23000000000")).toBe(true);
  });

  it("control: el mismo número con otro verificador no pasa, ni el que da 10", () => {
    for (const cuit of ["20123456787", "30000000008", "30712345679", "23000000001"]) expect(cuitValido(cuit)).toBe(false);
    // 2000000001 da 10 en el cálculo: ningún verificador sirve, igual que en `nucleo.cuit_valido`.
    for (let d = 0; d <= 9; d++) expect(cuitValido(`2000000001${d}`)).toBe(false);
    // Control de la vuelta: con otra base, exactamente un verificador sirve.
    expect([...Array(10).keys()].filter((d) => cuitValido(`2000000003${d}`))).toEqual([6]);
  });

  it("solo marca el error con los 11 números escritos: mientras se escribe, no", () => {
    expect(cuitConError("2012345678")).toBe(false);
    expect(cuitConError("20-12345678-6")).toBe(false);
    expect(cuitConError("20123456787")).toBe(true);
    expect(cuitConError("20123456786")).toBe(false);
  });
});

function Campo() {
  const [valor, setValor] = useState("");
  return <CampoCuit id="cuit" valor={valor} deshabilitado={false} alCambiar={setValor} />;
}

describe("<CampoCuit>", () => {
  it("con el verificador mal lo dice en el mismo campo, y se va al corregirlo", () => {
    render(<Campo />);
    const campo = screen.getByLabelText("CUIT (11 números, sin guiones)");
    fireEvent.change(campo, { target: { value: "2012345678" } });
    expect(screen.queryByText(CUIT_INVALIDO)).toBeNull();
    fireEvent.change(campo, { target: { value: "20123456787" } });
    expect(screen.getByRole("alert").textContent).toBe(CUIT_INVALIDO);
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(campo.getAttribute("aria-describedby")).toBe("cuit-error");
    fireEvent.change(campo, { target: { value: "20123456786" } });
    expect(screen.queryByText(CUIT_INVALIDO)).toBeNull();
    expect(campo.getAttribute("aria-invalid")).toBe("false");
  });
});
