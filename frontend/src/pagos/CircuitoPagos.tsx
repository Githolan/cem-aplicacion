import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { ESTADO, type Aviso, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { CargandoPagos } from "./CargandoPagos";
import { Bandeja } from "./Bandeja";
import { Ficha, type Seguir } from "./Ficha";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import { CONSULTA_AL_LADO, useAncho } from "./useAncho";

// `/pagos` y `/pagos/:orden`: una bandeja y una ficha (§1). En PC la ficha se abre al lado de la
// bandeja, con la primera orden si no se eligió otra; en tablet y teléfono, aparte (§15).

type Elemento = components["schemas"]["ElementoBandejaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[] };

export function CircuitoPagos() {
  const elegido = useConsorcio(REQUISITOS.pagos);
  return <CircuitoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function CircuitoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const { orden = null } = useParams();
  const navegar = useNavigate();
  const alLado = useAncho(CONSULTA_AL_LADO);
  const veBandeja = alLado || orden === null;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  /** Cambia al terminar un acto: la ficha de al lado, aunque sea la misma orden, se lee de nuevo. */
  const [acto, setActo] = useState(0);
  /** La orden que se abrió sola en PC. Queda fija: la bandeja se reordena al releerse (emitir mueve
   * `actualizada_en`) y cambiar de ficha perdería la cuenta recién abierta. Se suelta al terminar. */
  const [primera, setPrimera] = useState<string | null>(null);

  useEffect(() => {
    if (consorcio === "" || !veBandeja) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setLectura({ tipo: "ok", elementos: data.elementos });
          setPrimera((p) => p ?? data.elementos[0]?.orden_id ?? null);
          setAviso(sinLaFalla);
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  }, [consorcio, veBandeja, vuelta, aEntrar, aInicio]);

  const alCambiar = useCallback(() => setVuelta((v) => v + 1), []);

  const terminar = useCallback(
    (texto: string, seguir?: Seguir) => {
      const irA = (destino: string, aviso = texto) => {
        navegar(destino, { state: { aviso, consorcio } });
        setPrimera(null);
        setVuelta((v) => v + 1);
        setActo((a) => a + 1);
      };
      if (seguir === undefined) return irA(RUTA_PAGOS);
      // Si mientras tanto se abrió otra ficha, esa queda; la bandeja igual cambió.
      const irSiSigue = (destino: string, aviso = texto) => (seguir.abierta.current ? irA(destino, aviso) : setVuelta((v) => v + 1));
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } })
        .then(({ data, response }) => {
          exigirSesion(response);
          // Sin la lista va a la bandeja, que dice qué pasó al leerla.
          const quedan = data?.elementos.filter((e) => e.paso === seguir.paso) ?? [];
          const [siguiente] = quedan;
          if (siguiente === undefined) irSiSigue(RUTA_PAGOS);
          else irSiSigue(rutaFicha(siguiente.orden_id), `${texto} Sigue este, 1 de ${quedan.length}.`);
        })
        .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : irSiSigue(RUTA_PAGOS)));
    },
    [aEntrar, consorcio, navegar],
  );

  function elegir(id: string) {
    setConsorcio(id);
    if (orden !== null) navegar(RUTA_PAGOS);
  }

  const abierta = orden ?? (alLado ? primera : null);
  const conFicha = abierta !== null && consorcio !== "";
  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";

  return (
    <div className={alLado && conFicha ? "circuito-pagos circuito-pagos--al-lado" : "circuito-pagos"}>
      <div>
        {veBandeja && <h1>Pagos</h1>}
        {!veBandeja && <CabeceraInterna titulo="Orden de pago" volver={RUTA_PAGOS} destino="Pagos" />}
        <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={elegir} />
        {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
        {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
        <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
        {veBandeja && <AvisoPantalla aviso={aviso} />}
        {veBandeja && conPantalla && (
          <>
            {lectura.tipo === "cargando" && <CargandoPagos />}
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
            {lectura.tipo === "ok" && <Bandeja elementos={lectura.elementos} consorcio={consorcio} abierta={abierta} />}
          </>
        )}
      </div>
      {conFicha && conPantalla && (
        <div>
          <Ficha key={`${abierta}-${acto}`} consorcio={consorcio} orden={abierta}
            aEntrar={aEntrar} aInicio={aInicio} terminar={terminar} alCambiar={alCambiar} />
        </div>
      )}
    </div>
  );
}
