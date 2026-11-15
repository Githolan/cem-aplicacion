"""RF50: el Consejo autoriza a un habitante a ver los proveedores (`0105`; respuesta del
propietario del 15-11-2026: los habitantes autorizados son «los que autorice el Consejo»).

Contra servicios reales (PostgreSQL y Redis). Cada negación lleva su control. Datos SINTETICOS /
PRUEBA / NO REAL.
"""

import json

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import RAIZ, clave_idempotencia
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion
from tests.test_rf50_configurable_0104 import correr  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import cliente, codigo, get
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MOTIVO = "SINTETICO: integra la comisión de obras"
CONSULTA = "select count(*) from nucleo.proveedores_consulta(null, :actor, :origen, :sesion)"
AUTORIZAR = (
    "select nucleo.autorizar_ver_proveedores(:persona, :autorizar, :motivo, :actor, :origen,"
    " :sesion)"
)


def cambiar(e, persona, *, autorizada=True, motivo=MOTIVO, quien=None):
    quien = quien or e.mc1
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/autorizaciones-proveedores/{persona}",
            json={"autorizada": autorizada, "motivo": motivo},
            headers=clave_idempotencia(),
        )


def configurar(motor_app, e, *, roles=None, por_persona=None) -> None:
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        c.execute(
            text("select nucleo.configurar_autorizacion_proveedores(:r, :p, null, 'soporte')"),
            {"r": roles, "p": por_persona},
        )


@pytest.fixture
def h(motor_migrate, e):
    """La escena económica con sesión para el propietario: el habitante que se autoriza."""
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    return e


# Los dos controles del pedido: sin autorización 403, autorizado ve la ficha ---------------------


def test_sin_autorizacion_403_y_autorizado_ve_la_ficha(motor_migrate, motor_app, h):
    assert codigo(get(h, h.prop, "consulta-proveedores")) == (403, "sin_permiso")
    assert codigo(get(h, h.prop, f"consulta-proveedores/{h.proveedor}")) == (403, "sin_permiso")
    niega(motor_migrate, h, h.prop, CONSULTA, "42501")
    r = cambiar(h, h.prop)
    assert r.status_code == 200, r.text
    assert (r.json()["autorizada"], r.json()["motivo"]) == (True, MOTIVO)
    lista = get(h, h.prop, "consulta-proveedores")
    assert lista.status_code == 200, lista.text
    assert {p["id"] for p in lista.json()["elementos"]} == {str(h.proveedor), str(h.otro)}
    ficha = get(h, h.prop, f"consulta-proveedores/{h.proveedor}")
    assert ficha.status_code == 200, ficha.text
    assert ficha.json()["proveedor"]["id"] == str(h.proveedor)
    assert not any("cuenta" in k for k in ficha.json()["proveedor"])
    # La base dice lo mismo, sin la API.
    assert llamar(motor_migrate, h, h.prop, CONSULTA) == 2


