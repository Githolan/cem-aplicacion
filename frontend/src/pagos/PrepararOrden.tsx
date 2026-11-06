import { useRef } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { useConsorcio } from "../egresos/useConsorcio";
import { REQUISITOS } from "../permisos";
import { FormularioOrden } from "./FormularioOrden";
import { Pestanas } from "./Pestanas";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import { useActo } from "./useActo";

// `/pagos/nueva`: preparar una orden. Al guardar sigue a su ficha, donde está el paso que falta.

/** El proveedor recién dado de alta con el que se vuelve a preparar la orden (`state.proveedor`). */
function proveedorDe(estado: unknown): string | null {
  if (typeof estado !== "object" || estado === null || !("proveedor" in estado)) return null;
  return typeof estado.proveedor === "string" ? estado.proveedor : null;
}

export function PrepararOrden() {
  const elegido = useConsorcio(REQUISITOS.ordenes);
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve.
  const proveedor = useRef(proveedorDe(useLocation().state));
  return <PrepararEnElEdificio key={elegido.consorcio} {...elegido} proveedor={proveedor.current} />;
}

type Props = ReturnType<typeof useConsorcio> & { proveedor: string | null };

function PrepararEnElEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, proveedor }: Props) {
  const navegar = useNavigate();
  const acto = useActo(aEntrar);
  const { aviso, pendiente, setPendiente, enviando } = acto;
  return (
    <>
      <p>
        <Link to={RUTA_PAGOS}>Volver a Pagos</Link>
      </p>
      <h1>Preparar una orden</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar: repetir } = pendiente;
            setPendiente(null);
            repetir();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <div hidden={pendiente !== null}>
          <FormularioOrden consorcio={consorcio} acto={acto} aEntrar={aEntrar} aInicio={aInicio} proveedorInicial={proveedor}
            alGuardar={(orden, texto) => navegar(rutaFicha(orden), { state: { aviso: texto, consorcio } })}
            alCancelar={() => navegar(RUTA_PAGOS, { state: { consorcio } })} />
        </div>
      )}
      <Pestanas />
    </>
  );
}
