import { useRef } from "react";
import { useLocation, useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import type { Cargado } from "../CargaConLectura";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { useConsorcio } from "../egresos/useConsorcio";
import { REQUISITOS } from "../permisos";
import { FormularioOrden, noSeEnvio, type OrdenInicial } from "./FormularioOrden";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import { useActo } from "./useActo";

// `/pagos/nueva`: preparar una orden. Lo normal es guardarla y enviarla a aprobar en un solo acto
// (decisión del propietario del 12-11-2026); al terminar sigue a su ficha, con lo que pasó.

/** Con qué abre: el proveedor recién dado de alta o el de otra orden, y lo que falta de ella; al
 * volver del alta del proveedor de una factura, también su número y la factura ya subida. Todo es
 * del edificio que trae la navegación (`consorcio`): en otro edificio no se usa. */
type Inicial = { consorcio: string | null; orden: OrdenInicial };

function inicialDe(estado: unknown): Inicial {
  if (typeof estado !== "object" || estado === null) return { consorcio: null, orden: {} };
  const consorcio = (estado as Record<string, unknown>).consorcio;
  return { consorcio: typeof consorcio === "string" ? consorcio : null, orden: ordenDe(estado) };
}

function ordenDe(estado: object): OrdenInicial {
  const texto = (campo: string) => {
    const valor = (estado as Record<string, unknown>)[campo];
    return typeof valor === "string" ? valor : undefined;
  };
  return {
    proveedor: texto("proveedor"),
    importe: texto("importe"),
    moneda: texto("moneda"),
    concepto: texto("concepto"),
    comprobante: texto("comprobante"),
    factura: facturaDe((estado as Record<string, unknown>).factura),
  };
}

/** La factura ya subida que vuelve del alta del proveedor (`AltaDesdeLaFactura`). */
function facturaDe(valor: unknown): Cargado | undefined {
  if (typeof valor !== "object" || valor === null) return undefined;
  const { documento, version, nombre } = valor as Record<string, unknown>;
  return typeof documento === "string" && typeof version === "string" && typeof nombre === "string"
    ? { documento, version, nombre }
    : undefined;
}

const SIN_DATOS: OrdenInicial = {};

export function PrepararOrden() {
  const elegido = useConsorcio(REQUISITOS.ordenes);
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve.
  const inicial = useRef(inicialDe(useLocation().state));
  // El proveedor, los importes y la factura de la llegada son de su edificio: al elegir otro, nada.
  const deEsteEdificio = inicial.current.consorcio === elegido.consorcio ? inicial.current.orden : SIN_DATOS;
  return <PrepararEnElEdificio key={elegido.consorcio} {...elegido} inicial={deEsteEdificio} />;
}

type Props = ReturnType<typeof useConsorcio> & { inicial: OrdenInicial };

function PrepararEnElEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, inicial }: Props) {
  const navegar = useNavigate();
  const acto = useActo(aEntrar);
  const { aviso, pendiente, setPendiente, enviando } = acto;
  /** La orden ya guardada mientras falta enviarla: si se cancela el código, se va a su ficha. */
  const creada = useRef<string | null>(null);
  const aLaFicha = (orden: string, texto: string, falla = false) =>
    navegar(rutaFicha(orden), { state: { aviso: texto, falla, consorcio } });
  return (
    <>
      <CabeceraInterna titulo="Preparar una orden" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
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
          alCancelar={() => {
            setPendiente(null);
            if (creada.current !== null) aLaFicha(creada.current, noSeEnvio("No se confirmó el código."), true);
          }}
        />
      )}
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <div hidden={pendiente !== null}>
          <FormularioOrden consorcio={consorcio} acto={acto} aEntrar={aEntrar} aInicio={aInicio} inicial={inicial}
            alGuardar={aLaFicha} alCrear={(orden) => (creada.current = orden)}
            alCancelar={() => navegar(RUTA_PAGOS, { state: { consorcio } })} />
        </div>
      )}
    </>
  );
}
