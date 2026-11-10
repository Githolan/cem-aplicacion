import { type ReactNode, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import { RUTA_INICIO } from "../enlace";
import { type Aviso, ESTADO, importe, mensajeDeError, momento, sinLaFalla, sinPisarLoHecho, TEXTO_ESTADO_ORDEN } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { periodo } from "../egresos/Liquidacion";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { cumple, REQUISITOS, useAcciones } from "../permisos";
import { RUTA_CONSULTA, rutaFicha } from "../pagos/rutas";
import {
  estaVacio,
  faltantesPorEstado,
  gastoPorMes,
  gastoPorRubro,
  mesesConPagos,
  monedas,
  ordenesPorEstado,
  type Panel as DatosPanel,
} from "./datos";
import { GraficoBarras } from "./GraficoBarras";

// Página /panel: el panel del Consejo en solo lectura (RF48; app 0094, H4a corte 1). Lo decide la
// API con `fiscalizacion.ver_panel`; la pantalla solo muestra. Cada gráfico dice su base de cálculo
// y enlaza a los pagos si la persona además puede abrirlos (regla de fricción del 05-11-2026).

export const RUTA_PANEL = "/panel";

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

  return (
    <>
      <CabeceraInterna titulo="Panel del Consejo" volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Todavía no hay edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando el panel…</p>}
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
              <p className="tarjeta">Todavía no hay órdenes de pago en este edificio: el panel se arma con ellas.</p>
            ) : (
              <Contenido panel={lectura.panel} consorcio={consorcio} veRegistros={veRegistros} />
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

function Contenido({ panel, consorcio, veRegistros }: { panel: DatosPanel; consorcio: string; veRegistros: boolean }) {
  const registros = veRegistros ? <EnlacePagos consorcio={consorcio} /> : undefined;
  const lista = monedas(panel);
  return (
    <div className="panel-consejo">
      {lista.map((moneda) => (
        <PorMoneda key={moneda} panel={panel} moneda={moneda} conMoneda={lista.length > 1} registros={registros} />
      ))}
      <GraficoBarras
        titulo="Comprobantes faltantes"
        base="Órdenes aprobadas o pagadas cuya versión vigente no tiene el archivo de la factura. Es una señal para pedirlo, no una conclusión."
        barras={faltantesPorEstado(panel)}
        columna="Estado de la orden"
        valor="Sin factura"
        vacio="No falta ningún comprobante."
        registros={<Faltantes panel={panel} consorcio={consorcio} veRegistros={veRegistros} />}
      />
    </div>
  );
}

type PropsMoneda = { panel: DatosPanel; moneda: string; conMoneda: boolean; registros: ReactNode };

function PorMoneda({ panel, moneda, conMoneda, registros }: PropsMoneda) {
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
        base="Cantidad de órdenes en cada estado, con el importe de su versión vigente. No incluye borradores."
        barras={ordenesPorEstado(panel, moneda)}
        columna="Estado"
        valor="Órdenes e importe"
        vacio="No hay órdenes enviadas a revisión."
        registros={registros}
      />
      <GraficoBarras
        titulo={`Gasto pagado por mes${en}`}
        base="Lo pagado en cada mes, por la fecha del pago y el importe que realmente se pagó: los mismos que usa la liquidación."
        barras={gastoPorMes(panel, moneda)}
        columna="Mes"
        valor="Pagado"
        vacio="Todavía no hay pagos con fecha."
        registros={registros}
      />
      {meses.length > 0 && (
        <GraficoBarras
          titulo={`Gasto pagado por rubro${en}`}
          base="Lo pagado en el mes elegido, por el rubro del proveedor al momento de pagar."
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
          registros={registros}
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
          registros={registros}
        />
      )}
    </>
  );
}

function Faltantes({ panel, consorcio, veRegistros }: { panel: DatosPanel; consorcio: string; veRegistros: boolean }) {
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
