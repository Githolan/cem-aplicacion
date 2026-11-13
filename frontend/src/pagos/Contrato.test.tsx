import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import type { components } from "../api/tipos";
import { TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { Acciones, aDibujar } from "./Acciones";
import { Barra } from "./Barra";
import { TABLA } from "./pantallaDePrueba";
import { fechaCorta, NOMBRE_ESTADO, PASOS_DEL_CAMINO, TITULO_GRUPO, TONO_ESTADO } from "./textos";
import { type AccionOfrecida, type AccionPago, CON_ENLACE, CON_FORMULARIO, TEXTO_ACCION } from "./textosDeAcciones";

// El contrato de la pantalla de pagos visto desde el frontend (informe de fricción, §4.4): la pantalla
// dibuja exactamente lo que manda el servidor, con una sola palabra por estado y un solo principal.

type Situacion = components["schemas"]["SituacionPago"];

afterEach(cleanup);

const NEGATIVAS = new Set<AccionPago>(["retirar", "rechazar", "observar", "declarar_no_pago", "cerrar_reclamo", "cerrar_sin_pagar"]);

function dibujar(acciones: AccionOfrecida[]) {
  return render(
    <MemoryRouter>
      <Acciones acciones={acciones} enviando={false} alElegir={() => undefined}
        enlaces={{ preparar_otra: { a: "/pagos/nueva", estado: null }, preparar_faltante: { a: "/pagos/nueva", estado: null } }} />
    </MemoryRouter>,
  );
}

const nombres = () => [...screen.queryAllByRole("button"), ...screen.queryAllByRole("link")].map((b) => b.textContent);

describe("botón = lo que llegó", () => {
  // Cada fila de la tabla, con todas sus acciones aceptadas por la base: lo que llega es lo que se ve.
  it.each(TABLA.filas.map((f) => [`${f.situacion}/${String(f.paso)}`, f] as const))("%s", (_, fila) => {
    const acciones: AccionOfrecida[] = [
      ...(fila.principal !== null ? [{ codigo: fila.principal, tipo: "principal" as const }] : []),
      ...fila.secundarias.map((codigo) => ({ codigo, tipo: "secundaria" as const })),
      ...fila.negativas.map((codigo) => ({ codigo, tipo: "negativa" as const })),
    ];
    dibujar(acciones);
    expect(nombres()).toEqual(aDibujar(acciones).map((a) => TEXTO_ACCION[a.codigo]));
    // Un solo principal: como mucho un botón lleno (sin «secundario»).
    const llenos = screen.queryAllByRole("button").filter((b) => !b.classList.contains("secundario"));
    expect(llenos.length).toBeLessThanOrEqual(1);
    // Las negativas van en rojo.
    for (const b of screen.queryAllByRole("button")) {
      const codigo = b.getAttribute("data-accion") as AccionPago;
      if (b.getAttribute("data-tipo") === "negativa") expect(b.classList.contains("peligro")).toBe(true);
      expect(NEGATIVAS.has(codigo) || !b.classList.contains("peligro") || b.getAttribute("data-tipo") === "principal").toBe(true);
    }
    cleanup();
  });

  it("sin acciones no dibuja nada, y una que no llegó no aparece", () => {
    const { container } = dibujar([]);
    expect(container.textContent).toBe("");
    cleanup();
    dibujar([{ codigo: "aprobar", tipo: "principal" }]);
    expect(nombres()).toEqual(["Aprobar la orden"]);
    expect(screen.queryByRole("button", { name: "Rechazar" })).toBeNull();
  });

  it("las de formulario no son un botón suelto y las de enlace son un enlace", () => {
    dibujar([
      { codigo: "subir_comprobante", tipo: "principal" },
      { codigo: "preparar_otra", tipo: "secundaria" },
    ]);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("link", { name: "Preparar otra orden" }).getAttribute("href")).toBe("/pagos/nueva");
    expect(CON_FORMULARIO.has("subir_comprobante") && CON_ENLACE.has("preparar_otra")).toBe(true);
  });
});

