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
import { ListoDelActo } from "./ListoDelActo";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import type { Paso } from "./textos";
import { CONSULTA_AL_LADO, useAncho } from "./useAncho";

// `/pagos` y `/pagos/:orden`: una bandeja y una ficha (§1). En PC la ficha se abre al lado de la
// bandeja, con la primera orden si no se eligió otra; en tablet y teléfono, aparte (§15). En PC,
// terminado un acto, no se abre ninguna orden sola: al lado queda lo que pasó con esa orden y
// «Seguir con lo pendiente» (decisión del propietario del 12-11-2026, P-01).

type Elemento = components["schemas"]["ElementoBandejaPagos"];
type BandejaPagos = components["schemas"]["BandejaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; bandeja: BandejaPagos };
/** El acto que se acaba de terminar en PC: sobre qué orden, qué dijo y a qué paso seguía. */
type Hecho = { orden: string; texto: string; paso: Paso | null };

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
  const [hecho, setHecho] = useState<Hecho | null>(null);

  // Abrir una orden (una fila, «Seguir con lo pendiente») deja atrás lo que pasó con la anterior.
  useEffect(() => {
    if (orden !== null) setHecho(null);
  }, [orden]);

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
          setLectura({ tipo: "ok", elementos: data.elementos, bandeja: data });
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
    (desde: string, texto: string, seguir?: Seguir, paso: Paso | null = null) => {
      if (alLado) {
        navegar(RUTA_PAGOS, { state: { consorcio } });
        setHecho({ orden: desde, texto, paso: seguir?.paso ?? paso });
        setPrimera(null);
        setVuelta((v) => v + 1);
        setActo((a) => a + 1);
        return;
      }
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
    [aEntrar, alLado, consorcio, navegar],
  );

  function elegir(id: string) {
    setConsorcio(id);
    if (orden !== null) navegar(RUTA_PAGOS);
  }

  const abierta = orden ?? (alLado && hecho === null ? primera : null);
  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";
  const listo = alLado && orden === null && conPantalla ? hecho : null;
  const conFicha = abierta !== null && consorcio !== "";
  // Lo pendiente que queda, sin la orden recién hecha: primero lo del mismo paso que se venía haciendo.
  const pendientes =
    listo === null || lectura.tipo !== "ok" ? [] : lectura.elementos.filter((e) => e.pendiente && e.orden_id !== listo.orden);
  const siguiente = pendientes.find((e) => e.paso === listo?.paso) ?? pendientes[0];

  return (
    <div className={alLado && (conFicha || listo !== null) ? "circuito-pagos circuito-pagos--al-lado" : "circuito-pagos"}>
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
            {lectura.tipo === "ok" && (
              <Bandeja bandeja={lectura.bandeja} consorcio={consorcio} abierta={abierta} aEntrar={aEntrar} alCambiar={alCambiar} />
            )}
          </>
        )}
      </div>
      {listo !== null && (
        <div>
          <ListoDelActo key={acto} consorcio={consorcio} orden={listo.orden} texto={listo.texto}
            siguiente={siguiente?.orden_id ?? null} quedan={pendientes.length} aEntrar={aEntrar} />
        </div>
      )}
      {listo === null && abierta !== null && consorcio !== "" && conPantalla && (
        <div>
          <Ficha key={`${abierta}-${acto}`} consorcio={consorcio} orden={abierta}
            aEntrar={aEntrar} aInicio={aInicio} terminar={(texto, seguir, paso) => terminar(abierta, texto, seguir, paso)} alCambiar={alCambiar} />
        </div>
      )}
    </div>
  );
}
