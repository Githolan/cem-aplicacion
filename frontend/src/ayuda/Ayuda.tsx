import { useEffect, useId, useRef, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_CUENTA } from "../Cuenta";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe, soloDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { fechaCorta } from "../pagos/textos";
import { REQUISITOS } from "../permisos";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import {
  type Aviso,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  mensajeDeActo,
  mensajeDeError,
  renovarClave,
  sinLaFalla,
  sinPisarLoHecho,
} from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { ayudaEnCurso, empezarAAyudar, RUTA_AYUDA } from "./estadoAyuda";

// Página /ayuda (RF53, app 0127). Dos partes, cada una solo si sirve:
// - «Ayudar a otra persona»: las autorizaciones que esta cuenta recibió. «Ayudar a …» empieza la ayuda
//   en esta pestaña (`estadoAyuda.ts`): la aplicación vuelve al inicio como la persona asistida, con lo
//   que la autorización alcanza.
// - «Quién puede ayudar»: el propietario con el vínculo confirmado autoriza a otra cuenta, con un
//   alcance de la lista cerrada, y revoca. Nada se edita: para cambiar el alcance se revoca y se da otra.
// Mientras se ayuda, la página solo deja dejar de ayudar: autorizar es un acto propio.

type Otorgada = components["schemas"]["AyudaOtorgada"];
type Recibida = components["schemas"]["AyudaRecibida"];
type Alcance = components["schemas"]["AlcanceAyuda"];

export { RUTA_AYUDA };
export const TITULO_AYUDA = "Ayuda de otra persona";
export const ENLACE_AYUDA = "Ayuda de otra persona";
export const AYUDAR_A = (nombre: string) => `Ayudar a ${nombre}`;
export const AUTORIZAR = "Autorizar a una persona";
export const CONFIRMAR_AUTORIZAR = "Autorizar";
export const REVOCAR = "Revocar la autorización";
export const CONFIRMAR_REVOCAR = "Sí, revocar";
export const CORREO = "Correo de la persona";
export const AYUDA_CORREO = "La persona entra con su propia cuenta de Consorcio en Mano.";
export const QUE_PUEDE = "Qué puede hacer";
export const NUNCA = "Nunca vota, firma, aprueba ni paga, ni cambia permisos, modos o datos de la cuenta.";
export const FALTA_CORREO = "Falta escribir el correo.";
export const FALTA_ALCANCE = "Falta elegir qué puede hacer.";
export const SIN_AUTORIZADAS = "Nadie tiene autorización para ayudar en este edificio.";
export const SIN_NADA = "Esta cuenta no recibió autorizaciones para ayudar ni tiene unidades para autorizar a otra persona.";
export const SIN_ALMACEN = "Este navegador no deja guardar datos de la pestaña: así no se puede ayudar. Hay que habilitarlo o usar otro navegador.";
/** Ayudando, la página no se usa: autorizar es un acto de cada cuenta. El aviso de arriba la termina. */
export const mientrasSeAyuda = (asistida: string) =>
  `Mientras se ayuda a ${asistida}, esta página no se usa: autorizar a otra persona lo hace cada cuenta por sí misma. Para eso, primero «Dejar de ayudar», en el aviso de arriba.`;
export const VIGENTE = "Vigente";
export const revocadaEl = (fecha: string) => `Revocada el ${fecha}`;
export const textoAutorizada = (nombre: string) => `Se autorizó a ${nombre} a ayudar.`;
export const textoRevocada = (nombre: string) => `Se revocó la autorización de ${nombre}.`;
export const ACLARACION_REVOCAR = "Desde ahora no puede ayudar en este edificio. Para volver a autorizarla se da una autorización nueva.";

/** Cada alcance en palabras. «orientarse» no se otorga: lo tiene toda ayuda y no da datos. */
export const TEXTO_ALCANCE: Record<Alcance, string> = {
  orientarse: "Orientarse en la aplicación",
  consultar_expensas: "Consultar las expensas y los reclamos",
  preparar_reclamos: "Presentar y retirar reclamos sobre la liquidación",
};
/** Lo que un alcance exige (el contrato, `REQUIERE`): presentar reclamos sin verlos no tiene sentido. */
export const REQUIERE: Partial<Record<Alcance, Alcance>> = { preparar_reclamos: "consultar_expensas" };

