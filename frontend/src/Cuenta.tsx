import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { type EstadoContrasena, SeccionContrasena } from "./SeccionContrasena";
import { RUTA_SEGUNDO_FACTOR } from "./SegundoFactor";

// Página /cuenta ("Mi cuenta", RF04): quién es la persona de la sesión, sus sesiones vigentes
// con la actual marcada (cada una se puede cerrar, o todas las otras de una vez), salir, y el
// estado del segundo factor con el enlace a su página, y la contraseña (SeccionContrasena). Sin sesión manda a /entrar, igual que
// después de cerrar la sesión actual. Diseño provisorio: el definitivo llega con G3.

type Yo = components["schemas"]["SesionActual"];
type Sesion = components["schemas"]["SesionDispositivo"];

export const RUTA_CUENTA = "/cuenta";

const ESTADO_SIN_SESION = 401;
const ESTADO_NO_EXISTE = 404;
const SIN_CONEXION = "No pudimos completar el pedido. Revisá la conexión y probá de nuevo.";

const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});

const fecha = (iso: string) => FORMATO_FECHA.format(new Date(iso));

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "lista"; yo: Yo; sesiones: Sesion[]; segundoFactor: boolean; contrasena: EstadoContrasena };

type Aviso = { texto: string; ok: boolean };

const fallo = (texto: string): Aviso => ({ texto, ok: false });

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO_SIN_SESION) throw new SinSesion();
}

