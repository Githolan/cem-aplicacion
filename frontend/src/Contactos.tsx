import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "./api/cliente";
import {
  borrarCopia,
  borrarCopias,
  conservarSolo,
  type Contacto,
  type Copia,
  guardarCopia,
  hoyEn,
  leerCopias,
  vigentes,
} from "./copiaContactos";
import { RUTA_CONTACTOS_GESTION } from "./ContactosGestion";
import { RUTA_ENTRAR } from "./enlace";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";

// Página /contactos (H2b 2b-7b; doc/diseno/H2b_7_Diseno.md §5 del repositorio CEM): los contactos
// de emergencia vigentes del edificio, que no son personales, para tenerlos a mano también sin
// conexión. Cada lectura buena reemplaza la copia del edificio; un 403 o 404 la borra, y un 401
// borra todas. La lista de edificios descarta las copias de los que ya no figuran. Si la API no
// responde (sin red o caída), se muestra la copia con su fecha, volviendo a aplicar la vigencia con
// el día del consorcio. La lista de gestión (`/contactos/gestion`) nunca se pide ni se guarda acá.

export const RUTA_CONTACTOS = "/contactos";

const ESTADO = { sinSesion: 401, sinPermiso: 403, noEncontrado: 404 } as const;

const TEXTO_FUNCION: Record<Contacto["funcion"], string> = {
  administracion: "Administración",
  encargado: "Encargado o encargada",
  emergencias: "Emergencias",
  aseguradora: "Aseguradora",
  proveedor_critico: "Proveedor crítico",
  servicio_publico: "Servicio público",
  otro: "Otro",
};

const FORMATO_MOMENTO = new Intl.DateTimeFormat("es-AR", { dateStyle: "long", timeStyle: "short" });
const momento = (iso: string) => FORMATO_MOMENTO.format(new Date(iso));

/** Lo que muestra la página para el edificio elegido. */
type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_permiso" }
  | { tipo: "en_linea"; copia: Copia; guardada: boolean }
  | { tipo: "copia"; copia: Copia; mensaje: string }
  | { tipo: "sin_servicio"; mensaje: string };

type Pantalla =
  | { tipo: "cargando" }
  | { tipo: "sin_consorcios" }
  | { tipo: "lista"; consorcios: { id: string; nombre: string }[] }
  /** La API no respondió: los edificios salen de las copias guardadas. */
  | { tipo: "copias"; consorcios: { id: string; nombre: string }[]; mensaje: string };

class SinSesion extends Error {}

function exigirSesion(response: Response) {
  if (response.status === ESTADO.sinSesion) throw new SinSesion();
}