describe("un vocabulario", () => {
  it("la palabra de cada estado es la de la tabla del contrato (decisión 11)", () => {
    expect(NOMBRE_ESTADO).toEqual(TABLA.estados);
    expect(Object.keys(TONO_ESTADO).sort()).toEqual(Object.keys(TABLA.estados).sort());
  });

  it("el estado guardado usa las mismas palabras: no hay otro mapa de nombres", () => {
    const palabras = new Set(Object.values(NOMBRE_ESTADO));
    for (const texto of Object.values(TEXTO_ESTADO_ORDEN)) expect(palabras.has(texto)).toBe(true);
    // Control: las palabras viejas ya no están.
    for (const vieja of ["En revisión", "En ejecución", "En verificación", "Fallida", "Conciliada", "Aprobada"])
      expect(Object.values(TEXTO_ESTADO_ORDEN)).not.toContain(vieja);
  });

  it("ningún grupo de la bandeja se llama como un estado de otro momento (P-08)", () => {
    const estados = new Set(Object.values(NOMBRE_ESTADO));
    for (const titulo of Object.values(TITULO_GRUPO)) expect(estados.has(titulo)).toBe(false);
  });

  it("los textos de las acciones son impersonales y sin palabras internas", () => {
    // Bordes de palabra con letras acentuadas: `\b` de JavaScript solo conoce ASCII.
    const VOSEO = /(?<!\p{L})(vos|te|tu|tus|usted|tenés|podés|hacé|mirá|revisá)(?!\p{L})/iu;
    const PROHIBIDAS = /(?<!\p{L})(versión|franja|huella|riel|umbral|fraccionamiento)(?!\p{L})/iu;
    for (const texto of [...Object.values(TEXTO_ACCION), ...TABLA.filas.map((f) => f.que_decir)]) {
      expect(texto).not.toMatch(VOSEO);
      expect(texto).not.toMatch(PROHIBIDAS);
    }
    expect("revisá la versión").toMatch(VOSEO); // control del detector
    expect("revisá la versión").toMatch(PROHIBIDAS);
    expect("Retirar el borrador").not.toMatch(VOSEO); // «te» dentro de una palabra no cuenta
  });
});

describe("barra", () => {
  function marcas(situacion: Situacion) {
    cleanup();
    render(<Barra barra={TABLA.situaciones[situacion].barra} />);
    return screen.queryAllByRole("listitem").map((li) => li.getAttribute("data-estado"));
  }

  it("un borrador y una orden enviada no tienen la misma barra (P-06)", () => {
    expect(marcas("borrador")).toEqual(["ahora", null, null, null, null]);
    expect(marcas("en_revision")).toEqual(["hecho", "ahora", null, null, null]);
  });

  it("pagada deja Cotejar como actual; pagada y cotejada, todo hecho (P-05)", () => {
    expect(marcas("pagada")).toEqual(["hecho", "hecho", "hecho", "hecho", "ahora"]);
    expect(marcas("pagada_y_cotejada")).toEqual(["hecho", "hecho", "hecho", "hecho", "hecho"]);
    expect(screen.getAllByRole("listitem").map((li) => li.textContent?.replace(" (hecho)", ""))).toEqual([...PASOS_DEL_CAMINO]);
  });

  it("en «No se pagó» no desaparece; en una retirada no hay barra", () => {
    expect(marcas("no_se_pago")).toEqual(["hecho", "hecho", "alerta", null, null]);
    expect(marcas("retirada")).toEqual([]);
  });
});

describe("fecha corta", () => {
  it("abrevia para no ensanchar la fila", () => {
    expect(fechaCorta("2026-11-09T15:00:00Z", new Date("2026-11-13T12:00:00Z"))).toBe("9 nov");
    expect(fechaCorta("2025-11-09T15:00:00Z", new Date("2026-11-13T12:00:00Z"))).toBe("9 nov 2025");
  });
});
