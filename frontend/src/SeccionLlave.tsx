import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ACTIVAR_CON_LLAVE } from "./ActivarLlave";
import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { Hoja } from "./Hoja";
import {
  esDeEsteDispositivo,
  llaveDisponible,
  nombreDelDispositivo,
  quitarLlave,
  recordarLlave,
  registrarLlave,
  type Resultado,
} from "./llaveAcceso";

// La llave de acceso en /cuenta/segundo-factor: activarla es un solo botón, sin aplicación aparte.
// Si el navegador no sabe usarlas, la sección no aparece y queda la aplicación de códigos. Con la
// llave de este dispositivo no queda nada para hacer, y la sección lo dice: confirmar no es un paso
// aparte, se pide en el momento. Cuando falta, `PedirCodigo` ofrece la llave o un código, también
// el de recuperación, y el pedido se repite: quien perdió el dispositivo puede quitar su llave o
// sumar otra. Los códigos de recuperación salen de acá apenas llegan (`alCodigos`): los muestra la
// página, que no los suelta hasta que la persona dice que los guardó, y hasta entonces no se
// consulta nada más.
//
// Informe de fricción (tanda 7): quitar una llave pide confirmarlo en una hoja (AC-14). Si en la
// lista ya hay una llave con el nombre de este equipo, no se invita a agregarlo otra vez: puede ser
// otro navegador del mismo equipo, que la usa igual (AC-12).

type Llave = components["schemas"]["LlaveResumen"];

interface Props {
  /** Cambiaron las llaves: la página vuelve a leer su estado. */
  alCambiar: () => void;
  /** Llegaron códigos de recuperación, que se muestran una sola vez. */
  alCodigos: (codigos: string[]) => void;
  /** Terminó la acción: la página sigue a la pantalla siguiente con este aviso. */
  alTerminar: (aviso: string) => void;
  /** Empieza (true) o termina (false) el alta de una llave: con ella en camino, la página no deja
   * salir, porque los códigos de recuperación llegan una sola vez (revisión de #237). */
  alOcupar?: (ocupado: boolean) => void;
}

