import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { RUTA_ENTRAR } from "./enlace";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { olvidarEdificio } from "./permisos";
import { type EstadoContrasena, SeccionContrasena } from "./SeccionContrasena";
import { SeccionPrivacidad } from "./SeccionPrivacidad";
import { RUTA_SEGUNDO_FACTOR } from "./SegundoFactor";

// Página /cuenta ("Mi cuenta", RF04): quién es la persona de la sesión, sus sesiones vigentes
// con la actual marcada (cada una se puede cerrar, o todas las otras de una vez), salir, y el
// estado del segundo factor con el enlace a su página, y la contraseña (SeccionContrasena). Sin sesión manda a /entrar, igual que
// después de cerrar la sesión actual. Cada parte se lee por su lado: si una falla, esa parte dice qué
// pasó y deja reintentar, y las demás siguen a la vista. Diseño provisorio: el definitivo llega con G3.

type Yo = components["schemas"]["SesionActual"];
type Sesion = components["schemas"]["SesionDispositivo"];

export const RUTA_CUENTA = "/cuenta";

const ESTADO_SIN_SESION = 401;
const ESTADO_NO_EXISTE = 404;

const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});

const fecha = (iso: string) => FORMATO_FECHA.format(new Date(iso));

/** Lo que devolvió la lectura de una parte de la página. */
type Parte<T> = { ok: true; valor: T } | { ok: false; mensaje: string };

interface Partes {
  yo: Parte<Yo>;
  sesiones: Parte<Sesion[]>;
  segundoFactor: Parte<boolean>;
  contrasena: Parte<EstadoContrasena>;
}

type Pantalla = { tipo: "cargando" } | { tipo: "sin_servicio" } | ({ tipo: "lista" } & Partes);

type Aviso = { texto: string; ok: boolean };

const fallo = (texto: string): Aviso => ({ texto, ok: false });

/** Un 401 en cualquier pedido de la página: se va a /entrar. */
class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO_SIN_SESION) throw new SinSesion();
}

/** Lee una parte. Un 401 sigue siendo de toda la página; cualquier otra falla queda en la parte. */
async function leer<D, T>(
  pedido: Promise<{ data?: D; error?: unknown; response: Response }>,
  tomar: (datos: D) => T,
): Promise<Parte<T>> {
  try {
    const { data, error, response } = await pedido;
    exigirSesion(response);
    if (data === undefined) return { ok: false, mensaje: mensajeDeFalla(response.status, codigoDe(error)) };
    return { ok: true, valor: tomar(data) };
  } catch (e) {
    if (e instanceof SinSesion) throw e;
    return { ok: false, mensaje: SIN_CONEXION };
  }
}

const LECTURAS: { [P in keyof Partes]: () => Promise<Partes[P]> } = {
  yo: () => leer(api.GET("/api/v1/auth/sesion"), (datos) => datos),
  sesiones: () => leer(api.GET("/api/v1/auth/sesiones"), (datos) => datos.elementos),
  segundoFactor: () => leer(api.GET("/api/v1/auth/totp"), (datos) => datos.activo || datos.llaves > 0),
  contrasena: () => leer(api.GET("/api/v1/auth/contrasena"), (datos) => datos),
};

interface FallaDeParteProps {
  mensaje: string;
  deshabilitado: boolean;
  alReintentar: () => void;
}

function FallaDeParte({ mensaje, deshabilitado, alReintentar }: FallaDeParteProps) {
  return (
    <>
      <p className="aviso-error" role="alert">
        {mensaje}
      </p>
      <button type="button" onClick={alReintentar} disabled={deshabilitado}>
        Reintentar
      </button>
    </>
  );
}

