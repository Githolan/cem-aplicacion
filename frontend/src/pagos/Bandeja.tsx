import { useId, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, importe, mensajeDeActo } from "../egresos/comun";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { SIN_CONEXION } from "../fallas";
import { usePuede } from "../permisos";
import { TEXTO_ACCION } from "./textosDeAcciones";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_SIN_EXPLICAR, rutaFicha } from "./rutas";
import {
  AVISO_LECTURA_DISPUTADA,
  ENTENDIDO,
  fechaCorta,
  type Grupo,
  NOMBRE_ESTADO,
  PREPARAR_OTRA,
  TEXTO_LE_TOCA_A,
  textoRechazada,
  textoRepetida,
  TITULO_GRUPO,
  TITULO_SIN_EXPLICAR,
  VER_LO_COTEJADO,
  VER_SIN_EXPLICAR,
  VERBO,
} from "./textos";

// Lo que esta persona tiene que hacer ahora, agrupado por el próximo paso (§2). La arma el servidor
// con la tabla de la pantalla: una fila aparece solo si la base va a aceptar la acción, ya viene en
// el orden en que se muestra y dice si cuenta como pendiente. Sin secciones vacías. Lo que no es para
// hacer (lo que espera a otra persona, los borradores viejos) queda plegado. Cada fila dice su fecha
// y su estado, apilados para no ensanchar la bandeja (decisiones 5 y 11 del 12-11-2026).

type BandejaPagos = components["schemas"]["BandejaPagos"];
type Elemento = components["schemas"]["ElementoBandejaPagos"];
type AvisoRechazo = components["schemas"]["AvisoRechazo"];

type PropsFila = { e: Elemento; consorcio: string; abierta: string | null };

function Fila({ e, consorcio, abierta }: PropsFila) {
  const repetida = e.posible_duplicada_de;
  return (
    <li>
      <Link to={rutaFicha(e.orden_id)} state={{ consorcio }} className="fila-pago" aria-current={e.orden_id === abierta ? "true" : undefined}>
        <span className="fila-pago__arriba">
          <strong>{e.proveedor}</strong>
          <span className="fila-pago__importe">{importe(e.importe_centavos, e.moneda)}</span>
        </span>
        <span>{e.concepto}</span>
        <span className="tabla-lista__dato">
          <time dateTime={e.creada_en}>{fechaCorta(e.creada_en)}</time> · {NOMBRE_ESTADO[e.estado_visible]}
        </span>
        {/* El verbo es la acción principal de la ficha: el botón dice lo mismo (P-09). */}
        <span className="fila-pago__verbo">{e.principal === null ? VERBO[e.paso] : TEXTO_ACCION[e.principal]}</span>
        {e.grupo === "esperando" && <span>{TEXTO_LE_TOCA_A[e.le_toca_a]}</span>}
        {e.lectura_disputada && <span>{AVISO_LECTURA_DISPUTADA}</span>}
        {repetida !== null && (
          <span className="fila-pago__nota">{textoRepetida(fechaCorta(repetida.creada_en), repetida.estado_visible)}</span>
        )}
      </Link>
    </li>
  );
}

function Seccion({ grupo, filas, consorcio, abierta }: { grupo: Grupo; filas: Elemento[]; consorcio: string; abierta: string | null }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="titulo-grupo">
        {TITULO_GRUPO[grupo]} <span className="cantidad">{filas.length}</span>
      </h2>
      <ul className="filas-pago">
        {filas.map((e) => (
          <Fila key={e.orden_id} e={e} consorcio={consorcio} abierta={abierta} />
        ))}
      </ul>
    </section>
  );
}

function Plegado({ grupo, filas, consorcio, abierta }: { grupo: Grupo; filas: Elemento[]; consorcio: string; abierta: string | null }) {
  return (
    <details className="historial">
      <summary>
        {TITULO_GRUPO[grupo]} ({filas.length})
      </summary>
      <ul className="filas-pago">
        {filas.map((e) => (
          <Fila key={e.orden_id} e={e} consorcio={consorcio} abierta={abierta} />
        ))}
      </ul>
    </details>
  );
}

type PropsAviso = { aviso: AvisoRechazo; consorcio: string; aEntrar: () => void; alCambiar: () => void };

/** Una orden rechazada, una vez, a quien la preparó (decisión 3): con «Entendido» pasa a la consulta. */
function AvisoDeRechazo({ aviso, consorcio, aEntrar, alCambiar }: PropsAviso) {
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);

  function entendido() {
    setEnviando(true);
    setFalla(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/rechazo-visto", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: consorcio, orden_id: aviso.orden_id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: crypto.randomUUID() },
        },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) alCambiar();
        else setFalla(mensajeDeActo(response.status, error));
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setFalla(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  return (
    <div className="tarjeta aviso-rechazo" role="status">
      <p>
        {textoRechazada(aviso.proveedor, importe(aviso.importe_centavos, aviso.moneda), fechaCorta(aviso.creada_en))}
      </p>
      <p>
        <strong>Motivo:</strong> {aviso.motivo}
      </p>
      {falla !== null && (
        <p className="aviso-error" role="alert">
          {falla}
        </p>
      )}
      <div className="acciones">
        <Link to={RUTA_NUEVA} state={{ consorcio, proveedor: aviso.proveedor_id }} className="enlace-boton enlace-boton--borde">
          {PREPARAR_OTRA}
        </Link>
        <button type="button" className="secundario" disabled={enviando} onClick={entendido}>
          {ENTENDIDO}
        </button>
      </div>
    </div>
  );
}

