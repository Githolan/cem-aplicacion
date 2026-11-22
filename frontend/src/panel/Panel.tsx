import { useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, importe, mensajeDeError, sinLaFalla, sinPisarLoHecho, TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { DESDE_EL_PANEL, RUTA_SOLICITUDES } from "../fiscalizacion/Solicitudes";
import { cumple, REQUISITOS, useAcciones } from "../permisos";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { FilaDeLectura, FilaEnlace } from "../pagos/FilaEnlace";
import { RUTA_CONSULTA, rutaFicha } from "../pagos/rutas";
import { SUBIR_FACTURA, SubirFactura } from "../pagos/SubirFactura";
import { useAncho } from "../pagos/useAncho";
import { AvisoEnlaceDocumentacion } from "../fiscalizacion/AvisoEnlaceDocumentacion";
import { RUTA_LIQUIDACION_OFICIAL } from "../fiscalizacion/LiquidacionOficial";
import {
  estaVacio,
  type Faltante,
  gastoLiquidadoPorPeriodo,
  gastoPorMes,
  gastoPorRubro,
  mesesConPagos,
  monedas,
  ordenesPorEstado,
  type Panel as DatosPanel,
  periodosConRubros,
  rubrosDelPeriodo,
} from "./datos";
import { GraficoBarras } from "./GraficoBarras";

// Página /panel: el resumen de pagos del edificio en solo lectura (RF48; app 0094, H4a corte 1). Lo
// decide la API con `fiscalizacion.ver_panel`; la pantalla solo muestra. Cada gráfico dice su base
// de cálculo (plegada, salvo lo que evita leerlo mal: PAN-05), un bloque sin datos no se muestra
// (PAN-03) y, si la persona además puede abrir los pagos, un solo enlace arriba lleva a ellos
// (regla de fricción del 05-11-2026; informe de fricción, AC-44). En «Facturas faltantes», quien puede
// cargar la factura al preparar la sube desde la fila (decisión del propietario del 13-11-2026, app
// 0099): queda adjunta sin versión nueva y la orden sale de la lista cuando el antivirus la acepta.
// En Modo Consejo no hay órdenes: el gasto sale de las liquidaciones oficiales (app 0120), por período y
// por rubro, con lo que no cierra en la vigente (RN33: la diferencia, sin calificarla) y el aviso de la
// vigente sin el enlace a la documentación que pide el perfil normativo (RF47, app 0125).

export const RUTA_PANEL = "/panel";
/** El mismo nombre en el inicio, la barra, la cabecera y la facultad (informe de fricción, AC-42):
 * lo ven el Consejo y la administración, así que no es «del Consejo». */
export const TITULO_PANEL = "Resumen de pagos del edificio";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; panel: DatosPanel };

export function Panel() {
  const elegido = useConsorcio(REQUISITOS.panel);
  return <PanelDelEdificio key={elegido.consorcio} {...elegido} />;
}

function PanelDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const acciones = useAcciones(consorcio);
  // Mientras no se sabe, no se ofrece: el enlace llevaría a una pantalla que puede no abrir.
  const veRegistros = acciones !== null && cumple(acciones, REQUISITOS.pagos);
  // null mientras no se sabe: ni el botón ni «La sube la administración».
  const subeFacturas = acciones === null ? null : cumple(acciones, REQUISITOS.subirFactura);
  const veLiquidaciones = acciones !== null && cumple(acciones, REQUISITOS.liquidacionOficial);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/panel", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setAviso(sinLaFalla);
          setLectura({ tipo: "ok", panel: data });
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  // Una factura subida sigue en el antivirus aunque se haya cerrado su hoja: el resumen se vuelve a
  // leer hasta que termina, para que la fila diga si la aceptó o la rechazó y vuelva a ofrecerla.
  const facturaEnRevision =
    lectura.tipo === "ok" && lectura.panel.comprobantes_faltantes.some((f) => f.factura_subida === "en_cuarentena");
  useConsultaDelAntivirus(facturaEnRevision, () => setVuelta((v) => v + 1));

  return (
    <>
      <CabeceraInterna titulo={TITULO_PANEL} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando el resumen…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lectura.tipo === "ok" && lectura.panel.solicitudes_abiertas > 0 && (
            <LineaSolicitudes consorcio={consorcio} abiertas={lectura.panel.solicitudes_abiertas} />
          )}
          {lectura.tipo === "ok" &&
            (estaVacio(lectura.panel) ? (
              <p className="tarjeta">{SIN_DATOS}</p>
            ) : (
              <Contenido panel={lectura.panel} consorcio={consorcio} veRegistros={veRegistros} subeFacturas={subeFacturas}
                veLiquidaciones={veLiquidaciones} aEntrar={aEntrar} alCambiar={() => setVuelta((v) => v + 1)} />
            ))}
        </>
      )}
    </>
  );
}

