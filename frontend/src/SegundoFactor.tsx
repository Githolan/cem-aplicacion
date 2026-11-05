import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { CodigoQr } from "./CodigoQr";
import { borrarCopias } from "./copiaContactos";
import { RUTA_CUENTA } from "./Cuenta";
import { encabezadoCsrf } from "./csrf";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { RUTA_ENTRAR } from "./enlace";
import { esDeConexion, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { SeccionLlave } from "./SeccionLlave";

// Página /cuenta/segundo-factor (TB-CEM-3): la persona da de alta una aplicación de
// autenticación (QR o secreto a mano), la confirma con el primer código, guarda los diez
// códigos de recuperación, que se muestran una sola vez, y puede verificar en esta sesión y
// desactivarla. Desactivar pide haber verificado en los últimos 10 minutos; el backend lo exige
// y, si falta, la página lo pide acá mismo y repite el pedido. Terminada una acción se sigue a
// /cuenta, o a `state.volver` si se llegó desde otra pantalla. Los códigos de recuperación, los de
// la aplicación y los de la llave, son la única pantalla que no se va sola: ninguna respuesta la
// reemplaza hasta que la persona dice que los guardó. Diseño provisorio: el definitivo llega con G3.

type EstadoFactor = components["schemas"]["EstadoSegundoFactor"];
type Alta = components["schemas"]["AltaTotp"];

export const RUTA_SEGUNDO_FACTOR = "/cuenta/segundo-factor";

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
const CODIGO_INVALIDO = "segundo_factor_invalido";
const CODIGO_SIN_ALTA = "segundo_factor_sin_alta";
const CODIGO_YA_ACTIVO = "segundo_factor_ya_activo";
const CODIGO_REQUERIDO = "segundo_factor_requerido";

/** Agrupa el secreto en bloques de cuatro, para copiarlo a mano sin perderse. */
export function secretoEnBloques(secreto: string): string {
  return secreto.match(/.{1,4}/g)?.join(" ") ?? "";
}

/** El código de la aplicación (seis números) como lo escribió la persona, o null. */
export function codigoAplicacion(valor: string): string | null {
  const limpio = valor.replace(/[\s-]/g, "");
  return /^[0-9]{6}$/.test(limpio) ? limpio : null;
}

/**
 * Lo que acepta la verificación: el código de la aplicación o uno de recuperación
 * (`k7m2q-x9p4t`, con o sin guion, en mayúsculas o minúsculas), en la forma del contrato.
 */
export function codigoVerificacion(valor: string): string | null {
  const aplicacion = codigoAplicacion(valor);
  if (aplicacion) return aplicacion;
  const limpio = valor.replace(/[\s-]/g, "").toLowerCase();
  return /^[a-z2-9]{10}$/.test(limpio) ? `${limpio.slice(0, 5)}-${limpio.slice(5)}` : null;
}

/** La pantalla desde la que se llegó, si la navegación la trae. */
function volverDe(estado: unknown): string | null {
  if (typeof estado !== "object" || estado === null || !("volver" in estado)) return null;
  return typeof estado.volver === "string" ? estado.volver : null;
}

const ACTIVADO = {
  aplicacion: "Listo: el segundo factor quedó activado.",
  llave: "Listo: tu llave quedó activada.",
};

const FORMATO_FECHA = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "long",
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});
const FORMATO_HORA = new Intl.DateTimeFormat("es-AR", {
  timeStyle: "short",
  hourCycle: "h23",
  timeZone: "America/Argentina/Buenos_Aires",
});

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_sesion" }
  | { tipo: "sin_servicio"; deConexion: boolean }
  | { tipo: "inactivo" }
  | { tipo: "alta"; alta: Alta }
  | { tipo: "codigos"; codigos: string[]; medio: keyof typeof ACTIVADO }
  | { tipo: "activo"; estado: EstadoFactor }
  | { tipo: "terminado"; aviso: string };

type Aviso = { texto: string; ok: boolean };

/** Lo que espera a que la persona confirme que es ella para repetirse. */
type Pendiente = { acto: string; reintentar: () => void };

const fallo = (texto: string): Aviso => ({ texto, ok: false });

/** Respuesta de error con el cuerpo uniforme (`Error`), para leer su código. */
function codigoDe(error: unknown): string | undefined {
  return typeof error === "object" && error !== null && "codigo" in error
    ? String(error.codigo)
    : undefined;
}