type Props = {
  bandeja: BandejaPagos;
  consorcio: string;
  abierta: string | null;
  aEntrar: () => void;
  /** Algo cambió (se vio un rechazo): la bandeja se vuelve a leer. */
  alCambiar: () => void;
  /** Hay una ficha o un «Listo» al lado, con su propio botón principal. */
  alLado: boolean;
};

export function Bandeja({ bandeja, consorcio, abierta, aEntrar, alCambiar, alLado }: Props) {
  const { elementos, avisos_rechazo: avisos, por_cotejar: porCotejar, sin_explicar: sinExplicar } = bandeja;
  const puede = usePuede(consorcio);
  const prepara = puede("egresos.preparar_orden");
  /** Quien coteja llega al cotejo aunque no quede nada por cotejar: ahí está lo ya cotejado y
   * «Deshacer el cotejo» (Codex en #238). El mismo requisito que la ruta (`REQUISITOS.cotejo`). */
  const coteja = puede("egresos.conciliar_pago");
  const grupos = [...new Set(elementos.map((e) => e.grupo))];
  const paraHacer = grupos.filter((g) => elementos.some((e) => e.grupo === g && e.pendiente));
  const plegados = grupos.filter((g) => !paraHacer.includes(g));
  /** Lo que el extracto no explica (pregunta 8, opción c): para quien concilia es algo que mirar;
   * para la administración, una consulta de solo lectura, abajo y sin contar como pendiente. */
  const sinExplicarParaMirar = coteja && sinExplicar > 0;
  return (
    <div className="bandeja">
      {/* Lo que más hace quien prepara va arriba, antes de la lista (P-35, 12-11-2026); las consultas,
          abajo. Un solo botón lleno: el del rol (comentario 24, 09-11-2026). */}
      {prepara && (
        <p className="salidas-pago salidas-pago--arriba">
          {/* Un solo principal por pantalla (pregunta 13): con la ficha o el «Listo» al lado, el suyo. */}
          <Link to={RUTA_NUEVA} state={{ consorcio }} className={alLado ? "enlace-boton enlace-boton--borde" : "enlace-boton enlace-boton--principal"}>
            Preparar una orden
          </Link>
        </p>
      )}
      {avisos.map((a) => (
        <AvisoDeRechazo key={a.orden_id} aviso={a} consorcio={consorcio} aEntrar={aEntrar} alCambiar={alCambiar} />
      ))}
      {paraHacer.length === 0 && porCotejar === 0 && !sinExplicarParaMirar && (
        <div className="tarjeta bandeja-al-dia">
          <p className="etiqueta-situacion" data-tono="bien">
            Al día
          </p>
          <p>Nada pendiente.</p>
        </div>
      )}
      {paraHacer.map((g) => (
        <Seccion key={g} grupo={g} filas={elementos.filter((e) => e.grupo === g)} consorcio={consorcio} abierta={abierta} />
      ))}
      {/* Pagos conciliados que falta cotejar con el banco (P-24): solo si hay. */}
      {porCotejar > 0 && (
        <section aria-label={TITULO_GRUPO.para_cotejar}>
          <h2 className="titulo-grupo">
            {TITULO_GRUPO.para_cotejar} <span className="cantidad">{porCotejar}</span>
          </h2>
          <p className="salidas-pago">
            <Link to={RUTA_COTEJO} state={{ consorcio }} className={`enlace-boton ${prepara ? "enlace-boton--borde" : "enlace-boton--principal"}`}>
              {TEXTO_ACCION.cotejar}
            </Link>
          </p>
        </section>
      )}
      {sinExplicarParaMirar && (
        <section aria-label={TITULO_SIN_EXPLICAR}>
          <h2 className="titulo-grupo">
            {TITULO_SIN_EXPLICAR} <span className="cantidad">{sinExplicar}</span>
          </h2>
          <p>Débitos del banco que ningún pago explica. La administración también los ve.</p>
          <p className="salidas-pago">
            <Link to={RUTA_SIN_EXPLICAR} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
              {VER_SIN_EXPLICAR}
            </Link>
          </p>
        </section>
      )}
      {plegados.map((g) => (
        <Plegado key={g} grupo={g} filas={elementos.filter((e) => e.grupo === g)} consorcio={consorcio} abierta={abierta} />
      ))}
      <p className="salidas-pago">
        <Link to={RUTA_CONSULTA} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
          Consultar pagos
        </Link>
        {prepara && (
          <Link to={RUTA_PROVEEDORES} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
            Proveedores
          </Link>
        )}
        {!coteja && sinExplicar > 0 && (
          <Link to={RUTA_SIN_EXPLICAR} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
            {`${TITULO_SIN_EXPLICAR} (${sinExplicar})`}
          </Link>
        )}
        {coteja && porCotejar === 0 && (
          <Link to={RUTA_COTEJO} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
            {VER_LO_COTEJADO}
          </Link>
        )}
      </p>
    </div>
  );
}
