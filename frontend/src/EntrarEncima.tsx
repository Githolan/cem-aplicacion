import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router";

import { api } from "./api/cliente";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { FormasDeEntrar } from "./FormasDeEntrar";
import { Hoja } from "./Hoja";
import { salirDeLaCuenta } from "./sesion";
import { comprobarSesion, faltaConocerSesion, olvidarRecuperada, olvidarSesion, salidas, useEstadoEncima } from "./sesionEncima";

// La hoja de entrar encima (`sesionEncima.ts`) y el aviso de que se sigue donde se estaba. En /entrar
// no hace falta: la persona ya está entrando, y al terminar se suelta lo que esperaba.

/** Inicio y entrar ya preguntan por la sesión; las otras se usan sin ella: no hay nada que conservar. */
const SIN_LECTURA_PROPIA: ReadonlySet<string> = new Set([RUTA_INICIO, RUTA_ENTRAR, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO]);

/** Sin respuesta tampoco se sabe nada: la sesión sigue sin conocerse y decide el servidor. */
const leerSesion = () => api.GET("/api/v1/auth/sesion", { fetch: comprobarSesion }).then(undefined, () => undefined);

function HojaEntrar({ correo }: { correo: string }) {
  const [escrito, setEscrito] = useState<string | null>(null);
  const [falla, setFalla] = useState<string | null>(null);
  const [saliendo, setSaliendo] = useState(false);

  // Si entró por el enlace del correo, en otra pestaña, al volver acá ya está adentro.
  useEffect(() => {
    const alVolver = () => {
      if (document.visibilityState === "visible") void leerSesion();
    };
    document.addEventListener("visibilitychange", alVolver);
    window.addEventListener("focus", alVolver);
    return () => {
      document.removeEventListener("visibilitychange", alVolver);
      window.removeEventListener("focus", alVolver);
    };
  }, []);

  /** Leer la sesión nueva es lo que cierra la hoja. Si esa lectura falla, se dice y se repite. */
  const seguir = useCallback(() => {
    setFalla(null);
    void api.GET("/api/v1/auth/sesion", { fetch: comprobarSesion }).then(
      ({ data, error, response }) => {
        if (data === undefined) setFalla(mensajeDeFalla(response.status, codigoDe(error)));
      },
      () => setFalla(SIN_CONEXION),
    );
  }, []);

  function salir() {
    setSaliendo(true);
    setFalla(null);
    void salirDeLaCuenta(mensajeDeFalla).then((motivo) => {
      if (motivo === null) salidas.aEntrar();
      else {
        setFalla(motivo);
        setSaliendo(false);
      }
    });
  }

  return (
    <Hoja titulo="La sesión venció" encima>
      <p>Hay que entrar de nuevo para seguir. Lo escrito queda acá.</p>
      <FormasDeEntrar correo={escrito ?? correo} alCambiarCorreo={setEscrito} alEntrar={seguir} />
      {falla !== null && (
        <>
          <p className="aviso-error" role="alert">
            {falla}
          </p>
          <button className="secundario" type="button" onClick={seguir}>
            Reintentar
          </button>
        </>
      )}
      <div className="formulario">
        <div className="acciones">
          <button type="button" className="secundario" disabled={saliendo} onClick={salir}>
            Salir de la cuenta
          </button>
        </div>
      </div>
    </Hoja>
  );
}

export function EntrarEncima() {
  const { pathname } = useLocation();
  const { pidiendo, correo, recuperada } = useEstadoEncima();

  useEffect(() => {
    olvidarRecuperada();
    if (pathname === RUTA_ENTRAR) olvidarSesion();
    if (SIN_LECTURA_PROPIA.has(pathname)) return undefined;
    // Sin saber de quién es la sesión la hoja no se puede abrir: si esta lectura falló, se repite.
    const conocer = () => {
      if (faltaConocerSesion() && document.visibilityState === "visible") void leerSesion();
    };
    conocer();
    window.addEventListener("online", conocer);
    window.addEventListener("focus", conocer);
    document.addEventListener("visibilitychange", conocer);
    return () => {
      window.removeEventListener("online", conocer);
      window.removeEventListener("focus", conocer);
      document.removeEventListener("visibilitychange", conocer);
    };
  }, [pathname]);

  return (
    <>
      {recuperada && (
        <p className="aviso-ok aviso-fijo" role="status">
          La pantalla sigue donde estaba.
        </p>
      )}
      {pidiendo && pathname !== RUTA_ENTRAR && <HojaEntrar correo={correo} />}
    </>
  );
}