export function Cuenta() {
  const id = useId();
  const navegar = useNavigate();
  const cliente = useQueryClient();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const consultado = useRef(false);
  const textoAviso = useRef<HTMLParagraphElement>(null);

  // El aviso va en la tarjeta de las sesiones y lleva el foco: al cerrar una, su fila desaparece y
  // en un teléfono el resultado podría quedar fuera de la vista.
  useEffect(() => {
    if (aviso !== null) textoAviso.current?.focus();
  }, [aviso]);

  // Toda salida a /entrar (salir, cerrar la sesión actual o una sesión vencida) borra las copias de
  // los contactos (H2b_7 §5) y el edificio elegido.
  const aEntrar = useCallback(() => {
    borrarCopias();
    olvidarEdificio(cliente);
    navegar(RUTA_ENTRAR, { replace: true });
  }, [navegar, cliente]);

  /** Ante un pedido sin respuesta: sin sesión, a /entrar; si no, el aviso de conexión. */
  const atajar = useCallback(
    (e: unknown) => {
      if (e instanceof SinSesion) aEntrar();
      else setAviso(fallo(SIN_CONEXION));
    },
    [aEntrar],
  );

  const consultar = useCallback(() => {
    void Promise.all([LECTURAS.yo(), LECTURAS.sesiones(), LECTURAS.segundoFactor(), LECTURAS.contrasena()])
      .then(([yo, sesiones, segundoFactor, contrasena]) => {
        // Sin ninguna parte no hay nada que conservar: un solo aviso y no cuatro iguales.
        if (![yo, sesiones, segundoFactor, contrasena].some((parte) => parte.ok))
          setPantalla({ tipo: "sin_servicio" });
        else setPantalla({ tipo: "lista", yo, sesiones, segundoFactor, contrasena });
      })
      .catch(atajar);
  }, [atajar]);

  useEffect(() => {
    // StrictMode monta dos veces: las consultas salen una sola vez.
    if (consultado.current) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  /** Vuelve a leer solo la parte que falló; las demás no se tocan. */
  function reintentar<P extends keyof Partes>(parte: P) {
    setEnviando(true);
    setAviso(null);
    void LECTURAS[parte]()
      .then((lectura) => setPantalla((actual) => (actual.tipo === "lista" ? { ...actual, [parte]: lectura } : actual)))
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  function reintentarTodo() {
    setPantalla({ tipo: "cargando" });
    consultar();
  }

  /** Saca de la lista las sesiones que ya no están vigentes. */
  function quitar(seQueda: (s: Sesion) => boolean) {
    setPantalla((actual) =>
      actual.tipo === "lista" && actual.sesiones.ok
        ? { ...actual, sesiones: { ok: true, valor: actual.sesiones.valor.filter(seQueda) } }
        : actual,
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
      .then(({ error, response }) => {
        exigirSesion(response);
        // 404: la sesión ya no existe (venció o se cerró desde otro lado); el resultado es el mismo.
        if (!response.ok && response.status !== ESTADO_NO_EXISTE) {
          setAviso(fallo(mensajeDeFalla(response.status, codigoDe(error))));
          return;
        }
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
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          setAviso(fallo(mensajeDeFalla(response.status, codigoDe(error))));
          return;
        }
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
      .then(({ error, response }) => {
        exigirSesion(response);
        if (response.ok) aEntrar();
        else setAviso(fallo(mensajeDeFalla(response.status, codigoDe(error))));
      })
      .catch(atajar)
      .finally(() => setEnviando(false));
  }

  const lista = pantalla.tipo === "lista" ? pantalla : null;
  const hayOtras = lista?.sesiones.ok === true && lista.sesiones.valor.some((s) => !s.actual);

  /** El error de una parte con su Reintentar; nada si la lectura salió bien. */
  const fallaDe = (parte: keyof Partes, lectura: Parte<unknown>) =>
    !lectura.ok && (
      <FallaDeParte mensaje={lectura.mensaje} deshabilitado={enviando} alReintentar={() => reintentar(parte)} />
    );

  return (
    <>
      <h1>Mi cuenta</h1>

      {lista !== null && (
        <>
          <section aria-labelledby={`${id}-yo`} className="tarjeta">
            <h2 id={`${id}-yo`}>Tus datos</h2>
            {fallaDe("yo", lista.yo)}
            {lista.yo.ok && (
              <dl className="detalles">
                <dt>Nombre</dt>
                <dd>{lista.yo.valor.nombre}</dd>
                <dt>Correo</dt>
                <dd>{lista.yo.valor.correo}</dd>
              </dl>
            )}
            <button type="button" onClick={salir} disabled={enviando}>
              Salir
            </button>
          </section>

          <section aria-labelledby={`${id}-sesiones`} className="tarjeta">
            <h2 id={`${id}-sesiones`}>Dónde tenés la sesión abierta</h2>
            <div role="status" aria-live="polite">
              {aviso && (
                <p ref={textoAviso} tabIndex={-1} className={aviso.ok ? "aviso-ok" : "aviso-error"}>
                  {aviso.texto}
                </p>
              )}
            </div>
            {fallaDe("sesiones", lista.sesiones)}
            {lista.sesiones.ok && (
              <ul className="sesiones">
                {lista.sesiones.valor.map((s) => (
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
            )}
            {hayOtras && (
              <button type="button" onClick={cerrarOtras} disabled={enviando}>
                Cerrar las otras sesiones
              </button>
            )}
          </section>

          <section aria-labelledby={`${id}-factor`} className="tarjeta">
            <h2 id={`${id}-factor`}>Segundo factor</h2>
            {fallaDe("segundoFactor", lista.segundoFactor)}
            {lista.segundoFactor.ok && (
              <>
                <p>
                  {lista.segundoFactor.valor
                    ? "Está activado: las acciones delicadas te piden confirmar que sos vos."
                    : "No está activado."}
                </p>
                <p>
                  <Link to={RUTA_SEGUNDO_FACTOR}>
                    {lista.segundoFactor.valor ? "Ver el segundo factor" : "Activar el segundo factor"}
                  </Link>
                </p>
              </>
            )}
          </section>

          {lista.yo.ok && lista.segundoFactor.ok && lista.contrasena.ok ? (
            <SeccionContrasena
              estado={lista.contrasena.valor}
              correo={lista.yo.valor.correo}
              segundoFactor={lista.segundoFactor.valor}
              fecha={fecha}
              alGuardar={(guardada) => {
                setPantalla((actual) =>
                  actual.tipo === "lista"
                    ? {
                        ...actual,
                        contrasena: { ok: true, valor: { definida: true, cambiada_en: guardada.cambiada_en } },
                      }
                    : actual,
                );
                // Cambiar una existente cierra las sesiones de los otros dispositivos.
                if (guardada.sesiones_cerradas > 0) quitar((s) => s.actual);
              }}
              sinSesion={aEntrar}
            />
          ) : (
            // El formulario depende de las tres lecturas: se ofrece reintentar la que falta.
            <section aria-labelledby={`${id}-contrasena`} className="tarjeta">
              <h2 id={`${id}-contrasena`}>Contraseña</h2>
              {fallaDe("contrasena", lista.contrasena) ||
                fallaDe("segundoFactor", lista.segundoFactor) ||
                fallaDe("yo", lista.yo)}
            </section>
          )}

          {/* Pide o no la contraseña según esta lectura: sin ella no se puede armar. */}
          {lista.contrasena.ok && (
            <SeccionPrivacidad contrasena={lista.contrasena.valor.definida} fecha={fecha} sinSesion={aEntrar} />
          )}
        </>
      )}

      <div role="status" aria-live="polite" className={lista === null ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <>
            <p className="aviso-error">
              No pudimos consultar tu cuenta. Revisá la conexión y probá de nuevo.
            </p>
            <button type="button" onClick={reintentarTodo}>
              Reintentar
            </button>
          </>
        )}
      </div>
    </>
  );
}
