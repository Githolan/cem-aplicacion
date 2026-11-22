"""Identidad y ámbito por consorcio de cada petición (documento 13, §7.2 y §10.4).

`obtener_identidad` lee la cookie de sesión (`app/identidad/sesiones.py`), la valida en la
base (existe, no está revocada ni vencida, la persona sigue activa), corre el vencimiento
deslizante y, si la petición modifica estado, exige el token CSRF. Sin sesión válida responde
401: no hay ningún atajo de desarrollo que permita entrar sin credenciales. Las pruebas que no
prueban la autenticación la reemplazan con `app.dependency_overrides`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, NoReturn
from uuid import UUID

from cem_contracts.ayuda import AlcanceAyuda
from cem_contracts.errores import CodigoError
from cem_contracts.estados import ModoOperacion, TipoVinculo
from fastapi import Depends, Request, Response
from sqlalchemy import Engine, text

from app import ayuda as estado_ayuda
from app.autorizacion.evaluador import ContextoAutorizacion, Facultad, Mandato, Vinculo
from app.ayuda import AyudaVigente
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
    identidad_humana: UUID | None = None
    """La identidad humana verificada de la persona en este consorcio (A13-C, 0026)."""
    segundo_factor_reciente: bool = False
    """La sesión verificó el segundo factor hace menos de 10 minutos (TB-CEM-3, R10)."""
    identidades: Mapping[UUID, UUID] = field(default_factory=dict)
    """Las identidades de otras personas que necesita la acción; las agrega `Permiso`."""
    roles_configurados: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """Acción → roles que la configuración del consorcio lista para ella (`0104`)."""
    autorizaciones: frozenset[str] = frozenset()
    """Las acciones que el Consejo le autorizó a esta persona y que cuentan hoy (`0105`)."""
    sesion_id: UUID | None = None
    """La sesión de la petición: las funciones económicas de la base la vuelven a comprobar
    (`exigir_acto_economico`). None solo en las pruebas que reemplazan la identidad."""
    ayuda: AyudaVigente | None = None
    """RF53: la sesión es de otra persona, que ayuda a `persona_id` (la asistida) con esta
    autorización. Sin mandatos, facultades ni autorizaciones del Consejo: no se prestan."""

    @property
    def autorizacion(self) -> ContextoAutorizacion:
        identidades = dict(self.identidades)
        if self.identidad_humana is not None:
            identidades[self.persona_id] = self.identidad_humana
        return ContextoAutorizacion(
            persona_id=self.persona_id,
            consorcio_id=self.consorcio_id,
            hoy=self.hoy,
            vinculos=self.vinculos,
            modo=self.modo,
            mandatos=self.mandatos,
            facultades=self.facultades,
            hay_administrador_vigente=self.hay_administrador_vigente,
            identidades=identidades,
            segundo_factor_reciente=self.segundo_factor_reciente,
            roles_configurados=self.roles_configurados,
            autorizaciones=self.autorizaciones,
            ayuda=None if self.ayuda is None else self.ayuda.alcances | {AlcanceAyuda.ORIENTARSE},
        )


def obtener_motor(request: Request) -> Engine:
    """None sin `CEM_DATABASE_URL`: `transaccion()` responde 503 cuando se usa (`app/db.py`)."""
    return request.app.state.motor


MotorDep = Annotated[Engine, Depends(obtener_motor)]


def obtener_identidad(request: Request, response: Response, motor: MotorDep) -> Identidad:
    token = request.cookies.get(sesiones.COOKIE_SESION)
    if not sesiones.token_valido(token):
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Hay que entrar para continuar.")
    # Ninguna persona en el ámbito: la validación la hace una función `security definer`.
    with transaccion(motor, persona_id=None) as conn:
        sesion = sesiones.validar(conn, token)
    if sesion is None:
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Hay que entrar para continuar.")
    sesiones.exigir_csrf(request, token)
    if sesion.renovada:
        sesiones.fijar_sesion(response, token)
    return Identidad(persona_id=sesion.persona_id, sesion_id=sesion.sesion_id)


IdentidadDep = Annotated[Identidad, Depends(obtener_identidad)]


def exigir_ruta_con_ayuda(request: Request) -> None:
    """Dependencia de toda la app (RF53): el encabezado de la ayuda solo vale en una ruta que arma
    el contexto del edificio o en el selector (`app.state.rutas_con_ayuda`). En cualquier otra
    (la cuenta, la privacidad, la entrada, soporte) se rechaza antes de todo: ahí nadie actúa como
    otra persona, y no se ignora en silencio."""
    if estado_ayuda.pedida() is None:
        return
    ruta = request.scope.get("route")
    if (request.method, getattr(ruta, "path", None)) not in request.app.state.rutas_con_ayuda:
        raise ErrorApi(403, CodigoError.AYUDA_NO_ADMITIDA, AYUDA_NO_ADMITIDA)


AYUDA_NO_ADMITIDA = "Esto no se puede hacer mientras se ayuda a otra persona."


def _ayuda_vigente(motor: Engine, identidad: Identidad, consorcio_id: UUID) -> AyudaVigente:
    """La autorización del encabezado, vigente, de la persona de la sesión en este consorcio y de
    una persona asistida que sigue siendo propietaria confirmada (`nucleo.ayuda_vigente`). Si no,
    `rechazar_ayuda_no_vigente`: `ayuda_revocada` a quien la tenía, 404 a cualquier otra persona."""
    try:
        autorizacion = UUID(estado_ayuda.pedida() or "")
    except ValueError:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No se encontró lo pedido.") from None
    with transaccion(motor, persona_id=identidad.persona_id, consorcio_id=consorcio_id) as conn:
        fila = conn.execute(
            text("select otorgante_id, alcances from nucleo.ayuda_vigente(:a, :s)"),
            {"a": autorizacion, "s": identidad.sesion_id},
        ).one_or_none()
    if fila is None:
        rechazar_ayuda_no_vigente(motor, identidad, autorizacion)
    vigente = AyudaVigente(
        autorizacion_id=autorizacion,
        consorcio_id=consorcio_id,
        otorgante_id=fila.otorgante_id,
        autorizado_id=identidad.persona_id,
        alcances=frozenset(AlcanceAyuda(a) for a in fila.alcances),
    )
    estado_ayuda.fijar(vigente)
    return vigente


AYUDA_REVOCADA = "La persona asistida dejó de autorizar esta ayuda."


def rechazar_ayuda_no_vigente(motor: Engine, identidad: Identidad, autorizacion: UUID) -> NoReturn:
    """La autorización del encabezado no vale. Si es de quien pide y la persona asistida la revocó
    o dejó de ser propietaria confirmada, 403 `ayuda_revocada`: la pantalla sale sola de la ayuda.
    Si no, 404, sin decir si existe (`nucleo.ayuda_terminada`)."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        terminada = conn.execute(
            text("select nucleo.ayuda_terminada(:a, :s)"),
            {"a": autorizacion, "s": identidad.sesion_id},
        ).scalar_one()
    if terminada:
        raise ErrorApi(403, CodigoError.AYUDA_REVOCADA, AYUDA_REVOCADA)
    raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No se encontró lo pedido.")