const textoAlcances = (alcances: readonly Alcance[]) => alcances.map((a) => TEXTO_ALCANCE[a]).join(" · ");

/** Marcar uno marca lo que exige; desmarcar uno desmarca lo que lo exige. */
export function cambiarAlcance(elegidos: readonly Alcance[], alcance: Alcance, marcado: boolean): Alcance[] {
  if (marcado) {
    const requerido = REQUIERE[alcance];
    return [...new Set([...elegidos, alcance, ...(requerido === undefined ? [] : [requerido])])];
  }
  return elegidos.filter((a) => a !== alcance && REQUIERE[a] !== alcance);
}

export function Ayuda() {
  const enCurso = ayudaEnCurso();
  if (enCurso !== null) {
    return (
      <>
        <CabeceraInterna titulo={TITULO_AYUDA} volver={RUTA_CUENTA} destino="Cuenta" />
        <p className="tarjeta">{mientrasSeAyuda(enCurso.asistida)}</p>
      </>
    );
  }
  return <SinAyudaEnCurso />;
}

function SinAyudaEnCurso() {
  const elegido = useConsorcio(REQUISITOS.ayudas);
  const recibidas = useRecibidas(elegido.aEntrar);
  const puedeAutorizar = elegido.pantalla.tipo === "lista" && elegido.pantalla.consorcios.length > 0;
  const nada = recibidas.tipo === "ok" && recibidas.elementos.length === 0 && !puedeAutorizar && elegido.pantalla.tipo !== "cargando";
  return (
    <>
      <CabeceraInterna titulo={TITULO_AYUDA} volver={RUTA_CUENTA} destino="Cuenta" />
      <AyudasRecibidas lectura={recibidas} />
      {puedeAutorizar && <QuienPuedeAyudar key={elegido.consorcio} {...elegido} />}
      {elegido.pantalla.tipo === "sin_servicio" && <ConsorcioSinServicio pantalla={elegido.pantalla} reintentar={elegido.reintentar} />}
      {nada && <p className="tarjeta">{SIN_NADA}</p>}
    </>
  );
}

type LecturaRecibidas = { tipo: "cargando" } | { tipo: "falla"; texto: string; reintentar: () => void } | { tipo: "ok"; elementos: Recibida[] };