/** RF49 (app 0108): las solicitudes formales que no se respondieron ni se retiraron, con el enlace a
 * la pantalla. Quien ve el panel ve también las solicitudes. Sin ninguna abierta no se dice nada
 * (PAN-01): la pantalla ya está en el menú. */
export const textoSolicitudesAbiertas = (n: number) => `Solicitudes abiertas: ${n}`;

function LineaSolicitudes({ consorcio, abiertas }: { consorcio: string; abiertas: number }) {
  return (
    <p>
      <Link to={RUTA_SOLICITUDES} state={{ consorcio, desde: DESDE_EL_PANEL }} className="enlace-suelto">
        {textoSolicitudesAbiertas(abiertas)}
      </Link>
    </p>
  );
}

function EnlacePagos({ consorcio }: { consorcio: string }) {
  return (
    <p>
      <Link to={RUTA_CONSULTA} state={{ consorcio }} className="enlace-suelto">
        Consultar los pagos
      </Link>
    </p>
  );
}

type PropsContenido = {
  panel: DatosPanel;
  consorcio: string;
  veRegistros: boolean;
  /** Si puede subir la factura que falta: prepara órdenes y carga documentos. */
  subeFacturas: boolean | null;
  aEntrar: () => void;
  /** Una factura quedó adjunta: el resumen se vuelve a leer. */
  alCambiar: () => void;
};

/** Sin órdenes ni liquidaciones oficiales: de qué se arma el resumen. */
export const SIN_DATOS =
  "Todavía no hay datos para el resumen. Se arma con las órdenes de pago o con las liquidaciones de la administración que confirma el Consejo.";
export const TITULO_GASTO_POR_PERIODO = "Gasto por período";
export const TITULO_GASTO_POR_RUBRO = "Gasto por rubro";
export const SEGUN_LAS_OFICIALES = "Según las liquidaciones oficiales.";
export const VER_LIQUIDACIONES = "Ver las liquidaciones oficiales";
/** RN33: lo que no cierra entre lo que informa la liquidación y lo que suma, sin calificarlo. Se
 * controló sobre el documento tal como llegó, antes de las correcciones (hallazgo de Codex en #276):
 * las cifras de los gráficos son las oficiales. */
export const textoNoCierra = (mes: string, n: number) =>
  `Lo que no cierra en el documento de ${periodo(mes)}: ${n === 1 ? "1 diferencia" : `${n} diferencias`}`;

function Contenido({ panel, consorcio, veRegistros, subeFacturas, veLiquidaciones, aEntrar, alCambiar }: PropsContenido & { veLiquidaciones: boolean }) {
  const lista = monedas(panel);
  return (
    <div className="panel-consejo">
      {veRegistros && <EnlacePagos consorcio={consorcio} />}
      {panel.gasto_liquidado.length > 0 && <Liquidadas panel={panel} consorcio={consorcio} veLiquidaciones={veLiquidaciones} />}
      {lista.map((moneda) => (
        <PorMoneda key={moneda} panel={panel} moneda={moneda} conMoneda={lista.length > 1} />
      ))}
      <Faltantes panel={panel} consorcio={consorcio} veRegistros={veRegistros} subeFacturas={subeFacturas} aEntrar={aEntrar} alCambiar={alCambiar} />
    </div>
  );
}

type PropsMoneda = { panel: DatosPanel; moneda: string; conMoneda: boolean };

