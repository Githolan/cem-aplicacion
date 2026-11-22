import { useCallback, useEffect, useId, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo as mesLargo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio, useLecturaMiPersona } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { fechaCorta } from "../pagos/textos";
import { GraficoBarras } from "../panel/GraficoBarras";
import { cumple, REQUISITOS, useAcciones } from "../permisos";
import type { CampoCitado } from "./borradorReclamo";
import { enPesos } from "./LiquidacionDelAdministrador";
import { MisReclamos, type Pedido } from "./MisReclamos";
import { HACER_RECLAMO } from "./textosReclamos";

// Página /mis-expensas (H5 §6, D3-A; app 0119): para cada propietario con el vínculo confirmado, lo
// que dice la liquidación oficial del período, la que la administración mandó y el Consejo confirmó.
// De cada unidad propia (`cuenta.ver_propia`), el total, el saldo anterior, los pagos, la deuda y el
// interés; del edificio (`fiscalizacion.ver_resumen`), el gasto por rubro y el total de los últimos
// períodos (app 0120), cada gráfico con su tabla equivalente (documento 08, §13.4). Nunca otra unidad,
// ni el detalle de los gastos, ni las
// diferencias (las ve solo el panel completo, RN33). La pantalla solo muestra: lo decide la API.
// Quien presenta reclamos (app 0123, RF34) tiene en cada unidad «Hacer un reclamo» y, desde el total o
// el saldo, el reclamo con ese dato citado; debajo, «Mis reclamos» con los borradores sin enviar
// (RF38) y los presentados (MisReclamos.tsx).

type MiCuenta = components["schemas"]["MiCuenta"];
type Resumen = components["schemas"]["ResumenLiquidacion"];
type Unidad = components["schemas"]["UnidadEnLaLiquidacion"];

export const RUTA_MIS_EXPENSAS = "/mis-expensas";
export const TITULO_MIS_EXPENSAS = "Mis expensas";
export const SIN_LIQUIDACION = "Todavía no hay una liquidación de la administración confirmada en este edificio.";
export const NO_FIGURA = "Esta unidad no figura en la liquidación de la administración.";
export const GASTO_DEL_PERIODO = "Gasto del período por rubro";
export const GASTO_POR_PERIODO = "Gasto por período";
export const segunLaLiquidacion = (periodo: string, confirmada: string) =>
  `Período ${mesLargo(periodo)}, según la liquidación de la administración, confirmada el ${fechaCorta(confirmada)}.`;

const enPesosOVacio = (importe: string | null) => (importe === null ? "—" : enPesos(importe));

export const CAMBIO_AL_LEER = "La liquidación oficial cambió mientras se leía. Recargue la página para ver la vigente.";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "distintas" } | { tipo: "ok"; cuenta: MiCuenta | null; resumen: Resumen | null };

/** La liquidación oficial de la que sale una respuesta: `null` si no hay ninguna; `undefined` si no
 * se pidió o falló (nada que comparar). */
const liquidacionDe = (x: { data?: { liquidacion: { importacion_id: string } | null } } | null) =>
  x?.data === undefined ? undefined : (x.data.liquidacion?.importacion_id ?? null);

/** La cuenta y el resumen describen liquidaciones oficiales distintas (o una sí y la otra no). */
function sonDistintas(c: Parameters<typeof liquidacionDe>[0], r: Parameters<typeof liquidacionDe>[0]): boolean {
  const a = liquidacionDe(c);
  const b = liquidacionDe(r);
  return a !== undefined && b !== undefined && a !== b;
}

