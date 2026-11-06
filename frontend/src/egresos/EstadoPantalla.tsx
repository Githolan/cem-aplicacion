import type { ReactNode } from "react";

import { SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import { type Aviso, conLaRedDeVuelta, TEXTO_SESION_VENCIDA } from "./comun";
import { type PantallaConsorcio, useAlVolverLaRed } from "./useConsorcio";

// Lo que las pantallas de egresos muestran cuando algo salió o no salió (regla de fricción del
// 05-11-2026): el resultado en la misma pantalla y, si falló, una salida a mano para reintentar.

/** El resultado de lo último. Una sesión de más de 12 horas no se dice acá: la pide la hoja encima. */
export function AvisoPantalla({ aviso }: { aviso: Aviso | null }) {
  if (aviso === null || aviso.texto === TEXTO_SESION_VENCIDA) return null;
  return (
    <p className={aviso.ok ? "aviso-ok" : "aviso-error"} role={aviso.ok ? "status" : "alert"}>
      {aviso.texto}
    </p>
  );
}

type PropsReintentar = { alReintentar: () => void; children?: ReactNode };

/** El botón para volver a pedir lo que falló; `children` suma otra salida al lado. */
export function Reintentar({ alReintentar, children }: PropsReintentar) {
  return (
    <div className="formulario">
      <div className="acciones">
        <button type="button" onClick={alReintentar}>
          Reintentar
        </button>
        {children}
      </div>
    </div>
  );
}

type PropsFalla = PropsReintentar & { avisar: (cambio: (actual: Aviso | null) => Aviso | null) => void };

/** La lectura de la pantalla no llegó. El porqué va en el aviso, salvo que este guarde un «Listo».
 * Cuando vuelve la red se repite sola. */
export function FallaDeLectura({ alReintentar, avisar, children }: PropsFalla) {
  useAlVolverLaRed(true, () => {
    alReintentar();
    avisar(conLaRedDeVuelta);
  });
  return (
    <div className="tarjeta">
      <p>No pudimos cargar los datos de esta pantalla.</p>
      <Reintentar alReintentar={alReintentar}>{children}</Reintentar>
    </div>
  );
}

type PropsConsorcio = { pantalla: PantallaConsorcio; reintentar: () => void };

/** La primera consulta, la de los edificios, no llegó. */
export function ConsorcioSinServicio({ pantalla, reintentar }: PropsConsorcio) {
  useAlVolverLaRed(pantalla.tipo === "sin_servicio", reintentar);
  if (pantalla.tipo !== "sin_servicio") return null;
  return (
    <>
      <p className="aviso-error" role="alert">
        {pantalla.deConexion ? SIN_CONEXION : SERVICIO_NO_DISPONIBLE}
      </p>
      <Reintentar alReintentar={reintentar} />
    </>
  );
}