function PorMoneda({ panel, moneda, conMoneda }: PropsMoneda) {
  const id = useId();
  const meses = mesesConPagos(panel, moneda);
  const [elegido, setElegido] = useState("");
  const mes = meses.includes(elegido) ? elegido : (meses[0] ?? "");
  const en = conMoneda ? ` (${moneda})` : "";
  return (
    <>
      <GraficoBarras
        titulo={`Órdenes de pago por estado${en}`}
        aviso="No incluye borradores."
        base="Cuántas órdenes hay en cada paso y cuánto suman."
        barras={ordenesPorEstado(panel, moneda)}
        columna="Estado"
        valor="Órdenes e importe"
      />
      <GraficoBarras
        titulo={`Gasto pagado por mes${en}`}
        base="Lo pagado en cada mes, por la fecha del pago y el importe que realmente se pagó: los mismos que usa la liquidación."
        barras={gastoPorMes(panel, moneda)}
        columna="Mes"
        valor="Pagado"
      />
      {/* Con un solo mes no hay nada que elegir: el mes va en el título (PAN-07). */}
      <GraficoBarras
        titulo={`Gasto pagado por rubro${meses.length === 1 ? `, ${periodo(mes)}` : ""}${en}`}
        base="Lo pagado en el mes elegido, por el rubro que tenía el proveedor cuando se pagó."
        control={
          meses.length > 1 && (
            <div className="formulario">
              <label htmlFor={`${id}-mes`}>Mes</label>
              <select id={`${id}-mes`} value={mes} onChange={(e) => setElegido(e.target.value)}>
                {meses.map((m) => (
                  <option key={m} value={m}>
                    {periodo(m)}
                  </option>
                ))}
              </select>
            </div>
          )
        }
        barras={gastoPorRubro(panel, moneda, mes)}
        columna="Rubro"
        valor="Pagado"
      />
      <GraficoBarras
        titulo={`Pagos con el fondo de reserva${en}`}
        aviso="No es el saldo del fondo."
        base="Lo que se pagó con el fondo de reserva en cada mes: la aplicación todavía no registra sus ingresos."
        barras={gastoPorMes(panel, moneda, true)}
        columna="Mes"
        valor="Pagado con el fondo"
      />
    </>
  );
}

type PropsLiquidadas = { panel: DatosPanel; consorcio: string; veLiquidaciones: boolean };

/** Modo Consejo (app 0120): el gasto según las liquidaciones oficiales, por período y por rubro, cada
 * gráfico con su tabla equivalente. Un solo enlace lleva a las liquidaciones, si la persona las puede
 * abrir: el de lo que no cierra, si hay diferencias; si no, el general. */
function Liquidadas({ panel, consorcio, veLiquidaciones }: PropsLiquidadas) {
  const id = useId();
  const periodos = periodosConRubros(panel);
  const [elegido, setElegido] = useState("");
  const mes = periodos.includes(elegido) ? elegido : (periodos[0] ?? "");
  const vigente = panel.liquidacion_vigente;
  const noCierra = vigente !== null && vigente.diferencias > 0 ? textoNoCierra(vigente.periodo, vigente.diferencias) : null;
  const enlace = (texto: string) => (
    <p>
      <Link to={RUTA_LIQUIDACION_OFICIAL} state={{ consorcio }} className="enlace-suelto">
        {texto}
      </Link>
    </p>
  );
  return (
    <>
      {vigente !== null && <AvisoEnlaceDocumentacion periodo={vigente.periodo} enlace={vigente.enlace_documentacion} />}
      {noCierra !== null && (veLiquidaciones ? enlace(noCierra) : <p>{noCierra}</p>)}
      {noCierra === null && veLiquidaciones && enlace(VER_LIQUIDACIONES)}
      <GraficoBarras
        titulo={TITULO_GASTO_POR_PERIODO}
        aviso={SEGUN_LAS_OFICIALES}
        base="El total del gasto que informa la liquidación de cada período, la que la administración mandó y el Consejo confirmó, con los totales por grupo. Los últimos períodos que tienen una."
        barras={gastoLiquidadoPorPeriodo(panel)}
        columna="Período"
        valor="Gasto"
        detalle="Por grupo"
      />
      {/* Con un solo período no hay nada que elegir: el período va en el título (PAN-07). */}
      <GraficoBarras
        titulo={`${TITULO_GASTO_POR_RUBRO}${periodos.length === 1 ? `, ${periodo(mes)}` : ""}`}
        aviso={SEGUN_LAS_OFICIALES}
        base="Lo que la liquidación del período elegido informa para cada rubro, tal como la confirmó el Consejo."
        control={
          periodos.length > 1 && (
            <div className="formulario">
              <label htmlFor={`${id}-periodo`}>Período</label>
              <select id={`${id}-periodo`} value={mes} onChange={(e) => setElegido(e.target.value)}>
                {periodos.map((m) => (
                  <option key={m} value={m}>
                    {periodo(m)}
                  </option>
                ))}
              </select>
            </div>
          )
        }
        barras={rubrosDelPeriodo(panel, mes)}
        columna="Rubro"
        valor="Gasto"
      />
    </>
  );
}

