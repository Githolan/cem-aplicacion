import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { encabezadoCsrf } from "./csrf";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { confirmarConLlave, llaveDisponible, registrarLlave } from "./llaveAcceso";

// La llave de acceso en /cuenta/segundo-factor: activarla es un solo botón, sin aplicación aparte.
// Si el navegador no sabe usarlas, la sección no aparece y queda la aplicación de códigos.

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

  function activar() {
    setEnviando(true);
    setAviso(null);
    setListo(null);
    void registrarLlave()
      .then((r) => {
        if (!r.ok) {
          setAviso(r.mensaje);
          return;
        }
        setCodigos(r.valor);
        setListo("Listo: tu llave quedó activada.");
        consultar();
        alCambiar();
      })
      .finally(() => setEnviando(false));
  }

  async function quitar(llave: Llave) {
    const confirmada = await confirmarConLlave();
    if (!confirmada.ok) return confirmada.mensaje;
    const { error, response } = await api.DELETE("/api/v1/auth/llave/{llave_id}", {
      params: { path: { llave_id: llave.id } },
      headers: encabezadoCsrf(),
    });
    return response.ok ? null : mensajeDeFalla(response.status, codigoDe(error));
  }

  function pedirQuitar(llave: Llave) {
    setEnviando(true);
    setAviso(null);
    setListo(null);
    void quitar(llave)
      .then((mensaje) => {
        if (mensaje !== null) {
          setAviso(mensaje);
          return;
        }
        setListo("Listo: quitaste la llave.");
        consultar();
        alCambiar();
      })
      .catch(() => setAviso(SIN_CONEXION))
      .finally(() => setEnviando(false));
  }

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
                onClick={() => pedirQuitar(k)}
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
    </section>
  );
}