def require_scope(consorcio_id: UUID, identidad: IdentidadDep, motor: MotorDep) -> Contexto:
    """Arma el `Contexto` de la persona en el consorcio de la ruta (C3, C5).

    El ámbito es un vínculo vigente **o** un mandato vigente con cuenta (R2). "Vigente" es con
    la fecha del consorcio (`nucleo.hoy_del_consorcio`), no con la de la base, que corre en GMT.
    Sin ninguno de los dos responde 404, igual que si el consorcio no existiera. Un vínculo
    importado pendiente (RF02, `0024`) no cuenta: no da ámbito ni ninguna concesión por vínculo.

    **Ayuda (RF53).** Con el encabezado de la ayuda, el contexto es el de la persona asistida, solo
    con sus vínculos: sin mandatos, facultades, configuración, autorizaciones del Consejo, identidad
    humana ni segundo factor. Quien actúa sigue siendo la sesión (`sesion_id`, `ayuda`).
    """
    asistida = (
        None if estado_ayuda.pedida() is None else _ayuda_vigente(motor, identidad, consorcio_id)
    )
    p = identidad.persona_id if asistida is None else asistida.otorgante_id
    with transaccion(motor, persona_id=p, consorcio_id=consorcio_id) as conn:
        hoy = conn.execute(
            text(
                "select nucleo.hoy_del_consorcio(c.id) from nucleo.consorcio c"
                " where c.id = :c and c.eliminado_en is null"
            ),
            {"c": consorcio_id},
        ).scalar_one_or_none()
        if hoy is None:  # no existe, está dado de baja (E28) o no está en el ámbito
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No se encontró lo pedido.")
        vinculos = tuple(
            Vinculo(tipo=TipoVinculo(f.tipo), unidad_id=f.unidad_id, confirmado=f.confirmado)
            for f in conn.execute(
                text(
                    "select tipo::text as tipo, unidad_id,"
                    " confirmado_en is not null as confirmado from nucleo.vinculo"
                    " where consorcio_id = :c and persona_id = :p"
                    " and daterange(vigente_desde, vigente_hasta) @> :hoy"
                    # RF02: el importado pendiente no da ámbito ni concesiones (0024).
                    " and id not in (select nucleo.vinculos_importados_pendientes())"
                    " order by tipo, unidad_id"
                ),
                {"c": consorcio_id, "p": p, "hoy": hoy},
            )
        )
        # Ayudando, los mandatos de la asistida no cuentan (RF53): ni para el ámbito.
        mandatos = (
            ()
            if asistida is not None
            else tuple(
                Mandato(
                    id=f.id,
                    consorcio_id=f.consorcio_id,
                    vigente_desde=f.vigente_desde,
                    vigente_hasta=f.vigente_hasta,
                    causa_baja=f.causa_baja,
                    tipo=f.tipo,
                )
                for f in conn.execute(
                    text(
                        "select id, consorcio_id, vigente_desde, vigente_hasta,"
                        " causa_baja::text as causa_baja, tipo::text as tipo from nucleo.mandato"
                        " where consorcio_id = :c and persona_id = :p"
                        " and nucleo.mandato_vigente(id, :hoy) order by id"
                    ),
                    {"c": consorcio_id, "p": p, "hoy": hoy},
                )
            )
        )
        if not vinculos and not mandatos:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No se encontró lo pedido.")
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
                # A14: el único mandato vigente de administración (`0108`).
                "select nucleo.mandato_administracion_vigente(:c, :hoy) is not null"
            ),
            {"c": consorcio_id, "hoy": hoy},
        ).scalar_one()
        # RF50 configurable (0104): qué roles ven lo que la configuración del consorcio decide.
        roles_configurados = (
            {}
            if asistida is not None
            else {
                f.accion: frozenset(f.roles)
                for f in conn.execute(
                    text("select accion, roles from nucleo.roles_configurados(:c)"),
                    {"c": consorcio_id},
                )
            }
        )
        # 0105: lo que el Consejo le autorizó a esta persona (p. ej. ver los proveedores).
        autorizaciones = (
            frozenset()
            if asistida is not None
            else frozenset(
                conn.execute(
                    text("select a from nucleo.acciones_autorizadas(:c) a"), {"c": consorcio_id}
                ).scalars()
            )
        )
        modo = conn.execute(
            text(
                "select modo from nucleo.modo_operacion where consorcio_id = :c"
                " and daterange(vigente_desde, vigente_hasta) @> :hoy"
            ),
            {"c": consorcio_id, "hoy": hoy},
        ).scalar_one_or_none()
        identidad_humana = (
            None
            if asistida is not None
            else conn.execute(
                text(
                    "select identidad_id from nucleo.identidad_persona where consorcio_id = :c"
                    " and persona_id = :p and desvinculada_en is null"
                ),
                {"c": consorcio_id, "p": p},
            ).scalar_one_or_none()
        )
        # R10: sin la sesión de la petición (solo en pruebas) no hay controles económicos.
        controles = None
        if identidad.sesion_id is not None and asistida is None:
            controles = conn.execute(
                text(
                    "select coalesce(segundo_factor_en > now() - :v, false) as segundo_factor"
                    " from nucleo.sesion where id = :s"
                ),
                {
                    "s": identidad.sesion_id,
                    "v": sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE,
                },
            ).one()
    return Contexto(
        persona_id=p,
        consorcio_id=consorcio_id,
        hoy=hoy,
        vinculos=vinculos,
        modo=ModoOperacion(modo) if modo is not None else None,
        mandatos=mandatos,
        facultades=facultades,
        hay_administrador_vigente=hay_administrador,
        identidad_humana=identidad_humana,
        segundo_factor_reciente=controles is not None and controles.segundo_factor,
        roles_configurados=roles_configurados,
        autorizaciones=autorizaciones,
        sesion_id=identidad.sesion_id,
        ayuda=asistida,
    )


ContextoDep = Annotated[Contexto, Depends(require_scope)]
