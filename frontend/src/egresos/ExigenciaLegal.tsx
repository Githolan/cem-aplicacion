import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { BLOQUEO_POR_DOCUMENTACION, SALTEO_EN_UNA_URGENCIA } from "../pagos/textos";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, momentoAlFinal, renovarClave } from "./comun";
import { CARGAR, SubirDocumentacion, TEXTO_TIPO, type RequisitoLegal } from "./DocumentacionProveedor";
import { exigirSesion, SinSesion } from "./useConsorcio";

// Lo que exige la ley al proveedor (RF50 configurable; app 0104; sí del propietario del
// 15-11-2026). Quien prepara órdenes le asigna los rubros legales («Ascensores», «Gas»); la base
// dice qué requisito de esos rubros le falta o tiene vencido. Qué hace la falta lo decide la
// configuración del edificio: «bloquear» no deja prepararle órdenes nuevas; «avisar» solo marca.
// Quien solo consulta ve lo mismo, sin los botones.
//
// Informe de fricción, tanda 4: agregar un rubro se hace en su ventana (SEG-01), con el botón
// siempre activo (C8P-21); «Quitar» no repite el rubro que tiene al lado (SEG-02); y sin nada que
// mostrar ni hacer, la sección no se dibuja (SEG-03).
//
// Flujos 18-11, tanda 1: el aviso dice lo mismo que el formulario de la orden y, con un salteo que
// espera su orden, lo dice en vez del bloqueo (FL-LEG-07); cada requisito que falta se carga desde su
// renglón, con el tipo y el requisito ya elegidos (FL-LEG-05); y agregar un rubro dice antes qué se le
// va a exigir y qué pasa mientras falte (FL-LEG-10).

type Faltante = components["schemas"]["RequisitoFaltante"];
type Modo = components["schemas"]["ModoExigencia"];

export const TITULO_EXIGENCIA = "Lo que exige la ley";
export const AVISO_FALTA_BLOQUEA = `${BLOQUEO_POR_DOCUMENTACION} Las órdenes que ya existen siguen su curso.`;
/** FL-LEG-07: con un salteo que espera su orden, el aviso no dice que no se puede. */
export const textoConSalteoHasta = (venceEn: string) =>
  `Le falta documentación que exige la ley, o la tiene vencida. Hay un salteo por urgencia: se le puede preparar una orden hasta el ${momentoAlFinal(venceEn)}`;
/** FL-LEG-10: lo que pasa a exigirse al agregar el rubro, antes de agregarlo. */
export const textoSeLeVaAExigir = (requisitos: readonly string[], modo: Modo) =>
  `Se le va a exigir: ${requisitos.join("; ")}. ${
    modo === "bloquear" ? "Mientras falte, no se le pueden preparar órdenes nuevas." : "Mientras falte, queda marcado."
  }`;
export const AVISO_FALTA_AVISA = "Le falta documentación que exige la ley, o la tiene vencida. Se le puede seguir contratando; queda marcado.";
/** FL-LEG-08: a quien no cambia los rubros ni saltea, lo que falta, sin lo que pasa con las órdenes. */
export const AVISO_FALTA_SOLO_LEE = "Le falta documentación que exige la ley, o la tiene vencida:";
export const AGREGAR_UN_RUBRO = "Agregar un rubro legal";
export const AGREGAR_RUBRO = "Agregar el rubro";
export const RUBRO_LEGAL = "Rubro legal";
export const FALTA_RUBRO = "Falta elegir el rubro.";
export const QUITAR = "Quitar";

/** Los rubros de la matriz legal en palabras; uno nuevo que la pantalla no conoce va por su código. */
export const TEXTO_RUBRO: Record<string, string> = {
  general: "Todo proveedor (Ley 941)",
  con_personal: "Con personal en relación de dependencia",
  ascensores: "Ascensores y montacargas",
  incendio: "Instalaciones fijas contra incendio",
  matafuegos: "Matafuegos",
  gas: "Gas",
  electricidad: "Electricidad",
  desratizacion: "Desratización",
  fachadas: "Fachadas y trabajos en altura",
  calderas: "Calderas y termotanques",
};
export const textoRubro = (rubro: string) => TEXTO_RUBRO[rubro] ?? rubro;