export function SeccionLlave({ alCambiar, alCodigos, alTerminar, alOcupar }: Props) {
  const id = useId();
  const [llaves, setLlaves] = useState<Llave[] | null>(null);
  const [fallaConsulta, setFallaConsulta] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [listo, setListo] = useState<string | null>(null);
  const [pendiente, setPendiente] = useState<(() => void) | null>(null);
  const [aQuitar, setAQuitar] = useState<Llave | null>(null);
  const consultado = useRef(false);

  const consultar = useCallback(() => {
    setFallaConsulta(null);
    void api
      .GET("/api/v1/auth/llave")
      .then(({ data, error, response }) => {
        if (data !== undefined) setLlaves(data);
        else setFallaConsulta(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => setFallaConsulta(SIN_CONEXION));
  }, []);

  useEffect(() => {
    // StrictMode monta dos veces: la consulta sale una sola.
    if (consultado.current || !llaveDisponible()) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  if (!llaveDisponible()) return null;

  /** Corre un pedido; si falta confirmar, lo deja pendiente hasta que la persona confirme. */
  function intentar<T>(pedido: () => Promise<Resultado<T>>, alListo: (valor: T) => void, alta = false) {
    setEnviando(true);
    setAviso(null);
    setListo(null);
    if (alta) alOcupar?.(true);
    void pedido()
      .then((r) => {
        // En el mismo paso: la página deja de esperar y ya tiene los códigos a la vista.
        if (alta) alOcupar?.(false);
        if (r.ok) alListo(r.valor);
        else if (r.faltaConfirmar) setPendiente(() => () => intentar(pedido, alListo, alta));
        else if (r.yaEstaAca !== true || !reconocerEste()) setAviso(r.mensaje);
      })
      .finally(() => setEnviando(false));
  }

  /** El navegador dijo que este dispositivo ya tiene llave: es la de la lista con su mismo nombre. */
  function reconocerEste(): boolean {
    const nombre = nombreDelDispositivo(navigator.userAgent);
    const suyas = (llaves ?? []).filter((k) => k.nombre === nombre);
    suyas.forEach((k) => recordarLlave(k.credencial_id));
    return suyas.length > 0;
  }

  function activar() {
    intentar(
      registrarLlave,
      (nuevos) => {
        if (nuevos !== null) alCodigos(nuevos);
        else alTerminar("Listo: la llave quedó activada.");
      },
      true,
    );
  }

  function quitar(llave: Llave) {
    intentar(
      () => quitarLlave(llave.id),
      () => {
        setListo("Listo: la llave se quitó.");
        consultar();
        alCambiar();
      },
    );
  }

  if (pendiente !== null)
    return (
      <PedirCodigo
        acto="seguir"
        alVerificar={() => {
          setPendiente(null);
          pendiente();
        }}
        alCancelar={() => setPendiente(null)}
      />
    );

  const enEste = llaves?.some((k) => esDeEsteDispositivo(k.credencial_id)) ?? false;
  const nombreDeEste = nombreDelDispositivo(navigator.userAgent);
  // Sin la marca de este navegador, una llave con el nombre de este equipo puede ser la suya.
  const conSuNombre = !enEste && (llaves?.some((k) => k.nombre === nombreDeEste) ?? false);

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Llave de acceso</h2>
      <p>
        Se confirma con el PIN, la huella o la cara de este dispositivo, sin escribir códigos y sin
        instalar nada.
      </p>
      {enEste && (
        <p className="aviso-ok">
          La llave ya funciona en este dispositivo. No hace falta nada más: cuando una acción lo
          pida, se confirma ahí mismo.
        </p>
      )}
      {listo !== null && <p className="aviso-ok">{listo}</p>}
      {aviso !== null && (
        <p role="alert" className="aviso-error">
          {aviso}
        </p>
      )}
      {fallaConsulta !== null && (
        <>
          <p role="alert" className="aviso-error">
            {fallaConsulta}
          </p>
          <button className="secundario" type="button" onClick={consultar}>
            Reintentar
          </button>
        </>
      )}
      {llaves !== null && llaves.length > 0 && (
        <ul aria-label="Llaves de la cuenta">
          {llaves.map((k) => (
            <li key={k.id}>
              {k.nombre}
              {esDeEsteDispositivo(k.credencial_id) && " (este dispositivo)"}{" "}
              <button
                type="button"
                className="secundario peligro"
                disabled={enviando}
                onClick={() => setAQuitar(k)}
              >
                Quitar
              </button>
            </li>
          ))}
        </ul>
      )}
      {conSuNombre && (
        <p>Si este equipo es «{nombreDeEste}», no hace falta hacer nada: la llave se confirma al usarla.</p>
      )}
      {llaves !== null && llaves.length > 0 && !enEste && !conSuNombre && (
        <p>Si este dispositivo no está en la lista, agregarlo.</p>
      )}
      {llaves !== null && !enEste && (
        <button type="button" className={conSuNombre ? "secundario" : undefined} onClick={activar} disabled={enviando}>
          {llaves.length === 0 ? ACTIVAR_CON_LLAVE : conSuNombre ? "Es otro equipo: agregarlo" : "Agregar este dispositivo"}
        </button>
      )}
      {aQuitar !== null && (
        <Hoja titulo={`¿Quitar la llave de ${aQuitar.nombre}?`} alEscapar={() => setAQuitar(null)}>
          <p>Para confirmar pagos desde ese equipo habrá que activarla de nuevo.</p>
          <div className="formulario">
            <div className="acciones">
              <button
                type="button"
                className="peligro"
                onClick={() => {
                  setAQuitar(null);
                  quitar(aQuitar);
                }}
              >
                Quitar la llave
              </button>
              <button type="button" className="secundario" data-foco onClick={() => setAQuitar(null)}>
                Volver
              </button>
            </div>
          </div>
        </Hoja>
      )}
    </section>
  );
}