export function MisExpensas() {
  const elegido = useConsorcio(REQUISITOS.misExpensas);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [elegido, setElegido] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const acciones = useAcciones(consorcio);
  // Cada parte se pide solo si la persona la alcanza: nada que se sabe que se niega.
  const veCuenta = acciones !== null && cumple(acciones, [["cuenta.ver_propia"]]);
  const veResumen = acciones !== null && cumple(acciones, [["fiscalizacion.ver_resumen"]]);
  // Los reclamos (app 0123): presentarlos sobre una unidad propia y ver los propios.
  const presenta = acciones !== null && cumple(acciones, [["reclamos_liquidacion.presentar"]]);
  const veReclamos = acciones !== null && cumple(acciones, [["reclamos_liquidacion.ver"]]);
  // Los borradores son de quien los escribió (RF38): sin saber quién es no se ofrece reclamar.
  const persona = useLecturaMiPersona(presenta || veReclamos);
  const reclama = presenta && persona.tipo === "ok";
  const [pedido, setPedido] = useState<Pedido | null>(null);
  const tomarPedido = useCallback(() => setPedido(null), []);
  const releerCuenta = useCallback(() => setVuelta((v) => v + 1), []);

  useEffect(() => {
    if (consorcio === "" || acciones === null) return;
    let vigente = true;
    const path = { consorcio_id: consorcio };
    const delPeriodo = (periodo: string | null) => ({ path, query: periodo === null ? {} : { periodo } });
    // Primero la cuenta; el resumen, del período que trajo la cuenta. Cada respuesta dice de qué
    // liquidación oficial sale: si entre las dos se confirmó o se reemplazó una, no coinciden.
    async function leerUnaVez() {
      const c = veCuenta ? await api.GET("/api/v1/consorcios/{consorcio_id}/mi-cuenta", { params: delPeriodo(elegido) }) : null;
      if (c !== null) exigirSesion(c.response);
      if (c !== null && c.data === undefined) return [c, null] as const;
      const periodo = c?.data?.liquidacion?.periodo ?? elegido;
      const r = veResumen ? await api.GET("/api/v1/consorcios/{consorcio_id}/resumen", { params: delPeriodo(periodo) }) : null;
      if (r !== null) exigirSesion(r.response);
      return [c, r] as const;
    }
    void (async () => {
      let [c, r] = await leerUnaVez();
      // Otra liquidación entró entre las dos lecturas: se lee una vez más, nunca en bucle.
      if (sonDistintas(c, r)) [c, r] = await leerUnaVez();
      return [c, r] as const;
    })()
      .then(([c, r]) => {
        if (!vigente) return;
        const falla = [c, r].find((x) => x !== null && x.data === undefined);
        if (falla === undefined || falla === null) {
          if (sonDistintas(c, r)) {
            // Siguen sin coincidir: no se mezclan datos de dos liquidaciones.
            setLectura({ tipo: "distintas" });
            setAviso(sinLaFalla);
            return;
          }
          setAviso(sinLaFalla);
          setLectura({ tipo: "ok", cuenta: c?.data ?? null, resumen: r?.data ?? null });
          return;
        }
        const codigo = codigoDe(falla.error);
        if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else if (falla.response.status === ESTADO.noEncontrado && elegido !== null) {
          // El período elegido ya no tiene liquidación vigente: vuelve al último que la tiene.
          setAviso(sinPisarLoHecho(mensajeDeError(falla.response.status, codigo)));
          setElegido(null);
        } else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(falla.response.status, codigo)));
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
  }, [consorcio, acciones, veCuenta, veResumen, elegido, vuelta, aEntrar, aInicio]);

  const datos = lectura.tipo === "ok" ? lectura : null;
  const periodos = datos?.cuenta?.periodos ?? datos?.resumen?.periodos ?? [];
  const cuenta = datos?.cuenta?.liquidacion ?? null;
  const resumen = datos?.resumen?.liquidacion ?? null;
  const serie = datos?.resumen?.gasto_por_periodo ?? [];
  const cabeza = cuenta ?? resumen;

  return (
    <>
      <CabeceraInterna titulo={TITULO_MIS_EXPENSAS} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando las expensas…</p>}
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
          {lectura.tipo === "distintas" && (
            <p className="tarjeta" role="alert">
              {CAMBIO_AL_LEER}
            </p>
          )}
          {datos !== null && cabeza === null && <p className="tarjeta">{SIN_LIQUIDACION}</p>}
          {cabeza !== null && (
            <>
              {/* Con un solo período no hay nada que elegir: va en la línea de abajo. */}
              {periodos.length > 1 && (
                <div className="formulario">
                  <label htmlFor={`${id}-periodo`}>Período</label>
                  <select id={`${id}-periodo`} value={cabeza.periodo} onChange={(e) => setElegido(e.target.value)}>
                    {periodos.map((p) => (
                      <option key={p.periodo} value={p.periodo}>
                        {mesLargo(p.periodo)}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <p>{segunLaLiquidacion(cabeza.periodo, cabeza.confirmada_en)}</p>
              {cuenta !== null && cuenta.vencimientos.length > 0 && <p>Vencimientos: {cuenta.vencimientos.join(", ")}</p>}
              {cuenta?.unidades.map((u) => (
                <DeLaUnidad
                  key={u.unidad_id}
                  unidad={u}
                  alReclamar={reclama ? (campo) => setPedido({ unidad_id: u.unidad_id, designacion: u.designacion, figura: u.figura, campo }) : undefined}
                />
              ))}
              {(presenta || veReclamos) && (
                <MisReclamos
                  consorcio={consorcio}
                  cuenta={cuenta}
                  presenta={presenta}
                  ve={veReclamos}
                  persona={persona}
                  pedido={pedido}
                  alTomarPedido={tomarPedido}
                  aEntrar={aEntrar}
                  alCambiarLaLiquidacion={releerCuenta}
                />
              )}
              {resumen !== null && (
                <>
                  <GraficoBarras
                    titulo={GASTO_DEL_PERIODO}
                    base="Lo que la liquidación de la administración informa para cada rubro del período, tal como la confirmó el Consejo."
                    barras={resumen.rubros.flatMap((r, i) =>
                      r.total === null
                        ? []
                        : [{ clave: `${r.numero ?? i}`, etiqueta: r.nombre ?? `Rubro ${r.numero ?? i + 1}`, valor: Number(r.total), texto: enPesos(r.total) }],
                    )}
                    columna="Rubro"
                    valor="Gasto"
                  />
                  {resumen.total !== null && <p>Total del gasto del período: {enPesos(resumen.total)}</p>}
                  {cuenta === null && resumen.vencimientos.length > 0 && <p>Vencimientos: {resumen.vencimientos.join(", ")}</p>}
                  <GraficoBarras
                    titulo={GASTO_POR_PERIODO}
                    base="El total del gasto que informa la liquidación de la administración de cada período, tal como la confirmó el Consejo. Los últimos períodos que tienen una."
                    barras={serie.flatMap((p) =>
                      p.total === null ? [] : [{ clave: p.periodo, etiqueta: mesLargo(p.periodo), valor: Number(p.total), texto: enPesos(p.total) }],
                    )}
                    columna="Período"
                    valor="Gasto"
                  />
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

export const RECLAMAR_TOTAL = "Reclamar el total";
export const RECLAMAR_SALDO = "Reclamar el saldo";

/** `alReclamar`: solo para quien presenta reclamos (app 0123); abre la ventana con el dato citado. */
function DeLaUnidad({ unidad, alReclamar }: { unidad: Unidad; alReclamar?: (campo: CampoCitado | null) => void }) {
  const id = useId();
  return (
    <section className="tarjeta" aria-labelledby={`${id}-unidad`}>
      <h2 id={`${id}-unidad`}>Unidad {unidad.designacion}</h2>
      {unidad.figura ? (
        <dl className="detalles">
          <dt>Total del período</dt>
          <dd>{enPesosOVacio(unidad.total)}</dd>
          <dt>Saldo anterior</dt>
          <dd>{enPesosOVacio(unidad.saldo_anterior)}</dd>
          <dt>Pagos</dt>
          <dd>{enPesosOVacio(unidad.pagos)}</dd>
          <dt>Deuda</dt>
          <dd>{enPesosOVacio(unidad.deuda)}</dd>
          <dt>Interés</dt>
          <dd>{enPesosOVacio(unidad.interes)}</dd>
        </dl>
      ) : (
        <p>{NO_FIGURA}</p>
      )}
      {alReclamar !== undefined && (
        <div className="acciones-pantalla">
          {/* Desde un dato, la ventana lo cita: el total del período o el saldo de la unidad. */}
          {unidad.figura && (
            <>
              <button type="button" className="secundario" onClick={() => alReclamar("importe_unidad")}>
                {RECLAMAR_TOTAL}
              </button>
              <button type="button" className="secundario" onClick={() => alReclamar("saldo_unidad")}>
                {RECLAMAR_SALDO}
              </button>
            </>
          )}
          <button type="button" className="secundario" onClick={() => alReclamar(null)}>
            {HACER_RECLAMO}
          </button>
        </div>
      )}
    </section>
  );
}
