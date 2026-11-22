import { useEffect, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { RUTA_INICIO } from "../enlace";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { FichaReclamo } from "./FichaReclamo";
import { LARGO_DOCUMENTACION } from "./Solicitudes";
import { resumenDe, SIN_RECLAMOS, TEXTO_ESTADO, tituloDe } from "./textosReclamos";

// Página /reclamos (RF34, app 0123; diseño P0 S7 del repositorio CEM): para quien atiende
// (`reclamos.atender`, el Consejo y la administración), los reclamos de los propietarios sobre la
// liquidación oficial. Los presentados primero; cada ficha ofrece «Responder» y, a quien además
// prepara solicitudes formales, «Pasar a solicitud formal». Un reclamo no cambia ningún importe.

type Lista = components["schemas"]["ListaReclamos"];

export const RUTA_RECLAMOS = "/reclamos";
export const TITULO_RECLAMOS = "Reclamos sobre la liquidación";
export const ATENDIDOS = "Ver los atendidos y retirados";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; lista: Lista };

export function Reclamos() {
  const elegido = useConsorcio(REQUISITOS.reclamos);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const fichas = useFichas<string>();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", lista: data });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, aInicio]);

  const lista = lectura.tipo === "ok" ? lectura.lista : null;
  const abiertos = lista?.elementos.filter((r) => r.estado === "presentado") ?? [];
  const cerrados = lista?.elementos.filter((r) => r.estado !== "presentado") ?? [];
  const fila = (r: Lista["elementos"][number]) => (
    <li key={r.id}>
      <FilaDeRegistro
        titulo={tituloDe(r)}
        resumen={resumenDe(r, true)}
        estado={TEXTO_ESTADO[r.estado]}
        alAbrir={() => fichas.abrir(r.id)}
        refFila={fichas.refFila(r.id)}
        abierta={fichas.abierta === r.id}
      />
    </li>
  );

  return (
    <>
      <CabeceraInterna titulo={TITULO_RECLAMOS} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los reclamos…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lista !== null && (
            <>
              <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
              {lista.elementos.length === 0 ? (
                <p className="tarjeta">{SIN_RECLAMOS}</p>
              ) : (
                <>
                  {abiertos.length > 0 && (
                    <ul className="filas-registro" aria-label={TITULO_RECLAMOS}>
                      {abiertos.map(fila)}
                    </ul>
                  )}
                  {cerrados.length > 0 && (
                    <details>
                      <summary>
                        {ATENDIDOS} ({cerrados.length})
                      </summary>
                      <ul className="filas-registro" aria-label={ATENDIDOS}>
                        {cerrados.map(fila)}
                      </ul>
                    </details>
                  )}
                </>
              )}
              {fichas.abierta !== null && (
                <FichaReclamo
                  key={fichas.abierta}
                  consorcio={consorcio}
                  id={fichas.abierta}
                  lista={lista}
                  largoSolicitud={LARGO_DOCUMENTACION}
                  aEntrar={aEntrar}
                  alCerrar={() => fichas.cerrar()}
                  alTerminar={(texto) => {
                    fichas.cerrar(false);
                    setResultado({ texto, ok: true });
                    setVuelta((v) => v + 1);
                  }}
                />
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