def test_retirar_vuelve_a_403_y_nada_se_borra(motor_migrate, motor_app, h):
    assert cambiar(h, h.prop).status_code == 200
    assert get(h, h.prop, "consulta-proveedores").status_code == 200  # control
    r = cambiar(h, h.prop, autorizada=False, motivo="SINTETICO: dejó la comisión")
    assert r.status_code == 200, r.text
    assert r.json()["autorizada"] is False
    assert codigo(get(h, h.prop, "consulta-proveedores")) == (403, "sin_permiso")
    niega(motor_migrate, h, h.prop, CONSULTA, "42501")
    with motor_migrate.connect() as c:
        filas = c.execute(text(
            "select cambio, motivo, registrado_por from nucleo.autorizacion_ver_proveedores"
            " where consorcio_id = :c and persona_id = :p order by numero"),
            {"c": h.cons, "p": h.prop}).all()  # fmt: skip
    assert [tuple(f) for f in filas] == [
        ("autorizar", MOTIVO, h.mc1), ("retirar", "SINTETICO: dejó la comisión", h.mc1),
    ]  # fmt: skip
    for sentencia in ("update nucleo.autorizacion_ver_proveedores set motivo = 'x'",
                      "delete from nucleo.autorizacion_ver_proveedores"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_cada_cambio_queda_en_la_auditoria(motor_migrate, motor_app, h):
    assert cambiar(h, h.prop).status_code == 200
    with transaccion(motor_app, persona_id=h.mc1, consorcio_id=h.cons) as c:
        filas = c.execute(text(
            "select actor_persona_id, antes, despues from auditoria.eventos"
            " where accion = 'consorcio.proveedor.autorizacion_ver' and entidad_id = :p"),
            {"p": h.prop}).all()  # fmt: skip
    assert [tuple(f) for f in filas] == [
        (h.mc1, {"autorizada": False}, {"autorizada": True, "motivo": MOTIVO})
    ]


# Quién autoriza --------------------------------------------------------------------------------


def test_solo_el_consejo_autoriza(motor_migrate, motor_app, h):
    for quien in (h.adm, h.prop):
        assert codigo(cambiar(h, h.prop, quien=quien)) == (403, "sin_permiso")
        assert codigo(get(h, quien, "autorizaciones-proveedores")) == (403, "sin_permiso")
        niega(motor_migrate, h, quien, AUTORIZAR, "42501", persona=h.prop, autorizar=True,
              motivo=MOTIVO)  # fmt: skip
    assert cambiar(h, h.prop).status_code == 200  # control: el Consejo sí


def test_estar_autorizado_a_ver_no_deja_autorizar(motor_migrate, motor_app, h):
    assert cambiar(h, h.prop).status_code == 200
    assert codigo(cambiar(h, h.prop, autorizada=False, quien=h.prop)) == (403, "sin_permiso")


def test_quien_autoriza_lo_fija_la_configuracion(motor_migrate, motor_app, h):
    configurar(motor_app, h, roles=["administrador"])
    assert codigo(cambiar(h, h.prop)) == (403, "sin_permiso")
    niega(motor_migrate, h, h.mc1, AUTORIZAR, "42501", persona=h.prop, autorizar=True,
          motivo=MOTIVO)  # fmt: skip
    assert cambiar(h, h.prop, quien=h.adm).status_code == 200  # control
    configurar(motor_app, h, roles=[])
    assert codigo(cambiar(h, h.prop, autorizada=False, quien=h.adm)) == (403, "sin_permiso")


# Lo que la base exige ---------------------------------------------------------------------------


def test_solo_se_autoriza_a_un_habitante(motor_migrate, motor_app, h):
    sin_vinculo = otra_persona(motor_migrate, h.cons)  # verificada, sin unidad
    assert codigo(cambiar(h, sin_vinculo)) == (422, "persona_sin_vinculo")
    assert cambiar(h, h.prop).status_code == 200  # control


def test_el_motivo_es_obligatorio(motor_migrate, motor_app, h):
    for motivo in ("", "   ", "x" * 501):
        assert codigo(cambiar(h, h.prop, motivo=motivo)) == (422, "peticion_invalida")
        niega(motor_migrate, h, h.mc1, AUTORIZAR, "motivo_invalido", persona=h.prop,
              autorizar=True, motivo=motivo)  # fmt: skip
    assert cambiar(h, h.prop, motivo="x" * 500).status_code == 200  # control


def test_lo_que_ya_esta_asi_se_niega(motor_migrate, motor_app, h):
    assert codigo(cambiar(h, h.prop, autorizada=False)) == (409, "autorizacion_sin_cambios")
    assert cambiar(h, h.prop).status_code == 200  # control
    assert codigo(cambiar(h, h.prop)) == (409, "autorizacion_sin_cambios")


def test_la_lista_muestra_cada_habitante_y_la_ultima_fila(motor_migrate, motor_app, h):
    sin_vinculo = otra_persona(motor_migrate, h.cons)
    r = get(h, h.mc1, "autorizaciones-proveedores")
    assert r.status_code == 200, r.text
    antes = {x["persona_id"]: x for x in r.json()["elementos"]}
    assert antes[str(h.prop)]["autorizada"] is False
    assert antes[str(h.prop)]["motivo"] is None
    assert antes[str(h.prop)]["unidades"]
    assert str(sin_vinculo) not in antes  # sin vínculo no es habitante
    assert cambiar(h, h.prop).status_code == 200
    despues = {x["persona_id"]: x for x in get(h, h.mc1, "autorizaciones-proveedores").json()[
        "elementos"]}  # fmt: skip
    fila = despues[str(h.prop)]
    assert (fila["autorizada"], fila["motivo"]) == (True, MOTIVO)
    assert fila["registrado_por_nombre"] and fila["registrado_en"]


# La configuración: las dos formas conviven ------------------------------------------------------


def test_apagada_la_autorizacion_por_persona_no_cuenta(motor_migrate, motor_app, h):
    assert cambiar(h, h.prop).status_code == 200
    configurar(motor_app, h, por_persona=False)
    assert codigo(get(h, h.prop, "consulta-proveedores")) == (403, "sin_permiso")
    niega(motor_migrate, h, h.prop, CONSULTA, "42501")
    assert codigo(cambiar(h, h.prop, autorizada=False)) == (
        409, "autorizacion_por_persona_apagada")  # fmt: skip
    assert codigo(get(h, h.mc1, "autorizaciones-proveedores")) == (
        409, "autorizacion_por_persona_apagada")  # fmt: skip
    configurar(motor_app, h, por_persona=True)  # control: la autorización seguía registrada
    assert get(h, h.prop, "consulta-proveedores").status_code == 200


def test_el_rol_y_la_persona_conviven(motor_migrate, motor_app, h):
    """Con los propietarios en `roles_que_ven` el propietario ve sin autorización; quitarlos no
    le quita lo que el Consejo le autorizó."""
    with transaccion(motor_app, persona_id=None, consorcio_id=h.cons) as c:
        c.execute(
            text(
                "select nucleo.configurar_proveedores(null, null, null,"
                " array['miembro_consejo', 'propietario'], null, null, null, 'soporte')"
            )
        )
    assert get(h, h.prop, "consulta-proveedores").status_code == 200
    assert cambiar(h, h.prop).status_code == 200
    with transaccion(motor_app, persona_id=None, consorcio_id=h.cons) as c:
        c.execute(
            text(
                "select nucleo.configurar_proveedores(null, null, null,"
                " array['miembro_consejo'], null, null, null, 'soporte')"
            )
        )
    assert get(h, h.prop, "consulta-proveedores").status_code == 200
    # Control: el mismo cambio de los demás valores conservó los parámetros de 0105.
    with motor_migrate.connect() as c:
        fila = c.execute(text(
            "select roles_que_autorizan, autoriza_por_persona from"
            " nucleo.configuracion_proveedores where consorcio_id = :c order by numero desc"
            " limit 1"), {"c": h.cons}).one()  # fmt: skip
    assert tuple(fila) == (["miembro_consejo"], True)


def test_solo_el_equipo_configura_la_autorizacion(motor_migrate, motor_app, h):
    sql = "select nucleo.configurar_autorizacion_proveedores(null, false, :actor, :origen)"
    for persona, actor, origen in ((h.mc1, h.mc1, "api"), (None, None, "api"),
                                   (h.mc1, None, "soporte")):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=persona, consorcio_id=h.cons) as c:
                c.execute(text(sql), {"actor": actor, "origen": origen})
        assert error.value.orig.diag.message_hint == "origen_invalido"
    with pytest.raises(DBAPIError) as error:
        configurar(motor_app, h, roles=["propietario"])  # un vínculo no autoriza
    assert error.value.orig.diag.message_hint == "rol_invalido"
    configurar(motor_app, h, por_persona=False)  # control
    with pytest.raises(DBAPIError) as error:
        configurar(motor_app, h, por_persona=False)
    assert error.value.orig.diag.message_hint == "configuracion_sin_cambios"


