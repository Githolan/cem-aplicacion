import { useId, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, importe, mensajeDeActo } from "../egresos/comun";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { SIN_CONEXION } from "../fallas";
import { usePuede } from "../permisos";
import { FilaEnlace } from "./FilaEnlace";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_SIN_EXPLICAR, rutaFicha } from "./rutas";
import {
  AVISO_LECTURA_DISPUTADA,
  ENTENDIDO,
  fechaCorta,
  type Grupo,
  MARCA_REPETIDA,
  NADA_PENDIENTE,
  PREPARAR_OTRA,
  textoRechazada,
  TITULO_GRUPO,
  TITULO_SIN_EXPLICAR,
  VER_LO_COTEJADO,
} from "./textos";

// Lo que esta persona tiene que hacer ahora, agrupado por el próximo paso (§2). La arma el servidor
// con la tabla de la pantalla: una fila aparece solo si la base va a aceptar la acción, ya viene en
// el orden en que se muestra y dice si cuenta como pendiente. Sin secciones vacías.
//
// Informe de fricción del 17-11-2026 (tanda 6): cada orden es una fila compacta, como el Padrón
// (proveedor; concepto, fecha e importe), sin el verbo ni el estado, que ya dice el título del grupo
// (BAN-04). Lo que espera a otra persona no es para hacer: no está en la bandeja, sino en la
// consulta, «En curso» (BAN-05); los borradores viejos sí son de quien prepara y quedan plegados.
// El cotejo y lo que el extracto no explica son una fila cada uno, que lleva a su pantalla (BAN-07).

type BandejaPagos = components["schemas"]["BandejaPagos"];
type Elemento = components["schemas"]["ElementoBandejaPagos"];
type AvisoRechazo = components["schemas"]["AvisoRechazo"];

const cantidad = (n: number, una: string, varias: string) => (n === 1 ? `1 ${una}` : `${n} ${varias}`);

function Fila({ e, consorcio }: { e: Elemento; consorcio: string }) {
  // Las marcas cortas; lo que quieren decir lo explica la ficha.
  const marcas = [e.lectura_disputada ? AVISO_LECTURA_DISPUTADA : null, e.posible_duplicada_de === null ? null : MARCA_REPETIDA].filter(
    (m): m is string => m !== null,
  );
  return (
    <li>
      <FilaEnlace
        a={rutaFicha(e.orden_id)}
        estadoNavegacion={{ consorcio }}
        titulo={e.proveedor}
        estado={marcas.length === 0 ? undefined : marcas.join(" · ")}
        resumen={
          <>
            {e.concepto} · <time dateTime={e.creada_en}>{fechaCorta(e.creada_en)}</time> · {importe(e.importe_centavos, e.moneda)}
          </>
        }
      />
    </li>
  );
}

function Seccion({ grupo, filas, consorcio }: { grupo: Grupo; filas: Elemento[]; consorcio: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="titulo-grupo">
        {TITULO_GRUPO[grupo]} <span className="cantidad">{filas.length}</span>
      </h2>
      <ul className="filas-registro">
        {filas.map((e) => (
          <Fila key={e.orden_id} e={e} consorcio={consorcio} />
        ))}
      </ul>
    </section>
  );
}

function Plegado({ grupo, filas, consorcio }: { grupo: Grupo; filas: Elemento[]; consorcio: string }) {
  return (
    <details className="historial">
      <summary>
        {TITULO_GRUPO[grupo]} ({filas.length})
      </summary>
      <ul className="filas-registro">
        {filas.map((e) => (
          <Fila key={e.orden_id} e={e} consorcio={consorcio} />
        ))}
      </ul>
    </details>
  );
}

type PropsAviso = { aviso: AvisoRechazo; consorcio: string; aEntrar: () => void; alCambiar: () => void };

/** Una orden rechazada, una vez, a quien la preparó (decisión 3): con «Entendido» o con «Preparar
 * otra orden» queda vista y pasa a la consulta (BAN-08). */
