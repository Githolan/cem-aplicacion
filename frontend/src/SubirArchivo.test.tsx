import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CSV, problemaDelArchivo, TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "./archivos";
import { SubirArchivo } from "./SubirArchivo";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PDF = new File(["%PDF-1.4 SINTETICO"], "comprobante.pdf", { type: "application/pdf" });
const FOTO = new File(["\xff\xd8\xff SINTETICO"], "foto.jpg", { type: "image/jpeg" });
const TEXTO = new File(["hola"], "notas.txt", { type: "text/plain" });

/** El ancho como lo pregunta la aplicación; la zona no tiene que preguntarlo. */
function ancho(pixeles: number) {
  const consulta = vi.fn((texto: string) => ({ matches: texto.includes("max-width") ? pixeles <= 480 : pixeles >= 1025, addEventListener: () => undefined, removeEventListener: () => undefined }));
  vi.stubGlobal("matchMedia", consulta);
  return consulta;
}

function documento(extra: Partial<Parameters<typeof SubirArchivo>[0]> = {}) {
  const alElegir = vi.fn();
  render(<SubirArchivo titulo="Subir el comprobante" de="del comprobante" tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} alElegir={alElegir} {...extra} />);
  return alElegir;
}

describe("SubirArchivo (comentario 32)", () => {
  it.each([
    ["teléfono", 375],
    ["PC", 1280],
  ])("en %s ofrece a la vez soltar, la cámara, la galería y el archivo", (_, pixeles) => {
    const consulta = ancho(pixeles);
    documento();
    expect(screen.getByRole("group", { name: "Subir el comprobante" })).toBeTruthy();
    expect(screen.getByText("Soltar el archivo acá")).toBeTruthy();
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"]) expect(screen.getByRole("button", { name: nombre })).toBeTruthy();
    expect(screen.getByText("PDF, JPEG o PNG, hasta 20 MB.")).toBeTruthy();
    // La cámara abre la de atrás y solo admite imágenes; la galería no abre la cámara; el archivo admite todo.
    const foto = screen.getByLabelText("Foto del comprobante");
    expect(foto.getAttribute("capture")).toBe("environment");
    expect(foto.getAttribute("accept")).toBe(".jpg,.jpeg,image/jpeg,.png,image/png");
    const galeria = screen.getByLabelText("Imagen de la galería del comprobante");
    expect(galeria.hasAttribute("capture")).toBe(false);
    expect(galeria.getAttribute("accept")).toBe(".jpg,.jpeg,image/jpeg,.png,image/png");
    expect(screen.getByLabelText("Archivo del comprobante").getAttribute("accept")).toBe(".pdf,application/pdf,.jpg,.jpeg,image/jpeg,.png,image/png");
    // Control: no hay un corte por ancho, ni siquiera preguntado.
    expect(consulta).not.toHaveBeenCalled();
  });

  it("cada botón abre su campo del sistema", () => {
    documento();
    const clics = (["Foto del comprobante", "Imagen de la galería del comprobante", "Archivo del comprobante"] as const).map((etiqueta) => {
      const clic = vi.fn();
      screen.getByLabelText(etiqueta).addEventListener("click", clic);
      return clic;
    });
    fireEvent.click(screen.getByRole("button", { name: "Sacar una foto" }));
    fireEvent.click(screen.getByRole("button", { name: "Elegir de la galería" }));
    fireEvent.click(screen.getByRole("button", { name: "Elegir un archivo" }));
    expect(clics.map((c) => c.mock.calls.length)).toEqual([1, 1, 1]);
  });

  it("elegir por cualquier camino es el único paso: el archivo pasa enseguida, sin otro botón", () => {
    const alElegir = documento();
    fireEvent.change(screen.getByLabelText("Foto del comprobante"), { target: { files: [FOTO] } });
    fireEvent.change(screen.getByLabelText("Imagen de la galería del comprobante"), { target: { files: [FOTO] } });
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [PDF] } });
    expect(alElegir.mock.calls.map(([a]) => (a as File).name)).toEqual(["foto.jpg", "foto.jpg", "comprobante.pdf"]);
    expect(screen.queryByRole("button", { name: /^Subir/ })).toBeNull();
    // El campo queda vacío: elegir de nuevo el mismo archivo vuelve a avisar.
    expect((screen.getByLabelText("Archivo del comprobante") as HTMLInputElement).value).toBe("");
  });

  it("soltar un archivo en la zona lo entrega, y la zona se marca mientras pasa por encima", () => {
    const alElegir = documento();
    const zona = screen.getByRole("group", { name: "Subir el comprobante" });
    fireEvent.dragOver(zona);
    expect(zona.className).toContain("subir-archivo--encima");
    fireEvent.drop(zona, { dataTransfer: { files: [PDF] } });
    expect(zona.className).not.toContain("subir-archivo--encima");
    expect(alElegir).toHaveBeenCalledWith(PDF);
  });

  it("control: un archivo de otro tipo se rechaza con el aviso, sin entregarlo, al soltarlo o al elegirlo", () => {
    const alElegir = documento();
    fireEvent.drop(screen.getByRole("group", { name: "Subir el comprobante" }), { dataTransfer: { files: [TEXTO] } });
    expect(screen.getByRole("alert").textContent).toBe("Solo se aceptan archivos PDF, JPEG o PNG. Hay que elegir otro archivo.");
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [TEXTO] } });
    expect(alElegir).not.toHaveBeenCalled();
    // Uno que sirve borra el aviso y sí pasa.
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [PDF] } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(alElegir).toHaveBeenCalledTimes(1);
  });

  it("un archivo que pasa del tope se rechaza; uno justo en el tope pasa", () => {
    const alElegir = documento();
    const grande = new File(["x"], "grande.pdf", { type: "application/pdf" });
    Object.defineProperty(grande, "size", { value: TOPE_BYTES + 1 });
    const justo = new File(["x"], "justo.pdf", { type: "application/pdf" });
    Object.defineProperty(justo, "size", { value: TOPE_BYTES });
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [grande] } });
    expect(screen.getByRole("alert").textContent).toBe("El archivo pasa de 20 MB, el máximo. Hay que elegir uno más chico.");
    fireEvent.change(screen.getByLabelText("Archivo del comprobante"), { target: { files: [justo] } });
    expect(alElegir).toHaveBeenCalledWith(justo);
  });

  it("sin imágenes entre los tipos (el extracto CSV) no ofrece la cámara ni la galería", () => {
    const alElegir = vi.fn();
    render(<SubirArchivo de="del extracto" tipos={[CSV]} tope={null} alElegir={alElegir} />);
    expect(screen.getByRole("button", { name: "Elegir un archivo" })).toBeTruthy();
    expect(screen.getByText("Soltar el archivo acá")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sacar una foto" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Elegir de la galería" })).toBeNull();
    expect(screen.queryByLabelText("Foto del extracto")).toBeNull();
    expect(screen.getByText("CSV.")).toBeTruthy();
    // Un CSV se reconoce también por el nombre: Windows con Excel lo informa como otro tipo.
    const deExcel = new File(["a;b"], "extracto.CSV", { type: "application/vnd.ms-excel" });
    fireEvent.drop(screen.getByText("Soltar el archivo acá").parentElement as HTMLElement, { dataTransfer: { files: [deExcel] } });
    expect(alElegir).toHaveBeenCalledWith(deExcel);
    // Control: un PDF no pasa por CSV.
    fireEvent.drop(screen.getByText("Soltar el archivo acá").parentElement as HTMLElement, { dataTransfer: { files: [PDF] } });
    expect(screen.getByRole("alert").textContent).toBe("Solo se aceptan archivos CSV. Hay que elegir otro archivo.");
    expect(alElegir).toHaveBeenCalledTimes(1);
  });

  it("deshabilitada no entrega nada, ni soltando", () => {
    const alElegir = documento({ deshabilitada: true });
    for (const nombre of ["Sacar una foto", "Elegir de la galería", "Elegir un archivo"])
      expect((screen.getByRole("button", { name: nombre }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.drop(screen.getByRole("group", { name: "Subir el comprobante" }), { dataTransfer: { files: [PDF] } });
    expect(alElegir).not.toHaveBeenCalled();
  });

  it("con un archivo ya elegido lo dice y la zona habla de reemplazarlo", () => {
    documento({ reemplaza: true, elegido: "acta.pdf (1 KB)", nota: "Antes de mostrarlo lo revisa el antivirus." });
    expect(screen.getByText("Elegido: acta.pdf (1 KB)")).toBeTruthy();
    expect(screen.getByText("Soltar otro archivo acá")).toBeTruthy();
    expect(screen.getByText("PDF, JPEG o PNG, hasta 20 MB. Antes de mostrarlo lo revisa el antivirus.")).toBeTruthy();
  });

  it("los avisos no tutean ni vosean", () => {
    const grande = new File(["x"], "g.pdf", { type: "application/pdf" });
    Object.defineProperty(grande, "size", { value: TOPE_BYTES + 1 });
    const avisos = [problemaDelArchivo(TEXTO, TIPOS_DE_DOCUMENTO, TOPE_BYTES), problemaDelArchivo(grande, TIPOS_DE_DOCUMENTO, TOPE_BYTES)];
    for (const aviso of avisos) expect(aviso).not.toMatch(/Elegí|Subí|Soltá|Cambiá|elegí|tenés|podés/);
    // Control: el patrón sí detecta el texto de antes.
    expect("Solo se aceptan archivos PDF, JPEG o PNG. Elegí otro archivo.").toMatch(/Elegí|Subí|Soltá|Cambiá|elegí|tenés|podés/);
  });
});