def test_los_valores_por_defecto_son_los_del_contrato():
    from cem_contracts import egresos

    from tests.test_quitar_retirar_0089 import _migracion

    m = _migracion(RAIZ / "migrations" / "versions" / "0105_autorizacion_ver_proveedores.py")
    assert (m.ROLES_QUE_AUTORIZAN, m.AUTORIZA_POR_PERSONA, m.MOTIVO_MAXIMO) == (
        egresos.ROLES_QUE_AUTORIZAN_PROVEEDORES, egresos.AUTORIZA_POR_PERSONA,
        egresos.LARGO_MOTIVO_AUTORIZACION,
    )  # fmt: skip


def test_el_script_del_equipo(motor_migrate, motor_app, e, correr):
    resultado, salida, errores = correr("--consorcio", str(e.cons))
    assert (resultado, errores) == (0, "")
    impreso = json.loads(salida)
    assert (impreso["roles_que_autorizan"], impreso["autoriza_por_persona"]) == (
        ["miembro_consejo"], True)  # fmt: skip
    resultado, salida, errores = correr(
        "--consorcio", str(e.cons), "--dias-por-vencer", "15", "--roles-que-autorizan",
        "miembro_consejo,administrador", "--autoriza-por-persona", "no",
    )  # fmt: skip
    assert (resultado, errores) == (0, "")
    impreso = json.loads(salida)
    assert (
        impreso["dias_por_vencer"],
        impreso["roles_que_autorizan"],
        impreso["autoriza_por_persona"],
    ) == (15, ["administrador", "miembro_consejo"], False)
    resultado, salida, errores = correr("--consorcio", str(e.cons), "--roles-que-autorizan",
                                        "inquilino")  # fmt: skip
    assert (resultado, salida) == (1, "")
    assert "rol_invalido" in errores


