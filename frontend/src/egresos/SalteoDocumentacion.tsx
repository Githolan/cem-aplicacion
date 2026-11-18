import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { PedidoDeSalteo, pendientesDe, textoPedidoAlConsejo, usePedidosSalteo } from "./PedidoSalteo";
import { cambioLoVisto, ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, momento, momentoAlFinal, renovarClave } from "./comun";
import { exigirSesion, SinSesion } from "./useConsorcio";

// El salteo con motivo del bloqueo por documentación legal (app 0106; respuesta del propietario
// del 16-11-2026 a la pregunta 3 del diseño, una urgencia como una pérdida de gas: «si con
// motivo»). Con el edificio en «bloquear» y al proveedor le falta algo, quien la configuración
// diga (por defecto, un miembro del Consejo) registra el salteo con el motivo. Lo usa una sola
// orden: la próxima que se le prepare a ese proveedor, y no la puede preparar quien registró el
// salteo. Nada se borra: la ficha muestra cada salteo, quién, cuándo y qué orden lo usó.
//
// Informe de fricción, tanda 4: el motivo se pide en una ventana (SEG-04), con el botón siempre
// activo (C8P-22) y lo que sirve para decidir adentro, no en la ficha (SEG-06); a la vista queda
// solo el salteo que espera su orden y los demás van plegados (SEG-07). Comentario 40 del
// propietario (lección 21) e informe de fricción C8P-03: quien tiene un salteo propio que espera su
// orden no ve otra vez el botón (la base no le deja registrar otro: `salteo_vigente`). La API dice
// de cada salteo si es de esta cuenta (`propio`, app 0109), también al volver a abrir la pantalla.
// Flujos 18-11, tanda 1: con cualquier salteo que espera su orden no se ofrece otro (decisión 5,
// FL-SAL-05), y el último que venció sin usarse se dice a la vista (FL-SAL-04).
// Flujos 18-11, tanda 9 (pregunta 3 del propietario, app 0110): quien prepara pide el salteo desde
// acá (`PedidoDeSalteo`), y quien saltea ve los pedidos que esperan, con el importe y el motivo, y el
// motivo del último ya escrito en la ventana (FL-SAL-01, FL-SAL-02).

type Salteo = components["schemas"]["SalteoDocumentacion"];
type Modo = components["schemas"]["ModoExigencia"];

export const TITULO_SALTEO = "Salteo por urgencia";
/** Lo que sirve para decidir, dentro de la ventana del salteo (SEG-06). */
export const EXPLICACION_SALTEO = "Sirve para una sola orden, la próxima, y no la puede preparar quien registra el salteo.";
export const SALTEAR = "Saltear el bloqueo para una orden";
export const CONFIRMAR_SALTEO = "Confirmar el salteo";
export const MOTIVO_SALTEO = "Motivo de la urgencia";
export const FALTA_MOTIVO_SALTEO = "Falta el motivo de la urgencia.";
export const LARGO_MOTIVO_SALTEO = 500;
export const SALTEO_REGISTRADO = "Salteo registrado: la próxima orden a este proveedor se puede preparar.";

export function textoSalteo(s: Salteo): string {
  const quien = `${momento(s.registrado_en)}, ${s.registrado_por_nombre}: ${s.motivo}`;
  // Sin la versión de la orden: es un dato interno (SEG-07).
  if (s.estado === "usado") return `Usado por una orden. Registrado el ${quien}`;
  // «p. m.» ya trae su punto: la frase no lleva otro (comentario 40).
  if (s.estado === "vencido") return `Venció sin usarse el ${momentoAlFinal(s.vence_en)} Registrado el ${quien}`;
  return `Espera su orden hasta el ${momentoAlFinal(s.vence_en)} Registrado el ${quien}`;
}

/** FL-SAL-04: el último salteo venció sin que se preparara la orden. */
export const textoVencioSinUsarse = (s: Salteo) => `El salteo del ${momento(s.registrado_en)} venció sin usarse.`;

/** La línea de la ficha de la orden: la versión se preparó con un salteo. */
export function textoSalteoDeLaVersion(s: Salteo): string {
  return `Preparada con un salteo por urgencia del bloqueo por documentación legal, registrado el ${momento(s.registrado_en)} por ${s.registrado_por_nombre}: ${s.motivo}`;
}

type Props = {
  consorcio: string;
  proveedorId: string;
  salteos: readonly Salteo[];
  modo: Modo;
  hayFaltantes: boolean;
  /** Lo que la configuración del edificio deja saltear. Solo para mostrar: la API y la base deciden. */
  puedeSaltear: boolean;
  /** Quien prepara órdenes pide el salteo al Consejo (con las monedas del edificio); el proveedor, activo. */
  pedir?: { monedas: readonly [string, ...string[]]; razonSocial: string } | null;
  aEntrar: () => void;
  /** Se registró el salteo, o lo visto ya no es lo que hay: volver a leer la ficha. */
  alCambiar: () => void;
};