function AvisoDeRechazo({ aviso, consorcio, aEntrar, alCambiar }: PropsAviso) {
  const navegar = useNavigate();
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);

  function darPorVisto(despues: () => void) {
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
        if (data !== undefined) despues();
        else setFalla(mensajeDeActo(response.status, error));
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setFalla(SIN_CONEXION)))
      .finally(() => setEnviando(false));
  }

  return (
    <div className="tarjeta aviso-rechazo" role="status">
      {/* El aviso abre la orden rechazada, para ver su factura o su concepto (FL-BAN-01). */}
      <p>
        <Link to={rutaFicha(aviso.orden_id)} state={{ consorcio }}>
          {textoRechazada(aviso.proveedor, importe(aviso.importe_centavos, aviso.moneda), fechaCorta(aviso.creada_en), aviso.concepto)}
        </Link>
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
        {/* FL-ORD-05 (tanda 9): solo si hoy se le puede preparar una orden a ese proveedor. */}
        {aviso.proveedor_admite_orden && (
          <button
            type="button"
            className="secundario"
            disabled={enviando}
            onClick={() => darPorVisto(() => navegar(RUTA_NUEVA, { state: { consorcio, proveedor: aviso.proveedor_id } }))}
          >
            {PREPARAR_OTRA}
          </button>
        )}
        <button type="button" className="secundario" disabled={enviando} onClick={() => darPorVisto(alCambiar)}>
          {ENTENDIDO}
        </button>
      </div>
    </div>
  );
}

type Props = {
  bandeja: BandejaPagos;
  consorcio: string;
  aEntrar: () => void;
  /** Algo cambió (se vio un rechazo): la bandeja se vuelve a leer. */
  alCambiar: () => void;
};

export function Bandeja({ bandeja, consorcio, aEntrar, alCambiar }: Props) {
  const { avisos_rechazo: avisos, por_cotejar: porCotejar, sin_explicar: sinExplicar, marcados_no_es_pago: marcados } = bandeja;
  const elementos = bandeja.elementos.filter((e) => e.grupo !== "esperando");
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
  /** La salida de abajo a esa pantalla: la administración, para leer lo sin explicar, y los dos si
   * solo quedan marcas vigentes, que si no quedan sin camino para retirarlas (Codex en #240). */
  const consultaExtracto = !sinExplicarParaMirar && (sinExplicar > 0 || marcados > 0);
  return (
    <div className="bandeja">
      {/* Lo que más hace quien prepara va arriba, antes de la lista (P-35, 12-11-2026); las consultas,
          abajo. Un solo botón lleno: el del rol (comentario 24, 09-11-2026). */}
      {prepara && (
        <p className="salidas-pago salidas-pago--arriba">
          <Link to={RUTA_NUEVA} state={{ consorcio }} className="enlace-boton enlace-boton--principal">
            Preparar una orden
          </Link>
        </p>
      )}
      {avisos.map((a) => (
        <AvisoDeRechazo key={a.orden_id} aviso={a} consorcio={consorcio} aEntrar={aEntrar} alCambiar={alCambiar} />
      ))}
      {paraHacer.length === 0 && porCotejar === 0 && !sinExplicarParaMirar && <p className="tarjeta bandeja-al-dia">{NADA_PENDIENTE}</p>}
      {paraHacer.map((g) => (
        <Seccion key={g} grupo={g} filas={elementos.filter((e) => e.grupo === g)} consorcio={consorcio} />
      ))}
      {/* Pagos conciliados que falta cotejar con el banco (P-24) y lo que el extracto no explica: solo si hay. */}
      {(porCotejar > 0 || sinExplicarParaMirar) && (
        <ul className="filas-registro bandeja-banco">
          {porCotejar > 0 && (
            <li>
              <FilaEnlace a={RUTA_COTEJO} estadoNavegacion={{ consorcio }} titulo={TITULO_GRUPO.para_cotejar} resumen={cantidad(porCotejar, "pago", "pagos")} />
            </li>
          )}
          {sinExplicarParaMirar && (
            <li>
              <FilaEnlace a={RUTA_SIN_EXPLICAR} estadoNavegacion={{ consorcio }} titulo={TITULO_SIN_EXPLICAR} resumen={cantidad(sinExplicar, "débito", "débitos")} />
            </li>
          )}
        </ul>
      )}
      {plegados.map((g) => (
        <Plegado key={g} grupo={g} filas={elementos.filter((e) => e.grupo === g)} consorcio={consorcio} />
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
        {consultaExtracto && (
          <Link to={RUTA_SIN_EXPLICAR} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
            {sinExplicar > 0 ? `${TITULO_SIN_EXPLICAR} (${sinExplicar})` : TITULO_SIN_EXPLICAR}
          </Link>
        )}
        {/* Llega con lo cotejado ya a la vista (FL-CON-01); sin nada cotejado, no se ofrece (tanda 9). */}
        {coteja && porCotejar === 0 && bandeja.cotejados > 0 && (
          <Link to={RUTA_COTEJO} state={{ consorcio, cotejados: true }} className="enlace-boton enlace-boton--borde">
            {VER_LO_COTEJADO}
          </Link>
        )}
      </p>
    </div>
  );
}
