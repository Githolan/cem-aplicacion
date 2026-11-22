import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, mensajeDeError, momento, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { FichaDeRegistro, FilaDeRegistro, type DatoDeFicha, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { VentanaDeFormulario, type FinDeEnvio } from "../VentanaDeFormulario";
import { type Borrador, borrarBorrador, type CampoCitado, guardarBorrador, leerBorradores } from "./borradorReclamo";
import { FichaReclamo } from "./FichaReclamo";
import { LARGO_DOCUMENTACION } from "./Solicitudes";
import {
  BORRADOR_SIN_ENVIAR,
  CITAR_LA_VIGENTE,
  CONFLICTO_REEMPLAZADA,
  DESCARTAR,
  ENVIAR_AHORA,
  HACER_RECLAMO,
  LIQUIDACION_REEMPLAZADA,
  mesDe,
  MIS_RECLAMOS,
  motivoDe,
  NO_CAMBIA_IMPORTES,
  NO_SE_ENVIO,
  PERMISO_VENCIDO,
  POR_ENVIAR,
  resumenDe,
  SALIERON,
  SALIO,
  SEGUIR_ESCRIBIENDO,
  SIN_BORRADORES,
  SIN_CONEXION_BORRADOR,
  SIN_CONEXION_SIN_CAMBIO,
  SIN_CONEXION_SIN_GUARDAR,
  SIN_GUARDAR,
  SIN_LA_VIGENTE,
  SIN_RECLAMOS,
  SOBRE_LA_REEMPLAZADA,
  TEXTO_CAMPO,
  TEXTO_ESTADO,
  tituloDe,
} from "./textosReclamos";

// «Mis reclamos», dentro de «Mis expensas» (RF34 y RF38, app 0123; PA16). El propietario reclama
// sobre lo que la liquidación oficial dice de una unidad suya, con el dato citado si lo abrió desde
// ese dato. Lo que escribe se guarda como borrador en el dispositivo («Borrador sin enviar») hasta
// que el servidor lo recibe; sin conexión queda para enviarse solo al volver. El servidor revalida
// todo al recibirlo: con el vínculo vencido se rechaza y el borrador queda marcado para descartarlo;
// con la liquidación reemplazada se ofrece citar la vigente o reclamar sobre la reemplazada. Nada se
// borra sin que la persona lo pida.

type Cuenta = components["schemas"]["CuentaDelPeriodo"];
type Lista = components["schemas"]["ListaReclamos"];
type Reclamo = components["schemas"]["Reclamo"];

/** Lo que pide la pantalla al tocar «Hacer un reclamo» o «Reclamar» en un dato de una unidad. */
export type Pedido = { unidad_id: string; designacion: string; figura: boolean; campo: CampoCitado | null };

/** Cómo terminó un envío. `b` es el borrador como quedó (con la clave que corresponde al reintento);
 * `guardado` dice si quedó guardado en el dispositivo para salir solo. */
type Desenlace =
  | { tipo: "ok"; r: Reclamo }
  | { tipo: "sin_conexion"; b: Borrador; guardado: boolean }
  | { tipo: "sin_sesion" }
  | { tipo: "rechazado" }
  | { tipo: "reemplazada" }
  | { tipo: "corregir"; b: Borrador; errores: ErroresDeCampo; texto: string }
  | { tipo: "falla"; b: Borrador; texto: string };

const ahora = () => new Date().toISOString();

/** Manda el borrador con su clave (la misma en cada reintento: un envío que llegó no se repite) y
 * deja el borrador en el estado que corresponde. Solo lo borra si el servidor lo recibió. */
async function mandar(b: Borrador): Promise<Desenlace> {
  const clave = b.estado.tipo === "por_enviar" ? b.estado.clave : crypto.randomUUID();
  const enCamino: Borrador = { ...b, estado: { tipo: "por_enviar", clave } };
  const guardado = guardarBorrador(enCamino);
  const marcar = (estado: Borrador["estado"]) => guardarBorrador({ ...b, estado });
  const escribiendo: Borrador = { ...b, estado: { tipo: "escribiendo" } };
  let respuesta;
  try {
    respuesta = await api.POST("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion", {
      headers: encabezadoCsrf(),
      params: { path: { consorcio_id: b.consorcio_id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
      body: { liquidacion_id: b.liquidacion_id, unidad_id: b.unidad_id, campo: b.campo, texto: b.texto.trim(), acepta_reemplazada: b.acepta_reemplazada },
    });
    exigirSesion(respuesta.response);
  } catch (e: unknown) {
    return e instanceof SinSesion ? { tipo: "sin_sesion" } : { tipo: "sin_conexion", b: enCamino, guardado };
  }
  const { data, error, response } = respuesta;
  if (data !== undefined) {
    borrarBorrador(b);
    return { tipo: "ok", r: data };
  }
  const codigo = codigoDe(error);
  // PA16: el servidor revalidó y el vínculo ya no alcanza (o el edificio ya no es suyo).
  if (response.status === 403 || response.status === 404) {
    marcar({ tipo: "rechazado", motivo: PERMISO_VENCIDO });
    return { tipo: "rechazado" };
  }
  if (codigo === "liquidacion_reemplazada") {
    marcar({ tipo: "reemplazada" });
    return { tipo: "reemplazada" };
  }
  if (response.status === 422) {
    marcar({ tipo: "escribiendo" });
    const texto = motivoDe(codigo, mensajeDeError(response.status, codigo));
    return { tipo: "corregir", b: escribiendo, errores: erroresDe(error, texto), texto };
  }
  // Lo mismo se reintenta con la misma clave (un 5xx, un pedido igual en curso); lo demás, con otra.
  const renovar = renovarClave(response.status, codigo);
  if (renovar) marcar({ tipo: "escribiendo" });
  return { tipo: "falla", b: renovar ? escribiendo : enCamino, texto: mensajeDeActo(response.status, error) };
}

type Props = {
  consorcio: string;
  /** La liquidación oficial que se muestra (la de los reclamos nuevos), o null si no hay. */
  cuenta: Cuenta | null;
  presenta: boolean;
  ve: boolean;
  /** Lo que la persona tocó en una unidad; la pantalla lo vuelve a null al tomarlo. */
  pedido: Pedido | null;
  alTomarPedido: () => void;
  aEntrar: () => void;
  /** La liquidación cambió (un reemplazo): la pantalla relee la cuenta. */
  alCambiarLaLiquidacion: () => void;
};

export function MisReclamos({ consorcio, cuenta, presenta, ve, pedido, alTomarPedido, aEntrar, alCambiarLaLiquidacion }: Props) {
  const id = useId();
  const [borradores, setBorradores] = useState<Borrador[] | null>(() => leerBorradores(consorcio));
  const [lista, setLista] = useState<Lista | null>(null);
  const [fallaLista, setFallaLista] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [escribiendo, setEscribiendo] = useState<{ b: Borrador; figura: boolean } | null>(null);
  const [enFicha, setEnFicha] = useState<string | null>(null);
  const fichas = useFichas<string>();
  const sincronizando = useRef(false);

  const releerBorradores = useCallback(() => setBorradores(leerBorradores(consorcio)), [consorcio]);

  useEffect(() => {
    if (!ve) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) {
          setLista(data);
          setFallaLista(null);
        } else setFallaLista(mensajeDeError(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setFallaLista(SIN_CONEXION);
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, ve, vuelta, aEntrar]);

  /** Manda los borradores que quedaron para enviar (al abrir la pantalla y al volver la conexión). */
  const sincronizar = useCallback(async () => {
    if (sincronizando.current) return;
    const pendientes = (leerBorradores(consorcio) ?? []).filter((b) => b.estado.tipo === "por_enviar");
    if (pendientes.length === 0) return;
    sincronizando.current = true;
    let salieron = 0;
    let aviso: Resultado | null = null;
    for (const b of pendientes) {
      const d = await mandar(b);
      if (d.tipo === "ok") salieron += 1;
      else if (d.tipo === "sin_sesion") {
        aEntrar();
        break;
      } else if (d.tipo === "sin_conexion") break;
      else if (d.tipo === "rechazado") aviso = { texto: PERMISO_VENCIDO, ok: false };
      else if (d.tipo === "reemplazada") {
        aviso = { texto: CONFLICTO_REEMPLAZADA, ok: false };
        alCambiarLaLiquidacion();
      } else aviso = { texto: d.texto, ok: false };
    }
    sincronizando.current = false;
    releerBorradores();
    if (salieron > 0) setVuelta((v) => v + 1);
    setResultado(aviso ?? (salieron > 0 ? { texto: SALIERON(salieron), ok: true } : null));
  }, [consorcio, aEntrar, alCambiarLaLiquidacion, releerBorradores]);

  useEffect(() => {
    void sincronizar();
    const alVolver = () => void sincronizar();
    window.addEventListener("online", alVolver);
    return () => window.removeEventListener("online", alVolver);
  }, [sincronizar]);

  // Un pedido de la pantalla abre la ventana con un borrador nuevo, que se guarda al escribir.
  useEffect(() => {
    if (pedido === null || cuenta === null) return;
    alTomarPedido();
    setResultado(null);
    setEscribiendo({
      figura: pedido.figura,
      b: {
        id: crypto.randomUUID(), consorcio_id: consorcio, liquidacion_id: cuenta.importacion_id, periodo: cuenta.periodo,
        unidad_id: pedido.unidad_id, designacion: pedido.designacion, campo: pedido.campo, texto: "", acepta_reemplazada: false,
        guardado_en: ahora(), estado: { tipo: "escribiendo" },
      },
    });  // prettier-ignore
  }, [pedido, cuenta, consorcio, alTomarPedido]);

  /** Terminó un envío desde la ventana o desde la ficha del borrador. */
  function alMandar(d: Desenlace): void {
    releerBorradores();
    if (d.tipo === "ok") {
      setResultado({ texto: SALIO, ok: true });
      setVuelta((v) => v + 1);
    } else if (d.tipo === "sin_conexion") setResultado({ texto: d.guardado ? SIN_CONEXION_BORRADOR : SIN_CONEXION_SIN_CAMBIO, ok: false });
    else if (d.tipo === "rechazado") setResultado({ texto: PERMISO_VENCIDO, ok: false });
    else if (d.tipo === "reemplazada") {
      setResultado({ texto: CONFLICTO_REEMPLAZADA, ok: false });
      alCambiarLaLiquidacion();
    } else if (d.tipo === "sin_sesion") aEntrar();
    else setResultado({ texto: d.texto, ok: false });
  }

  function descartar(b: Borrador) {
    setEnFicha(null);
    if (!borrarBorrador(b)) setResultado({ texto: "Este navegador no deja borrar el borrador del dispositivo.", ok: false });
    releerBorradores();
  }

  const abierto = borradores?.find((b) => b.id === enFicha) ?? null;
  const reclamos = lista?.elementos ?? [];
  const hayBorradores = borradores !== null && borradores.length > 0;

  return (
    <section className="tarjeta" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>{MIS_RECLAMOS}</h2>
      <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
      {presenta && borradores === null && (
        <p role="alert" className="aviso-error">
          {SIN_BORRADORES}
        </p>
      )}
      {hayBorradores && (
        <ul className="filas-registro" aria-label="Borradores sin enviar">
          {borradores.map((b) => (
            <li key={b.id}>
              <FilaDeRegistro
                titulo={tituloDe(b)}
                resumen={`Guardado el ${momento(b.guardado_en)}`}
                estado={estadoDelBorrador(b)}
                alAbrir={() => setEnFicha(b.id)}
                abierta={enFicha === b.id}
              />
            </li>
          ))}
        </ul>
      )}
      {fallaLista !== null && (
        <p role="alert" className="aviso-error">
          {fallaLista}
        </p>
      )}
      {reclamos.length > 0 && (
        <ul className="filas-registro" aria-label={MIS_RECLAMOS}>
          {reclamos.map((r) => (
            <li key={r.id}>
              <FilaDeRegistro
                titulo={tituloDe(r)}
                resumen={resumenDe(r, false)}
                estado={TEXTO_ESTADO[r.estado]}
                alAbrir={() => fichas.abrir(r.id)}
                refFila={fichas.refFila(r.id)}
                abierta={fichas.abierta === r.id}
              />
            </li>
          ))}
        </ul>
      )}
      {ve && lista !== null && reclamos.length === 0 && !hayBorradores && <p>{SIN_RECLAMOS}</p>}
      {fichas.abierta !== null && lista !== null && (
        <FichaReclamo
          key={fichas.abierta}
          consorcio={consorcio}
          id={fichas.abierta}
          lista={lista}
          largoSolicitud={LARGO_DOCUMENTACION}
          aEntrar={aEntrar}
          alCerrar={() => fichas.cerrar()}
          alTerminar={(texto) => {
            fichas.cerrar(false);
            setResultado({ texto, ok: true });
            setVuelta((v) => v + 1);
          }}
        />
      )}
      {abierto !== null && (
        <FichaBorrador
          b={abierto}
          cuenta={cuenta}
          alCerrar={() => setEnFicha(null)}
          alSeguir={(b) => {
            setEnFicha(null);
            const unidad = cuenta?.unidades.find((u) => u.unidad_id === b.unidad_id);
            setEscribiendo({ b, figura: unidad?.figura ?? false });
          }}
          alMandar={async (b) => {
            setEnFicha(null);
            alMandar(await mandar(b));
          }}
          alDescartar={descartar}
        />
      )}
      {escribiendo !== null && (
        <VentanaReclamo
          inicial={escribiendo.b}
          figura={escribiendo.figura}
          largo={lista?.largo_texto ?? LARGO_RECLAMO}
          alCerrar={() => {
            setEscribiendo(null);
            releerBorradores();
          }}
          alMandar={alMandar}
        />
      )}
    </section>
  );
}

/** El tope del texto mientras la lista no llegó (el de `cem_contracts.reclamos`). */
const LARGO_RECLAMO = 2000;

function estadoDelBorrador(b: Borrador): string {
  switch (b.estado.tipo) {
    case "escribiendo":
      return BORRADOR_SIN_ENVIAR;
    case "por_enviar":
      return POR_ENVIAR;
    case "rechazado":
      return NO_SE_ENVIO;
    case "reemplazada":
      return LIQUIDACION_REEMPLAZADA;
  }
}

type PropsFichaBorrador = {
  b: Borrador;
  cuenta: Cuenta | null;
  alCerrar: () => void;
  alSeguir: (b: Borrador) => void;
  alMandar: (b: Borrador) => Promise<void>;
  alDescartar: (b: Borrador) => void;
};

/** La ficha de un borrador: lo escrito, en qué quedó y solo lo que se puede hacer con él. */
function FichaBorrador({ b, cuenta, alCerrar, alSeguir, alMandar, alDescartar }: PropsFichaBorrador) {
  const [ocupada, setOcupada] = useState(false);
  const datos: DatoDeFicha[] = [{ etiqueta: "Estado", valor: estadoDelBorrador(b) }];
  if (b.estado.tipo === "rechazado") datos.push({ etiqueta: "Motivo", valor: b.estado.motivo });
  if (b.estado.tipo === "reemplazada") datos.push({ etiqueta: "Motivo", valor: CONFLICTO_REEMPLAZADA });
  if (b.campo !== null) datos.push({ etiqueta: "Dato citado", valor: TEXTO_CAMPO[b.campo] });
  datos.push({ etiqueta: "Reclamo", valor: <pre className="texto-con-renglones">{b.texto}</pre> });
  // La vigente del período del borrador, si es la que se muestra y tiene la unidad.
  const vigente =
    cuenta !== null && cuenta.periodo === b.periodo && cuenta.importacion_id !== b.liquidacion_id && cuenta.unidades.some((u) => u.unidad_id === b.unidad_id)
      ? cuenta.importacion_id
      : null;
  /** Lo manda ya. Sin `estado` en el cambio va con otra clave: es otro pedido. */
  const mandarAsi = (cambio: Partial<Borrador>) => {
    setOcupada(true);
    void alMandar({ ...b, estado: { tipo: "escribiendo" }, ...cambio, guardado_en: ahora() }).finally(() => setOcupada(false));
  };
  return (
    <FichaDeRegistro titulo={tituloDe(b)} datos={datos} alCerrar={alCerrar} ocupada={ocupada}>
      <div className="ficha-registro__acciones">
        {b.estado.tipo === "escribiendo" && (
          <button type="button" onClick={() => alSeguir(b)}>
            {SEGUIR_ESCRIBIENDO}
          </button>
        )}
        {b.estado.tipo === "por_enviar" && (
          <button type="button" disabled={ocupada} onClick={() => mandarAsi({ estado: b.estado })}>
            {ENVIAR_AHORA}
          </button>
        )}
        {b.estado.tipo === "reemplazada" &&
          (vigente !== null ? (
            <button type="button" onClick={() => alSeguir({ ...b, liquidacion_id: vigente, acepta_reemplazada: false, estado: { tipo: "escribiendo" } })}>
              {CITAR_LA_VIGENTE}
            </button>
          ) : (
            <p className="comando-no-disponible">{cuenta !== null && cuenta.periodo !== b.periodo ? `Para citar la vigente, elegir el período ${mesDe(b.periodo)} en «Período».` : SIN_LA_VIGENTE}</p>
          ))}
        {b.estado.tipo === "reemplazada" && (
          <button type="button" className="secundario" disabled={ocupada} onClick={() => mandarAsi({ acepta_reemplazada: true })}>
            {SOBRE_LA_REEMPLAZADA}
          </button>
        )}
        <button type="button" className="secundario peligro" disabled={ocupada} onClick={() => alDescartar(b)}>
          {DESCARTAR}
        </button>
      </div>
    </FichaDeRegistro>
  );
}

type PropsVentana = {
  inicial: Borrador;
  figura: boolean;
  largo: number;
  alCerrar: () => void;
  alMandar: (d: Desenlace) => void;
};

/** Escribir el reclamo: se guarda en el dispositivo a cada cambio y se envía con «Enviar». */
function VentanaReclamo({ inicial, figura, largo, alCerrar, alMandar }: PropsVentana) {
  const id = useId();
  const [b, setB] = useState(inicial);
  // Al abrir uno ya escrito (seguir escribiendo, citar la vigente) queda guardado tal como se abre.
  const [guardado, setGuardado] = useState<boolean | null>(() => (inicial.texto.trim() === "" ? null : guardarBorrador(inicial)));
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);

  function cambiar(cambio: Partial<Borrador>, campo: string) {
    const nuevo = { ...b, ...cambio, guardado_en: ahora(), estado: { tipo: "escribiendo" as const } };
    setB(nuevo);
    setErrores((e) => sinElDe(e, campo));
    // Sin texto no hay borrador: lo vacío no queda guardado.
    if (nuevo.texto.trim() === "") {
      borrarBorrador(nuevo);
      setGuardado(null);
    } else setGuardado(guardarBorrador(nuevo));
  }

  async function enviar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ texto: [b.texto, "Falta escribir el reclamo."] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const d = await mandar(b);
    // La ventana sigue con el borrador como quedó: un reintento del mismo pedido lleva la misma clave.
    if (d.tipo === "corregir") {
      setB(d.b);
      setErrores(d.errores);
      return { rechazo: d.texto };
    }
    if (d.tipo === "falla") {
      setB(d.b);
      return { rechazo: d.texto };
    }
    // Sin conexión y sin poder guardarlo, cerrar la ventana perdería lo escrito: sigue abierta con el texto.
    if (d.tipo === "sin_conexion" && !d.guardado) {
      setB(d.b);
      setGuardado(false);
      return { rechazo: SIN_CONEXION_SIN_GUARDAR };
    }
    if (d.tipo === "sin_sesion") {
      alMandar(d);
      return "seguir";
    }
    alMandar(d);
    return "cerrar";
  }

  const campo = (nombre: string) => ({ id: `${id}-${nombre}`, ...marcaDe(`${id}-${nombre}`, errores[nombre]) });
  return (
    <VentanaDeFormulario titulo={HACER_RECLAMO} textoEnviar="Enviar el reclamo" alEnviar={enviar} alCerrar={alCerrar} ancha>
      <p>
        Unidad {b.designacion}, liquidación de {mesDe(b.periodo)}.
      </p>
      {figura && (
        <>
          <label htmlFor={`${id}-campo`}>Dato que se cita</label>
          <select
            {...campo("campo")}
            value={b.campo ?? ""}
            onChange={(ev) => cambiar({ campo: ev.target.value === "" ? null : (ev.target.value as CampoCitado) }, "campo")}
          >
            <option value="">Ninguno en particular</option>
            {(Object.keys(TEXTO_CAMPO) as CampoCitado[]).map((c) => (
              <option key={c} value={c}>
                {TEXTO_CAMPO[c]}
              </option>
            ))}
          </select>
          <ErrorDeCampo idCampo={`${id}-campo`} mensaje={errores.campo} />
        </>
      )}
      <label htmlFor={`${id}-texto`}>Reclamo</label>
      <textarea {...campo("texto")} data-foco value={b.texto} maxLength={largo} rows={6} onChange={(ev) => cambiar({ texto: ev.target.value }, "texto")} />
      <ErrorDeCampo idCampo={`${id}-texto`} mensaje={errores.texto} />
      {guardado === true && (
        <p className="ayuda" role="status">
          {BORRADOR_SIN_ENVIAR}: queda guardado en este dispositivo hasta enviarlo.
        </p>
      )}
      {guardado === false && (
        <p role="alert" className="aviso-error">
          {SIN_GUARDAR}
        </p>
      )}
      <p className="ayuda">{NO_CAMBIA_IMPORTES}</p>
    </VentanaDeFormulario>
  );
}