export function Contactos() {
  const id = useId();
  const navegar = useNavigate();
  const [pantalla, setPantalla] = useState<Pantalla>({ tipo: "cargando" });
  const [consorcio, setConsorcio] = useState("");
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const consultado = useRef(false);

  // Sin sesión no queda ninguna copia: el próximo que use el dispositivo no ve las listas.
  const aEntrar = useCallback(() => {
    borrarCopias();
    navegar(RUTA_ENTRAR, { replace: true });
  }, [navegar]);

  /** La API no respondió (sin red o caída): los edificios salen de las copias guardadas. */
  const desdeCopias = useCallback((mensaje: string) => {
    const copias = leerCopias();
    setPantalla({ tipo: "copias", consorcios: copias.map((c) => ({ id: c.consorcio_id, nombre: c.nombre })), mensaje });
    if (copias[0] !== undefined) setConsorcio(copias[0].consorcio_id);
  }, []);

  useEffect(() => {
    if (consultado.current) return;
    consultado.current = true;
    void api
      .GET("/api/v1/me/consorcios")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        // Cualquier falla que no sea un 401 (sin red, 5xx, 429): justo cuando hacen falta, las copias.
        if (data === undefined) {
          desdeCopias(mensajeDeFalla(response.status, codigoDe(error)));
          return;
        }
        // Un edificio que ya no figura (perdió el acceso, u otra cuenta en el mismo dispositivo) no
        // deja su copia.
        conservarSolo(data.map((c) => c.id));
        const primero = data[0];
        if (primero === undefined) {
          // Ya no participa de ningún edificio: ninguna copia sigue valiendo.
          borrarCopias();
          setPantalla({ tipo: "sin_consorcios" });
          return;
        }
        setPantalla({ tipo: "lista", consorcios: data });
        setConsorcio(primero.id);
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else desdeCopias(SIN_CONEXION);
      });
  }, [aEntrar, desdeCopias]);

  useEffect(() => {
    if (consorcio === "" || pantalla.tipo === "cargando" || pantalla.tipo === "sin_consorcios") return;
    const nombre = pantalla.consorcios.find((c) => c.id === consorcio)?.nombre ?? "";
    const sinRed = (mensaje: string) => {
      const copia = leerCopias().find((c) => c.consorcio_id === consorcio);
      setLectura(copia === undefined ? { tipo: "sin_servicio", mensaje } : { tipo: "copia", copia, mensaje });
    };
    if (pantalla.tipo === "copias") {
      sinRed(pantalla.mensaje);
      return;
    }
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/contactos", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        // Un 401, un 403 o un 404 valen aunque la respuesta llegue tarde (otro edificio elegido, o
        // la página cerrada): la sesión o el acceso ya no están, y la copia se borra igual. Solo la
        // pantalla queda detrás del guarda.
        exigirSesion(response);
        const negado = response.status === ESTADO.sinPermiso || response.status === ESTADO.noEncontrado;
        if (negado) borrarCopia(consorcio);
        if (!vigente) return;
        if (data !== undefined) {
          const copia: Copia = { consorcio_id: consorcio, nombre, guardada_en: new Date().toISOString(), lista: data };
          setLectura({ tipo: "en_linea", copia, guardada: guardarCopia(copia) });
        } else if (negado) setLectura({ tipo: "sin_permiso" });
        else sinRed(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
        else if (vigente) sinRed(SIN_CONEXION);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, pantalla, aEntrar]);

  return (
    <>
      <h1>Contactos de emergencia</h1>
      <p className="tarjeta">
        Los teléfonos de la administración, el encargado, emergencias, la aseguradora y los
        proveedores críticos del edificio. Quedan guardados en este dispositivo para verlos también
        sin conexión, y se borran al salir de la cuenta.
      </p>

      {pantalla.tipo !== "cargando" && pantalla.tipo !== "sin_consorcios" && pantalla.consorcios.length > 1 && (
        <div className="formulario formulario-movil tarjeta">
          <label htmlFor={`${id}-consorcio`}>Edificio</label>
          <select
            id={`${id}-consorcio`}
            value={consorcio}
            onChange={(e) => {
              setLectura({ tipo: "cargando" });
              setConsorcio(e.target.value);
            }}
          >
            {pantalla.consorcios.map((c) => (
              <option key={c.id} value={c.id}>
                {c.nombre}
              </option>
            ))}
          </select>
        </div>
      )}

      <div role="status" aria-live="polite">
        {pantalla.tipo === "cargando" && <p className="tarjeta">Consultando…</p>}
        {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Todavía no participás de ningún edificio.</p>}
        {pantalla.tipo === "copias" && pantalla.consorcios.length === 0 && (
          <p className="tarjeta aviso-error">
            {pantalla.mensaje} Este dispositivo no tiene contactos guardados: abrí esta página con conexión
            para guardarlos.
          </p>
        )}
        {pantalla.tipo !== "cargando" && pantalla.tipo !== "sin_consorcios" && pantalla.consorcios.length > 0 && estado()}
      </div>

      {(lectura.tipo === "en_linea" || lectura.tipo === "copia") && lista(lectura.copia, lectura.tipo === "copia")}

      <p className="tarjeta">
        <Link to={RUTA_CONTACTOS_GESTION}>Gestionar la lista</Link> (administración y Consejo)
      </p>
    </>
  );

  function estado() {
    switch (lectura.tipo) {
      case "cargando":
        return <p className="tarjeta">Consultando…</p>;
      case "sin_permiso":
        return <p className="tarjeta">No tenés acceso a los contactos de este edificio.</p>;
      case "sin_servicio":
        return <p className="tarjeta aviso-error">{lectura.mensaje} Este dispositivo no tiene una copia guardada de este edificio.</p>;
      case "copia":
        return (
          <p className="tarjeta aviso-error">
            {lectura.mensaje} Esta es la copia guardada el {momento(lectura.copia.guardada_en)}, y puede no
            tener los últimos cambios
          </p>
        );
      case "en_linea":
        return lectura.guardada ? null : (
          <p className="tarjeta aviso-error">Este navegador no deja guardar la lista: sin conexión no la vas a poder ver.</p>
        );
    }
  }

  function lista(copia: Copia, sinConexion: boolean) {
    const { lista: datos } = copia;
    // Sin conexión, un contacto cuyo fin ya llegó no se muestra aunque la copia sea anterior.
    const contactos = sinConexion ? vigentes(datos.elementos, hoyEn(datos.zona_horaria)) : datos.elementos;
    return (
      <section aria-labelledby={`${id}-lista`} className="tarjeta">
        <h2 id={`${id}-lista`}>{copia.nombre}</h2>
        <p>Actualizado el {momento(copia.guardada_en)}</p>
        {datos.actualizada_en !== null && <p>Último cambio de la lista: {momento(datos.actualizada_en)}</p>}
        {contactos.length === 0 ? (
          <p>El edificio no tiene contactos de emergencia cargados.</p>
        ) : (
          <ul className="sesiones" aria-label="Contactos de emergencia">
            {contactos.map((c) => (
              <li key={c.id}>
                <h3>
                  {TEXTO_FUNCION[c.funcion]}: {c.nombre}
                </h3>
                <dl className="detalles">
                  {c.telefono !== null && (
                    <>
                      <dt>Teléfono</dt>
                      <dd>
                        <a href={`tel:${c.telefono.replace(/[^+0-9]/g, "")}`}>{c.telefono}</a>
                      </dd>
                    </>
                  )}
                  {c.indicaciones !== null && (
                    <>
                      <dt>Indicaciones</dt>
                      <dd>{c.indicaciones}</dd>
                    </>
                  )}
                  {c.observado && (
                    <>
                      <dt>Aviso</dt>
                      <dd>Alguien señaló un dato como incorrecto; la administración lo está revisando.</dd>
                    </>
                  )}
                </dl>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }
}