export function SalteoDocumentacion({ consorcio, proveedorId, salteos, modo, hayFaltantes, puedeSaltear, pedir = null, aEntrar, alCambiar }: Props) {
  const id = useId();
  const [abierta, setAbierta] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const clave = useRef<string | null>(null);
  const bloquea = modo === "bloquear" && hayFaltantes;
  const enEspera = salteos.filter((s) => s.estado === "vigente");
  const anteriores = salteos.filter((s) => s.estado !== "vigente");
  /** El que se acaba de registrar, mientras la ficha vuelve a leerse y todavía no lo trae. */
  const [recien, setRecien] = useState<number | null>(null);
  const recienSinLeer = recien !== null && !salteos.some((s) => s.numero === recien);
  // Decisión 5 del propietario (18-11-2026, FL-SAL-05): mientras un salteo espera su orden, sea de quien
  // sea, no se ofrece otro: un segundo habilitaría una segunda orden.
  const ofreceSaltear = bloquea && puedeSaltear && enEspera.length === 0 && !recienSinLeer;
  /** FL-SAL-04: el último salteo venció sin usarse y no hay otro: se dice a la vista, no solo en «Anteriores». */
  const ultimo = salteos.reduce<Salteo | null>((a, s) => (a === null || s.numero > a.numero ? s : a), null);
  const vencioElUltimo = bloquea && enEspera.length === 0 && ultimo?.estado === "vencido" ? ultimo : null;
  /** Pedir el salteo (tanda 9): quien prepara, con el proveedor bloqueado y sin un salteo que espere. */
  const ofrecePedir = bloquea && pedir !== null && enEspera.length === 0 && !recienSinLeer;
  // Quien saltea ve los pedidos de otras personas que esperan para este proveedor (FL-SAL-02).
  const pedidos = usePedidosSalteo(consorcio, ofreceSaltear);
  const pedidosQueEsperan = ofreceSaltear && pedidos.data !== undefined ? pendientesDe(pedidos.data, proveedorId).filter((p) => !p.propio) : [];
  if (salteos.length === 0 && !ofreceSaltear && !ofrecePedir && resultado === null) return null;

  function abrir() {
    clave.current = null;
    // El motivo del pedido más nuevo, ya escrito: el Consejo lo confirma o lo corrige (FL-SAL-02).
    setMotivo(pedidosQueEsperan[0]?.motivo ?? "");
    setErrores(SIN_ERRORES);
    setResultado(null);
    setAbierta(true);
  }

  async function enviar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO_SALTEO] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/salteos-documentacion", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: consorcio, proveedor_id: proveedorId },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: { motivo: motivo.trim() },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        // Lo que se veía ya no es lo que hay: la ventana se cierra y la ficha se vuelve a leer.
        if (cambioLoVisto(response.status, codigo) || codigo === "salteo_innecesario" || codigo === "salteo_vigente") {
          setResultado({ texto: mensajeDeActo(response.status, error), ok: false });
          alCambiar();
          return "cerrar";
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        return { rechazo: mensajeDeActo(response.status, error) };
      }
      setRecien(data.numero);
      // El pedido que esperaba quedó atendido: se vuelve a leer.
      if (pedidosQueEsperan.length > 0) void pedidos.refetch();
      setResultado({ texto: SALTEO_REGISTRADO, ok: true });
      alCambiar();
      return "cerrar";
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    }
  }

  const idMotivo = `${id}-motivo`;
  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{TITULO_SALTEO}</h3>
      {vencioElUltimo !== null && (
        <p className="aviso-pendiente" role="note">
          {textoVencioSinUsarse(vencioElUltimo)}
        </p>
      )}
      {enEspera.length > 0 && (
        <ul className="versiones" aria-label="Salteos">
          {[...enEspera].reverse().map((s) => (
            <li key={s.numero}>{textoSalteo(s)}</li>
          ))}
        </ul>
      )}
      {anteriores.length > 0 && (
        <details className="historial">
          <summary>Anteriores</summary>
          <ul className="versiones" aria-label="Salteos anteriores">
            {[...anteriores].reverse().map((s) => (
              <li key={s.numero}>{textoSalteo(s)}</li>
            ))}
          </ul>
        </details>
      )}
      {pedidosQueEsperan.length > 0 && (
        <ul className="versiones" aria-label="Pedidos de salteo">
          {pedidosQueEsperan.map((p) => (
            <li key={p.numero}>{textoPedidoAlConsejo(p)}</li>
          ))}
        </ul>
      )}
      <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
      {ofrecePedir && (
        <PedidoDeSalteo consorcio={consorcio} proveedor={{ id: proveedorId, razon_social: pedir.razonSocial }} monedas={pedir.monedas}
          aEntrar={aEntrar} alCambiar={alCambiar} />
      )}
      {ofreceSaltear && (
        <button type="button" className="secundario peligro" onClick={abrir}>
          {SALTEAR}
        </button>
      )}
      {abierta && (
        <VentanaDeFormulario titulo={SALTEAR} textoEnviar={CONFIRMAR_SALTEO} peligro alEnviar={enviar} alCerrar={() => setAbierta(false)}>
          <p id={`${id}-ayuda`}>{EXPLICACION_SALTEO}</p>
          <label htmlFor={idMotivo}>{MOTIVO_SALTEO}</label>
          <textarea
            id={idMotivo}
            data-foco
            value={motivo}
            maxLength={LARGO_MOTIVO_SALTEO}
            {...marcaDe(idMotivo, errores.motivo, `${id}-ayuda`)}
            onChange={(ev) => {
              clave.current = null;
              setErrores((actuales) => sinElDe(actuales, "motivo"));
              setMotivo(ev.target.value);
            }}
          />
          <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
        </VentanaDeFormulario>
      )}
    </section>
  );
}
