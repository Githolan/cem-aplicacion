import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { abrirHoja } from "./pilaDeHojas";

function hoja(encima = false) {
  const fondo = document.createElement("div");
  const boton = document.createElement("button");
  boton.className = "secundario"; // solo foco: no es una pantalla con su botón principal
  fondo.append(boton);
  document.body.append(fondo);
  return { fondo, boton, abrir: () => abrirHoja({ fondo, encima, enfocar: () => boton.focus() }) };
}

describe("pila de hojas", () => {
  let pagina: HTMLButtonElement;

  beforeEach(() => {
    pagina = document.createElement("button");
    pagina.className = "secundario";
    document.body.append(pagina);
    pagina.focus();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("la que se monta después queda arriba y, al cerrarse, la de abajo vuelve a usarse", () => {
    const primera = hoja();
    const cerrarPrimera = primera.abrir();
    const segunda = hoja();
    const cerrarSegunda = segunda.abrir();

    expect(primera.fondo.inert).toBe(true);
    expect(segunda.fondo.inert).toBeFalsy();
    expect(document.activeElement).toBe(segunda.boton);

    cerrarSegunda();
    expect(primera.fondo.inert).toBe(false);
    expect(document.activeElement).toBe(primera.boton);

    cerrarPrimera();
    expect(pagina.inert).toBe(false);
    expect(document.activeElement).toBe(pagina);
  });

  it("una hoja que se monta debajo de la de encima no le saca el lugar ni el foco", () => {
    const entrar = hoja(true);
    const cerrarEntrar = entrar.abrir();
    const confirmar = hoja();
    const cerrarConfirmar = confirmar.abrir();

    expect(entrar.fondo.inert).toBeFalsy();
    expect(confirmar.fondo.inert).toBe(true);
    expect(document.activeElement).toBe(entrar.boton);

    cerrarEntrar();
    expect(confirmar.fondo.inert).toBe(false);
    expect(document.activeElement).toBe(confirmar.boton);

    cerrarConfirmar();
    expect(document.activeElement).toBe(pagina);
  });

  it("si la de abajo cambia, la de encima no se entera y el foco de la página no se pierde", () => {
    const confirmar = hoja();
    const cerrarConfirmar = confirmar.abrir();
    const entrar = hoja(true);
    const cerrarEntrar = entrar.abrir();

    cerrarConfirmar();
    const fallo = hoja();
    const cerrarFallo = fallo.abrir();
    expect(entrar.fondo.inert).toBeFalsy();
    expect(fallo.fondo.inert).toBe(true);
    expect(pagina.inert).toBe(true);
    expect(document.activeElement).toBe(entrar.boton);

    cerrarEntrar();
    cerrarFallo();
    expect(pagina.inert).toBe(false);
    expect(document.activeElement).toBe(pagina);
  });

  it("si la de abajo se cierra sola, la de encima sigue igual", () => {
    const confirmar = hoja();
    const cerrarConfirmar = confirmar.abrir();
    const entrar = hoja(true);
    const cerrarEntrar = entrar.abrir();

    cerrarConfirmar();
    expect(entrar.fondo.inert).toBeFalsy();
    expect(pagina.inert).toBe(true);
    expect(document.activeElement).toBe(entrar.boton);

    cerrarEntrar();
    expect(pagina.inert).toBe(false);
  });

  it("no suelta lo que ya estaba inerte antes de abrir", () => {
    const ajeno = document.createElement("div");
    ajeno.inert = true;
    document.body.append(ajeno);

    hoja().abrir()();

    expect(ajeno.inert).toBe(true);
  });
});