export function Cuenta() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const consultado = useRef(false);

  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);

  /** Ante un error: sin sesión, a /entrar; si no, el aviso de conexión. */
  const atajar = useCallback(
    (e: unknown) => {
      if (e instanceof SinSesion) aEntrar();
      else setAviso(fallo(SIN_CONEXION));
    },
    [aEntrar],
  );

  const consultar = useCallback(() => {
    void Promise.all([
      api.GET("/api/v1/auth/sesion"),
      api.GET("/api/v1/auth/sesiones"),
      api.GET("/api/v1/auth/totp"),
      api.GET("/api/v1/auth/contrasena"),
    ])
      .then(([sesion, sesiones, totp, contrasena]) => {
        for (const r of [sesion, sesiones, totp, contrasena]) exigirSesion(r.response);
        if (
          sesion.data === undefined ||
          sesiones.data === undefined ||
          totp.data === undefined ||
          contrasena.data === undefined
        )
          setPantalla({ tipo: "sin_servicio" });
        else
          setPantalla({
            tipo: "lista",
            yo: sesion.data,
            sesiones: sesiones.data.elementos,
            segundoFactor: totp.data.activo,
            contrasena: contrasena.data,
          });
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else setPantalla({ tipo: "sin_servicio" });
      });
  }, [aEntrar]);

  useEffect(() => {
    // StrictMode monta dos veces: las consultas salen una sola vez.
    if (consultado.current) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  /** Saca de la lista las sesiones que ya no están vigentes. */
  function quitar(seQueda: (s: Sesion) => boolean) {
    setPantalla((actual) =>
      actual.tipo === "lista" ? { ...actual, sesiones: actual.sesiones.filter(seQueda) } : actual,
    );
  }

  function cerrar(sesion: Sesion) {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/sesiones/{sesion_id}/revocar", {
        params: { path: { sesion_id: sesion.id } },
        headers: encabezadoCsrf(),
      })
      .then(({ response }) => {
        exigirSesion(response);
        // 404: la sesión ya no existe (venció o se cerró desde otro lado); el resultado es el mismo.
        if (!response.ok && response.status !== ESTADO_NO_EXISTE) throw new Error(String(response.status));
        if (sesion.actual) {
          aEntrar();
          return;
        }
        quitar((s) => s.id !== sesion.id);
        setAviso({ texto: `Listo: cerramos la sesión de ${sesion.dispositivo}.`, ok: true });
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  function cerrarOtras() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/sesiones/revocar-otras", { headers: encabezadoCsrf() })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (data === undefined) throw new Error(String(response.status));
        quitar((s) => s.actual);
        setAviso({
          texto:
            data.revocadas === 1
              ? "Listo: cerramos 1 sesión en otro dispositivo."
              : `Listo: cerramos ${data.revocadas} sesiones en otros dispositivos.`,
          ok: true,
        });
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  function salir() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/salir", { headers: encabezadoCsrf() })
      .then(({ response }) => {
        exigirSesion(response);
        if (!response.ok) throw new Error(String(response.status));
        // Al salir no queda ninguna copia de los contactos (H2b_7 §5).
        borrarCopias();
        aEntrar();
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  const hayOtras = pantalla.tipo === "lista" && pantalla.sesiones.some((s) => !s.actual);
  const conMensaje = aviso !== null || pantalla.tipo !== "lista";

  return (
    <>
      <h1>Mi cuenta</h1>

      {pantalla.tipo === "lista" && (
        <>
          <section aria-labelledby={`${id}-yo`} className="tarjeta">
            <h2 id={`${id}-yo`}>Tus datos</h2>
            <dl className="detalles">
              <dt>Nombre</dt>
              <dd>{pantalla.yo.nombre}</dd>
              <dt>Correo</dt>
              <dd>{pantalla.yo.correo}</dd>
            </dl>
            <button type="button" onClick={salir} disabled={enviando}>
              Salir
            </button>
          </section>

          <section aria-labelledby={`${id}-sesiones`} className="tarjeta">
            <h2 id={`${id}-sesiones`}>Dónde tenés la sesión abierta</h2>
            <ul className="sesiones">
              {pantalla.sesiones.map((s) => (
                <li key={s.id} aria-current={s.actual ? "true" : undefined}>
                  <h3 id={`${id}-${s.id}`}>
                    {s.dispositivo}
                    {s.actual && <span className="sesion-actual"> (este dispositivo)</span>}
                  </h3>
                  <dl className="detalles">
                    <dt>Abierta</dt>
                    <dd>{fecha(s.creada_en)}</dd>
                    <dt>Último uso</dt>
                    <dd>{fecha(s.ultimo_uso_en)}</dd>
                    <dt>Vence</dt>
                    <dd>{fecha(s.vence_en)}</dd>
                  </dl>
                  <button
                    type="button"
                    onClick={() => cerrar(s)}
                    disabled={enviando}
                    aria-describedby={`${id}-${s.id}`}
                  >
                    Cerrar esta sesión
                  </button>
                </li>
              ))}
            </ul>
            {hayOtras && (
              <button type="button" onClick={cerrarOtras} disabled={enviando}>
                Cerrar las otras sesiones
              </button>
            )}
          </section>

          <section aria-labelledby={`${id}-factor`} className="tarjeta">
            <h2 id={`${id}-factor`}>Segundo factor</h2>
            <p>
              {pantalla.segundoFactor
                ? "Está activado: las acciones delicadas te piden un código de la aplicación."
                : "No está activado."}
            </p>
            <p>
              <Link to={RUTA_SEGUNDO_FACTOR}>
                {pantalla.segundoFactor ? "Ver el segundo factor" : "Activar el segundo factor"}
              </Link>
            </p>
          </section>

          <SeccionContrasena
            estado={pantalla.contrasena}
            correo={pantalla.yo.correo}
            segundoFactor={pantalla.segundoFactor}
            fecha={fecha}
            alGuardar={(guardada) => {
              setPantalla((actual) =>
                actual.tipo === "lista"
                  ? { ...actual, contrasena: { definida: true, cambiada_en: guardada.cambiada_en } }
                  : actual,
              );
              // Cambiar una existente cierra las sesiones de los otros dispositivos.
              if (guardada.sesiones_cerradas > 0) quitar((s) => s.actual);
            }}
            sinSesion={aEntrar}
          />
        </>
      )}

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <p className="aviso-error">
            No pudimos consultar tu cuenta. Revisá la conexión y volvé en un rato.
          </p>
        )}
        {aviso && <p className={aviso.ok ? "aviso-ok" : "aviso-error"}>{aviso.texto}</p>}
      </div>
    </>
  );
}
