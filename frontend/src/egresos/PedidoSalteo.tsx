import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "../fallas";
import { FALTA_IMPORTE, NOMBRE_MONEDA } from "../pagos/textos";
import { FallaDeLectura, mensajeDeLectura, SIN_CACHE } from "../permisos";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { CampoImporte } from "./CampoImporte";
import { aCentavos, ENCABEZADO_IDEMPOTENCIA, importe, mensajeDeActo, momento, renovarClave } from "./comun";
import { exigirSesion, SinSesion } from "./useConsorcio";

// El pedido de salteo al Consejo (pregunta 3 del propietario, 18-11-2026, opción a; app 0110;
// flujos 18-11, tanda 9: FL-SAL-01 y FL-SAL-02). Con el proveedor bloqueado por documentación legal,
// quien prepara la orden pide el salteo desde el aviso: en el formulario de la orden y en la ficha del
// proveedor. La ventana lleva el motivo, el importe y la moneda de la orden que se quiere preparar. Al
// Consejo el pedido le aparece en «Para hacer» del inicio y en la ficha del proveedor, con lo pedido a
// la vista. Mientras haya un pedido propio que espera, no se ofrece otro: se dice su estado.

export type PedidoSalteo = components["schemas"]["PedidoSalteo"];

export const PEDIR_SALTEO = "Pedir un salteo al Consejo";
export const ENVIAR_PEDIDO = "Enviar el pedido";
export const EXPLICACION_PEDIDO =
  "El Consejo lo ve en su inicio. Si registra el salteo, sirve para una sola orden: la próxima a este proveedor.";
export const MOTIVO_PEDIDO = "Motivo de la urgencia";
export const IMPORTE_PEDIDO = "Importe de la orden";
export const FALTA_MOTIVO_PEDIDO = "Falta el motivo de la urgencia.";
export const IMPORTE_INVALIDO = "El importe va en números, por ejemplo 12345,50.";
export const PEDIDO_ENVIADO = "Pedido enviado: el Consejo lo ve en su inicio.";
const LARGO_MOTIVO = 500;
/** Los rechazos que dicen que lo visto ya no es lo que hay, dichos para quien pide (no para quien saltea). */
export const RECHAZO_DEL_PEDIDO: Readonly<Record<string, string>> = {
  pedido_salteo_pendiente: "Ya hay un pedido de salteo de este proveedor que espera al Consejo.",
  salteo_vigente: "Ya hay un salteo que sirve para preparar la orden a este proveedor.",
  salteo_innecesario: "Nada bloquea las órdenes a este proveedor: no hace falta pedir un salteo.",
};

/** A quien lo pidió, mientras espera: no se ofrece otro (`pedido_salteo_pendiente`). */
export const textoPedidoPropio = (p: PedidoSalteo) => `Salteo pedido al Consejo el ${momento(p.pedido_en)}: espera que lo registre.`;
/** Al Consejo, en la ficha del proveedor: lo pedido a la vista (FL-SAL-02). */
export const textoPedidoAlConsejo = (p: PedidoSalteo) =>
  `Pedido de ${p.pedido_por_nombre} el ${momento(p.pedido_en)}: una orden de ${importe(p.importe_centavos, p.moneda)}. ${p.motivo}`;

const claveDe = (consorcio: string) => ["pedidos-salteo", consorcio] as const;

async function leerPedidos(consorcio: string): Promise<PedidoSalteo[]> {
  const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pedidos-salteo", {
    params: { path: { consorcio_id: consorcio } },
  });
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.elementos;
}

/** Los pedidos de salteo del edificio, para quien saltea y para quien prepara. */
export function usePedidosSalteo(consorcio: string, activa: boolean) {
  return useQuery({ queryKey: claveDe(consorcio), queryFn: () => leerPedidos(consorcio), enabled: activa, ...SIN_CACHE });
}

/** Los que esperan al Consejo para ese proveedor. */
export const pendientesDe = (pedidos: readonly PedidoSalteo[], proveedorId: string) =>
  pedidos.filter((p) => p.proveedor_id === proveedorId && p.estado === "pendiente");

type Props = {
  consorcio: string;
  proveedor: { id: string; razon_social: string };
  /** Las monedas que se ofrecen (las del edificio); con una sola no se pregunta. */
  monedas: readonly [string, ...string[]];
  /** Lo que ya se escribió en la orden, si se viene de ella. */
  inicial?: { importe: string; moneda: string };
  aEntrar: () => void;
  /** Lo que se veía ya no es lo que hay (otro salteo, ya no bloquea): volver a leer los proveedores. */
  alCambiar: () => void;
};

