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
    // Control: la prueba encuentra el par del defecto 37 si esas dos rutas vuelven a ser dos entradas.
    const separadas = [
      ...DESTINOS_DEL_MENU,
      { ruta: "/proveedores", texto: "Consulta", icono: "proveedor" as const },
      { ruta: "/proveedores/quien-los-ve", texto: "Quién", icono: "ojo" as const },
    ];
    expect(prefijosSinExacta(separadas)).toContainEqual(["/proveedores", "/proveedores/quien-los-ve"]);
    // Control: «Inicio» es el comienzo de todas; sin `exacta`, aparece con cada ruta de las demás.
    const sinInicio = DESTINOS_DEL_MENU.map((d) => (d.ruta === "/" ? { ...d, exacta: false } : d));
    const rutasDeLasDemas = DESTINOS_DEL_MENU.filter((d) => d.ruta !== "/").reduce((n, d) => n + (d.opciones?.length ?? 1), 0);
    expect(prefijosSinExacta(sinInicio)).toHaveLength(rutasDeLasDemas);
  });

  it("las tres pantallas de proveedores marcan una sola entrada, «Proveedores» (defecto 37, MEN-05)", () => {
    for (const ruta of ["/proveedores/quien-los-ve", "/proveedores", "/egresos/proveedores"]) {
      expect(activas(menuEn(ruta))).toEqual(["Proveedores"]);
      cleanup();
    }
    // Control: las pantallas hijas de Pagos, sin entrada propia, siguen marcando «Pagos».
    expect(activas(menuEn("/pagos/consulta"))).toEqual(["Pagos"]);
  });

  it("una sola entrada de proveedores, que lleva a lo que la persona alcanza (MEN-05)", () => {
    const destino = () => within(screen.getByRole("navigation", { name: "Secciones" })).queryAllByRole("link", { name: /proveedores/i });
    // Quien gestiona en un edificio: a la gestión, aunque en otro solo consulte.
    menuEn("/");
    expect(destino().map((a) => [a.textContent, a.getAttribute("href")])).toEqual([["Proveedores", "/egresos/proveedores"]]);
    cleanup();
    // Quien solo consulta: a la consulta.
    consulta.actual = sabido(new Map([["e1", ["proveedores.ver", "proveedores.autorizar"]]]));
    menuEn("/");
    expect(destino().map((a) => a.getAttribute("href"))).toEqual(["/proveedores"]);
    cleanup();
    // Quien solo autoriza: a «Quién ve los proveedores».
    consulta.actual = sabido(new Map([["e1", ["proveedores.autorizar"]]]));
    menuEn("/");
    expect(destino().map((a) => a.getAttribute("href"))).toEqual(["/proveedores/quien-los-ve"]);
    cleanup();
    // Control: sin ninguna de las tres, no hay entrada.
    consulta.actual = sabido(new Map([["e1", ["documentos.ver"]]]));
    menuEn("/");
    expect(destino()).toEqual([]);
  });

  it("la entrada de solicitudes no repite el ícono de otra (MEN-05)", () => {
    const menu = menuEn("/");
    const icono = (nombre: string) => within(menu).getByRole("link", { name: nombre }).querySelector("svg")?.innerHTML;
    expect(icono("Solicitudes formales")).not.toBe(icono("Documentos"));
    // Control: la comparación distingue íconos distintos.
    expect(icono("Proveedores")).not.toBe(icono("Documentos"));
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

  it("FL-VER-04: quien de la gestión solo consulta proveedores la tiene con las principales, sin «Gestión»", () => {
    consulta.actual = sabido(new Map([["e1", ["proveedores.ver", "documentos.ver", "contactos.ver"]]]));
    const menu = menuEn("/proveedores");
    expect(within(menu).queryByText("Gestión")).toBeNull();
    expect(within(menu).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Inicio", "Documentos", "Contactos de emergenciaEmergencias", "Proveedores", "Cuenta",
    ]);  // prettier-ignore
    const proveedores = within(menu).getByRole("link", { name: "Proveedores" });
    expect(proveedores.classList.contains("navegacion__item--secundario")).toBe(false);
    // Es una pestaña más: en su pantalla «Inicio» no queda marcada.
    expect(within(menu).getByRole("link", { name: "Inicio" }).classList.contains("navegacion__item--desde-inicio")).toBe(false);
    expect(activas(menu)).toEqual(["Proveedores"]);
    cleanup();
    // Control: con otra pantalla de gestión, «Proveedores» sigue en «Gestión».
    consulta.actual = sabido(new Map([["e1", ["proveedores.ver", "padron.ver"]]]));
    const conGestion = menuEn("/proveedores");
    expect(within(conGestion).getByText("Gestión")).toBeTruthy();
    expect(within(conGestion).getByRole("link", { name: "Proveedores" }).classList.contains("navegacion__item--secundario")).toBe(true);
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
  it("«Mis expensas» y «Liquidación oficial» aparecen solo para quien las alcanza (H5 D3-A)", () => {
    // Una propietaria con el vínculo confirmado: su cuenta y el resumen, no la liquidación completa.
    consulta.actual = sabido(new Map([["e1", ["cuenta.ver_propia", "fiscalizacion.ver_resumen", "documentos.ver"]]]));
    let menu = menuEn("/");
    expect(within(menu).getByRole("link", { name: /Mis expensas/ })).toBeTruthy();
    expect(within(menu).queryByRole("link", { name: /Liquidación oficial/ })).toBeNull();
    cleanup();
    // El Consejo sin vínculo propio: la liquidación completa, no «Mis expensas».
    consulta.actual = sabido(new Map([["e1", ["liquidacion.ver_importada", "fiscalizacion.ver_panel"]]]));
    menu = menuEn("/");
    expect(within(menu).getByRole("link", { name: /Liquidación oficial/ })).toBeTruthy();
    expect(within(menu).queryByRole("link", { name: /Mis expensas/ })).toBeNull();
    cleanup();
    // Una inquilina: ninguna de las dos.
    consulta.actual = sabido(new Map([["e1", ["documentos.ver", "contactos.ver"]]]));
    menu = menuEn("/");
    expect(within(menu).queryByRole("link", { name: /Mis expensas|Liquidación oficial/ })).toBeNull();
  });

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
