import { useEffect, useId, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo as mesLargo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { fechaCorta } from "../pagos/textos";
import { GraficoBarras } from "../panel/GraficoBarras";
import { cumple, REQUISITOS, useAcciones } from "../permisos";
import { enPesos } from "./LiquidacionDelAdministrador";

// Página /mis-expensas (H5 §6, D3-A; app 0119): para cada propietario con el vínculo confirmado, lo
// que dice la liquidación oficial del período, la que la administración mandó y el Consejo confirmó.
// De cada unidad propia (`cuenta.ver_propia`), el total, el saldo anterior, los pagos, la deuda y el
// interés; del edificio (`fiscalizacion.ver_resumen`), el gasto por rubro en un gráfico que lleva su
// tabla equivalente (documento 08, §13.4). Nunca otra unidad, ni el detalle de los gastos, ni las
// diferencias (las ve solo el panel completo, RN33). La pantalla solo muestra: lo decide la API.

type MiCuenta = components["schemas"]["MiCuenta"];
type Resumen = components["schemas"]["ResumenLiquidacion"];
type Unidad = components["schemas"]["UnidadEnLaLiquidacion"];

export const RUTA_MIS_EXPENSAS = "/mis-expensas";
export const TITULO_MIS_EXPENSAS = "Mis expensas";
export const SIN_LIQUIDACION = "Todavía no hay una liquidación de la administración confirmada en este edificio.";
export const NO_FIGURA = "Esta unidad no figura en la liquidación de la administración.";
export const GASTO_DEL_PERIODO = "Gasto del período por rubro";
export const segunLaLiquidacion = (periodo: string, confirmada: string) =>
  `Período ${mesLargo(periodo)}, según la liquidación de la administración, confirmada el ${fechaCorta(confirmada)}.`;

const enPesosOVacio = (importe: string | null) => (importe === null ? "—" : enPesos(importe));

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; cuenta: MiCuenta | null; resumen: Resumen | null };

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

  useEffect(() => {
    if (consorcio === "" || acciones === null) return;
    let vigente = true;
    const params = { path: { consorcio_id: consorcio }, query: elegido === null ? {} : { periodo: elegido } };
    const pedirCuenta = veCuenta ? api.GET("/api/v1/consorcios/{consorcio_id}/mi-cuenta", { params }) : null;
    const pedirResumen = veResumen ? api.GET("/api/v1/consorcios/{consorcio_id}/resumen", { params }) : null;
    void Promise.all([pedirCuenta, pedirResumen])
      .then(([c, r]) => {
        for (const x of [c, r]) if (x !== null) exigirSesion(x.response);
        if (!vigente) return;
        const falla = [c, r].find((x) => x !== null && x.data === undefined);
        if (falla === undefined || falla === null) {
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
              {cuenta?.unidades.map((u) => <DeLaUnidad key={u.unidad_id} unidad={u} />)}
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
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

function DeLaUnidad({ unidad }: { unidad: Unidad }) {
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
    </section>
  );
}
