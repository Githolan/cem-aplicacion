import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CLASE_LECTURA_AMPLIADA,
  CLAVE_LECTURA_AMPLIADA,
  CONSULTA_MAS_CONTRASTE,
  aplicarLecturaAmpliada,
  preferenciaInicial,
} from "./lecturaAmpliada";
import { ModoLectura, NOMBRE_MODO_LECTURA } from "./ModoLectura";

// jsdom no trae matchMedia: se responde solo la consulta de contraste, y cualquier otra falla.
function sistemaPideMasContraste(pide: boolean) {
  vi.stubGlobal("matchMedia", (consulta: string) => {
    if (consulta !== CONSULTA_MAS_CONTRASTE) throw new Error(`consulta inesperada: ${consulta}`);
    return { matches: pide, media: consulta } as MediaQueryList;
  });
}

function almacenamientoBloqueado() {
  const bloqueado = () => {
    throw new DOMException("almacenamiento bloqueado", "SecurityError");
  };
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(bloqueado);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(bloqueado);
}

const claseActiva = () => document.documentElement.classList.contains(CLASE_LECTURA_AMPLIADA);
const boton = () => screen.getByRole("button", { name: NOMBRE_MODO_LECTURA });

async function sinFaltasDeAxe(contenedor: HTMLElement) {
  // jsdom no calcula estilos: el contraste se mide en el navegador (e2e/lectura-ampliada.e2e.ts).
  const resultado = await axe.run(contenedor, { rules: { "color-contrast": { enabled: false } } });
  expect(resultado.violations).toEqual([]);
}

beforeEach(() => {
  window.localStorage.clear();
  aplicarLecturaAmpliada(false);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("botón del modo lectura ampliada", () => {
  it("prende y apaga el modo con aria-pressed, y guarda cada elección", async () => {
    const { container } = render(<ModoLectura />);
    expect(boton().getAttribute("aria-pressed")).toBe("false");
    await sinFaltasDeAxe(container);

    fireEvent.click(boton());
    expect(boton().getAttribute("aria-pressed")).toBe("true");
    expect(claseActiva()).toBe(true);
    expect(window.localStorage.getItem(CLAVE_LECTURA_AMPLIADA)).toBe("1");
    await sinFaltasDeAxe(container);

    fireEvent.click(boton());
    expect(boton().getAttribute("aria-pressed")).toBe("false");
    expect(claseActiva()).toBe(false);
    expect(window.localStorage.getItem(CLAVE_LECTURA_AMPLIADA)).toBe("0");
  });

  it("el nombre accesible no cambia con el estado (lo dice aria-pressed)", () => {
    render(<ModoLectura />);
    fireEvent.click(boton());
    expect(boton().getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText(/no deja guardar/)).toBeNull();
  });

  it("arranca presionado si el modo ya estaba aplicado al cargar", () => {
    aplicarLecturaAmpliada(true);
    render(<ModoLectura />);
    expect(boton().getAttribute("aria-pressed")).toBe("true");
  });

  it("si el navegador no deja guardar, el modo igual funciona y lo avisa en pantalla", async () => {
    almacenamientoBloqueado();
    const { container } = render(<ModoLectura />);

    fireEvent.click(boton());

    expect(claseActiva()).toBe(true);
    expect(boton().getAttribute("aria-pressed")).toBe("true");
    const aviso = screen.getByText(/no deja guardar la preferencia/);
    expect(aviso.getAttribute("aria-live")).toBe("polite");
    await sinFaltasDeAxe(container);
  });
});

describe("preferencia inicial", () => {
  it("sin elección guardada sigue a prefers-contrast: more", () => {
    sistemaPideMasContraste(true);
    expect(preferenciaInicial()).toBe(true);
    sistemaPideMasContraste(false);
    expect(preferenciaInicial()).toBe(false);
  });

  it("la elección guardada vale sobre la del sistema, también la de apagado", () => {
    sistemaPideMasContraste(true);
    window.localStorage.setItem(CLAVE_LECTURA_AMPLIADA, "0");
    expect(preferenciaInicial()).toBe(false);

    sistemaPideMasContraste(false);
    window.localStorage.setItem(CLAVE_LECTURA_AMPLIADA, "1");
    expect(preferenciaInicial()).toBe(true);
  });

  it("si leer localStorage falla, usa la preferencia del sistema en vez de romper la página", () => {
    almacenamientoBloqueado();
    sistemaPideMasContraste(true);
    expect(preferenciaInicial()).toBe(true);
    sistemaPideMasContraste(false);
    expect(preferenciaInicial()).toBe(false);
  });

  it("un valor guardado desconocido se ignora y manda el sistema", () => {
    window.localStorage.setItem(CLAVE_LECTURA_AMPLIADA, "quizás");
    sistemaPideMasContraste(true);
    expect(preferenciaInicial()).toBe(true);
  });
});
