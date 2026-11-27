import { describe, expect, it } from "vitest";

import { seAbandono, textoDeDemora } from "./demoraDelAntivirus";

const EN_CUARENTENA = { estado: "en_cuarentena", motivo_rechazo: null, demora: null, se_abandono: false, proximo_intento: null } as const;

describe("textoDeDemora: ningún sistema falla en silencio (27-11-2026)", () => {
  it("sin intento fallido no hay nada que decir", () => {
    expect(textoDeDemora(EN_CUARENTENA)).toBeNull();
  });

  it("dice la causa de cada demora", () => {
    expect(textoDeDemora({ ...EN_CUARENTENA, demora: "firmas_viejas" })).toBe(
      "Todavía no se puede revisar el archivo: el antivirus no tiene al día su base de virus y CEM no acepta archivos hasta que se actualice. CEM lo vuelve a probar solo cuando el antivirus se recupere.",
    );
    expect(textoDeDemora({ ...EN_CUARENTENA, demora: "antivirus_no_disponible" })).toBe(
      "Todavía no se puede revisar el archivo: el antivirus no responde. CEM lo vuelve a probar solo cuando el antivirus se recupere.",
    );
    expect(textoDeDemora({ ...EN_CUARENTENA, demora: "otra_falla" })).toBe(
      "Todavía no se puede revisar el archivo: la revisión del archivo falló. CEM lo vuelve a probar solo cuando el antivirus se recupere.",
    );
  });

  it("con el próximo intento, dice la hora", () => {
    const texto = textoDeDemora({ ...EN_CUARENTENA, demora: "firmas_viejas", proximo_intento: "2026-11-27T21:42:00Z" });
    expect(texto).toMatch(/CEM lo vuelve a probar solo a las \d{2}:\d{2}\.$/);
  });

  it("abandonado por otra falla, pide avisar a soporte", () => {
    const abandonado = { ...EN_CUARENTENA, demora: "otra_falla", se_abandono: true } as const;
    expect(seAbandono(abandonado)).toBe(true);
    expect(textoDeDemora(abandonado)).toBe("CEM dejó de intentar revisar el archivo: la revisión del archivo falló varias veces. Hay que avisar a soporte.");
    expect(seAbandono(EN_CUARENTENA)).toBe(false); // control
  });

  it("resuelto, la demora no vale aunque venga", () => {
    expect(textoDeDemora({ ...EN_CUARENTENA, estado: "disponible", demora: "firmas_viejas" })).toBeNull();
    expect(seAbandono({ ...EN_CUARENTENA, estado: "rechazado", se_abandono: true })).toBe(false);
  });
});
