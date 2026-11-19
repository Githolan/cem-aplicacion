import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { ALTA_VENCIDA, codigoAplicacion, VentanaAltaAplicacion } from "./AltaAplicacion";
import { CabeceraInterna } from "./CabeceraInterna";
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

export { codigoAplicacion, secretoEnBloques } from "./AltaAplicacion";

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
//
// Tanda 9: el alta de la aplicación va en una ventana con «Cancelar», sin la tarjeta de la llave, y
// el código se verifica solo al sexto número (CTA-09); qué es la aplicación se explica ahí adentro
// (CTA-11). Con la llave a la vista, que ya está activado lo dice su tarjeta (CTA-10). Con pocos
// códigos queda solo el aviso, y la baja se explica en su hoja, no en la página (CTA-12).
//
// Flujos, tanda 10 (FL-CTA-05, app 0111): «Generar códigos nuevos» da diez sin desactivar nada (pide
// confirmar la identidad como la baja), y cuántos quedan se ve también con solo la llave.

type EstadoFactor = components["schemas"]["EstadoSegundoFactor"];
type Alta = components["schemas"]["AltaTotp"];

export const RUTA_SEGUNDO_FACTOR = "/cuenta/segundo-factor";

/** Con la llave, el segundo factor ya está: se informa, no se pide nada (comentario 23). */
export const ACTIVADO_CON_LLAVE = "Está activado con la llave de acceso: no hace falta hacer nada más.";

/** Con esta cantidad o menos de códigos sin usar, se avisa cómo tener otros (AC-18). */
export const POCOS_CODIGOS = 3;

const ESTADO_SIN_SESION = 401;
const CODIGO_YA_ACTIVO = "segundo_factor_ya_activo";
const CODIGO_REQUERIDO = "segundo_factor_requerido";

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
  aplicacion: "Listo: la confirmación con código quedó activada.",
  llave: "Listo: la llave quedó activada.",
};

/** FL-CTA-05 (tanda 10): códigos nuevos sin desactivar el medio. */
export const GENERAR_CODIGOS = "Generar códigos nuevos";
export const GENERANDO = "Generando los códigos";
export const CODIGOS_GENERADOS = "Listo: hay diez códigos de recuperación nuevos. Los anteriores ya no sirven.";

