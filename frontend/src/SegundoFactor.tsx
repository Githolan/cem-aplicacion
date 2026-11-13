import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { CabeceraInterna } from "./CabeceraInterna";
import { CodigoQr } from "./CodigoQr";
import { CodigosRecuperacion } from "./CodigosRecuperacion";
import { useAvisoAlSalir } from "./avisoAlSalir";
import { borrarCopias } from "./copiaContactos";
import { RUTA_CUENTA } from "./Cuenta";
import { encabezadoCsrf } from "./csrf";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { CODIGO_ENTRAR_DE_NUEVO, esDeConexion, mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";
import { Hoja } from "./Hoja";
import { llaveDisponible } from "./llaveAcceso";
import { SeccionLlave } from "./SeccionLlave";
import { pidiendoEntrar, useAEntrar } from "./sesionEncima";

// Página /cuenta/segundo-factor (TB-CEM-3): la persona da de alta una aplicación de
// autenticación (QR o secreto a mano), la confirma con el primer código, guarda los diez
// códigos de recuperación, que se muestran una sola vez, y puede desactivarla. Desactivar pide
// haber verificado en los últimos 10 minutos; el backend lo exige y, si falta, la página lo pide
// acá mismo y repite el pedido: nada se verifica por adelantado. Terminada una acción se sigue a
// /cuenta, o a `state.volver` si se llegó desde otra pantalla. Los códigos de recuperación, los de
// la aplicación y los de la llave, son la única pantalla que no se va sola: ninguna respuesta la
// reemplaza hasta que la persona dice que los guardó. Diseño provisorio: el definitivo llega con G3.
//
// Informe de fricción (tanda 7): la página tiene su cabecera de volver (AC-10); la llave es la única
// acción principal y la aplicación queda como otra opción (AC-15); en un teléfono, el alta empieza
// por abrir la aplicación y el QR queda plegado (AC-17); los códigos se pueden copiar o guardar, y
// con pocos se dice cómo tener otros (AC-18); desactivar pide confirmarlo en una hoja (AC-14).

type EstadoFactor = components["schemas"]["EstadoSegundoFactor"];
type Alta = components["schemas"]["AltaTotp"];

export const RUTA_SEGUNDO_FACTOR = "/cuenta/segundo-factor";

/** Con la llave, el segundo factor ya está: se informa, no se pide nada (comentario 23). */
export const ACTIVADO_CON_LLAVE = "Está activado con la llave de acceso: no hace falta hacer nada más.";

/** Con esta cantidad o menos de códigos sin usar, se avisa cómo tener otros (AC-18). */
export const POCOS_CODIGOS = 3;

/** Una pantalla táctil (el teléfono donde está la aplicación de códigos) no puede escanear su propio QR. */
function pantallaTactil(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

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
  // Toda salida a /entrar borra las copias de los contactos (H2b_7 §5).
  const aEntrar = useAEntrar(borrarCopias);
  const lugar = useLocation();
  // El estado del historial puede cambiar mientras se está en la página: vale el de la llegada.
  const [volver] = useState(() => volverDe(lugar.state));
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [conLlave, setConLlave] = useState(false);
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const [confirmarBaja, setConfirmarBaja] = useState(false);
  // Un alta en camino (llave o aplicación): su respuesta trae los códigos de recuperación, que llegan
  // una sola vez. Hasta que se muestran y la persona dice que los guardó, no se puede salir de la
  // página (revisión de #237).
  const [altaEnCurso, setAltaEnCurso] = useState(false);
  useAvisoAlSalir(altaEnCurso);
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
      aEntrar();
    } else if (pantalla.tipo === "terminado") navegar(volver ?? RUTA_CUENTA, { state: { aviso: pantalla.aviso } });
  }, [pantalla, volver, navegar, aEntrar]);

  /** El aviso para un código rechazado al confirmar. */
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

  /** Con la hoja de entrar encima, la pantalla sigue como está. */
  function sinSesion() {
    if (!pidiendoEntrar()) pasarA({ tipo: "sin_sesion" });
  }

  /** Una falla que no es del código. Pedir una entrada reciente no se dice: lo pide la hoja encima. */
  function avisarFalla(status: number, cuerpo: unknown) {
    if (codigoDe(cuerpo) !== CODIGO_ENTRAR_DE_NUEVO) setAviso(fallo(mensajeDeFalla(status, codigoDe(cuerpo))));
  }

  /** E53: con la sesión vencida, ofrecer entrar de nuevo en cualquier acción, como `empezarAlta`. */
  function rechazar(status: number, cuerpo: unknown) {
    if (status !== ESTADO_SIN_SESION) setAviso(avisoDeRechazo(status, cuerpo));
    else sinSesion();
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
        else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else avisarFalla(response.status, error);
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
    setAltaEnCurso(true);
    void api
      .POST("/api/v1/auth/totp/confirmar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        setAltaEnCurso(false);
        if (data !== undefined) {
          setCodigo("");
          setPantalla({ tipo: "codigos", codigos: data.codigos, medio: "aplicacion" });
        } else if (codigoDe(error) === CODIGO_SIN_ALTA) {
          pasarA({ tipo: "inactivo" });
          setAviso(fallo("El alta venció o se reemplazó desde otro dispositivo. Empezá de nuevo."));
        } else rechazar(response.status, error);
      })
      .catch(() => {
        setAltaEnCurso(false);
        setAviso(fallo(SIN_CONEXION));
      })
      .finally(() => setEnviando(false));
  }

  function desactivar() {
    setConfirmarBaja(false);
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/baja", { headers: encabezadoCsrf() })
      .then(({ error, response }) => {
        if (error === undefined && response.ok) terminar("Listo: el segundo factor quedó desactivado.");
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setPendiente({ acto: "desactivar el segundo factor", reintentar: desactivar });
        else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else avisarFalla(response.status, error);
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  function reintentarConsulta() {
    setPantalla({ tipo: "cargando" });
    consultar();
  }

  const conMensaje = aviso !== null || ["cargando", "sin_servicio"].includes(pantalla.tipo);
  // La llave ya se ofrece arriba como la acción principal: la aplicación queda como otra opción.
  const llaveArriba = llaveDisponible();

  // Los códigos no se sueltan hasta «Ya los guardé»: van en una hoja que deja inerte el resto de la
  // aplicación (la marca de la cabecera, el aviso de versión nueva) y no se cierra con Escape.
  if (pantalla.tipo === "codigos")
    return (
      <>
        <h1>Segundo factor</h1>
        <Hoja titulo="Guardar los códigos de recuperación">
          <p className="aviso-ok">{ACTIVADO[pantalla.medio]}</p>
          <CodigosRecuperacion
            codigos={pantalla.codigos}
            medio={pantalla.medio}
            alGuardar={() => setPantalla({ tipo: "terminado", aviso: ACTIVADO[pantalla.medio] })}
          />
        </Hoja>
      </>
    );

  return (
    <>
      {altaEnCurso ? (
        <h1>Segundo factor</h1>
      ) : (
        <CabeceraInterna
          titulo="Segundo factor"
          volver={volver ?? RUTA_CUENTA}
          destino={volver === null ? "Mi cuenta" : "la pantalla anterior"}
        />
      )}
      {altaEnCurso && (
        <Hoja titulo="Activando">
          <p role="status">Esperando la respuesta. Los códigos de recuperación aparecen acá.</p>
        </Hoja>
      )}
      {["inactivo", "alta", "activo"].includes(pantalla.tipo) && (
        <SeccionLlave
          alCambiar={consultar}
          alCodigos={mostrarCodigosDeLlave}
          alTerminar={terminar}
          alOcupar={setAltaEnCurso}
        />
      )}

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

      {/* Con la llave ya está activado: el estado va como información y la aplicación queda como
          opción secundaria, para que no parezca una tarea pendiente (comentarios 23 y 25). */}
      {pantalla.tipo === "inactivo" && pendiente === null && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          {conLlave && <p className="aviso-info">{ACTIVADO_CON_LLAVE}</p>}
          <h2 id={`${id}-titulo`}>
            {conLlave ? "Otro método, opcional" : llaveArriba ? "Otra opción: una aplicación de códigos" : "Todavía no está activado"}
          </h2>
          <p>
            Con una aplicación de autenticación en el teléfono (Google Authenticator, Microsoft
            Authenticator, Aegis u otra), las acciones delicadas piden además un código de seis
            números que cambia cada 30 segundos.
          </p>
          <button
            type="button"
            className={conLlave || llaveArriba ? "secundario" : undefined}
            onClick={empezarAlta}
            disabled={enviando}
          >
            {conLlave ? "Agregar también la aplicación" : llaveArriba ? "Usar una aplicación de códigos" : "Activar el segundo factor"}
          </button>
        </section>
      )}

      {pantalla.tipo === "alta" && (
        <section aria-labelledby={`${id}-titulo`} className="tarjeta">
          <h2 id={`${id}-titulo`}>Cargar la cuenta en la aplicación</h2>
          {pantallaTactil() ? (
            // En el teléfono de la aplicación, el QR no se puede escanear: primero, abrirla desde acá.
            <ol className="pasos">
              <li>
                <a href={pantalla.alta.uri} className="enlace-suelto">
                  Abrir mi aplicación de códigos
                </a>{" "}
                y aceptar la cuenta nueva. Si no se abre, escribir esta clave a mano en la aplicación:{" "}
                <code className="secreto" translate="no">
                  {secretoEnBloques(pantalla.alta.secreto)}
                </code>
                <details className="historial">
                  <summary>Estoy en otra pantalla: mostrar el código QR</summary>
                  <CodigoQr
                    texto={pantalla.alta.uri}
                    descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación"
                  />
                </details>
              </li>
              <li>Escribir el código de seis números que muestra la aplicación.</li>
            </ol>
          ) : (
            <ol className="pasos">
              <li>
                Abrir la aplicación de autenticación en el teléfono y elegir agregar una cuenta
                escaneando un código QR.
                <CodigoQr
                  texto={pantalla.alta.uri}
                  descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación"
                />
              </li>
              <li>
                Si no se puede escanear, <a href={pantalla.alta.uri}>abrir la aplicación desde acá</a>{" "}
                o escribir esta clave a mano:{" "}
                <code className="secreto" translate="no">
                  {secretoEnBloques(pantalla.alta.secreto)}
                </code>
              </li>
              <li>Escribir el código de seis números que muestra la aplicación.</li>
            </ol>
          )}
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
              Activar la aplicación
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
          </dl>
          {pantalla.estado.codigos_recuperacion_restantes <= POCOS_CODIGOS && (
            <p className="aviso-info">
              {pantalla.estado.codigos_recuperacion_restantes === 1
                ? "Queda 1 código de recuperación."
                : `Quedan ${pantalla.estado.codigos_recuperacion_restantes} códigos de recuperación.`}{" "}
              Para tener otros, desactivar la aplicación y volver a activarla: al activarla llegan
              diez nuevos.
            </p>
          )}
          <h3>Desactivar</h3>
          <p>
            Borra la aplicación cargada y los códigos de recuperación. Se avisa por correo.
          </p>
          <button type="button" className="peligro" onClick={() => setConfirmarBaja(true)} disabled={enviando}>
            Desactivar el segundo factor
          </button>
        </section>
      )}

      {confirmarBaja && (
        <Hoja titulo="¿Desactivar la aplicación de códigos?" alEscapar={() => setConfirmarBaja(false)}>
          <p>
            Se borran la aplicación cargada y los códigos de recuperación.
            {conLlave
              ? " Las acciones delicadas se siguen confirmando con la llave de acceso."
              : " Para aprobar, pagar o conciliar habrá que activar de nuevo un método de confirmación."}
          </p>
          <div className="formulario">
            <div className="acciones">
              <button type="button" className="peligro" onClick={desactivar}>
                Desactivar
              </button>
              <button type="button" className="secundario" data-foco onClick={() => setConfirmarBaja(false)}>
                Volver
              </button>
            </div>
          </div>
        </Hoja>
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
        {aviso && (
          <p className={aviso.ok ? "aviso-ok" : "aviso-error"}>{aviso.texto}</p>
        )}
      </div>
    </>
  );
}
