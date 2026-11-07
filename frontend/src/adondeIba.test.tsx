import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { guardarAdondeIba, olvidarAdondeIba, rutaPropia, tomarAdondeIba } from "./adondeIba";
import { useIrTrasEntrar } from "./sesion";
import { useAEntrar } from "./sesionEncima";

const ORIGEN = "https://cem.ejemplo.test";
const CLAVE = "cem.entrar.adonde_iba";

function Donde() {
  const { pathname, search } = useLocation();
  return <p>en {`${pathname}${search}`}</p>;
}

function Entrada() {
  const ir = useIrTrasEntrar();
  return (
    <button type="button" onClick={() => ir("Listo")}>
      Entrar
    </button>
  );
}

function Pantalla() {
  const aEntrar = useAEntrar();
  return (
    <button type="button" onClick={aEntrar}>
      Sin sesión
    </button>
  );
}

function abrir(inicial: string) {
  render(
    <MemoryRouter initialEntries={[inicial]}>
      <Routes>
        <Route path="/entrar" element={<Entrada />} />
        <Route path="/pantalla" element={<Pantalla />} />
        <Route path="*" element={<Donde />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(cleanup);

describe("rutaPropia", () => {
  it("acepta una ruta de la aplicación, con su consulta y sin el fragmento", () => {
    expect(rutaPropia("/pagos/ficha/1?volver=2#token=x", ORIGEN)).toBe("/pagos/ficha/1?volver=2");
  });

  it.each([
    ["otro sitio", "https://malo.test/pagos"],
    ["sin esquema", "//malo.test/pagos"],
    ["con barra invertida", "/\\malo.test/pagos"],
    ["relativa", "pagos"],
    ["javascript", "javascript:alert(1)"],
    ["la entrada", "/entrar"],
    ["una invitación", "/invitacion"],
  ])("rechaza %s", (_, ruta) => {
    expect(rutaPropia(ruta, ORIGEN)).toBeNull();
  });
});

describe("volver después de entrar", () => {
  it("un 401 guarda la ruta y entrar vuelve a ella, una sola vez", () => {
    window.history.replaceState(null, "", "/pagos/ficha/1?x=2#token=secreto");
    abrir("/pantalla");
    fireEvent.click(screen.getByRole("button", { name: "Sin sesión" }));
    expect(window.sessionStorage.getItem(CLAVE)).toBe("/pagos/ficha/1?x=2");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(screen.getByText("en /pagos/ficha/1?x=2")).toBeTruthy();
    expect(window.sessionStorage.getItem(CLAVE)).toBeNull();
  });

  it("control: sin nada guardado, entrar va al inicio", () => {
    abrir("/entrar");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(screen.getByText("en /")).toBeTruthy();
  });

  it.each(["https://malo.test/pagos", "//malo.test/pagos", "/\\malo.test/pagos"])("lo guardado a mano no lleva a otro sitio: %s", (ruta) => {
    window.sessionStorage.setItem(CLAVE, ruta);
    abrir("/entrar");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(screen.getByText("en /")).toBeTruthy();
    expect(window.sessionStorage.getItem(CLAVE)).toBeNull();
  });

  it("en /entrar no se guarda nada", () => {
    window.history.replaceState(null, "", "/entrar");
    guardarAdondeIba();
    expect(tomarAdondeIba()).toBeNull();
  });

  it("salir de la cuenta lo olvida", () => {
    window.history.replaceState(null, "", "/pagos");
    guardarAdondeIba();
    olvidarAdondeIba();
    expect(tomarAdondeIba()).toBeNull();
  });
});
