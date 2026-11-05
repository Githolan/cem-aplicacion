import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { llaveDisponible, quitarLlave, registrarLlave, type Resultado } from "./llaveAcceso";

// La llave de acceso en /cuenta/segundo-factor: activarla es un solo botón, sin aplicación aparte.
// Si el navegador no sabe usarlas, la sección no aparece y queda la aplicación de códigos. Cuando
// falta confirmar, `PedirCodigo` ofrece la llave o un código, también el de recuperación, y el
// pedido se repite: quien perdió el dispositivo puede quitar su llave o sumar otra. Los códigos de
// recuperación salen de acá apenas llegan (`alCodigos`): los muestra la página, que no los suelta
// hasta que la persona dice que los guardó, y hasta entonces no se consulta nada más.

type Llave = components["schemas"]["LlaveResumen"];

interface Props {
  /** Cambiaron las llaves: la página vuelve a leer su estado. */
  alCambiar: () => void;
  /** Llegaron códigos de recuperación, que se muestran una sola vez. */
  alCodigos: (codigos: string[]) => void;
  /** Terminó la acción: la página sigue a la pantalla siguiente con este aviso. */
  alTerminar: (aviso: string) => void;
}

export function SeccionLlave({ alCambiar, alCodigos, alTerminar }: Props) {
  const id = useId();
  const [llaves, setLlaves] = useState<Llave[] | null>(null);
  const [fallaConsulta, setFallaConsulta] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [listo, setListo] = useState<string | null>(null);
  const [pendiente, setPendiente] = useState<(() => void) | null>(null);
  const consultado = useRef(false);

  const consultar = useCallback(() => {
    setFallaConsulta(null);
    void api
      .GET("/api/v1/auth/llave")
      .then(({ data, error, response }) => {
        if (data !== undefined) setLlaves(data);
        else setFallaConsulta(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => setFallaConsulta(SIN_CONEXION));
  }, []);

  useEffect(() => {
    // StrictMode monta dos veces: la consulta sale una sola.
    if (consultado.current || !llaveDisponible()) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  if (!llaveDisponible()) return null;

  /** Corre un pedido; si falta confirmar, lo deja pendiente hasta que la persona confirme. */
  function intentar<T>(pedido: () => Promise<Resultado<T>>, alListo: (valor: T) => void) {
    setEnviando(true);
    setAviso(null);
    setListo(null);
    void pedido()
      .then((r) => {
        if (r.ok) alListo(r.valor);
        else if (r.faltaConfirmar) setPendiente(() => () => intentar(pedido, alListo));
        else setAviso(r.mensaje);
      })
      .finally(() => setEnviando(false));
  }

  function activar() {
    intentar(registrarLlave, (nuevos) => {
      if (nuevos !== null) alCodigos(nuevos);
      else alTerminar("Listo: tu llave quedó activada.");
    });
  }

  function quitar(llave: Llave) {
    intentar(
      () => quitarLlave(llave.id),
      () => {
        setListo("Listo: quitaste la llave.");
        consultar();
        alCambiar();
      },
    );
  }

  function confirmarSolo() {
    setAviso(null);
    setListo(null);
    setPendiente(() => () => alTerminar("Listo: confirmaste que sos vos, por 10 minutos."));
  }

  if (pendiente !== null)
    return (
      <PedirCodigo
        acto="seguir"
        alVerificar={() => {
          setPendiente(null);
          pendiente();
        }}
        alCancelar={() => setPendiente(null)}
      />
    );

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Tu llave de acceso</h2>
      <p>
        Confirmás con el PIN, la huella o la cara de este dispositivo, sin escribir códigos y sin
        instalar nada.
      </p>
      {listo !== null && <p className="aviso-ok">{listo}</p>}
      {aviso !== null && <p role="alert">{aviso}</p>}
      {fallaConsulta !== null && (
        <>
          <p role="alert">{fallaConsulta}</p>
          <button type="button" onClick={consultar}>
            Reintentar
          </button>
        </>
      )}
      {llaves !== null && llaves.length > 0 && (
        <ul aria-label="Tus llaves">
          {llaves.map((k) => (
            <li key={k.id}>
              {k.nombre}{" "}
              <button
                type="button"
                className="secundario"
                disabled={enviando}
                onClick={() => quitar(k)}
              >
                Quitar
              </button>
            </li>
          ))}
        </ul>
      )}
      {llaves !== null && (
        <button type="button" onClick={activar} disabled={enviando}>
          {llaves.length === 0 ? "Activar en este dispositivo" : "Agregar este dispositivo"}
        </button>
      )}
      {llaves !== null && llaves.length > 0 && (
        <button type="button" className="secundario" onClick={confirmarSolo} disabled={enviando}>
          Confirmar que soy yo
        </button>
      )}
    </section>
  );
}
