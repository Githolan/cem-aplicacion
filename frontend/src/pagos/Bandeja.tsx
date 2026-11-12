import { useId } from "react";
import { Link } from "react-router";

import type { components } from "../api/tipos";
import { importe } from "../egresos/comun";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { usePuede } from "../permisos";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, rutaFicha } from "./rutas";
import { AVISO_LECTURA_DISPUTADA, type Grupo, TEXTO_LE_TOCA_A, TITULO_GRUPO, VERBO } from "./textos";

// Lo que esta persona tiene que hacer ahora, agrupado por el próximo paso (§2). La arma el servidor:
// una fila aparece solo si la base va a aceptar la acción, y ya viene en el orden en que se muestra.
// Sin secciones vacías. Lo que espera a otra persona queda plegado: no es para hacer.

type Elemento = components["schemas"]["ElementoBandejaPagos"];

type PropsFila = { e: Elemento; consorcio: string; abierta: string | null };

function Fila({ e, consorcio, abierta }: PropsFila) {
  return (
    <li>
      <Link to={rutaFicha(e.orden_id)} state={{ consorcio }} className="fila-pago" aria-current={e.orden_id === abierta ? "true" : undefined}>
        <span className="fila-pago__arriba">
          <strong>{e.proveedor}</strong>
          <span className="fila-pago__importe">{importe(e.importe_centavos, e.moneda)}</span>
        </span>
        <span>{e.concepto}</span>
        <span className="fila-pago__verbo">{VERBO[e.paso]}</span>
        {e.grupo === "esperando" && <span>{TEXTO_LE_TOCA_A[e.le_toca_a]}</span>}
        {e.lectura_disputada && <span>{AVISO_LECTURA_DISPUTADA}</span>}
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

type Props = { elementos: Elemento[]; consorcio: string; abierta: string | null };

export function Bandeja({ elementos, consorcio, abierta }: Props) {
  const puede = usePuede(consorcio);
  const prepara = puede("egresos.preparar_orden");
  const coteja = puede("egresos.conciliar_pago");
  const grupos = [...new Set(elementos.map((e) => e.grupo))].filter((g) => g !== "esperando");
  const esperando = elementos.filter((e) => e.grupo === "esperando");
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
      {grupos.length === 0 && (
        <div className="tarjeta bandeja-al-dia">
          <p className="etiqueta-situacion" data-tono="bien">
            Al día
          </p>
          <p>Nada pendiente.</p>
        </div>
      )}
      {grupos.map((g) => (
        <Seccion key={g} grupo={g} filas={elementos.filter((e) => e.grupo === g)} consorcio={consorcio} abierta={abierta} />
      ))}
      {esperando.length > 0 && (
        <details className="historial">
          <summary>
            {TITULO_GRUPO.esperando} ({esperando.length})
          </summary>
          <ul className="filas-pago">
            {esperando.map((e) => (
              <Fila key={e.orden_id} e={e} consorcio={consorcio} abierta={abierta} />
            ))}
          </ul>
        </details>
      )}
      <p className="salidas-pago">
        {coteja && (
          <Link to={RUTA_COTEJO} state={{ consorcio }} className={`enlace-boton ${prepara ? "enlace-boton--borde" : "enlace-boton--principal"}`}>
            Cotejar con el banco
          </Link>
        )}
        <Link to={RUTA_CONSULTA} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
          Consultar pagos
        </Link>
        {prepara && (
          <Link to={RUTA_PROVEEDORES} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
            Proveedores
          </Link>
        )}
      </p>
    </div>
  );
}
