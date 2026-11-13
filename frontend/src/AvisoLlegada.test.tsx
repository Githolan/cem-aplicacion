import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { BrowserRouter, Link, MemoryRouter, Navigate, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { AvisoLlegada } from "./AvisoLlegada";

// El aviso llega en `state.aviso` de la navegación: se muestra en la pantalla de llegada, sigue si
// una redirección (`replace`) lleva a otra, y se va con la navegación siguiente sin reaparecer.

const AVISO = "Listo: sesión abierta.";

function Pantallas() {
  return (
    <>
      <AvisoLlegada />
      <Routes>
        <Route
          path="/"
          element={
            <>
              <h1>Inicio</h1>
              <Link to="/otra">A otra</Link>
              <Link to="/redirige" state={{ aviso: AVISO }}>
                Con aviso y redirección
              </Link>
              <Link to="/otra" state={{ renovar: true }}>
                Con otro estado
              </Link>
            </>
          }
        />
        <Route
          path="/otra"
          element={
            <>
              <h1>Otra</h1>
              <Link to="/">Al inicio</Link>
            </>
          }
        />
        <Route path="/redirige" element={<Navigate to="/otra" replace />} />
      </Routes>
    </>
  );
}

function abrir(entrada: string | { pathname: string; state: unknown }) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[entrada]}>
        <Pantallas />
      </MemoryRouter>
    </StrictMode>,
  );
}

function abrirEnElNavegador() {
  return render(
    <StrictMode>
      <BrowserRouter>
        <Pantallas />
      </BrowserRouter>
    </StrictMode>,
  );
}

/** El `state` de React Router, tal como queda guardado en el historial del navegador. */
function estadoGuardado(): unknown {
  return (window.history.state as { usr?: unknown } | null)?.usr;
}

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("AvisoLlegada", () => {
  it("muestra el aviso que trae la navegación, en una región que se anuncia", async () => {
    abrir({ pathname: "/", state: { aviso: AVISO } });

    const aviso = await screen.findByText(AVISO);
    expect(aviso.closest('[role="status"]')?.getAttribute("aria-live")).toBe("polite");
  });

  it("control: sin aviso en la navegación no muestra nada, y la región queda vacía", () => {
    abrir("/");

    expect(screen.getByRole("status").textContent).toBe("");
  });

  it.each([
    ["no es texto", { aviso: 3 }],
    ["trae otra cosa", { renovar: true }],
  ])("un estado que %s no es un aviso", (_, state) => {
    abrir({ pathname: "/", state });

    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("se va con la navegación siguiente y no reaparece al volver", async () => {
    abrir({ pathname: "/", state: { aviso: AVISO } });
    await screen.findByText(AVISO);

    fireEvent.click(screen.getByRole("link", { name: "A otra" }));
    expect(await screen.findByRole("heading", { name: "Otra" })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();

    fireEvent.click(screen.getByRole("link", { name: "Al inicio" }));
    expect(await screen.findByRole("heading", { name: "Inicio" })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
  });

  it("sigue a la vista si una redirección (`replace`) lleva a otra pantalla", async () => {
    abrir("/");

    fireEvent.click(screen.getByRole("link", { name: "Con aviso y redirección" }));

    expect(await screen.findByRole("heading", { name: "Otra" })).toBeTruthy();
    expect(screen.getByText(AVISO)).toBeTruthy();
  });

  it("no pisa otro estado de la navegación, como `renovar`", async () => {
    abrirEnElNavegador();

    fireEvent.click(screen.getByRole("link", { name: "Con otro estado" }));

    expect(await screen.findByRole("heading", { name: "Otra" })).toBeTruthy();
    expect(estadoGuardado()).toEqual({ renovar: true });
  });

  it("se borra del historial apenas se lee: una recarga no lo repite", async () => {
    window.history.replaceState({ usr: { aviso: AVISO }, key: "k", idx: 0 }, "", "/");
    expect(estadoGuardado()).toEqual({ aviso: AVISO });

    abrirEnElNavegador();

    expect(await screen.findByText(AVISO)).toBeTruthy();
    await waitFor(() => expect(estadoGuardado() ?? null).toBeNull());

    // "Recarga": se desmonta todo y se vuelve a montar sobre el mismo historial.
    cleanup();
    abrirEnElNavegador();

    expect(screen.getByRole("heading", { name: "Inicio" })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
  });

  it("no reaparece con el botón de volver del navegador", async () => {
    window.history.replaceState({ usr: { aviso: AVISO }, key: "k", idx: 0 }, "", "/");
    abrirEnElNavegador();
    await screen.findByText(AVISO);

    fireEvent.click(screen.getByRole("link", { name: "A otra" }));
    await screen.findByRole("heading", { name: "Otra" });

    act(() => window.history.back());

    expect(await screen.findByRole("heading", { name: "Inicio" })).toBeTruthy();
    expect(screen.queryByText(AVISO)).toBeNull();
  });
});
