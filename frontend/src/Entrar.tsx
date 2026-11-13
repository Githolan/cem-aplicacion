import { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router";

import { api } from "./api/cliente";
import { FormasDeEntrar } from "./FormasDeEntrar";
import { EnlaceInstalar } from "./Instalar";
import { RUTA_ENTRAR, tokenDelFragmento, VIGENCIA_ENLACE_MS } from "./enlace";
import { codigoDe, mensajeDeFallaConEnlace } from "./fallas";
import { recordarFormaDeEntrar } from "./formaDeEntrar";
import { useIrTrasEntrar, useSesion } from "./sesion";

// Página /entrar: la que abre el enlace del correo (documento 13, §10.3). El token llega en el
// fragmento (#token=…), que el navegador nunca manda al servidor: no queda en el registro de
// acceso de nginx ni en Referer. La página lo lee, lo borra de la URL y lo canjea con un POST,
// en el cuerpo. El backend comprueba además la cookie nonce del navegador que pidió el enlace.
//
// Antes de borrarlo de la URL lo guarda en sessionStorage (solo de esta pestaña; nunca viaja al
// servidor), así una recarga o una actualización de la aplicación a mitad de camino no lo pierde.
// Lo borra cuando el canje termina o cuando el backend dice que no sirve, y no usa uno guardado
// hace más que la vigencia del enlace. Una falla que no es del enlace (sin red, un 429, un 5xx) no
// lo gasta: se queda para reintentar.
//
// Es el paso de Acceso donde se elige cómo entrar (decisión del propietario del 05-11-2026, §2):
// primero el código por correo (decisión del 12-11-2026, pregunta 16) o la última forma usada en
// este dispositivo y, debajo, las otras formas, de a una por vez, con un solo correo para las tres.
// Si el enlace no sirve acá (vencido, usado o abierto en otro navegador), la página pasa al código
// del mismo correo, que sirve en cualquier navegador (AC-29). Terminada la entrada sigue sola al inicio. Con sesión abierta no hay nada que hacer
// acá y sigue también. Una sesión que vence mientras se trabaja no pasa por acá: se entra encima de
// la pantalla (`EntrarEncima.tsx`).

const CLAVE_GUARDADO = "cem.entrar.enlace";

const ESTADO_ENLACE_INVALIDO = 401;
// El token no tiene la forma del contrato (un enlace cortado): reintentar no lo arregla.
const ESTADO_TOKEN_MAL_FORMADO = 422;

interface Guardado {
  token: string;
  /** Milisegundos desde la época: después de esto el token guardado ya no se usa. */
  venceEn: number;
}

type Estado =
  | { tipo: "sin_enlace" }
  | { tipo: "entrando" }
  | { tipo: "rechazado" }
  | { tipo: "fallo"; mensaje: string };

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
  const [estado, setEstado] = useState<Estado>({ tipo: token ? "entrando" : "sin_enlace" });
  const [correo, setCorreo] = useState("");
  const canjeado = useRef(false);
  const saliendo = useRef(false);
  const terminarEntrada = useIrTrasEntrar();
  const sesion = useSesion(token === null);

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

  function canjear(elToken: string) {
    setEstado({ tipo: "entrando" });
    void api.POST("/api/v1/auth/canje", { body: { token: elToken } }).then(
      ({ error, response }) => {
        if (error === undefined) {
          olvidarToken();
          recordarFormaDeEntrar("correo");
          terminarEntrada("Listo: sesión abierta.");
        } else if (response.status === ESTADO_ENLACE_INVALIDO || response.status === ESTADO_TOKEN_MAL_FORMADO) {
          olvidarToken();
          setEstado({ tipo: "rechazado" });
        } else setEstado({ tipo: "fallo", mensaje: mensajeDeFallaConEnlace(response.status, codigoDe(error)) });
      },
      () => {
        if (!saliendo.current) setEstado({ tipo: "fallo", mensaje: mensajeDeFallaConEnlace(0, undefined) });
      },
    );
  }

  useEffect(() => {
    if (!token || canjeado.current) return;
    canjeado.current = true;
    // Primero a sessionStorage, después fuera de la barra de direcciones, del historial y de
    // una captura de pantalla. Uno ya guardado conserva su vencimiento.
    if (delFragmento) guardarToken(token, Date.now());
    window.history.replaceState(window.history.state, "", RUTA_ENTRAR);
    canjear(token);
    // `canjear` cambia en cada render; el canje sale una sola vez por `canjeado`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, delFragmento]);

  /** Elegir otra forma deja atrás el enlace que falló: ni su error ni su token siguen. */
  function alElegir() {
    olvidarToken();
    setEstado({ tipo: "sin_enlace" });
  }

  if (sesion === "con_sesion") return <Navigate to="/" replace />;

  return (
    <>
      <h1>Entrar a Consorcio en Mano</h1>
      <div role="status" aria-live="polite" className={estado.tipo === "sin_enlace" ? undefined : "tarjeta"}>
        {estado.tipo === "entrando" && <p>Entrando…</p>}
        {estado.tipo === "rechazado" && (
          <p className="aviso-error">
            Este enlace no sirve acá. Escribir el código de 6 números que llegó en el mismo correo;
            si el código venció o ya se usó, pedir uno nuevo.
          </p>
        )}
        {estado.tipo === "fallo" && token !== null && (
          <>
            <p className="aviso-error">{estado.mensaje}</p>
            <button className="secundario" type="button" onClick={() => canjear(token)}>
              Reintentar
            </button>
          </>
        )}
      </div>
      {estado.tipo !== "entrando" && (
        <>
          <FormasDeEntrar
            correo={correo}
            alCambiarCorreo={setCorreo}
            alElegir={alElegir}
            inicial={estado.tipo === "rechazado" ? "codigo" : undefined}
          />
          <EnlaceInstalar />
        </>
      )}
    </>
  );
}