/** Cuántos códigos de recuperación quedan, en una frase. */
export function textoRestantes(n: number): string {
  return n === 1 ? "Queda 1 código de recuperación." : `Quedan ${n} códigos de recuperación.`;
}

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
  | { tipo: "codigos"; codigos: string[]; medio: keyof typeof ACTIVADO; aviso: string }
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
  /** Los códigos de recuperación sin usar: con la aplicación o con solo la llave (FL-CTA-05). */
  const [restantes, setRestantes] = useState(0);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const [confirmarBaja, setConfirmarBaja] = useState(false);
  // Un alta en camino (llave o aplicación): su respuesta trae los códigos de recuperación, que llegan
  // una sola vez. Hasta que se muestran y la persona dice que los guardó, no se puede salir de la
  // página (revisión de #237).
  const [altaEnCurso, setAltaEnCurso] = useState(false);
  /** Un pedido de códigos nuevos en camino (FL-CTA-05): al llegar invalida los guardados, así que
   * hasta que los nuevos se muestran tampoco se puede salir de la página (Codex en el PR #262). */
  const [generando, setGenerando] = useState(false);
  const enCamino = altaEnCurso || generando;
  useAvisoAlSalir(enCamino);
  const consultado = useRef(false);

  /** Cambia de pantalla, salvo que estén a la vista códigos que no se pueden volver a mostrar. */
  const pasarA = useCallback((siguiente: Pantalla) => {
    setPantalla((actual) => (actual.tipo === "codigos" ? actual : siguiente));
  }, []);

  const terminar = useCallback((texto: string) => pasarA({ tipo: "terminado", aviso: texto }), [pasarA]);

  const mostrarCodigosDeLlave = useCallback((codigos: string[]) => {
    setPantalla({ tipo: "codigos", codigos, medio: "llave", aviso: ACTIVADO.llave });
  }, []);

  const consultar = useCallback(() => {
    setAviso(null);
    void api
      .GET("/api/v1/auth/totp")
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          setConLlave(data.llaves > 0);
          setRestantes(data.codigos_recuperacion_restantes);
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

  /** Con la hoja de entrar encima, la pantalla sigue como está. */
  function sinSesion() {
    if (!pidiendoEntrar()) pasarA({ tipo: "sin_sesion" });
  }

  /** Una falla que no es del código. Pedir una entrada reciente no se dice: lo pide la hoja encima. */
  function avisarFalla(status: number, cuerpo: unknown) {
    if (codigoDe(cuerpo) !== CODIGO_ENTRAR_DE_NUEVO) setAviso(fallo(mensajeDeFalla(status, codigoDe(cuerpo))));
  }

  /** E53: con la sesión vencida, ofrecer entrar de nuevo en cualquier acción, como `empezarAlta`. */
  function empezarAlta() {
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/alta", { headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
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

  /** «Cancelar» o Escape en el alta: vuelve a la página sin activar nada. */
  function cancelarAlta() {
    pasarA({ tipo: "inactivo" });
  }

  function desactivar() {
    setConfirmarBaja(false);
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/baja", { headers: encabezadoCsrf() })
      .then(({ error, response }) => {
        if (error === undefined && response.ok) terminar("Listo: la confirmación con código quedó desactivada.");
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setPendiente({ acto: "desactivar la confirmación con código", reintentar: desactivar });
        else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else avisarFalla(response.status, error);
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  /** FL-CTA-05: diez códigos nuevos; los anteriores dejan de servir. Pide confirmar la identidad si
   * no se confirmó hace poco, como desactivar, y repite el pedido. */
  function generarCodigos() {
    const medio = pantalla.tipo === "activo" ? "aplicacion" : "llave";
    setEnviando(true);
    setGenerando(true);
    setAviso(null);
    void api
      .POST("/api/v1/auth/totp/codigos-recuperacion", { headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) setPantalla({ tipo: "codigos", codigos: data.codigos, medio, aviso: CODIGOS_GENERADOS });
        else if (codigoDe(error) === CODIGO_REQUERIDO)
          setPendiente({ acto: "generar códigos de recuperación nuevos", reintentar: generarCodigos });
        else if (response.status === ESTADO_SIN_SESION) sinSesion();
        else avisarFalla(response.status, error);
      })
      .catch(() => setAviso(fallo(SIN_CONEXION)))
      .finally(() => {
        setEnviando(false);
        setGenerando(false);
      });
  }

  /** Cuántos códigos quedan y cómo tener otros (FL-CTA-05): con la aplicación o con solo la llave. */
  function codigosRestantes() {
    return (
      <>
        <p className={restantes <= POCOS_CODIGOS ? "aviso-info" : undefined}>{textoRestantes(restantes)}</p>
        <button type="button" className="secundario" onClick={generarCodigos} disabled={enviando}>
          {GENERAR_CODIGOS}
        </button>
      </>
    );
  }

  function reintentarConsulta() {
    setPantalla({ tipo: "cargando" });
    consultar();
  }

  const conMensaje = aviso !== null || ["cargando", "sin_servicio"].includes(pantalla.tipo);
  // La llave ya se ofrece arriba como la acción principal: la aplicación queda como otra opción.
  const llaveArriba = llaveDisponible();

  // Los códigos no se sueltan hasta «Códigos guardados: seguir»: van en una hoja que deja inerte el resto de la
  // aplicación (la marca de la cabecera, el aviso de versión nueva) y no se cierra con Escape.
  if (pantalla.tipo === "codigos")
    return (
      <>
        <h1>Confirmación con PIN, huella o código</h1>
        <Hoja titulo="Guardar los códigos de recuperación">
          <p className="aviso-ok">{pantalla.aviso}</p>
          <CodigosRecuperacion
            codigos={pantalla.codigos}
            medio={pantalla.medio}
            alGuardar={() => setPantalla({ tipo: "terminado", aviso: pantalla.aviso })}
          />
        </Hoja>
      </>
    );

  return (
    <>
      {enCamino ? (
        <h1>Confirmación con PIN, huella o código</h1>
      ) : (
        <CabeceraInterna
          titulo="Confirmación con PIN, huella o código"
          volver={volver ?? RUTA_CUENTA}
          destino={volver === null ? "Cuenta" : "la pantalla anterior"}
        />
      )}
      {enCamino && (
        <Hoja titulo={generando ? GENERANDO : "Activando"}>
          <p role="status">Esperando la respuesta. Los códigos de recuperación aparecen acá.</p>
        </Hoja>
      )}
      {/* Durante el alta de la aplicación, la llave no queda a la vista (CTA-09). */}
      {["inactivo", "activo"].includes(pantalla.tipo) && (
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
          {/* Con la tarjeta de la llave a la vista, ella ya dice que está activado (CTA-10). */}
          {conLlave && !llaveArriba && <p className="aviso-info">{ACTIVADO_CON_LLAVE}</p>}
          <h2 id={`${id}-titulo`}>
            {conLlave ? "Otro método, opcional" : llaveArriba ? "Otra opción: una aplicación de códigos" : "Todavía no está activado"}
          </h2>
          <button
            type="button"
            className={conLlave || llaveArriba ? "secundario" : undefined}
            onClick={empezarAlta}
            disabled={enviando}
          >
            {conLlave ? "Agregar también la aplicación" : llaveArriba ? "Usar una aplicación de códigos" : "Activar la confirmación"}
          </button>
        </section>
      )}

      {/* FL-CTA-05 (tanda 10): con solo la llave, también se ve cuántos códigos quedan y se generan otros. */}
      {pantalla.tipo === "inactivo" && conLlave && pendiente === null && (
        <section aria-labelledby={`${id}-codigos`} className="tarjeta">
          <h2 id={`${id}-codigos`}>Códigos de recuperación</h2>
          {codigosRestantes()}
        </section>
      )}

      {pantalla.tipo === "alta" && (
        <VentanaAltaAplicacion
          alta={pantalla.alta}
          alOcupar={setAltaEnCurso}
          alActivar={(codigos) => setPantalla({ tipo: "codigos", codigos, medio: "aplicacion", aviso: ACTIVADO.aplicacion })}
          alCerrar={cancelarAlta}
          alVencer={() => {
            pasarA({ tipo: "inactivo" });
            setAviso(fallo(ALTA_VENCIDA));
          }}
          alSinSesion={sinSesion}
        />
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
          </dl>
          {/* FL-CTA-05 (tanda 10): cuántos quedan y «Generar códigos nuevos», sin desactivar nada. */}
          {codigosRestantes()}
          {/* Qué borra la baja lo dice su hoja de confirmación (CTA-12). */}
          <button type="button" className="peligro" onClick={() => setConfirmarBaja(true)} disabled={enviando}>
            Desactivar la confirmación con código
          </button>
        </section>
      )}

      {confirmarBaja && (
        <Hoja titulo="¿Desactivar la aplicación de códigos?" alEscapar={() => setConfirmarBaja(false)}>
          <p>
            Se borran la aplicación cargada y los códigos de recuperación, y se avisa por correo.
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
                ? "No se pudo consultar la confirmación con PIN, huella o código. Hay que revisar la conexión y reintentar."
                : `No se pudo consultar la confirmación con PIN, huella o código. ${SERVICIO_NO_DISPONIBLE}`}
            </p>
            <button className="secundario" type="button" onClick={reintentarConsulta}>
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