function useRecibidas(aEntrar: () => void): LecturaRecibidas {
  const [lectura, setLectura] = useState<{ tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; elementos: Recibida[] }>({
    tipo: "cargando",
  });
  const [vuelta, setVuelta] = useState(0);
  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/me/ayudas")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos });
        else setLectura({ tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [vuelta, aEntrar]);
  if (lectura.tipo !== "falla") return lectura;
  return {
    ...lectura,
    reintentar: () => {
      setLectura({ tipo: "cargando" });
      setVuelta((v) => v + 1);
    },
  };
}

function AyudasRecibidas({ lectura }: { lectura: LecturaRecibidas }) {
  const id = useId();
  const [falla, setFalla] = useState<string | null>(null);
  if (lectura.tipo === "cargando") return <p role="status">Cargando…</p>;
  if (lectura.tipo === "falla")
    return (
      <div className="tarjeta">
        <p className="aviso-error">{lectura.texto}</p>
        <button type="button" className="secundario" onClick={lectura.reintentar}>
          Reintentar
        </button>
      </div>
    );
  if (lectura.elementos.length === 0) return null;

  function ayudar(r: Recibida) {
    const empezo = empezarAAyudar({ autorizacion: r.id, consorcio: r.consorcio_id, edificio: r.consorcio_nombre, asistida: r.otorgante_nombre });
    if (!empezo) setFalla(SIN_ALMACEN);
  }

  return (
    <section aria-labelledby={`${id}-recibidas`} className="tarjeta">
      <h2 id={`${id}-recibidas`}>Ayudar a otra persona</h2>
      <p>{NUNCA}</p>
      {falla !== null && (
        <p className="aviso-error" role="alert">
          {falla}
        </p>
      )}
      <ul className="filas-registro" aria-label="Personas que autorizaron a esta cuenta">
        {lectura.elementos.map((r) => (
          <li key={r.id} className="ayuda-recibida">
            <p>
              <strong>{r.otorgante_nombre}</strong> · {r.consorcio_nombre}
              <br />
              {textoAlcances(r.alcances)}
            </p>
            <button type="button" onClick={() => ayudar(r)}>
              {AYUDAR_A(r.otorgante_nombre)}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Otorgada[]; otorgables: Alcance[] };
type Ventana = { tipo: "autorizar" } | { tipo: "revocar"; ayuda: Otorgada } | null;

function QuienPuedeAyudar({ pantalla, consorcio, setConsorcio, aEntrar, aInicio }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const fichas = useFichas<string>();
  const [ventana, setVentana] = useState<Ventana>(null);
  const [correo, setCorreo] = useState("");
  const [alcances, setAlcances] = useState<Alcance[]>([]);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  const clave = useRef<string | null>(null);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ayudas", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos, otorgables: data.alcances_otorgables });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, aInicio]);

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  function abrirAutorizar() {
    clave.current = null;
    setAviso(null);
    setCorreo("");
    setAlcances([]);
    setErrores(SIN_ERRORES);
    fichas.cerrar();
    setVentana({ tipo: "autorizar" });
  }

  function abrirRevocar(ayuda: Otorgada) {
    clave.current = null;
    setVentana({ tipo: "revocar", ayuda });
  }

  function cerrarTodo() {
    setVentana(null);
    fichas.cerrar();
  }

  /** Lo que hace falta al terminar cualquier acto: la lista se vuelve a leer y el resultado queda arriba. */
  function terminar(texto: string, ok: boolean): FinDeEnvio {
    setAviso({ texto, ok });
    cerrarTodo();
    setVuelta((v) => v + 1);
    return "cerrar";
  }

  async function autorizar(): Promise<FinDeEnvio> {
    const faltan = { ...faltaCompletar({ correo: [correo, FALTA_CORREO] }), ...(alcances.length === 0 ? { alcances: FALTA_ALCANCE } : {}) };
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/ayudas", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { correo: correo.trim(), alcances },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        const marcados = response.status === ESTADO.invalido ? soloDe(erroresDe(error, mensajeDeError(response.status, codigo)), { correo: CORREO }) : SIN_ERRORES;
        if (hayErrores(marcados)) {
          setErrores(marcados);
          return "seguir";
        }
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      return terminar(textoAutorizada(data.autorizado_nombre), true);
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  async function revocar(ayuda: Otorgada): Promise<FinDeEnvio> {
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/ayudas/{autorizacion_id}/revocacion", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: consorcio, autorizacion_id: ayuda.id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        // Ya estaba revocada o ya no está: lo visto cambió, la lista se vuelve a leer.
        if (cambioLoVisto(response.status, codigo)) return terminar(mensajeDeActo(response.status, error), false);
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      return terminar(textoRevocada(data.autorizado_nombre), true);
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const enFicha = elementos.find((a) => a.id === fichas.abierta);
  const estadoDe = (a: Otorgada) => (a.revocada_en === null ? VIGENTE : revocadaEl(fechaCorta(a.revocada_en)));

  return (
    <section aria-labelledby={`${id}-quien`} className="tarjeta">
      <h2 id={`${id}-quien`}>Quién puede ayudar</h2>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      <AvisoPantalla aviso={aviso} />
      {lectura.tipo === "cargando" && <p role="status">Cargando las autorizaciones…</p>}
      {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
      {lectura.tipo === "ok" && (
        <>
          {elementos.length === 0 ? (
            <p>{SIN_AUTORIZADAS}</p>
          ) : (
            <ul className="filas-registro" aria-label="Autorizaciones para ayudar">
              {elementos.map((a) => (
                <li key={a.id}>
                  <FilaDeRegistro
                    titulo={a.autorizado_nombre}
                    resumen={`${estadoDe(a)} · ${textoAlcances(a.alcances)}`}
                    alAbrir={() => {
                      setAviso(null);
                      fichas.abrir(a.id);
                    }}
                    refFila={fichas.refFila(a.id)}
                    abierta={fichas.abierta === a.id}
                    deshabilitada={enviando}
                  />
                </li>
              ))}
            </ul>
          )}
          <p className="ficha-registro__acciones">
            <button type="button" onClick={abrirAutorizar}>
              {AUTORIZAR}
            </button>
          </p>
          {ventana?.tipo === "autorizar" && ventanaAutorizar(lectura.otorgables)}
        </>
      )}

      {enFicha !== undefined && (
        <FichaDeRegistro
          titulo={enFicha.autorizado_nombre}
          datos={[
            { etiqueta: "Estado", valor: estadoDe(enFicha) },
            { etiqueta: "Puede", valor: textoAlcances(enFicha.alcances) },
            { etiqueta: "Desde", valor: fechaCorta(enFicha.otorgada_en) },
          ]}
          alCerrar={() => fichas.cerrar()}
          ocupada={enviando}
          ventana={ventana?.tipo === "revocar" && ventana.ayuda.id === enFicha.id && ventanaRevocar(enFicha)}
        >
          {enFicha.revocada_en === null && (
            <p className="ficha-registro__acciones">
              <button type="button" className="secundario peligro" onClick={() => abrirRevocar(enFicha)}>
                {REVOCAR}
              </button>
            </p>
          )}
        </FichaDeRegistro>
      )}
    </section>
  );

  function ventanaAutorizar(otorgables: readonly Alcance[]) {
    const idCorreo = `${id}-correo`;
    const idAyudaCorreo = `${id}-ayuda-correo`;
    const idAlcances = `${id}-alcances`;
    return (
      <VentanaDeFormulario titulo={AUTORIZAR} textoEnviar={CONFIRMAR_AUTORIZAR} alEnviar={autorizar} alCerrar={() => setVentana(null)}>
        <label htmlFor={idCorreo}>{CORREO}</label>
        <input
          id={idCorreo}
          type="email"
          autoComplete="off"
          data-foco
          value={correo}
          {...marcaDe(idCorreo, errores.correo, idAyudaCorreo)}
          onChange={(ev) => {
            clave.current = null;
            setErrores((actuales) => sinElDe(actuales, "correo"));
            setCorreo(ev.target.value);
          }}
        />
        <p id={idAyudaCorreo}>{AYUDA_CORREO}</p>
        <ErrorDeCampo idCampo={idCorreo} mensaje={errores.correo} />
        <fieldset id={idAlcances} {...marcaDe(idAlcances, errores.alcances)}>
          <legend>{QUE_PUEDE}</legend>
          {otorgables.map((alcance) => (
            <label key={alcance} className="opcion">
              <input
                type="checkbox"
                checked={alcances.includes(alcance)}
                onChange={(ev) => {
                  clave.current = null;
                  setErrores((actuales) => sinElDe(actuales, "alcances"));
                  setAlcances((actuales) => cambiarAlcance(actuales, alcance, ev.target.checked));
                }}
              />
              {TEXTO_ALCANCE[alcance]}
            </label>
          ))}
        </fieldset>
        <ErrorDeCampo idCampo={idAlcances} mensaje={errores.alcances} />
        <p>{NUNCA}</p>
      </VentanaDeFormulario>
    );
  }

  function ventanaRevocar(ayuda: Otorgada) {
    return (
      <VentanaDeFormulario
        titulo={`Revocar la autorización de ${ayuda.autorizado_nombre}`}
        textoEnviar={CONFIRMAR_REVOCAR}
        peligro
        alEnviar={() => revocar(ayuda)}
        alCerrar={() => setVentana(null)}
      >
        <p>{ACLARACION_REVOCAR}</p>
      </VentanaDeFormulario>
    );
  }
}
