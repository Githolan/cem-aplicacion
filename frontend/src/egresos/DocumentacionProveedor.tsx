import { useCallback, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { useAvisoAlSalir } from "../avisoAlSalir";
import { TIPOS_DE_DOCUMENTO, tituloDeArchivo, TOPE_BYTES } from "../archivos";
import { TEXTO_SUBIENDO } from "../CargaConLectura";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
import { SubirArchivo } from "../SubirArchivo";
import { ENCABEZADO_IDEMPOTENCIA, fechaLegible, LARGO_TITULO, mensajeDeActo, renovarClave } from "./comun";
import { exigirSesion, SinSesion, useConsultaDelAntivirus } from "./useConsorcio";

/** El requisito con que arranca la hoja (FL-LEG-06): el único de ese tipo que falta; si no, ninguno. */
function requisitoInicial(faltantes: readonly Faltante[], tipo: Tipo): string {
  const deEsteTipo = faltantes.filter((f) => f.tipo === tipo);
  return deEsteTipo.length === 1 ? (deEsteTipo[0]?.codigo ?? "") : "";
}

// Seguros y habilitaciones en la ficha del proveedor (RF50; app 0103; diseño
// `RF50_Seguros_y_Habilitaciones.md` del repositorio CEM). Un documento vencido marca al proveedor y
// no lo excluye: se le puede seguir contratando y pagando. El estado lo calcula la base con el día
// del edificio; aquí solo se dice en palabras. Agregar o reemplazar es un solo paso: completar qué es
// y hasta cuándo vale, y elegir el archivo. Lo que el rol no puede hacer no aparece.
// RF50 configurable (app 0104): «No vence» con la fecha en blanco, si el edificio lo admite, y el
// requisito de la ley que cubre, a elegir de la matriz legal.
//
// Informe de fricción, tanda 4: el vencimiento lo dice la etiqueta de cada documento y la
// consecuencia, una sola vez, «Lo que exige la ley» (SEG-08); quien solo lee no ve una sección vacía
// con título (SEG-03); aceptado el archivo, la hoja se cierra sola y la ficha lo muestra (SEG-09);
// y un archivo elegido antes de completar los datos no se pierde: se carga con «Cargar» (SEG-10).
//
// Flujos 18-11, tanda 1: con requisitos de ese tipo que faltan, el requisito no arranca en «Ninguno en
// particular» (que no destraba): si falta uno solo, arranca en él; si faltan varios, hay que elegirlo,
// y un documento vigente que no cubre ninguno lo dice (FL-LEG-06). Mientras un documento espera al
// antivirus, la ficha se vuelve a leer sola (FL-LEG-09). Desde «Lo que falta», la hoja llega con el
// tipo y el requisito fijos (`fijo`, FL-LEG-05).

export type Documentacion = components["schemas"]["DocumentacionProveedor"];
export type RequisitoLegal = components["schemas"]["RequisitoLegal"];
type Faltante = components["schemas"]["RequisitoFaltante"];
type Estado = components["schemas"]["EstadoDocumentacionProveedor"];
type Tipo = components["schemas"]["TipoDocumentacionProveedor"];

export const TITULO_SECCION = "Seguros y habilitaciones";
export const AGREGAR = "Agregar un seguro o una habilitación";
export const SIN_DOCUMENTACION = "No tiene seguros ni habilitaciones cargados.";
export const NO_VENCE = "No vence";
export const SIN_REQUISITO = "Ninguno en particular";
export const FALTA_DESCRIPCION = "Falta decir qué cubre.";
export const FALTA_VENCIMIENTO = "Falta la fecha de vencimiento.";
export const CARGAR = "Cargar";
export const ELEGIR_REQUISITO = "Elegir un requisito";
export const FALTA_REQUISITO = "Falta elegir qué requisito de la ley cubre.";
export const NO_CUBRE_REQUISITO = "No cubre ningún requisito de la ley";
/** «Ninguno en particular» elegido a propósito, cuando hay requisitos de ese tipo que faltan. */
const NINGUNO = "ninguno";
export const TEXTO_TIPO: Record<Tipo, string> = { seguro: "Seguro", habilitacion: "Habilitación" };
/** Lo que cuenta para la marca y lo único que se reemplaza (`ESTADOS_QUE_VALEN` del contrato). */
const VALEN: ReadonlySet<Estado> = new Set<Estado>(["vigente", "por_vencer", "vencida"]);
const LARGO_DESCRIPCION = 120;
const DEL_ARCHIVO = new Set([408, 413, 415, 503]);

/** El estado en palabras, con la fecha cuando la tiene. */
export function textoEstado(d: Pick<Documentacion, "estado" | "vence_el" | "no_vence">): string {
  const fecha = d.vence_el === null ? "" : fechaLegible(d.vence_el);
  switch (d.estado) {
    case "vigente":
      return d.no_vence ? "Vigente: no vence" : `Vigente hasta el ${fecha}`;
    case "por_vencer":
      return `Vence pronto: el ${fecha}`;
    case "vencida":
      return `Vencido el ${fecha}`;
    case "en_revision":
      return "El antivirus está revisando el archivo";
    case "rechazada":
      return "El archivo no pasó el antivirus: no cuenta";
    case "reemplazada":
      return "Reemplazado";
    case "sin_documento":
      return "Sin el documento: no cuenta";
  }
}

const TONO: Partial<Record<Estado, string>> = { vencida: "alerta", por_vencer: "alerta", vigente: "ok" };

type Props = {
  consorcio: string;
  proveedor: { id: string; razon_social: string };
  documentacion: Documentacion[];
  puedeCargar: boolean;
  /** La matriz legal, para decir qué requisito cubre cada una y elegirlo al cargar. */
  requisitos: readonly RequisitoLegal[];
  /** Lo que le falta al proveedor: decide con qué requisito arranca la hoja (FL-LEG-06). */
  faltantes: readonly Faltante[];
  /** Si el edificio admite «No vence» (configuración, app 0104). */
  admiteNoVence: boolean;
  aEntrar: () => void;
  /** Se vuelve a leer la ficha: algo quedó cargado. */
  alCambiar: () => void;
};

export function DocumentacionProveedor({ consorcio, proveedor, documentacion, puedeCargar, requisitos, faltantes, admiteNoVence, aEntrar, alCambiar }: Props) {
  const id = useId();
  const [hoja, setHoja] = useState<{ reemplaza: Documentacion | null } | null>(null);
  // Las que valen y las que esperan, arriba; lo reemplazado y lo rechazado, como historia.
  const actuales = documentacion.filter((d) => VALEN.has(d.estado) || d.estado === "en_revision");
  const historia = documentacion.filter((d) => !actuales.includes(d));
  const conReemplazoEnCurso = new Set(
    documentacion.filter((d) => d.estado === "en_revision" && d.reemplaza_a !== null).map((d) => d.reemplaza_a),
  );
  // FL-LEG-09: «la ficha lo muestra cuando termine» se cumple sin cerrarla. Con la hoja abierta, la que
  // consulta es la hoja.
  useConsultaDelAntivirus(hoja === null && documentacion.some((d) => d.estado === "en_revision"), alCambiar);

  // Quien solo lee y no hay nada: una línea, sin título (la pantalla se llama «Proveedores y sus
  // seguros» y hace falta decirlo). Quien carga ve el título con su botón, sin la frase (SEG-03).
  if (!puedeCargar && documentacion.length === 0) return <p>{SIN_DOCUMENTACION}</p>;

  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{TITULO_SECCION}</h3>
      {actuales.length > 0 && (
        <ul className="lista-documentacion" aria-label={TITULO_SECCION}>
          {actuales.map((d) => (
            <li key={d.id}>
              <strong>
                {TEXTO_TIPO[d.tipo]}: {d.descripcion}
              </strong>
              {d.requisito_codigo !== null && <span>Cubre: {descripcionDe(requisitos, d.requisito_codigo)}</span>}
              {d.requisito_codigo === null && VALEN.has(d.estado) && faltantes.some((f) => f.tipo === d.tipo) && (
                <span>{NO_CUBRE_REQUISITO}</span>
              )}
              <span className="etiqueta-situacion" data-tono={TONO[d.estado]}>
                {textoEstado(d)}
              </span>
              {puedeCargar && VALEN.has(d.estado) && !conReemplazoEnCurso.has(d.id) && (
                <button type="button" className="secundario" onClick={() => setHoja({ reemplaza: d })}>
                  Reemplazar
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {historia.length > 0 && (
        <details className="historial">
          <summary>Anteriores</summary>
          <ul className="versiones">
            {historia.map((d) => (
              <li key={d.id}>
                {TEXTO_TIPO[d.tipo]}: {d.descripcion}. {textoEstado(d)}.
                {d.vence_el === null ? ` ${NO_VENCE}.` : ` Vencía el ${fechaLegible(d.vence_el)}.`}
              </li>
            ))}
          </ul>
        </details>
      )}
      {puedeCargar && (
        <button type="button" className="secundario" onClick={() => setHoja({ reemplaza: null })}>
          {AGREGAR}
        </button>
      )}
      {hoja !== null && (
        <SubirDocumentacion
          consorcio={consorcio}
          proveedor={proveedor}
          reemplaza={hoja.reemplaza}
          requisitos={requisitos}
          faltantes={faltantes}
          admiteNoVence={admiteNoVence}
          aEntrar={aEntrar}
          alCerrar={(cambio) => {
            setHoja(null);
            if (cambio) alCambiar();
          }}
        />
      )}
    </section>
  );
}

/** El requisito en palabras; uno que ya no está en la matriz se dice por su código. */
export function descripcionDe(requisitos: readonly RequisitoLegal[], codigo: string): string {
  return requisitos.find((r) => r.codigo === codigo)?.descripcion ?? codigo;
}

type Fase = { tipo: "elegir" } | { tipo: "subiendo" } | { tipo: "revisando" } | { tipo: "rechazada" };

type PropsHoja = {
  consorcio: string;
  proveedor: { id: string; razon_social: string };
  reemplaza: Documentacion | null;
  /** Desde un renglón de «Lo que falta» (FL-LEG-05): el tipo y el requisito no se eligen. */
  fijo?: { tipo: Tipo; requisito: string; descripcion: string };
  requisitos: readonly RequisitoLegal[];
  faltantes: readonly Faltante[];
  admiteNoVence: boolean;
  aEntrar: () => void;
  alCerrar: (cambio: boolean) => void;
};

export function SubirDocumentacion({ consorcio, proveedor, reemplaza, fijo, requisitos, faltantes, admiteNoVence, aEntrar, alCerrar }: PropsHoja) {
  const id = useId();
  const [tipo, setTipo] = useState<Tipo>(fijo?.tipo ?? reemplaza?.tipo ?? "seguro");
  const [descripcion, setDescripcion] = useState(fijo?.descripcion ?? reemplaza?.descripcion ?? "");
  const [vence, setVence] = useState("");
  const [noVence, setNoVence] = useState(false);
  const [requisito, setRequisito] = useState(
    fijo?.requisito ?? reemplaza?.requisito_codigo ?? requisitoInicial(faltantes, reemplaza?.tipo ?? "seguro"),
  );
  const deEsteTipo = requisitos.filter((r) => r.tipo === tipo);
  /** Con requisitos de este tipo que faltan, «Ninguno en particular» no es lo que se elige sin mirar (FL-LEG-06). */
  const hayQueElegir = fijo === undefined && deEsteTipo.length > 0 && faltantes.some((f) => f.tipo === tipo);
  const [fase, setFase] = useState<Fase>({ tipo: "elegir" });
  const [falla, setFalla] = useState<string | null>(null);
  /** El archivo elegido antes de completar los datos: queda para «Cargar» (SEG-10). */
  const [archivo, setArchivo] = useState<File | null>(null);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);
  const cambio = useRef(false);
  const enEspera = useRef<{ documento: string; version: string } | null>(null);

  const mirar = useCallback(() => {
    const cual = enEspera.current;
    if (cual === null) return;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/estado", {
        params: { path: { consorcio_id: consorcio, documento_id: cual.documento, version_id: cual.version } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (enEspera.current !== cual || data === undefined || data.estado === "en_cuarentena") return;
        enEspera.current = null;
        // Aceptado, la hoja se cierra sola y la ficha, al volver a leerse, muestra el documento con su
        // etiqueta (SEG-09). El rechazo queda en la hoja, para elegir otro archivo.
        if (data.estado === "disponible") alCerrar(true);
        else {
          setArchivo(null);
          setFase({ tipo: "rechazada" });
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion) aEntrar();
      });
  }, [consorcio, aEntrar, alCerrar]);
  const tarda = useConsultaDelAntivirus(fase.tipo === "revisando", mirar);

  /** Lo que falta para mandar el archivo, marcado junto a su campo. */
  function faltan(): ErroresDeCampo {
    return faltaCompletar({
      descripcion: [descripcion, FALTA_DESCRIPCION],
      ...(noVence ? {} : { vence: [vence, FALTA_VENCIMIENTO] as const }),
      ...(hayQueElegir ? { requisito: [requisito, FALTA_REQUISITO] as const } : {}),
    });
  }

  /** Elegir el archivo lo manda si los datos están; si falta alguno, el archivo queda elegido. */
  function elegir(elegido: File) {
    setArchivo(elegido);
    const marcados = faltan();
    setErrores(marcados);
    if (!hayErrores(marcados)) void subir(elegido);
  }

  function cargar() {
    const marcados = faltan();
    setErrores(marcados);
    if (archivo !== null && !hayErrores(marcados)) void subir(archivo);
  }

  async function subir(elegido: File) {
    setFase({ tipo: "subiendo" });
    setFalla(null);
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/documentacion", {
        headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
        params: {
          path: { consorcio_id: consorcio, proveedor_id: proveedor.id },
          query: {
            tipo,
            descripcion: descripcion.trim(),
            ...(noVence ? { no_vence: true } : { vence_el: vence }),
            ...(requisito === "" || requisito === NINGUNO ? {} : { requisito }),
            titulo: tituloDeArchivo(elegido, TEXTO_TIPO[tipo], LARGO_TITULO),
            ...(reemplaza === null ? {} : { reemplaza: reemplaza.id }),
          },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: elegido as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        setFase({ tipo: "elegir" });
        setFalla(
          `No se cargó. ${
            DEL_ARCHIVO.has(response.status)
              ? mensajeDeCarga(response.status, codigo, "subir", [], response.headers.has("Retry-After"))
              : mensajeDeActo(response.status, error)
          }`,
        );
        return;
      }
      clave.current = null;
      cambio.current = true;
      enEspera.current = { documento: data.documento_id, version: data.archivo.id };
      setArchivo(null);
      setFase({ tipo: "revisando" });
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else {
        setFase({ tipo: "elegir" });
        setFalla(`No se cargó. ${SIN_CONEXION}`);
      }
    }
  }

  const cerrar = () => alCerrar(cambio.current);
  const enviando = fase.tipo === "subiendo";
  useAvisoAlSalir(enviando);
  const ofreceElegir = fase.tipo === "elegir" || fase.tipo === "subiendo" || fase.tipo === "rechazada";
  const titulo = fijo !== undefined ? `${CARGAR}: ${fijo.descripcion}` : reemplaza === null ? AGREGAR : `Reemplazar: ${reemplaza.descripcion}`;

  return (
    <Hoja titulo={titulo} alEscapar={enviando ? undefined : cerrar}>
      <p>{proveedor.razon_social}</p>
      {fase.tipo === "revisando" && (
        <p role="status" className="aviso-ok">
          Listo: quedó cargado. El antivirus está revisando el archivo; cuenta cuando lo acepte.
        </p>
      )}
      {fase.tipo === "revisando" && tarda && (
        <p className="aviso-info">El antivirus está tardando. Se puede cerrar: la ficha lo muestra cuando termine.</p>
      )}
      {fase.tipo === "rechazada" && (
        <p role="alert" className="aviso-error">
          El archivo no pasó el antivirus y no cuenta. Hay que elegir otro.
        </p>
      )}
      {ofreceElegir && (
        <div className="formulario">
          {fijo !== undefined ? (
            <p>
              {TEXTO_TIPO[fijo.tipo]} que exige la ley: {fijo.descripcion}.
            </p>
          ) : reemplaza === null ? (
            <fieldset>
              <legend>Qué es</legend>
              {(["seguro", "habilitacion"] as const).map((t) => (
                <label key={t} className="opcion">
                  <input
                    type="radio"
                    name={`${id}-tipo`}
                    value={t}
                    checked={tipo === t}
                    disabled={enviando}
                    onChange={() => {
                      setTipo(t);
                      setRequisito(requisitoInicial(faltantes, t));
                      setErrores((actuales) => sinElDe(actuales, "requisito"));
                    }}
                  />
                  {TEXTO_TIPO[t]}
                </label>
              ))}
            </fieldset>
          ) : (
            <p>
              {TEXTO_TIPO[reemplaza.tipo]}. El anterior queda como reemplazado cuando el antivirus acepte el nuevo; mientras tanto sigue valiendo.
            </p>
          )}
          <label htmlFor={`${id}-descripcion`}>Qué cubre</label>
          <input
            id={`${id}-descripcion`}
            value={descripcion}
            maxLength={LARGO_DESCRIPCION}
            required
            disabled={enviando}
            placeholder="Por ejemplo: ART del personal, matrícula de gasista"
            {...marcaDe(`${id}-descripcion`, errores.descripcion)}
            onChange={(e) => {
              setErrores((actuales) => sinElDe(actuales, "descripcion"));
              setDescripcion(e.target.value);
            }}
          />
          <ErrorDeCampo idCampo={`${id}-descripcion`} mensaje={errores.descripcion} />
          {fijo === undefined && deEsteTipo.length > 0 && (
            <>
              <label htmlFor={`${id}-requisito`}>Qué requisito de la ley cubre</label>
              <select id={`${id}-requisito`} value={requisito} disabled={enviando} {...marcaDe(`${id}-requisito`, errores.requisito)}
                onChange={(e) => {
                  setErrores((actuales) => sinElDe(actuales, "requisito"));
                  setRequisito(e.target.value);
                }}>
                {hayQueElegir ? (
                  <>
                    <option value="">{ELEGIR_REQUISITO}</option>
                    <option value={NINGUNO}>{SIN_REQUISITO}</option>
                  </>
                ) : (
                  <option value="">{SIN_REQUISITO}</option>
                )}
                {deEsteTipo.map((r) => (
                  <option key={r.codigo} value={r.codigo}>
                    {r.descripcion}
                  </option>
                ))}
              </select>
              <ErrorDeCampo idCampo={`${id}-requisito`} mensaje={errores.requisito} />
            </>
          )}
          <label htmlFor={`${id}-vence`}>Vence el</label>
          <input
            id={`${id}-vence`}
            type="date"
            required={!noVence}
            value={noVence ? "" : vence}
            disabled={enviando || noVence}
            {...marcaDe(`${id}-vence`, errores.vence)}
            onChange={(e) => {
              setErrores((actuales) => sinElDe(actuales, "vence"));
              setVence(e.target.value);
            }}
          />
          <ErrorDeCampo idCampo={`${id}-vence`} mensaje={errores.vence} />
          {admiteNoVence && (
            <label className="opcion">
              <input
                type="checkbox"
                checked={noVence}
                disabled={enviando}
                onChange={(e) => {
                  setErrores((actuales) => sinElDe(actuales, "vence"));
                  setNoVence(e.target.checked);
                }}
              />
              {NO_VENCE}: la fecha queda en blanco
            </label>
          )}
          <SubirArchivo
            de="del documento"
            tipos={TIPOS_DE_DOCUMENTO}
            tope={TOPE_BYTES}
            reemplaza={archivo !== null}
            deshabilitada={enviando}
            elegido={archivo?.name ?? null}
            alElegir={elegir}
          />
          {/* Elegido antes de completar los datos: se manda con este botón, sin volver a elegirlo (SEG-10). */}
          {archivo !== null && !enviando && (
            <div className="acciones">
              <button type="button" onClick={cargar}>
                {CARGAR}
              </button>
            </div>
          )}
          {enviando && (
            <p role="status" className="aviso-info">
              {TEXTO_SUBIENDO}
            </p>
          )}
          {falla !== null && (
            <p role="alert" className="aviso-error">
              {falla}
            </p>
          )}
        </div>
      )}
      <div className="acciones">
        <button type="button" className="secundario" onClick={cerrar} disabled={enviando}>
          Cerrar
        </button>
      </div>
    </Hoja>
  );
}
