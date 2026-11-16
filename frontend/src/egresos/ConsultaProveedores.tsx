import { Fragment, useEffect, useId, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { RUTA_INICIO } from "../enlace";
import { REQUISITOS } from "../permisos";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho, TEXTO_ESTADO_PROVEEDOR } from "./comun";
import { DocumentacionProveedor } from "./DocumentacionProveedor";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { ExigenciaLegal } from "./ExigenciaLegal";
import { MARCA_FALTANTE, MARCA_POR_VENCER, MARCA_VENCIDA } from "./Proveedores";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /proveedores: la consulta de los proveedores y sus seguros para quien no los gestiona
// (RF50 configurable; app 0104; sí del propietario del 15-11-2026, P1 y P2). Por defecto la ve
// todo el Consejo y la administración; la ven también los habitantes que autorice el Consejo
// (app 0105, «Quién ve los proveedores») y, si el equipo los suma en la configuración del edificio,
// todos los de un tipo de vínculo. Solo lee: sin la cuenta del proveedor, sin cargar ni
// cambiar nada. Lo decide la API con `proveedores.ver`, y la base repite la regla.

type ProveedorConsulta = components["schemas"]["ProveedorConsulta"];
type Ficha = components["schemas"]["FichaProveedorConsulta"];

export const RUTA_CONSULTA_PROVEEDORES = "/proveedores";
export const TITULO_CONSULTA_PROVEEDORES = "Proveedores y sus seguros";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | {
      tipo: "ok";
      proveedores: ProveedorConsulta[];
      vencida: ReadonlySet<string>;
      porVencer: ReadonlySet<string>;
      faltante: ReadonlySet<string>;
    };

export function ConsultaProveedores() {
  const elegido = useConsorcio(REQUISITOS.consultaProveedores);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<Ficha | null>(null);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const { abrir: abrirFila, cerrar: cerrarFila, refFila } = useFichas<string>();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/consulta-proveedores", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined)
          setLectura({
            tipo: "ok",
            proveedores: data.elementos,
            vencida: new Set(data.documentacion_vencida),
            porVencer: new Set(data.documentacion_por_vencer),
            faltante: new Set(data.documentacion_faltante),
          });
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

  function verFicha(p: ProveedorConsulta) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/consulta-proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: p.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setFicha(data);
          abrirFila(p.id);
        } else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_CONSULTA_PROVEEDORES} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los proveedores…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "ok" &&
            ficha === null &&
            (lectura.proveedores.length === 0 ? (
              <p className="tarjeta">El edificio no tiene proveedores cargados.</p>
            ) : (
              <ul className="filas-registro" aria-label="Proveedores">
                {lectura.proveedores.map((p) => (
                  <li key={p.id}>
                    <FilaDeRegistro
                      titulo={p.razon_social}
                      refFila={refFila(p.id)}
                      alAbrir={() => verFicha(p)}
                      resumen={[
                        p.rubro,
                        TEXTO_ESTADO_PROVEEDOR[p.estado],
                        lectura.vencida.has(p.id) ? MARCA_VENCIDA : lectura.porVencer.has(p.id) ? MARCA_POR_VENCER : null,
                        lectura.faltante.has(p.id) ? MARCA_FALTANTE : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    />
                  </li>
                ))}
              </ul>
            ))}

          {ficha !== null && (
            <section className="tarjeta" aria-labelledby={`${id}-ficha`}>
              <h2 id={`${id}-ficha`}>{ficha.proveedor.razon_social}</h2>
              <dl className="detalles">
                <dt>CUIT</dt>
                <dd>{ficha.proveedor.cuit ?? "Sin CUIT"}</dd>
                <dt>Rubro</dt>
                <dd>{ficha.proveedor.rubro ?? "Sin rubro"}</dd>
                {(
                  [
                    ["Contacto", ficha.proveedor.contacto],
                    ["Teléfono", ficha.proveedor.telefono],
                    ["WhatsApp", ficha.proveedor.whatsapp],
                    ["Correo", ficha.proveedor.correo],
                    ["Domicilio", ficha.proveedor.domicilio],
                  ] as const
                )
                  .filter(([, valor]) => valor !== null)
                  .map(([nombre, valor]) => (
                    <Fragment key={nombre}>
                      <dt>{nombre}</dt>
                      <dd>{valor}</dd>
                    </Fragment>
                  ))}
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_PROVEEDOR[ficha.proveedor.estado]}</dd>
              </dl>
              <ExigenciaLegal
                consorcio={consorcio}
                proveedorId={ficha.proveedor.id}
                rubros={ficha.rubros_legales}
                faltantes={ficha.faltantes}
                requisitos={ficha.requisitos}
                modo={ficha.configuracion.modo_exigencia}
                puedeCambiar={false}
                aEntrar={aEntrar}
                alCambiar={() => undefined}
              />
              <DocumentacionProveedor
                consorcio={consorcio}
                proveedor={ficha.proveedor}
                documentacion={ficha.documentacion}
                puedeCargar={false}
                requisitos={ficha.requisitos}
                admiteNoVence={false}
                aEntrar={aEntrar}
                alCambiar={() => undefined}
              />
              <div className="acciones">
                <button
                  type="button"
                  className="secundario"
                  onClick={() => {
                    setFicha(null);
                    cerrarFila();
                  }}
                >
                  Volver a la lista
                </button>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
