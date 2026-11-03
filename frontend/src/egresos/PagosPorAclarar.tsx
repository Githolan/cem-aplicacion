import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { RUTA_ENTRAR } from "../enlace";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { ENCABEZADO_IDEMPOTENCIA, importe, mensajeDeError, momento, renovarClave, SEGUNDO_FACTOR_REQUERIDO } from "./comun";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useMontado } from "./useConsorcio";

// Página /egresos/por-aclarar (app 0067, P3 179): quien vio la cuenta en el acto de ejecución y
// ya no ejecuta pagos en ese edificio (renunció o lo reemplazaron) declara que no transfirió.
// Sin esa declaración la orden queda trabada: el Consejo no puede confirmar que no se pagó. Lo
// confirma otro miembro del Consejo. La lista viene de todos los edificios (sin el mandato, el
// edificio ya no aparece en la lista de la persona) y nunca trae la cuenta.

type PorAclarar = components["schemas"]["PagoPorAclarar"];

export const RUTA_POR_ACLARAR = "/egresos/por-aclarar";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: PorAclarar[] };
type Aviso = { texto: string; ok: boolean };

export function PagosPorAclarar() {
  const id = useId();
  const navegar = useNavigate();
  const aEntrar = useCallback(() => navegar(RUTA_ENTRAR, { replace: true }), [navegar]);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [motivos, setMotivos] = useState<Record<string, string>>({});
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<(() => void) | null>(null);
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/pagos-por-aclarar")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", elementos: data.elementos });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: SIN_CONEXION, ok: false });
        }
      });
    return () => {
      vigente = false;
    };
  }, [vuelta, aEntrar]);

  function declarar(p: PorAclarar, reintento = false) {
    const motivo = (motivos[p.orden_id] ?? "").trim();
    if (motivo === "" || !montado.current) return;
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    setEnviando(true);
    setAviso(null);
    void api
      .POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/no-realizado", {
        headers: encabezadoCsrf(),
        params: {
          path: { consorcio_id: p.consorcio_id, orden_id: p.orden_id },
          header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current },
        },
        body: { version: p.version, motivo },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setAviso({ texto: "Listo: declaraste que no transferiste. Ahora lo confirma otro miembro del Consejo.", ok: true });
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const actual = clave.current;
          setPendiente(() => () => {
            clave.current = actual;
            declarar(p, true);
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function alEnviar(evento: FormEvent, p: PorAclarar) {
    evento.preventDefault();
    declarar(p);
  }

  return (
    <>
      <h1>Pagos para aclarar</h1>
      {aviso !== null && (
        <p className={aviso.ok ? "aviso-ok" : "aviso-error"} role={aviso.ok ? "status" : "alert"}>
          {aviso.texto}
        </p>
      )}
      {pendiente !== null && (
        <PedirCodigo
          acto="declarar que no transferiste"
          alVerificar={() => {
            const reintentar = pendiente;
            setPendiente(null);
            reintentar();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}
      {pendiente === null && lectura.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pendiente === null &&
        lectura.tipo === "ok" &&
        (lectura.elementos.length === 0 ? (
          <p className="tarjeta">No tenés pagos para aclarar.</p>
        ) : (
          <>
            <p>
              Viste la cuenta para pagar estas órdenes y ya no ejecutás pagos en ese edificio. Si no transferiste, declaralo:
              sin eso, la orden no puede volver a pagarse. Si transferiste, avisale al Consejo del edificio.
            </p>
            <ul className="versiones" aria-label="Pagos para aclarar">
              {lectura.elementos.map((p) => (
                <li key={p.orden_id} className="tarjeta">
                  <h2>
                    {p.consorcio}: {importe(p.importe_centavos, p.moneda)}
                  </h2>
                  <p>{p.concepto}</p>
                  <p>Viste la cuenta el {momento(p.cuenta_vista_en)}.</p>
                  <form className="formulario" onSubmit={(e) => alEnviar(e, p)}>
                    <label htmlFor={`${id}-${p.orden_id}`}>Por qué no se transfirió</label>
                    <input id={`${id}-${p.orden_id}`} required maxLength={200} disabled={enviando}
                      value={motivos[p.orden_id] ?? ""}
                      onChange={(e) => setMotivos((m) => ({ ...m, [p.orden_id]: e.target.value }))} />
                    <div className="acciones">
                      <button type="submit" disabled={enviando || (motivos[p.orden_id] ?? "").trim() === ""}>
                        No transferí
                      </button>
                    </div>
                  </form>
                </li>
              ))}
            </ul>
          </>
        ))}
    </>
  );
}
