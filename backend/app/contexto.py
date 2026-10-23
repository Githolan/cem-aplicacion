"""Identidad y ámbito por consorcio de cada petición (documento 13, §7.2 y §10.4).

`obtener_identidad` lee la cookie de sesión (`app/identidad/sesiones.py`), la valida en la
base (existe, no está revocada ni vencida, la persona sigue activa), corre el vencimiento
deslizante y, si la petición modifica estado, exige el token CSRF. Sin sesión válida responde
401: no hay ningún atajo de desarrollo que permita entrar sin credenciales. Las pruebas que no
prueban la autenticación la reemplazan con `app.dependency_overrides`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.estados import ModoOperacion, TipoVinculo
from fastapi import Depends, Request, Response
from sqlalchemy import Engine, text

from app.autorizacion.evaluador import ContextoAutorizacion, Facultad, Mandato, Vinculo
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import sesiones


@dataclass(frozen=True)
class Identidad:
    persona_id: UUID
    sesion_id: UUID | None = None
    """La sesión de la petición; None solo en las pruebas que reemplazan la identidad."""


@dataclass(frozen=True)
class Contexto:
    """Lo que la persona es en el consorcio de la ruta, hoy (tabla de verdad, §1.1).

    Se arma una vez por pedido con `hoy = nucleo.hoy_del_consorcio(consorcio)`. Todo es de
    **este** consorcio: las consultas filtran por `consorcio_id` explícito y no confían solo en
    la RLS. Los mandatos son los vigentes según `nucleo.mandato_vigente` (nunca por fechas
    solas: un mandato corregido por `error_carga` no vale para ninguna fecha, A9).
    """

    persona_id: UUID
    consorcio_id: UUID
    hoy: date
    vinculos: tuple[Vinculo, ...]
    """Vínculos vigentes, con su unidad (C5)."""
    modo: ModoOperacion | None
    """Modo vigente hoy en el consorcio; None si todavía no se registró (sin acta de arranque).

    Filtra acciones en las políticas, nunca concede una facultad por sí solo.
    """
    mandatos: tuple[Mandato, ...] = ()
    facultades: tuple[Facultad, ...] = ()
    hay_administrador_vigente: bool = False
    """Si existe el único mandato vigente de administración del consorcio (A14)."""

    @property
    def autorizacion(self) -> ContextoAutorizacion:
        return ContextoAutorizacion(
            persona_id=self.persona_id,
            consorcio_id=self.consorcio_id,
            hoy=self.hoy,
            vinculos=self.vinculos,
            modo=self.modo,
            mandatos=self.mandatos,
            facultades=self.facultades,
            hay_administrador_vigente=self.hay_administrador_vigente,
        )


def obtener_motor(request: Request) -> Engine:
    """None sin `CEM_DATABASE_URL`: `transaccion()` responde 503 cuando se usa (`app/db.py`)."""
    return request.app.state.motor


MotorDep = Annotated[Engine, Depends(obtener_motor)]


def obtener_identidad(request: Request, response: Response, motor: MotorDep) -> Identidad:
    token = request.cookies.get(sesiones.COOKIE_SESION)
    if not sesiones.token_valido(token):
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")
    # Ninguna persona en el ámbito: la validación la hace una función `security definer`.
    with transaccion(motor, persona_id=None) as conn:
        sesion = sesiones.validar(conn, token)
    if sesion is None:
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")
    sesiones.exigir_csrf(request, token)
    if sesion.renovada:
        sesiones.fijar_sesion(response, token)
    return Identidad(persona_id=sesion.persona_id, sesion_id=sesion.sesion_id)


IdentidadDep = Annotated[Identidad, Depends(obtener_identidad)]


def require_scope(consorcio_id: UUID, identidad: IdentidadDep, motor: MotorDep) -> Contexto:
    """Arma el `Contexto` de la persona en el consorcio de la ruta (C3, C5).

    El ámbito es un vínculo vigente **o** un mandato vigente con cuenta (R2). "Vigente" es con
    la fecha del consorcio (`nucleo.hoy_del_consorcio`), no con la de la base, que corre en GMT.
    Sin ninguno de los dos responde 404, igual que si el consorcio no existiera. Un vínculo
    importado pendiente (RF02, `0024`) no cuenta: no da ámbito ni ninguna concesión por vínculo.
    """
    p = identidad.persona_id
    with transaccion(motor, persona_id=p, consorcio_id=consorcio_id) as conn:
        hoy = conn.execute(
            text(
                "select nucleo.hoy_del_consorcio(c.id) from nucleo.consorcio c"
                " where c.id = :c and c.eliminado_en is null"
            ),
            {"c": consorcio_id},
        ).scalar_one_or_none()
        if hoy is None:  # no existe, está dado de baja (E28) o no está en el ámbito
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás.")
        vinculos = tuple(
            Vinculo(tipo=TipoVinculo(f.tipo), unidad_id=f.unidad_id)
            for f in conn.execute(
                text(
                    "select tipo::text as tipo, unidad_id from nucleo.vinculo"
                    " where consorcio_id = :c and persona_id = :p"
                    " and daterange(vigente_desde, vigente_hasta) @> :hoy"
                    # RF02: el importado pendiente no da ámbito ni concesiones (0024).
                    " and id not in (select nucleo.vinculos_importados_pendientes())"
                    " order by tipo, unidad_id"
                ),
                {"c": consorcio_id, "p": p, "hoy": hoy},
            )
        )
        mandatos = tuple(
            Mandato(
                id=f.id,
                consorcio_id=f.consorcio_id,
                vigente_desde=f.vigente_desde,
                vigente_hasta=f.vigente_hasta,
                causa_baja=f.causa_baja,
            )
            for f in conn.execute(
                text(
                    "select id, consorcio_id, vigente_desde, vigente_hasta,"
                    " causa_baja::text as causa_baja from nucleo.mandato"
                    " where consorcio_id = :c and persona_id = :p"
                    " and nucleo.mandato_vigente(id, :hoy) order by id"
                ),
                {"c": consorcio_id, "p": p, "hoy": hoy},
            )
        )
        if not vinculos and not mandatos:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás.")
        facultades = tuple(
            Facultad(codigo=f.facultad, recurso=f.recurso, mandato_id=f.mandato_id)
            for f in conn.execute(
                text(
                    "select facultad, recurso, mandato_id from nucleo.facultad_otorgada"
                    " where consorcio_id = :c and mandato_id = any(:m)"
                    " order by facultad, mandato_id"
                ),
                {"c": consorcio_id, "m": [m.id for m in mandatos]},
            )
        )
        hay_administrador = conn.execute(
            text(
                "select exists (select 1 from nucleo.mandato where consorcio_id = :c"
                " and tipo in ('administrador', 'administrador_externo')"
                " and nucleo.mandato_vigente(id, :hoy))"
            ),
            {"c": consorcio_id, "hoy": hoy},
        ).scalar_one()
        modo = conn.execute(
            text(
                "select modo from nucleo.modo_operacion where consorcio_id = :c"
                " and daterange(vigente_desde, vigente_hasta) @> :hoy"
            ),
            {"c": consorcio_id, "hoy": hoy},
        ).scalar_one_or_none()
    return Contexto(
        persona_id=p,
        consorcio_id=consorcio_id,
        hoy=hoy,
        vinculos=vinculos,
        modo=ModoOperacion(modo) if modo is not None else None,
        mandatos=mandatos,
        facultades=facultades,
        hay_administrador_vigente=hay_administrador,
    )


ContextoDep = Annotated[Contexto, Depends(require_scope)]
