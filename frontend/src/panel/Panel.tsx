import { useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, importe, mensajeDeError, momento, sinLaFalla, sinPisarLoHecho, TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { cumple, REQUISITOS, useAcciones } from "../permisos";
import { RUTA_CONSULTA, rutaFicha } from "../pagos/rutas";
import { SUBIR_FACTURA, SubirFactura } from "../pagos/SubirFactura";
import {
  estaVacio,
  type Faltante,
  faltantesPorEstado,
  gastoPorMes,
  gastoPorRubro,
  mesesConPagos,
  monedas,
  ordenesPorEstado,
  type Panel as DatosPanel,
} from "./datos";
import { GraficoBarras } from "./GraficoBarras";

// Página /panel: el resumen de pagos del edificio en solo lectura (RF48; app 0094, H4a corte 1). Lo
// decide la API con `fiscalizacion.ver_panel`; la pantalla solo muestra. Cada gráfico dice su base
// de cálculo y, si la persona además puede abrir los pagos, un solo enlace arriba lleva a ellos
// (regla de fricción del 05-11-2026; informe de fricción, AC-44). En «Facturas faltantes», quien puede
// cargar la factura al preparar la sube desde la fila (decisión del propietario del 13-11-2026, app
// 0099): queda adjunta sin versión nueva y la orden sale de la lista cuando el antivirus la acepta.

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
  const subeFacturas = acciones !== null && cumple(acciones, REQUISITOS.subirFactura);

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
          {lectura.tipo === "ok" &&
            (estaVacio(lectura.panel) ? (
              <p className="tarjeta">Todavía no hay órdenes de pago en este edificio. El resumen se arma con ellas.</p>
            ) : (
              <Contenido panel={lectura.panel} consorcio={consorcio} veRegistros={veRegistros} subeFacturas={subeFacturas}
                aEntrar={aEntrar} alCambiar={() => setVuelta((v) => v + 1)} />
            ))}
        </>
      )}
    </>
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
  subeFacturas: boolean;
  aEntrar: () => void;
  /** Una factura quedó adjunta: el resumen se vuelve a leer. */
  alCambiar: () => void;
};

