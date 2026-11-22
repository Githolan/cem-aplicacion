import { useEffect, useState } from "react";

import { ayudaEnCurso, dejarDeAyudar, ayudaTerminada, olvidarAyudaTerminada } from "./estadoAyuda";

// El aviso fijo de la ayuda (RF53): mientras esta pestaña ayuda a otra persona, arriba de cada
// pantalla dice a quién, en qué edificio, que nada decisorio se hace, y cómo dejar de ayudar.

export const textoAvisoAyuda = (asistida: string, edificio: string) => `Se está ayudando a ${asistida} en ${edificio}.`;
export const LIMITE_AYUDA = "Ayudando no se vota, firma, aprueba ni paga.";
export const DEJAR_DE_AYUDAR = "Dejar de ayudar";
/** La autorización se revocó mientras se ayudaba: la pestaña ya salió de la ayuda (`vigilarRevocacion`). */
export const textoAyudaRevocada = (asistida: string) =>
  `${asistida} dejó de autorizar la ayuda. Esta pestaña volvió a la cuenta propia.`;

export function AvisoAyuda() {
  const ayuda = ayudaEnCurso();
  // Se lee una sola vez, al montar: el aviso queda hasta cambiar de pestaña o recargar.
  const [terminada] = useState(() => (ayuda === null ? ayudaTerminada() : null));
  useEffect(() => {
    if (terminada !== null) olvidarAyudaTerminada();
  }, [terminada]);
  if (ayuda === null)
    return terminada === null ? null : (
      <p className="aviso-pendiente aviso-enlace" role="status">
        {textoAyudaRevocada(terminada)}
      </p>
    );
  return (
    <section className="aviso-pendiente aviso-enlace aviso-fijo aviso-ayuda" aria-label="Ayuda en curso">
      <p>{textoAvisoAyuda(ayuda.asistida, ayuda.edificio)}</p>
      <p>{LIMITE_AYUDA}</p>
      <p>
        <button type="button" className="secundario" onClick={dejarDeAyudar}>
          {DEJAR_DE_AYUDAR}
        </button>
      </p>
    </section>
  );
}
