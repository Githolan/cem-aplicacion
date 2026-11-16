import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { cambioLoVisto, ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, momento, renovarClave } from "./comun";
import { exigirSesion, SinSesion } from "./useConsorcio";

// El salteo con motivo del bloqueo por documentación legal (app 0106; respuesta del propietario
// del 16-11-2026 a la pregunta 3 del diseño, una urgencia como una pérdida de gas: «si con
// motivo»). Con el edificio en «bloquear» y al proveedor le falta algo, quien la configuración
// diga (por defecto, un miembro del Consejo) registra el salteo con el motivo. Lo usa una sola
// orden: la próxima que se le prepare a ese proveedor, y no la puede preparar quien registró el
// salteo. Nada se borra: la ficha muestra cada salteo, quién, cuándo y qué orden lo usó.

type Salteo = components["schemas"]["SalteoDocumentacion"];
type Modo = components["schemas"]["ModoExigencia"];

export const TITULO_SALTEO = "Salteo por urgencia";
export const EXPLICACION_SALTEO =
  "En una urgencia (por ejemplo, una pérdida de gas), un miembro del Consejo puede dejar preparar una orden a este proveedor aunque le falte documentación, con el motivo. Sirve para una sola orden, la próxima que se prepare, y no la puede preparar quien registra el salteo.";
export const SALTEAR = "Saltear el bloqueo para una orden";
export const CONFIRMAR_SALTEO = "Confirmar el salteo";
export const MOTIVO_SALTEO = "Motivo de la urgencia";
export const LARGO_MOTIVO_SALTEO = 500;
export const SALTEO_REGISTRADO = "Salteo registrado: la próxima orden a este proveedor se puede preparar.";

export function textoSalteo(s: Salteo): string {
  const quien = `${momento(s.registrado_en)}, ${s.registrado_por_nombre}: ${s.motivo}`;
  if (s.estado === "usado" && s.version !== null && s.version !== undefined)
    return `Usado por una orden (versión ${s.version}). Registrado el ${quien}`;
  if (s.estado === "vencido") return `Venció sin usarse el ${momento(s.vence_en)}. Registrado el ${quien}`;
  return `Espera su orden hasta el ${momento(s.vence_en)}. Registrado el ${quien}`;
}

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
  aEntrar: () => void;
  /** Se registró el salteo, o lo visto ya no es lo que hay: volver a leer la ficha. */
  alCambiar: (texto: string, ok: boolean) => void;
};

export function SalteoDocumentacion({ consorcio, proveedorId, salteos, modo, hayFaltantes, puedeSaltear, aEntrar, alCambiar }: Props) {
  const id = useId();
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const clave = useRef<string | null>(null);
  const bloquea = modo === "bloquear" && hayFaltantes;
  if (salteos.length === 0 && !(bloquea && puedeSaltear)) return null;

  function cerrar() {
    setAbierto(false);
    setMotivo("");
    setFalla(null);
    clave.current = null;
  }

  async function enviar() {
    setEnviando(true);
    setFalla(null);
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
        if (cambioLoVisto(response.status, codigo) || codigo === "salteo_innecesario" || codigo === "salteo_vigente") {
          cerrar();
          alCambiar(mensajeDeActo(response.status, error), false);
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setFalla(mensajeDeActo(response.status, error));
        return;
      }
      cerrar();
      alCambiar(SALTEO_REGISTRADO, true);
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFalla(SIN_CONEXION);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{TITULO_SALTEO}</h3>
      {bloquea && puedeSaltear && <p>{EXPLICACION_SALTEO}</p>}
      {salteos.length > 0 && (
        <ul className="versiones" aria-label="Salteos">
          {[...salteos].reverse().map((s) => (
            <li key={s.numero}>{textoSalteo(s)}</li>
          ))}
        </ul>
      )}
      {bloquea &&
        puedeSaltear &&
        (!abierto ? (
          <div className="acciones">
            <button type="button" className="secundario peligro" onClick={() => setAbierto(true)}>
              {SALTEAR}
            </button>
          </div>
        ) : (
          <form
            className="formulario"
            onSubmit={(ev) => {
              ev.preventDefault();
              void enviar();
            }}
          >
            <label htmlFor={`${id}-motivo`}>{MOTIVO_SALTEO}</label>
            <textarea
              id={`${id}-motivo`}
              value={motivo}
              maxLength={LARGO_MOTIVO_SALTEO}
              required
              disabled={enviando}
              onChange={(ev) => setMotivo(ev.target.value)}
            />
            {falla !== null && (
              <p role="alert" className="aviso-error">
                {falla}
              </p>
            )}
            <div className="acciones">
              <button type="submit" className="peligro" disabled={enviando || motivo.trim() === ""}>
                {CONFIRMAR_SALTEO}
              </button>
              <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                Cancelar
              </button>
            </div>
          </form>
        ))}
    </section>
  );
}
