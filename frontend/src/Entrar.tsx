import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "./api/cliente";
import { EntrarConCodigo } from "./EntrarConCodigo";
import { RUTA_ENTRAR, tokenDelFragmento, VIGENCIA_ENLACE_MS } from "./enlace";

// Página /entrar: la que abre el enlace del correo (documento 13, §10.3). El token llega en el
// fragmento (#token=…), que el navegador nunca manda al servidor: no queda en el registro de
// acceso de nginx ni en Referer. La página lo lee, lo borra de la URL y lo canjea con un POST,
// en el cuerpo. El backend comprueba además la cookie nonce del navegador que pidió el enlace.
//
// Antes de borrarlo de la URL lo guarda en sessionStorage (solo de esta pestaña; nunca viaja al
// servidor), así una recarga o una actualización de la aplicación a mitad de camino no lo pierde.
// Lo borra apenas termina el canje, salga bien o mal, y no usa uno guardado hace más que la
// vigencia del enlace.

const CLAVE_GUARDADO = "cem.entrar.enlace";

interface Guardado {
  token: string;
  /** Milisegundos desde la época: después de esto el token guardado ya no se usa. */
  venceEn: number;
}

type Estado = "sin_enlace" | "entrando" | "adentro" | "rechazado";

interface Lectura {
  token: string | null;
  /** El token vino del fragmento y todavía no está guardado. */
  delFragmento: boolean;
}

export function guardarToken(token: string, ahora: number): void {
  const guardado: Guardado = { token, venceEn: ahora + VIGENCIA_ENLACE_MS };
  window.sessionStorage.setItem(CLAVE_GUARDADO, JSON.stringify(guardado));
}

export function olvidarToken(): void {
  window.sessionStorage.removeItem(CLAVE_GUARDADO);
}

/** El token guardado en esta pestaña si no venció; uno vencido se borra. */
export function tokenGuardado(ahora: number): string | null {
  const texto = window.sessionStorage.getItem(CLAVE_GUARDADO);
  if (texto === null) return null;
  const guardado = JSON.parse(texto) as Partial<Guardado>;
  if (typeof guardado.token !== "string" || typeof guardado.venceEn !== "number") {
    // Solo esta página escribe esa clave: otra forma es un error de programación, no un dato.
    throw new Error(`${CLAVE_GUARDADO} en sessionStorage no tiene la forma esperada`);
  }
  if (guardado.venceEn <= ahora) {
    olvidarToken();
    return null;
  }
  return guardado.token;
}

function leerToken(): Lectura {
  const delFragmento = tokenDelFragmento(window.location.hash);
  if (delFragmento !== null) return { token: delFragmento, delFragmento: true };
  return { token: tokenGuardado(Date.now()), delFragmento: false };
}

export function Entrar() {
  // En el inicializador: StrictMode monta dos veces y la segunda vez el fragmento ya no está.
  const [{ token, delFragmento }] = useState(leerToken);
  const [estado, setEstado] = useState<Estado>(token ? "entrando" : "sin_enlace");
  const canjeado = useRef(false);
  const saliendo = useRef(false);

  // Al recargar o irse, el navegador corta el canje en curso y fetch falla: ese canje no
  // terminó, así que el token se queda guardado para la página que viene.
  useEffect(() => {
    const alSalir = () => {
      saliendo.current = true;
    };
    const alVolver = () => {
      saliendo.current = false;
    };
    window.addEventListener("pagehide", alSalir);
    window.addEventListener("pageshow", alVolver);
    return () => {
      window.removeEventListener("pagehide", alSalir);
      window.removeEventListener("pageshow", alVolver);
    };
  }, []);

  useEffect(() => {
    if (!token || canjeado.current) return;
    canjeado.current = true;
    // Primero a sessionStorage, después fuera de la barra de direcciones, del historial y de
    // una captura de pantalla. Uno ya guardado conserva su vencimiento.
    if (delFragmento) guardarToken(token, Date.now());
    window.history.replaceState(window.history.state, "", RUTA_ENTRAR);
    const terminar = (final: Estado) => {
      olvidarToken();
      setEstado(final);
    };
    void api.POST("/api/v1/auth/canje", { body: { token } }).then(
      ({ error }) => terminar(error === undefined ? "adentro" : "rechazado"),
      () => {
        if (!saliendo.current) terminar("rechazado");
      },
    );
  }, [token, delFragmento]);

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