function Contenido({ panel, consorcio, veRegistros, subeFacturas, aEntrar, alCambiar }: PropsContenido) {
  const lista = monedas(panel);
  return (
    <div className="panel-consejo">
      {veRegistros && <EnlacePagos consorcio={consorcio} />}
      {lista.map((moneda) => (
        <PorMoneda key={moneda} panel={panel} moneda={moneda} conMoneda={lista.length > 1} />
      ))}
      <GraficoBarras
        titulo="Facturas faltantes"
        base="Órdenes aprobadas o pagadas sin el archivo de la factura. Conviene pedirlo."
        barras={faltantesPorEstado(panel)}
        columna="Estado de la orden"
        valor="Sin factura"
        vacio="No falta ninguna factura."
        registros={
          <Faltantes panel={panel} consorcio={consorcio} veRegistros={veRegistros} subeFacturas={subeFacturas} aEntrar={aEntrar} alCambiar={alCambiar} />
        }
      />
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
  const delFondo = gastoPorMes(panel, moneda, true);
  return (
    <>
      <GraficoBarras
        titulo={`Órdenes de pago por estado${en}`}
        base="Cuántas órdenes hay en cada paso y cuánto suman. No incluye borradores."
        barras={ordenesPorEstado(panel, moneda)}
        columna="Estado"
        valor="Órdenes e importe"
        vacio="No hay órdenes enviadas a aprobar."
      />
      <GraficoBarras
        titulo={`Gasto pagado por mes${en}`}
        base="Lo pagado en cada mes, por la fecha del pago y el importe que realmente se pagó: los mismos que usa la liquidación."
        barras={gastoPorMes(panel, moneda)}
        columna="Mes"
        valor="Pagado"
        vacio="Todavía no se pagó ninguna orden."
      />
      {meses.length > 0 && (
        <GraficoBarras
          titulo={`Gasto pagado por rubro${en}`}
          base="Lo pagado en el mes elegido, por el rubro que tenía el proveedor cuando se pagó."
          control={
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
          }
          barras={gastoPorRubro(panel, moneda, mes)}
          columna="Rubro"
          valor="Pagado"
          vacio="No hay pagos en ese mes."
        />
      )}
      {delFondo.length > 0 && (
        <GraficoBarras
          titulo={`Pagos con el fondo de reserva${en}`}
          base="Lo que se pagó con el fondo de reserva en cada mes. No es el saldo del fondo: la aplicación todavía no registra sus ingresos."
          barras={delFondo}
          columna="Mes"
          valor="Pagado con el fondo"
          vacio=""
          />
      )}
    </>
  );
}

/** Lo que ve en la fila quien no sube facturas (el Consejo): quién lo hace. */
export const TEXTO_LA_SUBE_LA_ADMINISTRACION = "La sube la administración.";

/** Cómo está la factura de una orden que falta, en palabras. */
export function estadoDeLaFactura(f: Faltante): string {
  if (f.factura_subida === "en_cuarentena") return "El antivirus la está revisando";
  if (f.factura_subida === "rechazado") return "La subida no pasó el antivirus";
  return "Falta";
}

function Faltantes({ panel, consorcio, veRegistros, subeFacturas, aEntrar, alCambiar }: PropsContenido) {
  const [subiendo, setSubiendo] = useState<Faltante | null>(null);
  if (panel.comprobantes_faltantes.length === 0) return null;
  return (
    <div className="tabla-lista tabla-tarjetas">
      <table aria-label="Órdenes sin factura">
        <thead>
          <tr>
            <th scope="col">Proveedor</th>
            <th scope="col">Estado</th>
            <th scope="col">Importe</th>
            <th scope="col">Último cambio</th>
            <th scope="col">Factura</th>
          </tr>
        </thead>
        <tbody>
          {panel.comprobantes_faltantes.map((f) => (
            <tr key={f.orden_id}>
              <th scope="row">
                {veRegistros ? (
                  <Link to={rutaFicha(f.orden_id)} state={{ consorcio }} className="enlace-fila">
                    {f.proveedor}
                  </Link>
                ) : (
                  f.proveedor
                )}
                <p className="tabla-lista__dato">{f.concepto}</p>
              </th>
              <td data-etiqueta="Estado">{TEXTO_ESTADO_ORDEN[f.estado]}</td>
              <td data-etiqueta="Importe">{importe(f.importe_centavos, f.moneda)}</td>
              <td data-etiqueta="Último cambio">{momento(f.actualizada_en)}</td>
              <td data-etiqueta="Factura">
                {estadoDeLaFactura(f)}
                {/* Mientras el antivirus revisa la subida, no se ofrece otra. Quien no la puede
                    subir (el Consejo) sabe quién lo hace (decisión del propietario, 13-11-2026). */}
                {f.factura_subida !== "en_cuarentena" &&
                  (subeFacturas ? (
                    <div className="acciones">
                      <button type="button" className="secundario" onClick={() => setSubiendo(f)}>
                        {SUBIR_FACTURA}
                      </button>
                    </div>
                  ) : (
                    <p className="tabla-lista__dato">{TEXTO_LA_SUBE_LA_ADMINISTRACION}</p>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {subiendo !== null && (
        <SubirFactura consorcio={consorcio} orden={subiendo.orden_id} version={subiendo.version} reemplaza={null}
          descripcion={`${subiendo.proveedor}, ${importe(subiendo.importe_centavos, subiendo.moneda)}: ${subiendo.concepto}`}
          aEntrar={aEntrar}
          alCerrar={(cambio) => {
            setSubiendo(null);
            if (cambio) alCambiar();
          }} />
      )}
    </div>
  );
}
