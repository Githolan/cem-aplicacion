import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DESTINOS_DEL_MENU, Navegacion, PersonaEnCabecera, prefijosSinExacta } from "./Navegacion";

// El menú (tanda 1 del informe de fricción, MEN-01, MEN-02, MEN-03, MEN-05 y MEN-06). Lo que alcanza
// la persona se fija acá: la consulta real se prueba con cada pantalla.

type Consulta = {
  porEdificio: ReadonlyMap<string, readonly string[]> | null;
  consultando: boolean;
  falla: string | null;
  reintentar: () => void;
  reintentando: boolean;
};

const consulta = vi.hoisted(() => ({ actual: null as unknown as Consulta }));

vi.mock("./permisos", async (original) => ({
  ...(await original<typeof import("./permisos")>()),
  useConsultaDeAcciones: () => consulta.actual,
}));

/** Todo lo que el menú puede ofrecer, en un edificio; y la consulta de proveedores en otro. */
const TODO: ReadonlyMap<string, readonly string[]> = new Map([
  [
    "e1",
    [
      "egresos.preparar_orden",
      "documentos.ver",
      "contactos.ver",
      "invitaciones.crear",
      "mandatos.ver",
      "padron.ver",
      "proveedores.autorizar",
      "liquidacion.preparar",
      "fiscalizacion.ver_panel",
    ],
  ],
  ["e2", ["proveedores.ver", "proveedores.autorizar"]],
]);

const sabido = (porEdificio = TODO): Consulta => ({ porEdificio, consultando: false, falla: null, reintentar: vi.fn(), reintentando: false });

function menuEn(ruta: string) {
  render(
    <MemoryRouter initialEntries={[ruta]}>
      <Navegacion />
    </MemoryRouter>,
  );
  return screen.getByRole("navigation", { name: "Secciones" });
}

/** Los textos de las entradas marcadas como la pantalla actual. */
const activas = (menu: HTMLElement) =>
  within(menu)
    .queryAllByRole("link")
    .filter((a) => a.getAttribute("aria-current") === "page")
    .map((a) => a.textContent);

beforeEach(() => {
  consulta.actual = sabido();
});

afterEach(() => cleanup());

describe("menú", () => {
  it("ninguna entrada es el comienzo de otra sin ser exacta (MEN-01)", () => {
    expect(prefijosSinExacta(DESTINOS_DEL_MENU)).toEqual([]);
    // Control: la prueba encuentra el par del defecto 37 si a «/proveedores» le falta `exacta`.
    const sinExacta = DESTINOS_DEL_MENU.map((d) => (d.ruta === "/proveedores" ? { ...d, exacta: false } : d));
    expect(prefijosSinExacta(sinExacta)).toEqual([["/proveedores", "/proveedores/quien-los-ve"]]);
    // Control: «Inicio» es el comienzo de todas; sin `exacta`, aparece con cada una.
    const sinInicio = DESTINOS_DEL_MENU.map((d) => (d.ruta === "/" ? { ...d, exacta: false } : d));
    expect(prefijosSinExacta(sinInicio)).toHaveLength(DESTINOS_DEL_MENU.length - 1);
  });

  it("en «Quién ve los proveedores» queda activa una sola entrada (defecto 37)", () => {
    const menu = menuEn("/proveedores/quien-los-ve");
    expect(activas(menu)).toEqual(["Quién ve los proveedores"]);
    cleanup();
    // Control: en la consulta, la consulta.
    expect(activas(menuEn("/proveedores"))).toEqual(["Proveedores y sus seguros"]);
    cleanup();
    // Control: las pantallas hijas de Pagos, sin entrada propia, siguen marcando «Pagos».
    expect(activas(menuEn("/pagos/consulta"))).toEqual(["Pagos"]);
  });

  it("las entradas de proveedores y de solicitudes no repiten el ícono de otra (MEN-05)", () => {
    const menu = menuEn("/");
    const icono = (nombre: string) => within(menu).getByRole("link", { name: nombre }).querySelector("svg")?.innerHTML;
    expect(icono("Quién ve los proveedores")).not.toBe(icono("Proveedores"));
    expect(icono("Solicitudes formales")).not.toBe(icono("Documentos"));
    // Control: la comparación distingue íconos iguales.
    expect(icono("Proveedores")).toBe(icono("Proveedores y sus seguros"));
  });

  it("en una pantalla de Gestión, «Inicio» queda marcada para el teléfono y la tablet (MEN-02)", () => {
    const menu = menuEn("/padron");
    const inicio = within(menu).getByRole("link", { name: "Inicio" });
    expect(inicio.classList.contains("navegacion__item--desde-inicio")).toBe(true);
    // En la PC la marca la entrada propia, una sola.
    expect(activas(menu)).toEqual(["Padrón del edificio"]);
    cleanup();
    // Control: en Pagos, que tiene su pestaña, «Inicio» no se marca.
    const enPagos = menuEn("/pagos");
    expect(within(enPagos).getByRole("link", { name: "Inicio" }).classList.contains("navegacion__item--desde-inicio")).toBe(false);
  });

  it("mientras se consulta no muestra un menú a medias, y aparece entero (MEN-06)", () => {
    consulta.actual = { ...sabido(), porEdificio: null, consultando: true };
    const menu = menuEn("/documentos");
    expect(menu.getAttribute("aria-busy")).toBe("true");
    expect(within(menu).queryAllByRole("link")).toEqual([]);
    cleanup();
    // Control: sabido, están todas.
    consulta.actual = sabido();
    expect(within(menuEn("/documentos")).getAllByRole("link").length).toBeGreaterThan(5);
  });

  it("si no se pudo leer, lo dice en el menú con «Reintentar», salvo en el inicio (MEN-06)", () => {
    const reintentar = vi.fn();
    consulta.actual = { porEdificio: null, consultando: false, falla: "Sin conexión.", reintentar, reintentando: false };
    const menu = menuEn("/documentos");
    expect(within(menu).getByRole("alert").textContent).toContain("No se pudo leer el menú: faltan secciones.");
    // Lo que no pide nada sigue a la vista.
    expect(within(menu).getAllByRole("link").map((a) => a.textContent)).toEqual(["Inicio", "Cuenta"]);
    fireEvent.click(within(menu).getByRole("button", { name: "Reintentar" }));
    expect(reintentar).toHaveBeenCalledOnce();
    cleanup();
    // En el inicio ya lo dice la pantalla: no se repite.
    expect(within(menuEn("/")).queryByRole("alert")).toBeNull();
  });
});

describe("cabecera", () => {
  it("el nombre de la persona no es otro camino a la cuenta (MEN-03)", () => {
    const { container } = render(
      <MemoryRouter>
        <PersonaEnCabecera nombre="Ana Ruiz" />
      </MemoryRouter>,
    );
    expect(screen.getByText("Ana Ruiz")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    // Control: las iniciales siguen.
    expect(container.querySelector(".persona-cabecera__avatar")?.textContent).toBe("AR");
  });
});
