import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { useId, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "./ErrorDeCampo";
import { FichaDeRegistro } from "./FichaDeRegistro";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// La ventana para cargar datos (tanda 1 del informe de fricción) sola y sobre una ficha. El pedido
// es una promesa que la prueba resuelve a mano: así se ve el estado mientras viaja.

type Pendiente = { resolver: (fin: FinDeEnvio) => void };

/** Una ficha con una acción «Señalar» que abre su ventana con el campo «Motivo». */
function FichaConAccion({ pedir, alListo }: { pedir: (motivo: string) => Promise<FinDeEnvio>; alListo?: () => void }) {
  const id = useId();
  const [ficha, setFicha] = useState(true);
  const [abierta, setAbierta] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  if (!ficha) return <p>Lista</p>;
  return (
    <FichaDeRegistro
      titulo="Plomero: Juan"
      datos={[{ etiqueta: "Teléfono", valor: "1111" }]}
      alCerrar={() => setFicha(false)}
      ventana={
        abierta && (
          <VentanaDeFormulario
            titulo="Señalar un dato incorrecto"
            textoEnviar="Señalar"
            alCerrar={() => {
              setAbierta(false);
              alListo?.();
            }}
            alEnviar={async () => {
              const faltan = faltaCompletar({ motivo: [motivo, "Falta escribir el motivo."] });
              if (hayErrores(faltan)) {
                setErrores(faltan);
                return "seguir";
              }
              return pedir(motivo);
            }}
          >
            <label htmlFor={`${id}-motivo`}>Motivo</label>
            <input
              id={`${id}-motivo`}
              value={motivo}
              {...marcaDe(`${id}-motivo`, errores.motivo)}
              onChange={(e) => {
                setErrores((actuales) => sinElDe(actuales, "motivo"));
                setMotivo(e.target.value);
              }}
            />
            <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
          </VentanaDeFormulario>
        )
      }
    >
      <p className="ficha-registro__acciones">
        <button type="button" className="secundario" onClick={() => setAbierta(true)}>
          Señalar un dato incorrecto
        </button>
      </p>
    </FichaDeRegistro>
  );
}

/** Un pedido que queda en viaje hasta que la prueba lo resuelve. */
function pedidoManual(): { pedir: (motivo: string) => Promise<FinDeEnvio>; pendiente: Pendiente; motivos: string[] } {
  const pendiente: Pendiente = { resolver: () => undefined };
  const motivos: string[] = [];
  return {
    pendiente,
    motivos,
    pedir: (motivo) => {
      motivos.push(motivo);
      return new Promise<FinDeEnvio>((r) => {
        pendiente.resolver = r;
      });
    },
  };
}

const ventana = () => screen.getByRole("dialog", { name: "Señalar un dato incorrecto" });
const ficha = () => screen.getByRole("dialog", { name: "Plomero: Juan" });

function abrirLaVentana() {
  const accion = within(ficha()).getByRole("button", { name: "Señalar un dato incorrecto" });
  accion.focus();
  fireEvent.click(accion);
  return accion;
}

afterEach(() => cleanup());

describe("VentanaDeFormulario", () => {
  it("la acción de la ficha abre su ventana encima, con solo su campo, el botón y «Cancelar»", async () => {
    const { pedir } = pedidoManual();
    render(<FichaConAccion pedir={pedir} />);
    abrirLaVentana();
    // Dos hojas: la ficha queda atrás, inerte, y la ventana arriba.
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    expect(ficha().closest<HTMLElement>(".hoja-fondo")?.inert).toBe(true);
    expect(ventana().closest<HTMLElement>(".hoja-fondo")?.inert).not.toBe(true);
    expect(within(ventana()).getAllByRole("button").map((b) => b.textContent)).toEqual(["Señalar", "Cancelar"]);
    // Los datos de la ficha no están en la ventana.
    expect(within(ventana()).queryByText("Teléfono")).toBeNull();
    const { violations } = await axe.run(ventana());
    expect(violations.map((v) => v.id)).toEqual([]);
  });

  it("con el campo vacío, el botón sigue activo y marca el campo sin enviar (decisión 4)", async () => {
    const { pedir, motivos } = pedidoManual();
    render(<FichaConAccion pedir={pedir} />);
    abrirLaVentana();
    const enviar = within(ventana()).getByRole("button", { name: "Señalar" });
    expect((enviar as HTMLButtonElement).disabled).toBe(false);
    await act(async () => fireEvent.click(enviar));
    const campo = within(ventana()).getByLabelText("Motivo");
    expect(campo.getAttribute("aria-invalid")).toBe("true");
    expect(within(ventana()).getByText("Falta escribir el motivo.")).toBeTruthy();
    expect(document.activeElement).toBe(campo);
    expect(motivos).toEqual([]);
    // Al escribir, la marca se va.
    fireEvent.change(campo, { target: { value: "El número no existe" } });
    expect(within(ventana()).queryByText("Falta escribir el motivo.")).toBeNull();
  });

  it("mientras viaja el pedido se apaga y no se cierra; si sale bien, se cierra sola", async () => {
    const { pedir, pendiente, motivos } = pedidoManual();
    const alListo = vi.fn();
    render(<FichaConAccion pedir={pedir} alListo={alListo} />);
    abrirLaVentana();
    fireEvent.change(within(ventana()).getByLabelText("Motivo"), { target: { value: "No atiende" } });
    fireEvent.click(within(ventana()).getByRole("button", { name: "Señalar" }));
    expect(motivos).toEqual(["No atiende"]);
    expect((within(ventana()).getByRole("button", { name: "Cancelar" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(ventana()).getByLabelText("Motivo") as HTMLInputElement).matches(":disabled")).toBe(true);
    fireEvent.keyDown(within(ventana()).getByLabelText("Motivo"), { key: "Escape" });
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    await act(async () => pendiente.resolver("cerrar"));
    expect(screen.queryByRole("dialog", { name: "Señalar un dato incorrecto" })).toBeNull();
    expect(alListo).toHaveBeenCalledOnce();
    // La ficha vuelve a la vista.
    expect(ficha().closest<HTMLElement>(".hoja-fondo")?.inert).toBe(false);
  });

  it("si sale mal, el motivo queda adentro, con el foco, y se puede corregir y volver a mandar", async () => {
    const { pedir, pendiente, motivos } = pedidoManual();
    render(<FichaConAccion pedir={pedir} />);
    abrirLaVentana();
    fireEvent.change(within(ventana()).getByLabelText("Motivo"), { target: { value: "x" } });
    fireEvent.click(within(ventana()).getByRole("button", { name: "Señalar" }));
    await act(async () => pendiente.resolver({ rechazo: "No se pudo señalar. Hay que revisar la conexión." }));
    const alerta = within(ventana()).getByRole("alert");
    expect(alerta.textContent).toBe("No se pudo señalar. Hay que revisar la conexión.");
    expect(document.activeElement).toBe(alerta);
    expect((within(ventana()).getByRole("button", { name: "Señalar" }) as HTMLButtonElement).disabled).toBe(false);
    // El segundo envío borra el rechazo anterior mientras viaja.
    fireEvent.click(within(ventana()).getByRole("button", { name: "Señalar" }));
    expect(within(ventana()).queryByRole("alert")).toBeNull();
    expect(motivos).toEqual(["x", "x"]);
  });

  it("«Cancelar» y Escape cierran solo la ventana, y el foco vuelve a la acción de la ficha", () => {
    const { pedir } = pedidoManual();
    render(<FichaConAccion pedir={pedir} />);
    const accion = abrirLaVentana();
    fireEvent.click(within(ventana()).getByRole("button", { name: "Cancelar" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(document.activeElement).toBe(within(ficha()).getByRole("button", { name: "Señalar un dato incorrecto" }));
    expect(accion.isConnected).toBe(true);
    // Escape adentro de la ventana no cierra también la ficha.
    abrirLaVentana();
    fireEvent.keyDown(within(ventana()).getByLabelText("Motivo"), { key: "Escape" });
    expect(screen.getAllByRole("dialog").map((d) => d.getAttribute("aria-labelledby") !== null)).toEqual([true]);
    expect(ficha()).toBeTruthy();
    // Control: Escape en la ficha sí la cierra.
    fireEvent.keyDown(ficha(), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Lista")).toBeTruthy();
  });
});

describe("faltaCompletar", () => {
  it("marca solo los campos vacíos, con su texto", () => {
    expect(faltaCompletar({ motivo: ["  ", "Falta escribir el motivo."], nombre: ["Ana", "Falta el nombre."] })).toEqual({
      motivo: "Falta escribir el motivo.",
    });
    expect(hayErrores(faltaCompletar({ motivo: ["algo", "Falta escribir el motivo."] }))).toBe(false);
    // Control: con un campo marcado, hay errores.
    expect(hayErrores({ motivo: "x" })).toBe(true);
  });
});

describe("VentanaDeFormulario sin el botón principal (tanda 3, C8G-02 y C8G-05)", () => {
  it("con `sinEnviar` no hay botón principal ni envío con Enter; queda «Cancelar», y la línea dice qué falta", () => {
    const alEnviar = vi.fn((): FinDeEnvio => "cerrar");
    const alCerrar = vi.fn();
    const { rerender } = render(
      <VentanaDeFormulario titulo="Dar de alta un mandato" textoEnviar="Dar de alta" alEnviar={alEnviar} alCerrar={alCerrar} sinEnviar>
        <label htmlFor="tipo">Tipo</label>
        <input id="tipo" data-foco defaultValue="Consejo" />
        <p>Nadie tiene todavía un vínculo confirmado en este edificio.</p>
      </VentanaDeFormulario>,
    );
    const hoja = screen.getByRole("dialog", { name: "Dar de alta un mandato" });
    expect(within(hoja).queryByRole("button", { name: "Dar de alta" })).toBeNull();
    expect(within(hoja).getByRole("button", { name: "Cancelar" })).toBeTruthy();
    fireEvent.submit(within(hoja).getByLabelText("Tipo").closest("form") as HTMLFormElement);
    expect(alEnviar).not.toHaveBeenCalled();
    // Control: sin `sinEnviar`, el botón vuelve y envía.
    rerender(
      <VentanaDeFormulario titulo="Dar de alta un mandato" textoEnviar="Dar de alta" alEnviar={alEnviar} alCerrar={alCerrar}>
        <label htmlFor="tipo">Tipo</label>
        <input id="tipo" data-foco defaultValue="Consejo" />
      </VentanaDeFormulario>,
    );
    fireEvent.click(within(screen.getByRole("dialog", { name: "Dar de alta un mandato" })).getByRole("button", { name: "Dar de alta" }));
    expect(alEnviar).toHaveBeenCalledTimes(1);
  });
});
