import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import { EntrarConCodigo } from "./EntrarConCodigo";

// Página /entrar: la que abre el enlace del correo (documento 13, §10.3). El token llega en el
// fragmento (#token=…), que el navegador nunca manda al servidor: no queda en el registro de
// acceso de nginx ni en Referer. La página lo lee, lo borra de la URL y lo canjea con un POST,
// en el cuerpo. El backend comprueba además la cookie nonce del navegador que pidió el enlace.

export const RUTA_ENTRAR = "/entrar";
const PARAMETRO_TOKEN = "token";

type Estado = "sin_enlace" | "entrando" | "adentro" | "rechazado";

/** El token del fragmento, o null si no hay. Se lee una vez, antes de borrarlo de la URL. */
export function tokenDelFragmento(fragmento: string): string | null {
  return new URLSearchParams(fragmento.replace(/^#/, "")).get(PARAMETRO_TOKEN);
}

export function Entrar() {
  // En el inicializador: StrictMode monta dos veces y la segunda vez el fragmento ya no está.
  const [token] = useState(() => tokenDelFragmento(window.location.hash));
  const [estado, setEstado] = useState<Estado>(token ? "entrando" : "sin_enlace");
  const canjeado = useRef(false);

  useEffect(() => {
    if (!token || canjeado.current) return;
    canjeado.current = true;
    // Fuera de la barra de direcciones, del historial y de una captura de pantalla.
    window.history.replaceState(window.history.state, "", RUTA_ENTRAR);
    void api
      .POST("/api/v1/auth/canje", { body: { token } })
      .then(({ error }) => setEstado(error === undefined ? "adentro" : "rechazado"))
      .catch(() => setEstado("rechazado"));
  }, [token]);

  return (
    <>
      <h1>Entrar a Consorcio en Mano</h1>
      <div role="status" aria-live="polite" className="tarjeta">
        {estado === "entrando" && <p>Entrando…</p>}
        {estado === "adentro" && (
          <>
            <p className="aviso-ok">Entraste.</p>
            <p>
              <Link to="/">Ir al inicio</Link>
            </p>
          </>
        )}
        {estado === "rechazado" && (
          <p className="aviso-error">
            No pudimos hacerte entrar con este enlace. Puede haber vencido, haberse usado o
            haberse abierto en otro navegador. Pedí uno nuevo desde el mismo navegador donde lo
            vas a abrir.
          </p>
        )}
        {estado === "sin_enlace" && (
          <p>Para entrar, abrí el enlace que te mandamos por correo.</p>
        )}
      </div>
      {(estado === "sin_enlace" || estado === "rechazado") && <EntrarConCodigo />}
    </>
  );
}