/** El aviso de lo que falta (FL-LEG-07). `salteoVence`: el vencimiento del salteo que espera su
 * orden. `conUrgencia`: a quien prepara órdenes se le dice también la salida de una urgencia. */
export function avisoDeFalta(modo: Modo, salteoVence: string | null = null, conUrgencia = false): string {
  if (modo !== "bloquear") return AVISO_FALTA_AVISA;
  if (salteoVence !== null) return textoConSalteoHasta(salteoVence);
  return conUrgencia ? `${AVISO_FALTA_BLOQUEA} ${SALTEO_EN_UNA_URGENCIA}` : AVISO_FALTA_BLOQUEA;
}

type Props = {
  consorcio: string;
  proveedorId: string;
  rubros: readonly string[];
  faltantes: readonly Faltante[];
  requisitos: readonly RequisitoLegal[];
  modo: Modo;
  /** Quien prepara órdenes asigna y quita rubros; quien consulta, no. */
  puedeCambiar: boolean;
  /** Cargar lo que falta desde su renglón (FL-LEG-05): lo dice la API. */
  puedeCargar: boolean;
  /** Para la hoja de carga: a quién se le carga y si el edificio admite «No vence». */
  razonSocial: string;
  admiteNoVence: boolean;
  /** El vencimiento del salteo que espera su orden, si hay uno (FL-LEG-07). */
  salteoVence: string | null;
  /** Quien saltea el bloqueo necesita saber qué hace la falta; quien solo lee, no (FL-LEG-08). */
  puedeSaltear: boolean;
  aEntrar: () => void;
  alCambiar: () => void;
};

