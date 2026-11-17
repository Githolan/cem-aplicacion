import { useRef, useState } from "react";

import { useAvisoAlSalir } from "./avisoAlSalir";

// Los códigos de recuperación, que se muestran una sola vez (TB-CEM-3): la lista, copiarlos y
// guardarlos como archivo (informe de fricción, AC-18: en un teléfono, anotar diez códigos a mano
// invita a tocar «Códigos guardados: seguir» sin guardarlos). Quien los muestra no los suelta hasta «Ya los
// guardé». Los usan la página del segundo factor, Cuenta y la hoja de confirmar un acto.
//
// Informe de fricción, tanda 9 (CTA-13): hecha la copia o la descarga, su botón se reemplaza por el
// resultado y el foco pasa a «Códigos guardados: seguir», que queda como único botón principal. La
// hoja no avanza sola: los códigos se muestran una sola vez.

export type MedioActivado = "aplicacion" | "llave";

/** Nombre del archivo: sin datos de la persona, que viajan solo dentro. */
export const ARCHIVO_CODIGOS = "codigos-de-recuperacion-consorcio-en-mano.txt";

/** Un enlace local liberado en el acto corta la descarga en algunos navegadores (SeccionPrivacidad). */
const ESPERA_LIBERAR_MS = 60_000;

function guardarComoArchivo(codigos: string[]) {
  const texto = `Códigos de recuperación de Consorcio en Mano. Cada uno sirve una sola vez.\n\n${codigos.join("\n")}\n`;
  const url = URL.createObjectURL(new Blob([texto], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = ARCHIVO_CODIGOS;
  a.hidden = true;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), ESPERA_LIBERAR_MS);
}


interface Props {
  codigos: string[];
  medio: MedioActivado;
  alGuardar: () => void;
}

export function CodigosRecuperacion({ codigos, medio, alGuardar }: Props) {
  const [copia, setCopia] = useState<"sin_copiar" | "copiados" | "fallo">("sin_copiar");
  const [guardado, setGuardado] = useState(false);
  const seguir = useRef<HTMLButtonElement>(null);
  useAvisoAlSalir(true);

  function copiar() {
    setCopia("sin_copiar");
    navigator.clipboard.writeText(codigos.join("\n")).then(
      () => {
        setCopia("copiados");
        seguir.current?.focus();
      },
      () => setCopia("fallo"),
    );
  }

  function guardar() {
    guardarComoArchivo(codigos);
    setGuardado(true);
    seguir.current?.focus();
  }

  return (
    <>
      <p>
        {medio === "llave" ? "Si se pierde este dispositivo" : "Si se pierde el teléfono"}, cada uno de
        estos códigos sirve <strong>una sola vez</strong>{" "}
        {medio === "llave" ? "para entrar en su lugar" : "en lugar del de la aplicación"}. Guardarlos en un
        lugar seguro: no se vuelven a mostrar.
      </p>
      <ul className="codigos-recuperacion" aria-label="Códigos de recuperación" translate="no">
        {codigos.map((c) => (
          <li key={c}>
            <code>{c}</code>
          </li>
        ))}
      </ul>
      <div role="status" aria-live="polite" className="acciones-pantalla">
        {copia === "copiados" ? (
          <p className="aviso-ok">Copiados. Pegarlos en un lugar seguro.</p>
        ) : (
          <button type="button" className="secundario" onClick={copiar}>
            Copiar los códigos
          </button>
        )}
        {guardado ? (
          <p className="aviso-ok">Archivo guardado en las descargas de este dispositivo.</p>
        ) : (
          <button type="button" className="secundario" onClick={guardar}>
            Guardar como archivo
          </button>
        )}
      </div>
      {copia === "fallo" && (
        <p className="aviso-error" role="alert">
          No se pudieron copiar. Guardarlos como archivo o anotarlos.
        </p>
      )}
      <button ref={seguir} type="button" data-foco onClick={alGuardar}>
        Códigos guardados: seguir
      </button>
    </>
  );
}