# La base: privilegios y migración --------------------------------------------------------------


def test_cem_app_no_toca_la_tabla_ni_las_internas(motor_migrate):
    internas = (
        "autorizados_ver_proveedores",
        "es_habitante",
        "puede_autorizar_proveedores",
        "exigir_autorizar_proveedores",
        "configuracion_proveedores_vigente",
    )
    rutas = ("acciones_autorizadas", "autorizar_ver_proveedores",
             "autorizaciones_ver_proveedores_lectura", "configurar_autorizacion_proveedores",
             "configuracion_proveedores_soporte")  # fmt: skip
    with motor_migrate.connect() as c:
        privilegios = [
            c.execute(text("select has_table_privilege('cem_app',"
                           " 'nucleo.autorizacion_ver_proveedores', :p)"), {"p": p}).scalar_one()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        ]  # fmt: skip
        filas = dict(c.execute(text(
            "select p.proname, has_function_privilege('cem_app', p.oid, 'EXECUTE')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo' and p.proname = any(:f)"),
            {"f": list(internas + rutas)}).all())  # fmt: skip
    assert privilegios == [False] * 4
    assert {f: filas[f] for f in internas} == dict.fromkeys(internas, False)
    assert {f: filas[f] for f in rutas} == dict.fromkeys(rutas, True)  # control


def _definiciones(motor) -> dict[str, tuple]:
    funciones = (
        "nucleo.configuracion_proveedores_vigente(uuid)",
        "nucleo.configuracion_proveedores_soporte()",
        "nucleo.configurar_proveedores(integer, text, boolean, text[], text[], text[], uuid, text)",
        "nucleo.roles_configurados(uuid)",
        "nucleo.puede_ver_proveedores(uuid, uuid)",
        "nucleo.exportar_proveedores(uuid, uuid)",
    )
    with motor.connect() as c:
        return {
            f: tuple(c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one())
            for f in funciones
        }  # fmt: skip


def test_0105_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0104")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0105")
    con = _definiciones(motor)
    assert all(con[f][0] != antes[f][0] for f in antes)  # control: la 0105 cambió las seis
    command.downgrade(cfg, "0104")
    assert _definiciones(motor) == antes
    with motor.connect() as c:
        assert (
            c.execute(text("select to_regclass('nucleo.autorizacion_ver_proveedores')")).scalar()
            is None
        )
    command.upgrade(cfg, "0105")
    assert _definiciones(motor) == con