export function ExigenciaLegal({
  consorcio,
  proveedorId,
  rubros,
  faltantes,
  requisitos,
  modo,
  puedeCambiar,
  puedeSaltear,
  puedeCargar,
  razonSocial,
  admiteNoVence,
  salteoVence,
  aEntrar,
  alCambiar,
}: Props) {
  const id = useId();
  /** El requisito que se está cargando desde su renglón (FL-LEG-05). */
  const [cargando, setCargando] = useState<Faltante | null>(null);
  const [agregando, setAgregando] = useState(false);
  const [nuevo, setNuevo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [quitando, setQuitando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
  const disponibles = [...new Set(requisitos.map((r) => r.rubro_legal))].filter((r) => !rubros.includes(r));
  const ofreceAgregar = puedeCambiar && disponibles.length > 0;
  // Sin rubros, sin faltas y sin nada que agregar, la sección no tiene nada que decir (SEG-03).
  if (rubros.length === 0 && faltantes.length === 0 && !ofreceAgregar) return null;

  /** Asigna o quita el rubro. Devuelve el rechazo, o null si salió bien (la ficha se vuelve a leer). */
  async function cambiar(rubro: string, asignar: boolean): Promise<string | null> {
    clave.current ??= crypto.randomUUID();
    const pedido = {
      headers: encabezadoCsrf(),
      params: {
        path: { consorcio_id: consorcio, proveedor_id: proveedorId, rubro },
        header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
      },
    };
    const ruta = "/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/rubros-legales/{rubro}";
    const { data, error, response } = asignar ? await api.POST(ruta, pedido) : await api.DELETE(ruta, pedido);
    exigirSesion(response);
    if (data === undefined) {
      if (renovarClave(response.status, codigoDe(error))) clave.current = null;
      return mensajeDeActo(response.status, error);
    }
    clave.current = null;
    alCambiar();
    return null;
  }

  async function quitar(rubro: string) {
    setQuitando(true);
    setFalla(null);
    try {
      setFalla(await cambiar(rubro, false));
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(SIN_CONEXION);
    } finally {
      setQuitando(false);
    }
  }

  /** El botón de la ventana: sin rubro elegido lo marca (C8P-21); si no, lo asigna. */
  async function agregar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ rubro: [nuevo, FALTA_RUBRO] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    try {
      const rechazo = await cambiar(nuevo, true);
      return rechazo === null ? "cerrar" : { rechazo };
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    }
  }

  function abrirAgregar() {
    clave.current = null;
    setNuevo("");
    setErrores(SIN_ERRORES);
    setFalla(null);
    setAgregando(true);
  }

  const idRubro = `${id}-rubro`;
  const exigidosDelNuevo = requisitos.filter((r) => r.rubro_legal === nuevo && r.exigido).map((r) => r.descripcion);
  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{TITULO_EXIGENCIA}</h3>
      {rubros.length > 0 && (
        <ul className="lista-documentacion" aria-label="Rubros legales">
          {rubros.map((r) => (
            <li key={r}>
              <strong>{textoRubro(r)}</strong>
              {puedeCambiar && (
                // El rubro ya está escrito al lado: el nombre completo es para el lector de pantalla.
                <button
                  type="button"
                  className="secundario peligro"
                  aria-label={`${QUITAR} ${textoRubro(r)}`}
                  disabled={quitando}
                  onClick={() => void quitar(r)}
                >
                  {QUITAR}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {faltantes.length > 0 && (
        <>
          {puedeCambiar || puedeSaltear ? (
            <p className={modo === "bloquear" && salteoVence === null ? "aviso-error" : "aviso-pendiente"}>
              {avisoDeFalta(modo, salteoVence, puedeCambiar)}
            </p>
          ) : (
            <p className="aviso-pendiente">{AVISO_FALTA_SOLO_LEE}</p>
          )}
          <ul className="versiones" aria-label="Lo que falta">
            {faltantes.map((f) => (
              <li key={f.codigo}>
                {TEXTO_TIPO[f.tipo]}: {f.descripcion} ({textoRubro(f.rubro_legal)})
                {puedeCargar && (
                  <>
                    {" "}
                    {/* El renglón ya dice qué es: el nombre completo es para el lector de pantalla. */}
                    <button type="button" className="secundario" aria-label={`${CARGAR} ${f.descripcion}`} onClick={() => setCargando(f)}>
                      {CARGAR}
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {ofreceAgregar && (
        <button type="button" className="secundario" onClick={abrirAgregar}>
          {AGREGAR_UN_RUBRO}
        </button>
      )}
      {falla !== null && (
        <p role="alert" className="aviso-error">
          {falla}
        </p>
      )}
      {agregando && (
        <VentanaDeFormulario titulo={AGREGAR_UN_RUBRO} textoEnviar={AGREGAR_RUBRO} alEnviar={agregar} alCerrar={() => setAgregando(false)}>
          <label htmlFor={idRubro}>{RUBRO_LEGAL}</label>
          <select
            id={idRubro}
            data-foco
            value={nuevo}
            {...marcaDe(idRubro, errores.rubro)}
            onChange={(e) => {
              clave.current = null;
              setErrores((actuales) => sinElDe(actuales, "rubro"));
              setNuevo(e.target.value);
            }}
          >
            <option value="">Elegir un rubro</option>
            {disponibles.map((r) => (
              <option key={r} value={r}>
                {textoRubro(r)}
              </option>
            ))}
          </select>
          <ErrorDeCampo idCampo={idRubro} mensaje={errores.rubro} />
          {exigidosDelNuevo.length > 0 && (
            <p className="aviso-pendiente" role="note">
              {textoSeLeVaAExigir(exigidosDelNuevo, modo)}
            </p>
          )}
        </VentanaDeFormulario>
      )}
      {cargando !== null && (
        <SubirDocumentacion
          consorcio={consorcio}
          proveedor={{ id: proveedorId, razon_social: razonSocial }}
          reemplaza={null}
          fijo={{ tipo: cargando.tipo, requisito: cargando.codigo, descripcion: cargando.descripcion }}
          requisitos={requisitos}
          faltantes={faltantes}
          admiteNoVence={admiteNoVence}
          aEntrar={aEntrar}
          alCerrar={(cambio) => {
            setCargando(null);
            if (cambio) alCambiar();
          }}
        />
      )}
    </section>
  );
}