/** Lo que lee una sola vez, sobre la lista, quien no sube facturas (el Consejo): quién lo hace. */
export const TEXTO_LA_SUBE_LA_ADMINISTRACION = "La sube la administración.";

/** Cómo está la factura de una orden que falta, en palabras; null si simplemente falta, porque la
 * lista ya se llama «Facturas faltantes» (PAN-06). */
export function estadoDeLaFactura(f: Faltante): string | null {
  if (f.factura_subida === "en_cuarentena") return "El antivirus la está revisando";
  if (f.factura_subida === "rechazado") return "La subida no pasó el antivirus";
  return null;
}

/** La cantidad va en el título y la lista dice cuáles: sin barras por estado que repitan la columna
 * «Estado» (PAN-04). */
export const tituloFaltantes = (n: number) => `Facturas faltantes (${n})`;

/** Desde 769 px la lista es una tabla; más angosta, filas compactas (FL-PAN-02, «Phablet»). */
const TABLA_DE_FALTANTES = "(min-width: 48.0625em)";

/** Una orden abierta desde el resumen vuelve al resumen, no a Pagos (FL-PAN-01). */
const abrirDesdeElResumen = (consorcio: string) => ({ consorcio, volver: RUTA_PANEL, volverCon: { consorcio } });

/** Las órdenes aprobadas o pagadas sin la factura. Sin ninguna, el bloque no se muestra (PAN-03). */
function Faltantes({ panel, consorcio, veRegistros, subeFacturas, aEntrar, alCambiar }: PropsContenido) {
  const id = useId();
  const [subiendo, setSubiendo] = useState<Faltante | null>(null);
  const enTabla = useAncho(TABLA_DE_FALTANTES);
  const fichas = useFichas<string>();
  if (panel.comprobantes_faltantes.length === 0) return null;
  const subida = subiendo !== null && (
    <SubirFactura consorcio={consorcio} orden={subiendo.orden_id} version={subiendo.version} reemplaza={null}
      descripcion={`${subiendo.proveedor}, ${importe(subiendo.importe_centavos, subiendo.moneda)}: ${subiendo.concepto}`}
      aEntrar={aEntrar}
      alCerrar={(cambio) => {
        setSubiendo(null);
        if (!cambio) return;
        fichas.cerrar(false);
        alCambiar();
      }} />
  );
  const abierta = enTabla ? undefined : panel.comprobantes_faltantes.find((f) => f.orden_id === fichas.abierta);
  return (
    <section className="tarjeta grafico" aria-labelledby={`${id}-titulo`}>
      <h2 id={`${id}-titulo`}>{tituloFaltantes(panel.comprobantes_faltantes.length)}</h2>
      {/* Quien no las sube sabe quién lo hace, una sola vez (decisión del propietario del 13-11-2026; PAN-06). */}
      {subeFacturas === false && <p className="grafico__base">{TEXTO_LA_SUBE_LA_ADMINISTRACION}</p>}
      {/* En el teléfono, una fila compacta por orden (FL-PAN-02): quien sube la factura lo hace desde la
          ficha de la fila; quien solo mira, abre la orden. */}
      {!enTabla && (
        <ul className="filas-registro" aria-label="Órdenes sin factura">
          {panel.comprobantes_faltantes.map((f) => {
            const titulo = `${f.proveedor} · ${importe(f.importe_centavos, f.moneda)}`;
            const factura = estadoDeLaFactura(f);
            const resumen = [f.concepto, TEXTO_ESTADO_ORDEN[f.estado], factura].filter((d) => d !== null).join(" · ");
            return (
              <li key={f.orden_id}>
                {subeFacturas === true ? (
                  <FilaDeRegistro titulo={titulo} resumen={resumen} alAbrir={() => fichas.abrir(f.orden_id)}
                    refFila={fichas.refFila(f.orden_id)} abierta={fichas.abierta === f.orden_id} />
                ) : veRegistros ? (
                  <FilaEnlace a={rutaFicha(f.orden_id)} estadoNavegacion={abrirDesdeElResumen(consorcio)} titulo={titulo} resumen={resumen} />
                ) : (
                  <FilaDeLectura titulo={titulo} resumen={resumen} />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {abierta !== undefined && (
        <FichaDeRegistro
          titulo={abierta.proveedor}
          datos={[
            { etiqueta: "Concepto", valor: abierta.concepto },
            { etiqueta: "Estado", valor: TEXTO_ESTADO_ORDEN[abierta.estado] },
            { etiqueta: "Importe", valor: importe(abierta.importe_centavos, abierta.moneda) },
            ...(estadoDeLaFactura(abierta) === null ? [] : [{ etiqueta: "Factura", valor: estadoDeLaFactura(abierta) }]),
          ]}
          alCerrar={() => fichas.cerrar()}
          ventana={subida}
        >
          <p className="ficha-registro__acciones">
            {/* Mientras el antivirus revisa la subida, no se ofrece otra. */}
            {abierta.factura_subida !== "en_cuarentena" && (
              <button type="button" onClick={() => setSubiendo(abierta)}>
                {SUBIR_FACTURA}
              </button>
            )}
            {veRegistros && (
              <Link to={rutaFicha(abierta.orden_id)} state={abrirDesdeElResumen(consorcio)} className="enlace-boton enlace-boton--borde">
                Ver la orden
              </Link>
            )}
          </p>
        </FichaDeRegistro>
      )}
      {enTabla && (
        <div className="tabla-lista tabla-tarjetas">
          <table aria-label="Órdenes sin factura">
            <thead>
              <tr>
                <th scope="col">Proveedor</th>
                <th scope="col">Estado</th>
                <th scope="col">Importe</th>
                {subeFacturas === true && <th scope="col">Factura</th>}
              </tr>
            </thead>
            <tbody>
              {panel.comprobantes_faltantes.map((f) => {
                const factura = estadoDeLaFactura(f);
                return (
                  <tr key={f.orden_id}>
                    <th scope="row">
                      {veRegistros ? (
                        <Link to={rutaFicha(f.orden_id)} state={abrirDesdeElResumen(consorcio)} className="enlace-fila">
                          {f.proveedor}
                        </Link>
                      ) : (
                        f.proveedor
                      )}
                      <p className="tabla-lista__dato">{f.concepto}</p>
                      {/* Quien no sube facturas ve solo lo que cambia: una subida en revisión o que no pasó. */}
                      {subeFacturas !== true && factura !== null && <p className="tabla-lista__dato">{factura}</p>}
                    </th>
                    <td data-etiqueta="Estado">{TEXTO_ESTADO_ORDEN[f.estado]}</td>
                    <td data-etiqueta="Importe">{importe(f.importe_centavos, f.moneda)}</td>
                    {subeFacturas === true && (
                      <td data-etiqueta="Factura">
                        {factura}
                        {/* Mientras el antivirus revisa la subida, no se ofrece otra. */}
                        {f.factura_subida !== "en_cuarentena" && (
                          <div className="acciones">
                            <button type="button" className="secundario" onClick={() => setSubiendo(f)}>
                              {SUBIR_FACTURA}
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {/* En la tabla, la subida se abre desde la fila; en el teléfono, sobre la ficha. */}
      {enTabla && subida}
    </section>
  );
}
