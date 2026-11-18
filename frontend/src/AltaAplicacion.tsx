import { type ChangeEvent, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { useAvisoAlSalir } from "./avisoAlSalir";
import { CodigoQr } from "./CodigoQr";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { ErrorDeCampo, marcaDe } from "./ErrorDeCampo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { Hoja } from "./Hoja";
import { useAEntrar } from "./sesionEncima";
import { type FinDeEnvio, VentanaDeFormulario } from "./VentanaDeFormulario";

// El alta de la aplicación de códigos en una ventana encima de lo que se estaba haciendo (FL-CTA-03):
// la usa la página de la confirmación, la hoja que la pide en medio de un acto y el aviso del inicio.
// La ventana carga la cuenta en la aplicación (QR o clave a mano; en un teléfono, primero abrirla) y
// verifica el código solo al sexto número (CTA-09). Mientras el código viaja no se puede salir: la
// respuesta trae los códigos de recuperación, que llegan una sola vez (revisión de #237).

type Alta = components["schemas"]["AltaTotp"];

const ESTADO_SIN_SESION = 401;
const ESTADO_DEMASIADOS = 429;
const CODIGO_INVALIDO = "segundo_factor_invalido";
const CODIGO_SIN_ALTA = "segundo_factor_sin_alta";

export const ALTA_VENCIDA = "El alta venció o se reemplazó desde otro dispositivo. Hay que empezar de nuevo.";
export const USAR_APLICACION = "Usar una aplicación de códigos";

/** Una pantalla táctil (el teléfono donde está la aplicación de códigos) no puede escanear su propio QR. */
function pantallaTactil(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

/** Agrupa el secreto en bloques de cuatro, para copiarlo a mano sin perderse. */
export function secretoEnBloques(secreto: string): string {
  return secreto.match(/.{1,4}/g)?.join(" ") ?? "";
}

/** El código de la aplicación (seis números) como lo escribió la persona, o null. */
export function codigoAplicacion(valor: string): string | null {
  const limpio = valor.replace(/[\s-]/g, "");
  return /^[0-9]{6}$/.test(limpio) ? limpio : null;
}

/** El motivo de un código rechazado al confirmar. */
function rechazoDelCodigo(status: number, cuerpo: unknown): string {
  if (status === ESTADO_DEMASIADOS) return "Hubo demasiados intentos. Reintentar en un rato.";
  if (codigoDe(cuerpo) === CODIGO_INVALIDO)
    return "El código no sirve. Hay que revisar que la hora del teléfono esté bien y usar el código nuevo.";
  // E53: un 5xx o un 429 no es de la conexión (regla E34 de fallas.ts).
  return mensajeDeFalla(status, codigoDe(cuerpo));
}

interface PropsVentana {
  alta: Alta;
  /** Quedó activada: los diez códigos de recuperación, que no se vuelven a mostrar. */
  alActivar: (codigos: string[]) => void;
  /** «Cancelar» o Escape: no se activó nada. */
  alCerrar: () => void;
  /** El alta venció o se reemplazó desde otro dispositivo: hay que empezar de nuevo. */
  alVencer: () => void;
  /** Un 401 de una sesión que este navegador no conocía (la hoja de entrar encima no lo resuelve). */
  alSinSesion: () => void;
  /** Empieza (true) o termina (false) el pedido que trae los códigos. */
  alOcupar?: (ocupado: boolean) => void;
}

export function VentanaAltaAplicacion({ alta, alActivar, alCerrar, alVencer, alSinSesion, alOcupar }: PropsVentana) {
  const id = useId();
  const [codigo, setCodigo] = useState("");
  // Lo escrito, leído en el mismo paso en que se completa el sexto número (el estado llega después).
  const codigoEscrito = useRef("");
  const [errorCodigo, setErrorCodigo] = useState<string | undefined>(undefined);

  /** El envío de la ventana: con el código escrito hasta ahora (llega solo al sexto número). */
  async function confirmar(): Promise<FinDeEnvio> {
    const limpio = codigoAplicacion(codigoEscrito.current);
    if (!limpio) {
      setErrorCodigo("Hacen falta los seis números que muestra la aplicación.");
      return "seguir";
    }
    alOcupar?.(true);
    let respuesta;
    try {
      respuesta = await api.POST("/api/v1/auth/totp/confirmar", { body: { codigo: limpio }, headers: encabezadoCsrf() });
    } catch {
      return { rechazo: SIN_CONEXION };
    } finally {
      alOcupar?.(false);
    }
    const { data, error, response } = respuesta;
    if (data !== undefined) {
      alActivar(data.codigos);
      return "seguir";
    }
    if (codigoDe(error) === CODIGO_SIN_ALTA) {
      alVencer();
      return "seguir";
    }
    if (response.status === ESTADO_SIN_SESION) {
      alSinSesion();
      return "seguir";
    }
    return { rechazo: rechazoDelCodigo(response.status, error) };
  }

  function escribirCodigo(evento: ChangeEvent<HTMLInputElement>) {
    const valor = evento.target.value;
    codigoEscrito.current = valor;
    setCodigo(valor);
    setErrorCodigo(undefined);
    // Al sexto número se verifica solo, como en la hoja de confirmar (CTA-09).
    if (codigoAplicacion(valor) !== null) evento.target.form?.requestSubmit();
  }

  return (
    <VentanaDeFormulario titulo="Cargar la cuenta en la aplicación" textoEnviar="Activar la aplicación" alEnviar={confirmar} alCerrar={alCerrar}>
      <p>
        Con una aplicación de autenticación en el teléfono (Google Authenticator, Microsoft
        Authenticator, Aegis u otra), las acciones delicadas piden además un código de seis
        números que cambia cada 30 segundos.
      </p>
      {pantallaTactil() ? (
        // En el teléfono de la aplicación, el QR no se puede escanear: primero, abrirla desde acá.
        <ol className="pasos">
          <li>
            <a href={alta.uri} className="enlace-suelto">
              Abrir mi aplicación de códigos
            </a>{" "}
            y aceptar la cuenta nueva. Si no se abre, escribir esta clave a mano en la aplicación:{" "}
            <code className="secreto" translate="no">
              {secretoEnBloques(alta.secreto)}
            </code>
            <details className="historial">
              <summary>Estoy en otra pantalla: mostrar el código QR</summary>
              <CodigoQr texto={alta.uri} descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación" />
            </details>
          </li>
          <li>Escribir el código de seis números que muestra la aplicación.</li>
        </ol>
      ) : (
        <ol className="pasos">
          <li>
            Abrir la aplicación de autenticación en el teléfono y elegir agregar una cuenta
            escaneando un código QR.
            <CodigoQr texto={alta.uri} descripcion="Código QR para cargar Consorcio en Mano en la aplicación de autenticación" />
          </li>
          <li>
            Si no se puede escanear, <a href={alta.uri}>abrir la aplicación desde acá</a> o escribir esta clave a mano:{" "}
            <code className="secreto" translate="no">
              {secretoEnBloques(alta.secreto)}
            </code>
          </li>
          <li>Escribir el código de seis números que muestra la aplicación.</li>
        </ol>
      )}
      <label htmlFor={`${id}-codigo`}>Código de la aplicación</label>
      <input
        id={`${id}-codigo`}
        data-foco
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={9}
        value={codigo}
        {...marcaDe(`${id}-codigo`, errorCodigo)}
        onChange={escribirCodigo}
      />
      <ErrorDeCampo idCampo={`${id}-codigo`} mensaje={errorCodigo} />
    </VentanaDeFormulario>
  );
}

interface PropsActivar {
  /** Quedó activada: los códigos de recuperación, que quien usa el botón muestra hasta que se guardan. */
  alActivar: (codigos: string[]) => void;
  /** Empieza (true) o termina (false) el pedido que trae los códigos. */
  alOcupar?: (ocupado: boolean) => void;
  /** Botón principal (por omisión) o secundario. */
  secundario?: boolean;
}

/** El botón que da de alta la aplicación ahí mismo, sin ir a otra pantalla ni perder lo escrito. */
export function ActivarAplicacion({ alActivar, alOcupar, secundario = false }: PropsActivar) {
  const aEntrar = useAEntrar(borrarCopias);
  const [alta, setAlta] = useState<Alta | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  useAvisoAlSalir(ocupado);

  function ocupar(valor: boolean) {
    setOcupado(valor);
    alOcupar?.(valor);
  }

  function empezar() {
    setEnviando(true);
    setFalla(null);
    void api
      .POST("/api/v1/auth/totp/alta", { headers: encabezadoCsrf() })
      .then(({ data, error, response }) => {
        if (data !== undefined) setAlta(data);
        else if (response.status === ESTADO_SIN_SESION) aEntrar();
        else setFalla(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => setFalla(SIN_CONEXION))
      .finally(() => setEnviando(false));
  }

  return (
    <>
      <button type="button" className={secundario ? "secundario" : undefined} disabled={enviando} onClick={empezar}>
        {USAR_APLICACION}
      </button>
      {falla !== null && (
        <p className="aviso-error" role="alert">
          {falla}
        </p>
      )}
      {ocupado && (
        <Hoja titulo="Activando">
          <p role="status">Esperando la respuesta. Los códigos de recuperación aparecen acá.</p>
        </Hoja>
      )}
      {alta !== null && (
        <VentanaAltaAplicacion
          alta={alta}
          alOcupar={ocupar}
          alActivar={(codigos) => {
            setAlta(null);
            alActivar(codigos);
          }}
          alCerrar={() => setAlta(null)}
          alVencer={() => {
            setAlta(null);
            setFalla(ALTA_VENCIDA);
          }}
          alSinSesion={aEntrar}
        />
      )}
    </>
  );
}