/** El botón «Pedir un salteo al Consejo» y su ventana; con un pedido propio que espera, su estado. */
export function PedidoDeSalteo({ consorcio, proveedor, monedas, inicial, aEntrar, alCambiar }: Props) {
  const id = useId();
  const consultas = useQueryClient();
  const pedidos = usePedidosSalteo(consorcio, true);
  const [abierta, setAbierta] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [monto, setMonto] = useState("");
  const [moneda, setMoneda] = useState<string>(monedas[0]);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const clave = useRef<string | null>(null);

  if (pedidos.isPending) return null;
  if (pedidos.isError)
    return (
      <p className="aviso-error" role="alert">
        No se pudo saber si ya hay un pedido de salteo. {mensajeDeLectura(pedidos.error)}
      </p>
    );
  const propio = pendientesDe(pedidos.data, proveedor.id).find((p) => p.propio);

  function abrir() {
    clave.current = null;
    setMotivo("");
    setMonto(inicial?.importe ?? "");
    setMoneda(inicial !== undefined && monedas.includes(inicial.moneda) ? inicial.moneda : monedas[0]);
    setErrores(SIN_ERRORES);
    setResultado(null);
    setAbierta(true);
  }

  async function enviar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO_PEDIDO], importe: [monto, FALTA_IMPORTE] });
    const centavos = aCentavos(monto);
    const conImporte = hayErrores(faltan) || centavos !== null ? faltan : { ...faltan, importe: IMPORTE_INVALIDO };
    if (hayErrores(conImporte) || centavos === null) {
      setErrores(conImporte);
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/pedidos-salteo", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: consorcio, proveedor_id: proveedor.id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: { motivo: motivo.trim(), importe_centavos: centavos, moneda },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        // Ya hay uno que espera: se vuelve a leer y se ve su estado, sin otro pedido.
        if (codigo === "pedido_salteo_pendiente") {
          void pedidos.refetch();
          setResultado({ texto: RECHAZO_DEL_PEDIDO[codigo] ?? mensajeDeActo(response.status, error), ok: false });
          return "cerrar";
        }
        // Ya hay un salteo que sirve, o ya nada bloquea: lo que se veía ya no es lo que hay.
        if (codigo === "salteo_vigente" || codigo === "salteo_innecesario") {
          setResultado({ texto: RECHAZO_DEL_PEDIDO[codigo] ?? mensajeDeActo(response.status, error), ok: false });
          alCambiar();
          return "cerrar";
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      consultas.setQueryData<PedidoSalteo[]>(claveDe(consorcio), (actuales) => [data, ...(actuales ?? [])]);
      setResultado({ texto: PEDIDO_ENVIADO, ok: true });
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    }
  }

  const tocar = (campo: string) => {
    clave.current = null;
    setErrores((actuales) => sinElDe(actuales, campo));
  };
  const idMotivo = `${id}-motivo`;
  const idImporte = `${id}-importe`;
  return (
    <>
      <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
      {propio !== undefined ? (
        <p className="aviso-pendiente" role="note">
          {textoPedidoPropio(propio)}
        </p>
      ) : (
        resultado?.ok !== true && (
          <p>
            <button type="button" className="secundario" onClick={abrir}>
              {PEDIR_SALTEO}
            </button>
          </p>
        )
      )}
      {abierta && (
        <VentanaDeFormulario titulo={`${PEDIR_SALTEO}: ${proveedor.razon_social}`} textoEnviar={ENVIAR_PEDIDO} alEnviar={enviar}
          alCerrar={() => setAbierta(false)}>
          <p id={`${id}-ayuda`}>{EXPLICACION_PEDIDO}</p>
          <label htmlFor={idMotivo}>{MOTIVO_PEDIDO}</label>
          <textarea id={idMotivo} data-foco value={motivo} maxLength={LARGO_MOTIVO} {...marcaDe(idMotivo, errores.motivo, `${id}-ayuda`)}
            onChange={(ev) => {
              tocar("motivo");
              setMotivo(ev.target.value);
            }} />
          <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
          <label htmlFor={idImporte}>{IMPORTE_PEDIDO}</label>
          <CampoImporte id={idImporte} valor={monto} deshabilitado={false} {...marcaDe(idImporte, errores.importe)}
            alCambiar={(texto) => {
              tocar("importe");
              setMonto(texto);
            }} />
          <ErrorDeCampo idCampo={idImporte} mensaje={errores.importe} />
          {monedas.length > 1 && (
            <>
              <label htmlFor={`${id}-moneda`}>Moneda</label>
              <select id={`${id}-moneda`} value={moneda}
                onChange={(ev) => {
                  clave.current = null;
                  setMoneda(ev.target.value);
                }}>
                {monedas.map((m) => (
                  <option key={m} value={m}>
                    {NOMBRE_MONEDA[m] ?? m}
                  </option>
                ))}
              </select>
            </>
          )}
        </VentanaDeFormulario>
      )}
    </>
  );
}
