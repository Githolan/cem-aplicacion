import { useState } from "react";

// Los códigos de recuperación, que se muestran una sola vez (TB-CEM-3): la lista, copiarlos y
// guardarlos como archivo (informe de fricción, AC-18: en un teléfono, anotar diez códigos a mano
// invita a tocar «Ya los guardé» sin guardarlos). Quien los muestra no los suelta hasta «Ya los
// guardé». Los usan la página del segundo factor, Mi cuenta y la hoja de confirmar un acto.

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

type Resultado = { texto: string; ok: boolean } | null;

interface Props {
  codigos: string[];
  medio: MedioActivado;
  alGuardar: () => void;
}

export function CodigosRecuperacion({ codigos, medio, alGuardar }: Props) {
  const [resultado, setResultado] = useState<Resultado>(null);

  function copiar() {
    setResultado(null);
    navigator.clipboard.writeText(codigos.join("\n")).then(
      () => setResultado({ texto: "Copiados. Pegarlos en un lugar seguro.", ok: true }),
      () => setResultado({ texto: "No se pudieron copiar. Guardarlos como archivo o anotarlos.", ok: false }),
    );
  }

  function guardar() {
    guardarComoArchivo(codigos);
    setResultado({ texto: "El archivo se guardó en las descargas de este dispositivo.", ok: true });
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
      <div className="acciones-pantalla">
        <button type="button" className="secundario" onClick={copiar}>
          Copiar los códigos
        </button>
        <button type="button" className="secundario" onClick={guardar}>
          Guardar como archivo
        </button>
      </div>
      <div role="status" aria-live="polite">
        {resultado !== null && <p className={resultado.ok ? "aviso-ok" : "aviso-error"}>{resultado.texto}</p>}
      </div>
      <button type="button" data-foco onClick={alGuardar}>
        Ya los guardé
      </button>
    </>
  );
}
