"""H3, infraestructura común (`0044`): nadie recibe una facultad económica de aprobar hasta que
decida el propietario (decisión 7 del 21-10).

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, «Infraestructura
común a todas las opciones». La migración que decida quién aprueba saca la facultad de
`SIN_DECIDIR` y cambia estas pruebas en el mismo PR: `0046` lo hizo con `egresos.aprobar_orden`,
que pasó al paquete del Consejo (decisión 2 del propietario, 29-10-2026).
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_mandatos_0012 import alta, escena  # noqa: F401  (`escena` es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

SIN_DECIDIR = ("liquidacion.aprobar", "coeficientes.aprobar")


def _probar(motor, sentencia, params=None) -> None:
    """Corre la sentencia y deshace siempre: no deja el catálogo cambiado."""
    with motor.connect() as c:
        tx = c.begin()
        try:
            c.execute(text(sentencia), params or {})
        finally:
            tx.rollback()


def _niega(motor, sentencia, params=None) -> None:
    with pytest.raises(DBAPIError) as error:
        _probar(motor, sentencia, params)
    assert error.value.orig.diag.sqlstate == "P0001"
    assert error.value.orig.diag.message_hint == "facultad_sin_decidir"


def _paquete(tipo, facultad) -> str:
    return f"insert into nucleo.paquete_facultad (tipo, facultad) values ('{tipo}', '{facultad}')"


OTORGAR = (
    "insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)"
    " values (:c, :m, :f, :d)"
)


def test_ningun_paquete_ni_mandato_tiene_una_facultad_de_aprobar_sin_decidir(
    motor_migrate,
    motor_app,
    escena,  # noqa: F811
):
    """Con el catálogo real y un mandato de cada tipo recién dado de alta."""
    e = escena
    for tipo, persona in (("administrador", e["p1"]), ("miembro_consejo", e["p2"])):
        alta(motor_app, e["cons"], e["per"], e["acta"], tipo=tipo, persona=persona)
    with motor_migrate.connect() as c:
        en_uso = c.execute(
            text(
                "select facultad from nucleo.paquete_facultad where facultad = any(:f)"
                " union all select facultad from nucleo.facultad_otorgada where facultad = any(:f)"
            ),
            {"f": list(SIN_DECIDIR)},
        ).all()
        catalogo = c.execute(
            text("select count(*) from nucleo.facultad where codigo = any(:f) and economica"),
            {"f": list(SIN_DECIDIR)},
        ).scalar_one()
        del_consorcio = c.execute(
            text("select count(*) from nucleo.facultad_otorgada where consorcio_id = :c"),
            {"c": e["cons"]},
        ).scalar_one()
    assert en_uso == []
    assert catalogo == 2  # control: las dos existen en el catálogo como económicas
    assert del_consorcio > 0  # control: las altas sí otorgaron facultades


@pytest.mark.parametrize("facultad", SIN_DECIDIR)
@pytest.mark.parametrize("tipo", ["administrador", "miembro_consejo"])
def test_la_base_niega_agregarla_a_un_paquete(motor_migrate, tipo, facultad):
    _niega(motor_migrate, _paquete(tipo, facultad))


def test_la_base_niega_cambiar_una_facultad_del_paquete_por_una_sin_decidir(motor_migrate):
    _niega(
        motor_migrate,
        "update nucleo.paquete_facultad set facultad = 'coeficientes.aprobar'"
        " where tipo = 'miembro_consejo' and facultad = 'liquidacion.revisar'",
    )


def test_las_demas_facultades_siguen_entrando(motor_migrate):
    """Control: la guarda mira solo las dos. `fondo_reserva.autorizar` y `egresos.aprobar_orden`
    ya están decididas."""
    _probar(motor_migrate, _paquete("miembro_consejo", "documentos.cargar"))
    _probar(motor_migrate, _paquete("administrador", "fondo_reserva.autorizar"))
    _probar(motor_migrate, _paquete("administrador", "egresos.aprobar_orden"))


def test_aprobar_ordenes_esta_solo_en_el_paquete_del_consejo(
    motor_migrate,
    motor_app,
    escena,  # noqa: F811
):
    """Decisión 2 (29-10-2026, `0046`): el consejero la recibe con su alta; el administrador no."""
    e = escena
    with motor_migrate.connect() as c:
        tipos = c.execute(
            text("select tipo::text from nucleo.paquete_facultad"
                 " where facultad = 'egresos.aprobar_orden'")
        ).scalars().all()  # fmt: skip
    assert tipos == ["miembro_consejo"]
    mc = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p2"])
    adm = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="administrador", persona=e["p1"])
    with motor_migrate.connect() as c:
        tiene = dict(
            c.execute(
                text("select m.id, bool_or(f.facultad = 'egresos.aprobar_orden')"
                     " from nucleo.mandato m join nucleo.facultad_otorgada f on f.mandato_id = m.id"
                     " where m.id in (:mc, :adm) group by m.id"),
                {"mc": mc, "adm": adm},
            ).all()
        )  # fmt: skip
    assert tiene == {mc: True, adm: False}


def test_la_base_niega_otorgarla_a_un_mandato(motor_migrate, motor_app, escena):  # noqa: F811
    e = escena
    mc = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p2"])
    datos = {"c": e["cons"], "m": mc, "d": e["acta"]}
    for f in SIN_DECIDIR:
        _niega(motor_migrate, OTORGAR, {**datos, "f": f})
    _niega(
        motor_migrate,
        "update nucleo.facultad_otorgada set facultad = 'liquidacion.aprobar'"
        " where mandato_id = :m and facultad = 'liquidacion.revisar'",
        datos,
    )
    _probar(motor_migrate, OTORGAR, {**datos, "f": "documentos.cargar"})  # control


def test_cem_app_no_escribe_el_catalogo_ni_las_facultades(motor_migrate):
    tablas = ("nucleo.paquete_facultad", "nucleo.facultad_otorgada", "nucleo.facultad")
    privilegios = ("SELECT", "INSERT", "UPDATE", "DELETE")
    with motor_migrate.connect() as c:
        tiene = {
            (t, p): c.execute(
                text("select has_table_privilege('cem_app', :t, :p)"), {"t": t, "p": p}
            ).scalar_one()
            for t in tablas
            for p in privilegios
        }
        ejecuta = c.execute(
            text(
                "select has_function_privilege('cem_app', 'nucleo.negar_facultad_sin_decidir()',"
                " 'EXECUTE')"
            )
        ).scalar_one()
    assert tiene == {(t, p): p == "SELECT" for t in tablas for p in privilegios}
    assert not ejecuta


def _triggers(motor) -> list[str]:
    with motor.connect() as c:
        return sorted(
            c.execute(
                text(
                    "select tgname from pg_trigger where tgname in"
                    " ('paquete_sin_facultad_sin_decidir', 'mandato_sin_facultad_sin_decidir')"
                )
            ).scalars()
        )


def _funcion(motor) -> bool:
    with motor.connect() as c:
        return c.execute(
            text("select to_regprocedure('nucleo.negar_facultad_sin_decidir()') is not null")
        ).scalar_one()


def test_0044_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0043")
    assert _triggers(motor) == [] and not _funcion(motor)
    command.upgrade(cfg, "0044")
    assert _triggers(motor) == [
        "mandato_sin_facultad_sin_decidir",
        "paquete_sin_facultad_sin_decidir",
    ]
    command.downgrade(cfg, "0043")
    assert _triggers(motor) == [] and not _funcion(motor)
    _probar(motor, _paquete("administrador", "liquidacion.aprobar"))  # sin la guarda, entra
    command.upgrade(cfg, "0044")  # control: vuelve a subir limpio
    _niega(motor, _paquete("administrador", "liquidacion.aprobar"))


def test_0044_no_sube_si_ya_hay_una_facultad_sin_decidir(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0043")
    with motor.begin() as c:
        c.execute(text(_paquete("miembro_consejo", "coeficientes.aprobar")))
    with pytest.raises(DBAPIError, match="ya hay una facultad de aprobar sin decidir"):
        command.upgrade(cfg, "0044")
    with motor.begin() as c:
        c.execute(
            text(
                "delete from nucleo.paquete_facultad"
                " where tipo = 'miembro_consejo' and facultad = 'coeficientes.aprobar'"
            )
        )
    command.upgrade(cfg, "0044")  # control: sin la fila, sube
    assert _funcion(motor)


def test_0044_no_sube_si_un_mandato_ya_tiene_una_facultad_sin_decidir(base_aparte):  # noqa: F811
    """La rama de `facultad_otorgada`, que tiene FORCE RLS: el bloque corre como `cem_migrate`
    (BYPASSRLS) y ve la fila aunque no haya consorcio en el ámbito."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0043")
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('SINTETICO') returning id")
        ).scalar_one()
        acta = c.execute(
            text(
                "insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                " referencia_acta, origen) values (:c, 'asamblea', current_date, 'SINTETICO',"
                " 'Acta SINTETICA', 'soporte') returning id"
            ),
            {"c": cons},
        ).scalar_one()
        mandato = c.execute(
            text(
                "insert into nucleo.mandato (consorcio_id, tipo, mandatario_nombre,"
                " mandatario_cuit, decision_id, vigente_desde, origen) values (:c,"
                " 'administrador_externo', 'SINTETICO', '30000000007', :d, current_date,"
                " 'soporte') returning id"
            ),
            {"c": cons, "d": acta},
        ).scalar_one()
        c.execute(text(OTORGAR), {"c": cons, "m": mandato, "f": "liquidacion.aprobar", "d": acta})
    with pytest.raises(DBAPIError, match="ya hay una facultad de aprobar sin decidir"):
        command.upgrade(cfg, "0044")
    with motor.begin() as c:
        c.execute(
            text("delete from nucleo.facultad_otorgada where mandato_id = :m"), {"m": mandato}
        )
    command.upgrade(cfg, "0044")  # control: sin la fila, sube
    assert _funcion(motor)