export function SegundoFactor() {
  const id = useId();
  const navegar = useNavigate();
  const lugar = useLocation();
  // El estado del historial puede cambiar mientras se está en la página: vale el de la llegada.
  const [volver] = useState(() => volverDe(lugar.state));
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [conLlave, setConLlave] = useState(false);
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const consultado = useRef(false);

  /** Cambia de pantalla, salvo que estén a la vista códigos que no se pueden volver a mostrar. */
  const pasarA = useCallback((siguiente: Pantalla) => {
    setPantalla((actual) => (actual.tipo === "codigos" ? actual : siguiente));
  }, []);

  const terminar = useCallback((texto: string) => pasarA({ tipo: "terminado", aviso: texto }), [pasarA]);

  const mostrarCodigosDeLlave = useCallback((codigos: string[]) => {
    setPantalla({ tipo: "codigos", codigos, medio: "llave" });
  }, []);

  const consultar = useCallback(() => {
    setAviso(null);
    void api
      .GET("/api/v1/auth/totp")
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setConLlave(data.llaves > 0);
          pasarA(data.activo ? { tipo: "activo", estado: data } : { tipo: "inactivo" });
        } else if (response.status === ESTADO_SIN_SESION) pasarA({ tipo: "sin_sesion" });
        else pasarA({ tipo: "sin_servicio", deConexion: esDeConexion(response.status, codigoDe(error)) });
      })
      .catch(() => pasarA({ tipo: "sin_servicio", deConexion: true }));
  }, [pasarA]);

  useEffect(() => {
    // StrictMode monta dos veces: la consulta sale una sola.
    if (consultado.current) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  // Las dos salidas de la página pasan por `pasarA`: con códigos a la vista, ninguna ocurre.
  useEffect(() => {
    if (pantalla.tipo === "sin_sesion") {
      // Toda salida a /entrar borra las copias de los contactos (H2b_7 §5).
      borrarCopias();
      navegar(RUTA_ENTRAR, { replace: true });
    } else if (pantalla.tipo === "terminado") navegar(volver ?? RUTA_CUENTA, { state: { aviso: pantalla.aviso } });
  }, [pantalla, volver, navegar]);

  /** El aviso para un código rechazado al confirmar o verificar. */
  function avisoDeRechazo(status: number, cuerpo: unknown): Aviso {
    if (status === ESTADO_DEMASIADOS)
      return fallo("Hubo demasiados intentos. Esperá un rato y probá de nuevo.");
    if (codigoDe(cuerpo) === CODIGO_INVALIDO)
      return fallo(
        "El código no sirve. Revisá que la hora del teléfono esté bien y usá el código nuevo.",
      );
    // E53: un 5xx o un 429 no es de la conexión (regla E34 de fallas.ts).
    return fallo(mensajeDeFalla(status, codigoDe(cuerpo)));
  }

  /** E53: con la sesión vencida, ofrecer entrar de nuevo en cualquier acción, como `empezarAlta`. */
  function rechazar(status: number, cuerpo: unknown) {
    if (status === ESTADO_SIN_SESION) pasarA({ tipo: "sin_sesion" });
    else setAviso(avisoDeRechazo(status, cuerpo));
  }

  function empezarAlta() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/alta", { headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          pasarA({ tipo: "alta", alta: data });
        } else if (codigoDe(error) === CODIGO_YA_ACTIVO) consultar();
        // Quien ya tiene una llave suma la aplicación confirmando con ella.
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setPendiente({ acto: "agregar la aplicación", reintentar: empezarAlta });
        else if (response.status === ESTADO_SIN_SESION) pasarA({ tipo: "sin_sesion" });
        else setAviso(fallo(mensajeDeFalla(response.status, codigoDe(error))));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function confirmar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoAplicacion(codigo);
    if (!limpio) {
      setAviso(fallo("Escribí los seis números que muestra la aplicación."));
      return;
    }
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/confirmar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          setPantalla({ tipo: "codigos", codigos: data.codigos, medio: "aplicacion" });
        } else if (codigoDe(error) === CODIGO_SIN_ALTA) {
          pasarA({ tipo: "inactivo" });
          setAviso(fallo("El alta venció o se reemplazó desde otro dispositivo. Empezá de nuevo."));
        } else rechazar(response.status, error);
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function verificar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const limpio = codigoVerificacion(codigo);
    if (!limpio) {
      setAviso(fallo("Escribí los seis números de la aplicación o un código de recuperación."));
      return;
    }
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/verificar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setCodigo("");
          terminar(`Listo: verificado hasta las ${FORMATO_HORA.format(new Date(data.vale_hasta))}.`);
        } else if (codigoDe(error) === CODIGO_SIN_ALTA) consultar();
        else rechazar(response.status, error);
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function desactivar() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/baja", { headers: encabezadoCsrf() })
      .then(({ error, response }) => {
        if (error === undefined && response.ok) terminar("Listo: el segundo factor quedó desactivado.");
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setPendiente({ acto: "desactivar el segundo factor", reintentar: desactivar });
        else if (response.status === ESTADO_SIN_SESION) pasarA({ tipo: "sin_sesion" });
        else setAviso(fallo(mensajeDeFalla(response.status, codigoDe(error))));
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function reintentarConsulta() {
    setPantalla({ tipo: "cargando" });
    consultar();
  }

  const conMensaje = aviso !== null || ["cargando", "sin_servicio"].includes(pantalla.tipo);

  if (pantalla.tipo === "codigos")
    return (
      <>
        <h1>Segundo factor</h1>
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Guardá tus códigos de recuperación</h2>
          <p className="aviso-ok">{ACTIVADO[pantalla.medio]}</p>
          <p>
            {pantalla.medio === "llave" ? "Si perdés este dispositivo" : "Si perdés el teléfono"}, cada uno
            de estos códigos sirve <strong>una sola vez</strong>{" "}
            {pantalla.medio === "llave" ? "para entrar en su lugar" : "en lugar del de la aplicación"}.
            Guardalos en un lugar seguro: no los vamos a volver a mostrar.
          </p>
          <ul className="codigos-recuperacion" aria-label="Códigos de recuperación" translate="no">
            {pantalla.codigos.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setPantalla({ tipo: "terminado", aviso: ACTIVADO[pantalla.medio] })}
          >
            Ya los guardé
          </button>
        </section>
      </>
    );

  return (
    <>
      <h1>Segundo factor</h1>
      {["inactivo", "alta", "activo"].includes(pantalla.tipo) && (
        <SeccionLlave alCambiar={consultar} alCodigos={mostrarCodigosDeLlave} alTerminar={terminar} />
      )}
      <p>
        Otra opción, si la preferís: con una aplicación de autenticación en el teléfono (Google Authenticator, Microsoft
        Authenticator, Aegis u otra), las acciones delicadas piden además un código de seis
        números que cambia cada 30 segundos.
      </p>

      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            setPendiente(null);
            pendiente.reintentar();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}

      {pantalla.tipo === "inactivo" && pendiente === null && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>{conLlave ? "Está activado con tu llave de acceso" : "Todavía no lo activaste"}</h2>
          <button type="button" onClick={empezarAlta} disabled={enviando}>
            {conLlave ? "Agregar la aplicación" : "Activar el segundo factor"}
          </button>
        </section>
      )}

      {pantalla.tipo === "alta" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Cargá la cuenta en la aplicación</h2>
          <ol className="pasos">
            <li>
              Abrí la aplicación de autenticación y elegí agregar una cuenta escaneando un código
              QR.
              <CodigoQr
                texto={pantalla.alta.uri}
                descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación"
              />
            </li>
            <li>
              Si no podés escanearlo, <a href={pantalla.alta.uri}>abrí la aplicación desde acá</a>{" "}
              o escribí esta clave a mano:{" "}
              <code className="secreto" translate="no">
                {secretoEnBloques(pantalla.alta.secreto)}
              </code>
            </li>
            <li>Escribí el código de seis números que muestra la aplicación.</li>
          </ol>
          <form className="formulario" onSubmit={confirmar} noValidate>
            <label htmlFor={`${id}-codigo`}>Código de la aplicación</label>
            <input
              id={`${id}-codigo`}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={9}
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
            />
            <button type="submit" disabled={enviando}>
              Confirmar
            </button>
          </form>
        </section>
      )}

      {pantalla.tipo === "activo" && pendiente === null && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Está activado</h2>
          <dl className="detalles">
            {pantalla.estado.activado_en && (
              <>
                <dt>Desde</dt>
                <dd>{FORMATO_FECHA.format(new Date(pantalla.estado.activado_en))}</dd>
              </>
            )}
            <dt>Códigos de recuperación sin usar</dt>
            <dd>{pantalla.estado.codigos_recuperacion_restantes}</dd>
            <dt>Verificado en esta sesión</dt>
            <dd>
              {pantalla.estado.verificado_en
                ? FORMATO_FECHA.format(new Date(pantalla.estado.verificado_en))
                : "Todavía no"}
            </dd>
          </dl>
          <form className="formulario" onSubmit={verificar} noValidate>
            <label htmlFor={`${id}-codigo`}>Código de la aplicación o de recuperación</label>
            <input
              id={`${id}-codigo`}
              type="text"
              autoComplete="one-time-code"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={13}
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
            />
            <button type="submit" disabled={enviando}>
              Verificar
            </button>
          </form>
          <h3>Desactivar</h3>
          <p>
            Borra la aplicación cargada y los códigos de recuperación. Te avisamos por correo.
          </p>
          <button type="button" onClick={desactivar} disabled={enviando}>
            Desactivar el segundo factor
          </button>
        </section>
      )}

      <div role="status" aria-live="polite" className={conMensaje ? "tarjeta" : undefined}>
        {pantalla.tipo === "cargando" && <p>Consultando…</p>}
        {pantalla.tipo === "sin_servicio" && (
          <>
            <p className="aviso-error">
              {pantalla.deConexion
                ? "No pudimos consultar el segundo factor. Revisá la conexión y probá de nuevo."
                : `No pudimos consultar el segundo factor. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button type="button" onClick={reintentarConsulta}>
              Reintentar
            </button>
          </>
        )}
        {aviso && <p className={aviso.ok ? "aviso-ok" : "aviso-error"}>{aviso.texto}</p>}
      </div>
    </>
  );
}
