import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ActivarLlave } from "./ActivarLlave";
import { ActivarAplicacion } from "./AltaAplicacion";
import { EntrarParaActivar, useAltaVencida } from "./altaDeConfirmacion";
import { api } from "./api/cliente";
import { CodigosRecuperacion } from "./CodigosRecuperacion";
import { codigoDe, mensajeDeFalla } from "./fallas";
import { Hoja } from "./Hoja";
import { llaveDisponible } from "./llaveAcceso";
import { FallaDeLectura, mensajeDeLectura, ofreceEnAlguno, type Requisito, SIN_CACHE, useAccionesPorEdificio } from "./permisos";

// Activar la confirmación desde el inicio (decisión del propietario del 18-11-2026, pregunta 10;
// FL-CTA-01): a quien puede aprobar, pagar o conciliar y todavía no tiene ningún método, el inicio se
// lo ofrece apenas entra. Es cuando la sesión es reciente, que es lo que el servidor pide para dar de
// alta el primer método (`backend/app/identidad/segundo_factor.py`, `exigir_entrada_reciente`). Si la
// sesión ya no es reciente, la hoja de entrar encima lo pide y, al entrar, el alta sigue sola.
//
// Se activa ahí mismo, sin ir a otra pantalla: con la llave de este dispositivo si el navegador la
// sabe usar y, si no o además, con una aplicación de códigos en una ventana encima (FL-CTA-03).

/** Los actos que piden la confirmación: aprobar, pagar o conciliar (políticas económicas de egresos). */
const PIDEN_CONFIRMACION: Requisito = [
  ["egresos.revisar_orden"],
  ["egresos.aprobar_orden"],
  ["egresos.ejecutar_pago"],
  ["egresos.conciliar_pago"],
];

export const FALTA_ACTIVAR = "Para aprobar, pagar o conciliar falta activar la confirmación con PIN, huella o código.";

type Activado = { medio: "llave" | "aplicacion"; codigos: string[] | null };

const LISTO: Record<Activado["medio"], string> = {
  llave: "Listo: la llave quedó activada.",
  aplicacion: "Listo: la confirmación con código quedó activada.",
};

async function tieneConfirmacion(): Promise<boolean> {
  const { data, error, response } = await api.GET("/api/v1/auth/totp");
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.activo || data.llaves > 0;
}

/** Si a esta cuenta le da uso la confirmación y si ya la tiene. El inicio la usa también para no
 * decir «No hay nada pendiente.» mientras falta activarla o no se sabe. */
export function useFaltaConfirmacion() {
  const porEdificio = useAccionesPorEdificio();
  const leDaUso = porEdificio !== null && ofreceEnAlguno(porEdificio, PIDEN_CONFIRMACION);
  const consulta = useQuery({ queryKey: ["tiene-confirmacion"], queryFn: tieneConfirmacion, enabled: leDaUso, ...SIN_CACHE });
  return { leDaUso, consulta, pendiente: leDaUso && consulta.data !== true };
}

/** `consulta` viene del inicio, que junta su falla con las demás bajo un solo «Reintentar» (AC-08). */
export function FaltaConfirmacion({ consulta: deAfuera }: { consulta?: ReturnType<typeof useFaltaConfirmacion> } = {}) {
  const propia = useFaltaConfirmacion();
  const { leDaUso, consulta } = deAfuera ?? propia;
  const [activado, setActivado] = useState<Activado | null>(null);
  const [ocupado, setOcupado] = useState(false);
  // FL-CTA-01 (tanda 9): solo si se va a ofrecer activar, si el plazo de esta sesión ya pasó.
  const alta = useAltaVencida(leDaUso && consulta.data === false && activado === null);

  if (activado !== null)
    return activado.codigos === null ? (
      <p className="aviso-ok tarjeta" role="status">
        {LISTO[activado.medio]}
      </p>
    ) : (
      // Los códigos llegan una sola vez: la hoja no se cierra hasta que la persona dice que los guardó.
      <Hoja titulo="Guardar los códigos de recuperación">
        <p className="aviso-ok">{LISTO[activado.medio]}</p>
        <CodigosRecuperacion
          codigos={activado.codigos}
          medio={activado.medio}
          alGuardar={() => setActivado({ medio: activado.medio, codigos: null })}
        />
      </Hoja>
    );

  if (!leDaUso || consulta.data === true) return null;
  if (consulta.isError && deAfuera !== undefined) return null;
  if (consulta.isError)
    return (
      <section aria-label="Confirmación con PIN, huella o código" className="tarjeta">
        <p className="aviso-error" role="alert">
          No se pudo saber si la confirmación con PIN, huella o código está activada. {mensajeDeLectura(consulta.error)}
        </p>
        <button type="button" className="secundario" disabled={consulta.isFetching} onClick={() => void consulta.refetch()}>
          Reintentar
        </button>
      </section>
    );
  if (consulta.data === undefined) return null;

  const conLlave = llaveDisponible();
  if (alta.vencida === true)
    return (
      <section aria-label="Confirmación con PIN, huella o código" className="tarjeta falta-confirmacion">
        <p>{FALTA_ACTIVAR}</p>
        <EntrarParaActivar plazo={alta.plazo} />
      </section>
    );
  return (
    <section aria-label="Confirmación con PIN, huella o código" className="tarjeta falta-confirmacion">
      <p>{FALTA_ACTIVAR}</p>
      <div className="acciones">
        <ActivarLlave alOcupar={setOcupado} alActivar={(codigos) => setActivado({ medio: "llave", codigos })} />
        <ActivarAplicacion
          secundario={conLlave}
          alActivar={(codigos) => setActivado({ medio: "aplicacion", codigos })}
        />
      </div>
      {/* La aplicación muestra su propia espera; esta es la de la llave. */}
      {ocupado && (
        <Hoja titulo="Activando">
          <p role="status">Seguir lo que pide el dispositivo: PIN, huella o cara.</p>
        </Hoja>
      )}
    </section>
  );
}
