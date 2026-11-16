"""RF50: en una urgencia, el Consejo saltea con motivo el bloqueo por documentación legal (`0106`;
respuesta del propietario del 16-11-2026 a la pregunta 3 del diseño: «si con motivo»).

Los controles pedidos: sin motivo, 422; quien no está en la configuración, 403; con motivo, la
orden pasa. Además: el salteo vale para una sola orden, queda en la ficha de la orden y en la del
proveedor, nada se borra, y quien prepara no saltea su propio bloqueo.

Contra PostgreSQL real. Cada negación lleva su control. Datos SINTETICOS / PRUEBA / NO REAL.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import RAIZ
from tests.test_orden_pago_neutra import niega, nueva_sesion
from tests.test_rf50_configurable_0104 import ASCENSORES, configurar, rubro
from tests.test_rutas_egresos import ORDEN, codigo, get, post, preparar
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MOTIVO = "SINTETICO: pérdida de gas en la cocina del 3° B, el gasista viene hoy"
SALTEAR_SQL = (
    "select numero from nucleo.saltear_documentacion_legal(:proveedor, :motivo, :actor, :origen,"
    " :sesion)"
)


def saltear(e, quien: UUID | None = None, motivo: str = MOTIVO, proveedor=None):
    quien = quien or e.mc1
    return post(e, quien, f"proveedores/{proveedor or e.proveedor}/salteos-documentacion",
                {"motivo": motivo})  # fmt: skip


def bloqueado(e) -> None:
    """El proveedor queda con documentación legal faltante (rubro ascensores, modo `bloquear` por
    defecto): una orden nueva a ese proveedor se niega."""
    r = rubro(e, "ascensores")
    assert r.status_code == 200, r.text
    assert sorted(x["codigo"] for x in r.json()["faltantes"]) == list(ASCENSORES)
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "documentacion_legal_faltante")


def configurar_salteo(motor_app, e, roles=None, horas=None) -> None:
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        c.execute(text("select nucleo.configurar_salteo_documentacion(:roles, :horas, null,"
                       " 'soporte')"), {"roles": roles, "horas": horas})  # fmt: skip


def salteos(motor_migrate, e) -> list[dict]:
    """Los salteos del consorcio de la escena (la base de pruebas tiene los de otras pruebas)."""
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(text(
            "select s.numero, s.registrado_por, s.motivo, u.orden_id, u.version"
            " from nucleo.salteo_documentacion_proveedor s"
            " left join nucleo.salteo_documentacion_uso u on u.salteo_numero = s.numero"
            " where s.consorcio_id = :c order by s.numero"), {"c": e.cons}).mappings()]  # fmt: skip


# Los tres controles pedidos ---------------------------------------------------------------------


def test_con_motivo_la_orden_pasa_una_vez_y_queda_en_las_dos_fichas(motor_migrate, e):
    bloqueado(e)
    r = saltear(e)
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["estado"], s["motivo"], s["orden_id"], s["version"]) == ("vigente", MOTIVO, None,
                                                                      None)  # fmt: skip
    # La orden pasa, preparada por otra persona (la administración).
    o = preparar(e)
    [v] = get(e, e.adm, f"ordenes-pago/{o}").json()["versiones"]
    assert v["salteo_documentacion"]["numero"] == s["numero"]
    assert v["salteo_documentacion"]["motivo"] == MOTIVO
    assert v["salteo_documentacion"]["estado"] == "usado"
    # En la ficha del proveedor, para quien consulta y para quien prepara.
    for quien, ruta in ((e.mc1, f"consulta-proveedores/{e.proveedor}"),
                        (e.adm, f"proveedores/{e.proveedor}")):  # fmt: skip
        [f] = get(e, quien, ruta).json()["salteos"]
        assert (f["estado"], f["orden_id"], f["version"]) == ("usado", str(o), 1)
    # Una sola vez: la orden siguiente vuelve a bloquearse.
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "documentacion_legal_faltante")
    # La auditoría tiene el salteo, sin el motivo (E31).
    with motor_migrate.connect() as c:
        [evento] = c.execute(text(
            "select actor_persona_id, despues from auditoria.eventos"
            " where accion = 'consorcio.proveedor.salteo_documentacion' and consorcio_id = :c"),
            {"c": e.cons}).all()  # fmt: skip
    assert evento.actor_persona_id == e.mc1
    assert evento.despues["numero"] == s["numero"] and "motivo" not in evento.despues
    # Nada se borra ni se cambia.
    for tabla in ("salteo_documentacion_proveedor", "salteo_documentacion_uso"):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(f"delete from nucleo.{tabla}"))
        assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_sin_motivo_se_niega_con_422(motor_migrate, e):
    bloqueado(e)
    for motivo in ("", "   ", "x" * 501):
        assert saltear(e, motivo=motivo).status_code == 422
    # La base dice lo mismo, sin la API.
    niega(motor_migrate, e, e.mc1, SALTEAR_SQL, "motivo_invalido", motivo="  ")
    assert salteos(motor_migrate, e) == []
    assert saltear(e).status_code == 201  # control


def test_quien_no_esta_en_la_configuracion_recibe_403(motor_migrate, e):
    bloqueado(e)
    # La administración prepara; por defecto no saltea.
    assert codigo(saltear(e, e.adm)) == (403, "sin_permiso")
    niega(motor_migrate, e, e.adm, SALTEAR_SQL, "42501", motivo=MOTIVO)
    # Un propietario tampoco.
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    assert codigo(saltear(e, e.prop)) == (403, "sin_permiso")
    niega(motor_migrate, e, e.prop, SALTEAR_SQL, "42501", motivo=MOTIVO)
    assert salteos(motor_migrate, e) == []
    assert saltear(e, e.mc2).status_code == 201  # control: otro miembro del Consejo


# Separación de funciones ------------------------------------------------------------------------


def test_quien_prepara_no_saltea_su_propio_bloqueo(motor_migrate, motor_app, e):
    bloqueado(e)
    # El equipo deja saltear también a la administración: la base igual separa.
    configurar_salteo(motor_app, e, roles=["administrador", "miembro_consejo"])
    assert saltear(e, e.adm).status_code == 201
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "salteo_de_quien_prepara")
    # Control: con el salteo de un miembro del Consejo, la orden pasa y usa ese, no el propio.
    numero = saltear(e, e.mc1).json()["numero"]
    o = preparar(e)
    usados = {s["numero"]: s["orden_id"] for s in salteos(motor_migrate, e)}
    assert usados[numero] == o
    assert [n for n, orden in usados.items() if orden is None] != []  # el de la administración


def test_la_misma_identidad_humana_con_otra_cuenta_tampoco(motor_migrate, e):
    bloqueado(e)
    assert saltear(e, e.mc1).status_code == 201
    # mc1 y adm pasan a ser el mismo ser humano: el salteo de mc1 no le sirve a adm.
    # La identidad humana con que prepara la administración, la de una orden a otro proveedor.
    hecha = preparar(e, proveedor_id=str(e.otro))
    with motor_migrate.begin() as c:
        identidad = c.execute(
            text("select identidad_preparo from nucleo.orden_pago_version where orden_id = :o"),
            {"o": hecha},
        ).scalar_one()
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor disable trigger user"))
        c.execute(text("update nucleo.salteo_documentacion_proveedor set identidad = :i"
                       " where consorcio_id = :c"), {"i": identidad, "c": e.cons})  # fmt: skip
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor enable trigger user"))
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "salteo_de_quien_prepara")
    assert saltear(e, e.mc2).status_code == 201
    preparar(e)  # control: el de otra identidad sí


# Cuándo se registra y cuándo vence ---------------------------------------------------------------


def test_sin_bloqueo_no_hace_falta_saltear(motor_migrate, motor_app, e):
    assert codigo(saltear(e)) == (409, "salteo_innecesario")  # no le falta nada
    bloqueado(e)
    configurar(motor_app, e, modo="avisar")
    assert codigo(saltear(e)) == (409, "salteo_innecesario")  # en «avisar» no bloquea
    configurar(motor_app, e, modo="bloquear")
    assert saltear(e).status_code == 201  # control


def test_un_salteo_por_persona_que_espera_su_orden(motor_migrate, e):
    bloqueado(e)
    assert saltear(e, e.mc1).status_code == 201
    assert codigo(saltear(e, e.mc1)) == (409, "salteo_vigente")
    assert saltear(e, e.mc2).status_code == 201  # control: otro miembro registra el suyo
    # La orden usa el más viejo, el de mc1; el de mc2 sigue esperando.
    preparar(e)
    usado = [s for s in salteos(motor_migrate, e) if s["orden_id"] is not None]
    assert [s["registrado_por"] for s in usado] == [e.mc1]
    assert codigo(saltear(e, e.mc2)) == (409, "salteo_vigente")
    assert saltear(e, e.mc1).status_code == 201  # control: el suyo ya se usó


def test_vencido_ya_no_deja_pasar(motor_migrate, motor_app, e):
    bloqueado(e)
    configurar_salteo(motor_app, e, horas=2)
    s = saltear(e).json()
    with motor_migrate.connect() as c:
        horas = c.execute(text(
            "select vence_en - registrado_en from nucleo.salteo_documentacion_proveedor"
            " where consorcio_id = :c"
        ), {"c": e.cons}).scalar_one()  # fmt: skip
    assert horas == timedelta(hours=2)
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor disable trigger user"))
        c.execute(text("update nucleo.salteo_documentacion_proveedor"
                       " set registrado_en = registrado_en - interval '3 hours',"
                       " vence_en = vence_en - interval '3 hours' where consorcio_id = :c"),
                  {"c": e.cons})  # fmt: skip
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor enable trigger user"))
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "documentacion_legal_faltante")
    [f] = get(e, e.mc1, f"consulta-proveedores/{e.proveedor}").json()["salteos"]
    assert (f["numero"], f["estado"]) == (s["numero"], "vencido")
    assert saltear(e).status_code == 201  # control: vencido, se puede registrar otro
    preparar(e)


def test_cambiar_la_orden_a_un_proveedor_bloqueado_usa_el_salteo(motor_migrate, e):
    bloqueado(e)
    otra = preparar(e, proveedor_id=str(e.otro))
    cambio = ORDEN | {"proveedor_id": str(e.proveedor), "version_esperada": 1}
    assert codigo(post(e, e.adm, f"ordenes-pago/{otra}/versiones", cambio)) == (
        422, "documentacion_legal_faltante",
    )  # fmt: skip
    assert saltear(e).status_code == 201
    r = post(e, e.adm, f"ordenes-pago/{otra}/versiones", cambio)
    assert r.status_code in (200, 201), r.text
    versiones = get(e, e.adm, f"ordenes-pago/{otra}").json()["versiones"]
    assert [v["salteo_documentacion"] is not None for v in versiones] == [False, True]


# Configuración ----------------------------------------------------------------------------------


def test_quien_saltea_se_configura(motor_migrate, motor_app, e):
    bloqueado(e)
    configurar_salteo(motor_app, e, roles=[])
    assert codigo(saltear(e)) == (403, "sin_permiso")
    niega(motor_migrate, e, e.mc1, SALTEAR_SQL, "42501", motivo=MOTIVO)
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        k = c.execute(text("select roles_que_saltean, horas_salteo"
                           " from nucleo.configuracion_proveedores_soporte()")).one()  # fmt: skip
        # Las otras configuraciones conservan los valores del salteo.
        c.execute(text("select nucleo.configurar_proveedores(10, null, null, null, null, null,"
                       " null, 'soporte')"))  # fmt: skip
        despues = c.execute(text(
            "select roles_que_saltean, horas_salteo from nucleo.configuracion_proveedores_soporte()"
        )).one()  # fmt: skip
    assert tuple(k) == tuple(despues) == ([], 24)
    configurar_salteo(motor_app, e, roles=["miembro_consejo"])
    assert saltear(e).status_code == 201  # control


def test_la_configuracion_rechaza_valores_fuera_de_rango(motor_app, e):
    for roles, horas, motivo in (
        (["propietario"], None, "rol_invalido"),
        (None, 0, "horas_invalidas"),
        (None, 169, "horas_invalidas"),
    ):
        with pytest.raises(DBAPIError) as error:
            configurar_salteo(motor_app, e, roles=roles, horas=horas)
        assert error.value.orig.diag.message_hint == motivo
    configurar_salteo(motor_app, e, horas=168)  # control


def test_los_valores_por_defecto_son_los_del_contrato(motor_migrate, motor_app, e):
    from cem_contracts import egresos

    from tests.test_quitar_retirar_0089 import _migracion

    m = _migracion(RAIZ / "migrations" / "versions" / "0106_salteo_documentacion_legal.py")
    assert (m.ROLES_QUE_SALTEAN, m.HORAS_SALTEO, m.MOTIVO_MAXIMO) == (
        egresos.ROLES_QUE_SALTEAN_DOCUMENTACION, egresos.HORAS_SALTEO,
        egresos.LARGO_MOTIVO_SALTEO,
    )  # fmt: skip
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        k = c.execute(text("select roles_que_saltean, horas_salteo"
                           " from nucleo.configuracion_proveedores_soporte()")).one()  # fmt: skip
    assert tuple(k) == (list(m.ROLES_QUE_SALTEAN), m.HORAS_SALTEO)
