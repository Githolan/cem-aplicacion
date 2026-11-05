import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { PedirCodigo } from "./egresos/PedirCodigo";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { llaveDisponible, quitarLlave, registrarLlave, type Resultado } from "./llaveAcceso";

// La llave de acceso en /cuenta/segundo-factor: activarla es un solo botón, sin aplicación aparte.
// Si el navegador no sabe usarlas, la sección no aparece y queda la aplicación de códigos. Cuando
// falta confirmar, `PedirCodigo` ofrece la llave o un código, también el de recuperación, y el
// pedido se repite: quien perdió el dispositivo puede quitar su llave o sumar otra.

type Llave = components["schemas"]["LlaveResumen"];

interface Props {
  alCambiar: () => void;
}

export function SeccionLlave({ alCambiar }: Props) {
  const id = useId();
  const [llaves, setLlaves] = useState<Llave[] | null>(null);
  const [codigos, setCodigos] = useState<string[] | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [listo, setListo] = useState<string | null>(null);
  const [pendiente, setPendiente] = useState<(() => void) | null>(null);
  const consultado = useRef(false);

  const consultar = useCallback(() => {
    void api
      .GET("/api/v1/auth/llave")
      .then(({ data, error, response }) => {
        if (data !== undefined) setLlaves(data);
        else setAviso(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => setAviso(SIN_CONEXION));
  }, []);

  useEffect(() => {
    // StrictMode monta dos veces: la consulta sale una sola.
    if (consultado.current || !llaveDisponible()) return;
    consultado.current = true;
    consultar();
  }, [consultar]);

  if (!llaveDisponible()) return null;

  /** Corre un pedido; si falta confirmar, lo deja pendiente hasta que la persona confirme. */
  function intentar<T>(pedido: () => Promise<Resultado<T>>, alTerminar: (valor: T) => void) {
    setEnviando(true);
    setAviso(null);
    setListo(null);
    void pedido()
      .then((r) => {
        if (r.ok) {
          alTerminar(r.valor);
          consultar();
          alCambiar();
        } else if (r.faltaConfirmar) setPendiente(() => () => intentar(pedido, alTerminar));
        else setAviso(r.mensaje);
      })
      .finally(() => setEnviando(false));
  }

  function activar() {
    intentar(registrarLlave, (nuevos) => {
      setCodigos(nuevos);
      setListo("Listo: tu llave quedó activada.");
    });
  }

  function quitar(llave: Llave) {
    intentar(
      () => quitarLlave(llave.id),
      () => setListo("Listo: quitaste la llave."),
    );
  }

  function confirmarSolo() {
    setAviso(null);
    setListo(null);
    setPendiente(() => () => setListo("Listo: confirmaste que sos vos, por 10 minutos."));
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
      {codigos !== null && (
        <>
          <p>
            Si perdés este dispositivo, cada uno de estos códigos sirve <strong>una sola vez</strong>{" "}
            para entrar en su lugar. Guardalos en un lugar seguro: no los vamos a volver a mostrar.
          </p>
          <ul className="codigos-recuperacion" aria-label="Códigos de recuperación" translate="no">
            {codigos.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
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
