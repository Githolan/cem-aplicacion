import { afterEach, describe, expect, it } from "vitest";

import { faltasDeBotones, faltasVistas, vigilarBotones } from "./pesoDeBotones";

// Controles de la regla de colores y del botón principal (pregunta 13, tanda 5). La regla corre
// sobre cada prueba de pantalla desde `configuracionPruebas.ts`; acá se demuestra que ve cada falta.

function dibujar(html: string): HTMLElement {
  const raiz = suelto(html);
  document.body.append(raiz);
  return raiz;
}

/** Fuera del documento: la falta que se dibuja a propósito no llega a la vigilancia de cada prueba. */
function suelto(html: string): HTMLElement {
  const raiz = document.createElement("div");
  raiz.innerHTML = html;
  return raiz;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("colores y botón principal (pregunta 13)", () => {
  it("una pantalla bien dibujada no tiene faltas", () => {
    const raiz = dibujar(`
      <button type="button">Guardar</button>
      <button type="button" class="secundario">Cancelar</button>
      <button type="button" class="secundario peligro">Retirar la orden</button>
      <div role="dialog"><button type="submit" class="peligro">Sí, retirar la orden</button><button class="secundario">No, dejarla</button></div>
      <a class="enlace-boton enlace-boton--borde" href="/x">Ver la otra</a>
      <details><summary>Más</summary><button type="button">Otro principal plegado</button></details>
      <div hidden><button type="button">Otro principal oculto</button></div>
      <button type="button" disabled>Deshabilitado</button>`);
    expect(faltasDeBotones(raiz)).toEqual([]);
  });

  it("control: dos principales llenos en la misma pantalla", () => {
    const raiz = suelto(`<button type="button">Activar</button><a class="enlace-boton enlace-boton--principal" href="/x">Seguir</a>`);
    expect(faltasDeBotones(raiz)).toEqual(["más de un botón principal a la vez: «Activar», «Seguir»"]);
  });

  it("control: «Cancelar» en rojo, y un acto negativo sin rojo", () => {
    const raiz = suelto(`<button class="secundario peligro">Cancelar</button><button class="secundario">Rechazar</button>`);
    expect(faltasDeBotones(raiz)).toEqual(["«Cancelar» deja de lado y está en rojo", "«Rechazar» es un acto negativo y no está en rojo"]);
  });

  it("control: la vigilancia ve una falta que aparece después de empezar la prueba", async () => {
    vigilarBotones();
    dibujar(`<button type="button">Uno</button><button type="button">Dos</button>`);
    await Promise.resolve();
    expect(faltasVistas()).toEqual(["más de un botón principal a la vez: «Uno», «Dos»"]);
    // La de esta prueba ya se tomó: la regla de configuracionPruebas.ts no la vuelve a contar.
    document.body.innerHTML = "";
    vigilarBotones();
  });
});
