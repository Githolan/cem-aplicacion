import { useState } from "react";

import { aplicarLecturaAmpliada, guardarLecturaAmpliada, lecturaAmpliadaActiva } from "./lecturaAmpliada";

export const NOMBRE_MODO_LECTURA = "Letra grande y alto contraste";

// Botón de la cabecera que prende y apaga el modo lectura ampliada (RF53). El nombre accesible no
// cambia; el estado lo dice aria-pressed (y la marca visible, oculta para el lector de pantalla).
export function ModoLectura() {
  const [activa, setActiva] = useState(lecturaAmpliadaActiva);
  const [sinGuardar, setSinGuardar] = useState(false);

  function alternar() {
    const nueva = !activa;
    aplicarLecturaAmpliada(nueva);
    setActiva(nueva);
    setSinGuardar(!guardarLecturaAmpliada(nueva));
  }

  return (
    <div className="modo-lectura">
      <button type="button" className="boton-modo-lectura" aria-pressed={activa} onClick={alternar}>
        <span className="marca-modo-lectura" aria-hidden="true">
          {activa ? "✓" : "Aa"}
        </span>
        {NOMBRE_MODO_LECTURA}
      </button>
      {/* aria-live sin role="status": el estado de cada página sigue siendo el único "status". */}
      <p aria-live="polite" className="aviso-modo-lectura">
        {sinGuardar && "Este navegador no deja guardar la preferencia: se pierde al recargar o cerrar la página."}
      </p>
    </div>
  );
}
